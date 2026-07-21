from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from zoneinfo import ZoneInfo

import storage
from automation import PHASE_LABELS, get_cultivation_day, get_current_phase, load_profile
from config import RELAY_LABELS, TIMEZONE

NODE_DEFINITIONS = [
    {"key": "atmosfera", "title": "Atmósfera", "kind": "sensor", "profile_key": "atmosfera"},
    {"key": "suelo", "title": "Humedad suelo", "kind": "sensor", "profile_key": "suelo"},
    {"key": "bomba", "title": "Bomba de riego", "kind": "actuator", "profile_key": "bomba"},
    {"key": "relays", "title": "Relés", "kind": "actuator", "profile_key": "relays"},
]

METRIC_LABELS = {
    "temp_c": "Temperatura",
    "hr_pct": "HR aire",
    "mq135": "Calidad aire",
    "soil_pct": "Humedad suelo",
    "soil_adc": "Señal suelo",
}


def _phase_label(name: str) -> str:
    return PHASE_LABELS.get(name, name.replace("_", " ").title())


def _parse_ts(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _downsample(points: list[dict], max_points: int = 120) -> list[dict]:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(index * step)] for index in range(max_points)]


def _metric_stats(points: list[dict]) -> dict | None:
    values = [float(point["v"]) for point in points if point.get("v") is not None]
    if not values:
        return None
    return {
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        "avg": round(mean(values), 1),
        "last": round(values[-1], 1),
        "count": len(values),
    }


def resolve_scope(profile: dict, scope: str) -> dict:
    scope = (scope or "etapa").lower()
    if scope not in {"etapa", "cultivo"}:
        scope = "etapa"

    day = get_cultivation_day(profile)
    phase = get_current_phase(profile)
    tz = ZoneInfo(TIMEZONE)
    start_date = date.fromisoformat(profile["dia_inicio"])

    if scope == "etapa":
        phase_start = phase["dias"][0]
        since_date = start_date + timedelta(days=phase_start - 1)
        label = (
            f"{_phase_label(phase['nombre'])} · días {phase['dias'][0]}–{phase['dias'][1]}"
        )
    else:
        since_date = start_date
        total_days = profile["fases"][-1]["dias"][1]
        label = f"Todo el cultivo · día {day} / {total_days}"

    since_local = datetime.combine(since_date, time.min, tzinfo=tz)
    since_utc = since_local.astimezone(timezone.utc)
    return {
        "scope": scope,
        "label": label,
        "since": since_utc.isoformat(),
        "dia_cultivo": day,
        "fase_actual": phase["nombre"],
        "fase_label": _phase_label(phase["nombre"]),
    }


def get_sensor_history_since(box_id: str, since: datetime, limit: int | None = None) -> list:
    if not box_id:
        return []
    return storage.get_sensor_history_since(box_id, since, limit=limit or 400)


def _atmosphere_series(readings: list) -> dict:
    series = {"temp_c": [], "hr_pct": [], "mq135": []}
    for reading in readings:
        parsed = storage.parse_atmosphere(reading["payload"])
        timestamp = reading["received_at"]
        if parsed.get("temp_c") is not None:
            series["temp_c"].append({"t": timestamp, "v": parsed["temp_c"]})
        if parsed.get("hr_pct") is not None:
            series["hr_pct"].append({"t": timestamp, "v": parsed["hr_pct"]})
        if parsed.get("mq135") is not None:
            series["mq135"].append({"t": timestamp, "v": parsed["mq135"]})
    return series


def _soil_series(readings: list) -> dict:
    series = {"soil_pct": [], "soil_adc": []}
    for reading in readings:
        parsed = storage.parse_soil_raw(reading["payload"])
        timestamp = reading["received_at"]
        if parsed and parsed.get("pct") is not None:
            series["soil_pct"].append({"t": timestamp, "v": parsed["pct"]})
        if parsed and parsed.get("raw") is not None:
            series["soil_adc"].append({"t": timestamp, "v": parsed["raw"]})
    return series


def _parse_relay_log(message: str) -> list[dict] | None:
    for prefix in ("AUTO relés -> ", "MANUAL relés -> "):
        if not message.startswith(prefix):
            continue
        items = []
        for label in RELAY_LABELS:
            token = f"{label}:"
            index = message.find(token)
            if index == -1:
                continue
            state_text = message[index + len(token) :]
            if state_text.startswith("ON"):
                items.append({"label": label, "on": True})
            elif state_text.startswith("OFF"):
                items.append({"label": label, "on": False})
        return items or None
    return None


def _is_pump_history_event(message: str) -> bool:
    if not message.startswith("Bomba:"):
        return False
    return message.startswith(
        (
            "Bomba: micropulso ",
            "Bomba: objetivo alcanzado",
            "Bomba: tope de ",
            "Bomba: apagada",
            "Bomba: manual ",
        )
    )


def _build_relay_events(logs: list) -> list[dict]:
    events = []
    for entry in logs:
        relays = _parse_relay_log(entry["message"])
        if not relays:
            continue
        source = "manual" if entry["message"].startswith("MANUAL") else "auto"
        events.append(
            {
                "at": entry["created_at"],
                "source": source,
                "relays": relays,
                "display": entry["message"],
            }
        )
    return events


def _build_pump_events(logs: list) -> list[dict]:
    events = []
    for entry in logs:
        message = entry["message"]
        if not _is_pump_history_event(message):
            continue
        event_type = "generic"
        if message.startswith("Bomba: micropulso "):
            event_type = "pulse"
        elif message.startswith("Bomba: objetivo alcanzado"):
            event_type = "target"
        elif message.startswith("Bomba: tope de "):
            event_type = "max_pulses"
        elif message.startswith("Bomba: manual "):
            event_type = "manual"
        elif "watchdog" in message:
            event_type = "watchdog"
        events.append(
            {
                "at": entry["created_at"],
                "type": event_type,
                "message": message,
                "display": message.replace("Bomba: ", "", 1),
            }
        )
    return events


def _build_sensor_node(
    definition: dict,
    box_id: str,
    readings: list,
    latest: dict | None,
) -> dict:
    if definition["key"] == "atmosfera":
        series = _atmosphere_series(readings)
        current = storage.parse_atmosphere(latest["payload"]) if latest else {}
    else:
        series = _soil_series(readings)
        current = {}
        if latest:
            parsed = storage.parse_soil_raw(latest["payload"])
            current = {
                "soil_pct": parsed["pct"] if parsed else storage.parse_soil(latest["payload"]),
                "soil_adc": parsed["raw"] if parsed else None,
            }

    age = storage._reading_age_seconds(latest["received_at"]) if latest else None
    status = storage._card_status(age, 120) if latest else "sin_datos"

    metrics = {}
    chart_series = {}
    for key, points in series.items():
        if not points:
            continue
        metrics[key] = _metric_stats(points)
        chart_series[key] = _downsample(points)

    recent = []
    for reading in reversed(readings[-25:]):
        recent.append(
            {
                "received_at": reading["received_at"],
                "display": storage.format_sensor_payload(reading["payload"]),
            }
        )

    result = {
        "key": definition["key"],
        "title": definition["title"],
        "kind": "sensor",
        "box_id": box_id,
        "status": status,
        "last_at": latest["received_at"] if latest else None,
        "age_seconds": age,
        "current": current,
        "metrics": metrics,
        "series": chart_series,
        "recent": recent,
        "reading_count": len(readings),
    }
    if definition["key"] == "suelo":
        result["calibration"] = storage.get_soil_calibration()
    return result


def _build_pump_node(box_id: str, logs: list | None = None) -> dict:
    state = storage.get_pump_state(box_id) if box_id else None
    last_seen_at = state.get("last_seen_at") if state else None
    updated_at = state.get("updated_at") if state else None
    age = storage._reading_age_seconds(last_seen_at) if last_seen_at else None
    status = storage._actuator_status(last_seen_at) if state else "sin_datos"

    events = _build_pump_events(logs or [])

    return {
        "key": "bomba",
        "title": "Bomba de riego",
        "kind": "actuator",
        "box_id": box_id,
        "status": status,
        "last_at": last_seen_at or updated_at,
        "age_seconds": age,
        "state_updated_at": updated_at,
        "current": {"active": bool(state and state.get("status"))},
        "metrics": {},
        "series": {},
        "recent": [],
        "events": events,
        "reading_count": len(events),
        "detail": "ON" if state and state.get("status") else "OFF",
    }


def _build_relays_node(box_id: str, logs: list | None = None) -> dict:
    if not box_id:
        return {
            "key": "relays",
            "title": "Relés",
            "kind": "actuator",
            "box_id": "",
            "status": "sin_datos",
            "current": {},
            "metrics": {},
            "series": {},
            "recent": [],
            "events": [],
            "reading_count": 0,
            "relays": [],
        }

    logical = storage.get_relay_state_logical(box_id)
    relays = logical.get("relays", [])
    relay_items = []
    for index, on in enumerate(relays):
        if index >= len(RELAY_LABELS):
            continue
        label = RELAY_LABELS[index]
        if label.startswith("R") and label[1:].isdigit():
            continue
        relay_items.append({"label": label, "on": bool(on)})

    last_seen_at = storage.get_relay_last_seen(box_id)
    age = storage._reading_age_seconds(last_seen_at) if last_seen_at else None

    events = _build_relay_events(logs or [])

    return {
        "key": "relays",
        "title": "Relés",
        "kind": "actuator",
        "box_id": box_id,
        "status": storage._actuator_status(last_seen_at),
        "last_at": last_seen_at,
        "age_seconds": age,
        "current": {"active_count": sum(1 for item in relay_items if item["on"])},
        "metrics": {},
        "series": {},
        "recent": [],
        "events": events,
        "reading_count": len(events),
        "relays": relay_items,
    }


def get_readings_hub(profile: dict | None = None, scope: str = "etapa") -> dict:
    profile = profile or load_profile()
    scope_info = resolve_scope(profile, scope)
    since = _parse_ts(scope_info["since"])
    nodos = profile.get("nodos", {})
    sensor_limit = 600 if scope_info["scope"] == "cultivo" else 350
    scope_logs = storage.get_automation_actuator_logs_since(since, limit=120)
    nodes = []

    for definition in NODE_DEFINITIONS:
        box_id = nodos.get(definition["profile_key"], "")
        if definition["kind"] == "sensor":
            readings = (
                get_sensor_history_since(box_id, since, limit=sensor_limit) if box_id else []
            )
            latest = readings[-1] if readings else None
            if not latest and box_id:
                latest = storage.get_latest_sensor_reading(box_id)
            nodes.append(_build_sensor_node(definition, box_id, readings, latest))
        elif definition["key"] == "bomba":
            nodes.append(_build_pump_node(box_id, scope_logs))
        elif definition["key"] == "relays":
            nodes.append(_build_relays_node(box_id, scope_logs))

    online = sum(1 for node in nodes if node["status"] == "online")
    return {
        **scope_info,
        "nodes": nodes,
        "summary": {
            "total": len(nodes),
            "online": online,
            "configured": sum(1 for node in nodes if node.get("box_id")),
        },
    }
