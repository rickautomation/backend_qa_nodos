import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import (
    AUTOMATION_INTERVAL_SEC,
    PROFILE_PATH,
    PUMP_MAX_RUN_EXTRA_SEC,
    PUMP_PULSE_ACK_TIMEOUT_SEC,
    RELAY_COUNT,
    RELAY_LABELS,
    TIMEZONE,
)
import storage

logger = logging.getLogger("nodos.automation")

_worker_started = False
_worker_lock = threading.Lock()
_profile_cache = None


def load_profile() -> dict:
    global _profile_cache
    if _profile_cache is None:
        with open(PROFILE_PATH, encoding="utf-8") as profile_file:
            _profile_cache = json.load(profile_file)
    return _profile_cache


def reload_profile() -> dict:
    global _profile_cache
    _profile_cache = None
    return load_profile()


def get_cultivation_day(profile: dict) -> int:
    start = date.fromisoformat(profile["dia_inicio"])
    return (date.today() - start).days + 1


def get_current_phase(profile: dict) -> dict:
    day = get_cultivation_day(profile)
    for phase in profile["fases"]:
        start_day, end_day = phase["dias"]
        if start_day <= day <= end_day:
            return {**phase, "dia_cultivo": day}
    last_phase = profile["fases"][-1]
    return {**last_phase, "dia_cultivo": day}


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def _now_in_timezone(now: datetime | None = None) -> datetime:
    tz = ZoneInfo(TIMEZONE)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def _light_window(profile: dict, now: datetime) -> tuple[datetime, datetime]:
    start_hour, start_minute = _parse_hhmm(profile["luz"]["inicio"])
    start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = start + timedelta(hours=profile["luz"]["horas_on"])
    if end.date() > start.date() and now.time() < start.time():
        start -= timedelta(days=1)
        end -= timedelta(days=1)
    return start, end


def is_light_on(profile: dict, now: datetime | None = None) -> bool:
    now = _now_in_timezone(now)
    start, end = _light_window(profile, now)
    return start <= now < end


def get_light_schedule(profile: dict, now: datetime | None = None) -> dict:
    now = _now_in_timezone(now)
    start, end = _light_window(profile, now)
    is_on = start <= now < end
    hours_on = profile["luz"]["horas_on"]

    if is_on:
        next_change = end
        next_state = "off"
    elif now < start:
        next_change = start
        next_state = "on"
    else:
        next_change = start + timedelta(days=1)
        next_state = "on"

    return {
        "timezone": TIMEZONE,
        "timezone_label": "Argentina",
        "now_local": now.strftime("%H:%M"),
        "now_date": now.strftime("%d/%m/%Y"),
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "hours_on": hours_on,
        "is_on": is_on,
        "range_label": f"{start.strftime('%H:%M')} – {end.strftime('%H:%M')}",
        "next_change_at": next_change.strftime("%H:%M"),
        "next_state": next_state,
    }


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _update_mq135_baseline(profile: dict, mq135: float | None) -> float:
    config = profile["mq135"]
    stored = storage.get_automation_value("mq135_baseline")
    if stored:
        baseline = float(stored)
    else:
        baseline = float(config.get("baseline", 1200))

    if mq135 and mq135 > 0 and config.get("auto_baseline", True):
        if mq135 < baseline or baseline <= 0:
            baseline = mq135
        else:
            baseline = (baseline * 0.9) + (mq135 * 0.1)
        storage.set_automation_value("mq135_baseline", str(round(baseline, 1)))

    return baseline


def _default_irrigation_state() -> dict:
    return {
        "phase": "idle",
        "pulse_count": 0,
        "settling_after_reading_id": None,
        "session_started_at": None,
        "pulse_requested_at": None,
    }


def _load_irrigation_state() -> dict:
    raw = storage.get_automation_value("irrigation_state", "")
    if raw:
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            state = _default_irrigation_state()
    else:
        state = _default_irrigation_state()
    return {**_default_irrigation_state(), **state}


def _save_irrigation_state(state: dict) -> None:
    storage.set_automation_value("irrigation_state", json.dumps(state))


def _settling_is_stable(readings: list, required: int, tolerance_pct: float) -> tuple[bool, list[float]]:
    values = []
    for reading in readings:
        soil_pct = storage.parse_soil(reading["payload"])
        if soil_pct is not None:
            values.append(float(soil_pct))

    if len(values) < required:
        return False, values

    window = values[-required:]
    spread = max(window) - min(window)
    return spread <= tolerance_pct, values


def on_pulse_completed(pump_box_id: str) -> None:
    """El nodo bomba ejecutó el micropulso y confirmó con POST."""
    profile = load_profile()
    soil_box_id = profile["nodos"]["suelo"]
    state = _load_irrigation_state()

    if state.get("phase") not in {"awaiting_node", "pulsing"}:
        return

    latest = storage.get_latest_sensor_reading(soil_box_id)
    state["phase"] = "settling"
    state["settling_after_reading_id"] = latest["id"] if latest else 0
    state["pulse_requested_at"] = None
    _save_irrigation_state(state)

    message = f"Bomba: micropulso {state.get('pulse_count', 0)} completado (nodo)"
    logger.info(message)
    storage.log_automation(message)


def _evaluate_irrigation(
    phase: dict,
    pump_box_id: str,
    soil_box_id: str,
    soil_pct: float | None,
    now_utc: datetime,
    dry_run: bool,
) -> tuple[bool, str, dict]:
    pulse_sec = int(phase.get("riego_segundos", 2))
    settle_readings = int(phase.get("riego_lecturas_estabilizar", 3))
    settle_tolerance = float(phase.get("riego_estabilizar_tolerancia_pct", 3.0))
    max_pulses = int(phase.get("riego_max_pulsos", 20))
    suelo_min = phase["suelo_min"]
    suelo_obj = phase["suelo_objetivo"]
    cooldown = timedelta(hours=phase.get("riego_cooldown_horas", 8))

    state = _load_irrigation_state()
    last_watering = _parse_iso(storage.get_automation_value("last_watering_at"))
    pump_pending = storage.get_pump_pulse_request(pump_box_id)
    reason = "Bomba: sin acción"
    current_phase = state.get("phase", "idle")

    def persist() -> None:
        if not dry_run:
            _save_irrigation_state(state)

    def force_pump_off(log_message: str | None = None) -> None:
        if dry_run:
            return
        storage.set_pump_status(pump_box_id, False)
        storage.clear_pump_pulse_request(pump_box_id)

    def end_session(message: str, *, success: bool = False) -> tuple[bool, str]:
        state.clear()
        state.update(_default_irrigation_state())
        if not dry_run:
            storage.set_automation_value(
                "last_watering_success", "true" if success else "false"
            )
            if success and cooldown.total_seconds() > 0:
                storage.set_automation_value("last_watering_at", now_utc.isoformat())
            force_pump_off()
            storage.log_automation(message)
        persist()
        return False, message

    def queue_pulse(message: str) -> tuple[bool, str]:
        if soil_pct is not None and soil_pct >= suelo_obj:
            return end_session(
                f"Bomba: objetivo alcanzado ({soil_pct:.0f}% >= {suelo_obj}%) "
                f"tras {state.get('pulse_count', 0)} pulsos",
                success=True,
            )
        state["phase"] = "awaiting_node"
        state["pulse_requested_at"] = now_utc.isoformat()
        state["pulse_count"] = int(state.get("pulse_count", 0)) + 1
        persist()
        if not dry_run:
            storage.queue_pump_pulse(pump_box_id, pulse_sec)
        return False, message

    if (
        soil_pct is not None
        and soil_pct >= suelo_obj
        and current_phase in {"awaiting_node", "pulsing", "settling"}
    ):
        _, reason = end_session(
            f"Bomba: objetivo alcanzado ({soil_pct:.0f}% >= {suelo_obj}%) "
            f"tras {state.get('pulse_count', 0)} pulsos",
            success=True,
        )

    elif current_phase in {"awaiting_node", "pulsing"}:
        requested_at = _parse_iso(state.get("pulse_requested_at"))
        if (
            not dry_run
            and requested_at
            and now_utc - requested_at >= timedelta(seconds=PUMP_PULSE_ACK_TIMEOUT_SEC)
        ):
            if soil_pct is not None and soil_pct >= suelo_obj:
                _, reason = end_session(
                    f"Bomba: objetivo alcanzado ({soil_pct:.0f}% >= {suelo_obj}%) "
                    f"tras {state.get('pulse_count', 0)} pulsos",
                    success=True,
                )
            else:
                storage.queue_pump_pulse(pump_box_id, pulse_sec)
                state["pulse_requested_at"] = now_utc.isoformat()
                persist()
                reason = "Bomba: reintentando micropulso (sin confirmación del nodo)"
        elif pump_pending:
            reason = (
                f"Bomba: nodo debe ejecutar micropulso {state.get('pulse_count', 0)} "
                f"({pulse_sec}s)"
            )
        else:
            reason = (
                f"Bomba: esperando micropulso {state.get('pulse_count', 0)} en el nodo"
            )

    elif current_phase == "settling":
        after_id = int(state.get("settling_after_reading_id") or 0)
        readings = storage.get_soil_readings_after(
            soil_box_id,
            after_id,
            limit=max(settle_readings + 2, 5),
        )
        stable, values = _settling_is_stable(readings, settle_readings, settle_tolerance)
        current = values[-1] if values else soil_pct

        if soil_pct is not None and soil_pct >= suelo_obj:
            _, reason = end_session(
                f"Bomba: objetivo alcanzado ({soil_pct:.0f}% >= {suelo_obj}%) "
                f"tras {state.get('pulse_count', 0)} pulsos",
                success=True,
            )
        elif not stable:
            spread = None
            if len(values) >= settle_readings:
                window = values[-settle_readings:]
                spread = max(window) - min(window)
            spread_note = f", Δ{spread:.0f}%" if spread is not None else ""
            reason = (
                f"Bomba: estabilizando sensor "
                f"({len(values)}/{settle_readings} lecturas, ±{settle_tolerance:.0f}%"
                f"{spread_note})"
            )
        elif current is None:
            reason = "Bomba: estabilizando sensor (sin % de suelo)"
        elif current >= suelo_obj:
            _, reason = end_session(
                f"Bomba: objetivo alcanzado ({current:.0f}% >= {suelo_obj}%) "
                f"tras {state.get('pulse_count', 0)} pulsos",
                success=True,
            )
        else:
            next_pulse = int(state.get("pulse_count", 0)) + 1
            batch_note = ""
            if max_pulses > 0 and next_pulse > max_pulses and (next_pulse - 1) % max_pulses == 0:
                batch_note = f" (lote {(next_pulse - 1) // max_pulses + 1})"
            _, reason = queue_pulse(
                f"Bomba: suelo {current:.0f}% < {suelo_obj}%, "
                f"micropulso {next_pulse}{batch_note}"
            )

    elif soil_pct is None:
        reason = "Bomba: sin lectura de suelo"
    elif soil_pct >= suelo_obj:
        if not dry_run and (
            current_phase != "idle"
            or pump_pending is not None
            or storage.get_pump_state(pump_box_id).get("status")
        ):
            if current_phase != "idle":
                _, reason = end_session(
                    f"Bomba: objetivo alcanzado ({soil_pct:.0f}% >= {suelo_obj}%) "
                    f"tras {state.get('pulse_count', 0)} pulsos",
                    success=True,
                )
            else:
                force_pump_off()
                storage.log_automation(
                    f"Bomba: apagada (suelo OK {soil_pct:.0f}% >= {suelo_obj}%)"
                )
                reason = (
                    f"Bomba: suelo OK ({soil_pct:.0f}% >= {suelo_obj}%), bomba apagada"
                )
        else:
            reason = f"Bomba: suelo OK ({soil_pct:.0f}% >= {suelo_obj}%)"
    elif soil_pct < suelo_min or (
        soil_pct < suelo_obj
        and storage.get_automation_value("last_watering_success") == "false"
    ):
        if (
            cooldown.total_seconds() > 0
            and last_watering
            and storage.get_automation_value("last_watering_success") == "true"
            and now_utc - last_watering < cooldown
        ):
            remaining = cooldown - (now_utc - last_watering)
            hours_left = max(remaining.total_seconds() / 3600, 0)
            reason = f"Bomba: pausa post-riego ({hours_left:.1f}h restantes)"
        else:
            if soil_pct >= suelo_min:
                reason_prefix = (
                    f"Bomba: suelo {soil_pct:.0f}% < {suelo_obj}%, "
                    "reanudando sesión"
                )
            else:
                reason_prefix = (
                    f"Bomba: suelo {soil_pct:.0f}% < {suelo_min}%, "
                    "sesión de micropulsos"
                )
            state["session_started_at"] = now_utc.isoformat()
            state["pulse_count"] = 0
            persist()
            _, reason = queue_pulse(reason_prefix)
    else:
        if not dry_run and (
            state.get("phase") != "idle"
            or pump_pending is not None
            or storage.get_pump_state(pump_box_id).get("status")
        ):
            if state.get("phase") != "idle":
                _, reason = end_session(
                    f"Bomba: detenida (suelo {soil_pct:.0f}% entre {suelo_min}% y {suelo_obj}%)",
                    success=True,
                )
            else:
                force_pump_off()
                storage.log_automation(
                    f"Bomba: apagada (suelo en banda {soil_pct:.0f}%)"
                )
                reason = (
                    f"Bomba: suelo {soil_pct:.0f}% "
                    f"(entre mín {suelo_min}% y obj {suelo_obj}%), bomba apagada"
                )
        else:
            reason = (
                f"Bomba: suelo {soil_pct:.0f}% "
                f"(entre mín {suelo_min}% y obj {suelo_obj}%)"
            )

    pump_state = storage.get_pump_state(pump_box_id)
    pump_pending = storage.get_pump_pulse_request(pump_box_id)
    max_run_sec = pulse_sec + PUMP_MAX_RUN_EXTRA_SEC
    pump_updated = _parse_iso(pump_state.get("updated_at"))

    if (
        not dry_run
        and pump_state.get("status")
        and pump_updated
        and (now_utc - pump_updated).total_seconds() > max_run_sec
    ):
        force_pump_off()
        if state.get("phase") != "idle":
            state.clear()
            state.update(_default_irrigation_state())
            persist()
        storage.log_automation(
            f"Bomba: apagada por watchdog ({max_run_sec}s, suelo "
            f"{soil_pct if soil_pct is not None else '—'}%)"
        )
        reason = f"Bomba: watchdog apagó riego (> {max_run_sec}s)"
        pump_state = storage.get_pump_state(pump_box_id)
        pump_pending = storage.get_pump_pulse_request(pump_box_id)

    pump_on = bool(pump_state.get("status")) or pump_pending is not None

    irrigation = {
        "phase": state.get("phase", "idle"),
        "pulse_count": int(state.get("pulse_count", 0)),
        "pulse_seconds": pulse_sec,
        "settle_readings": settle_readings,
        "settle_tolerance_pct": settle_tolerance,
        "max_pulses": max_pulses,
        "cooldown_horas": phase.get("riego_cooldown_horas", 0),
        "pulse_pending": pump_pending is not None,
        "message": reason,
    }
    return pump_on, reason, irrigation


def evaluate(profile: dict | None = None, dry_run: bool = False) -> dict:
    profile = profile or load_profile()
    phase = get_current_phase(profile)
    nodes = profile["nodos"]
    relay_map = profile["relays"]

    atmosphere = storage.get_latest_sensor_reading(nodes["atmosfera"])
    soil_reading = storage.get_latest_sensor_reading(nodes["suelo"])

    env = storage.parse_atmosphere(atmosphere["payload"]) if atmosphere else {}
    soil = storage.parse_soil(soil_reading["payload"]) if soil_reading else None
    temp_c = env.get("temp_c")
    hr_pct = env.get("hr_pct")
    mq135 = env.get("mq135")

    baseline = _update_mq135_baseline(profile, mq135) if not dry_run else float(
        storage.get_automation_value("mq135_baseline") or profile["mq135"].get("baseline", 1200)
    )
    mq135_delta = profile["mq135"].get("delta", 300)

    relays = [False] * RELAY_COUNT
    reasons = []

    if is_light_on(profile):
        relays[relay_map["luz"]] = True
        reasons.append("Luz: horario activo")
    else:
        reasons.append("Luz: fuera de horario")

    need_extractor = False
    if temp_c is not None and temp_c > phase["temp_max"]:
        need_extractor = True
        relays[relay_map["ventilador"]] = True
        reasons.append(f"Ventilador+Extractor: temp {temp_c:.1f}°C > {phase['temp_max']}°C")
    if hr_pct is not None and hr_pct > phase["hr_max"]:
        need_extractor = True
        reasons.append(f"Extractor: HR {hr_pct:.1f}% > {phase['hr_max']}%")
    if mq135 is not None and mq135 > 0 and mq135 > baseline + mq135_delta:
        need_extractor = True
        reasons.append(f"Extractor: MQ135 {mq135:.0f} > baseline {baseline:.0f}+{mq135_delta}")

    if need_extractor:
        relays[relay_map["extractor"]] = True

    if (
        phase.get("caloventor")
        and "caloventor" in relay_map
        and temp_c is not None
        and temp_c < phase["temp_min"]
    ):
        relays[relay_map["caloventor"]] = True
        calo_msg = f"Caloventor: temp {temp_c:.1f}°C < {phase['temp_min']}°C"
        if dry_run:
            calo_msg += " (MANUAL: activá AUTO para encender)"
        reasons.append(calo_msg)
    elif temp_c is not None and temp_c < phase["temp_min"]:
        reasons.append(
            f"Temp baja ({temp_c:.1f}°C < {phase['temp_min']}°C) "
            f"sin caloventor en esta etapa"
        )

    if (
        hr_pct is not None
        and hr_pct < phase["hr_min"]
        and not relays[relay_map["extractor"]]
    ):
        relays[relay_map["humidificador"]] = True
        reasons.append(f"Humidificador: HR {hr_pct:.1f}% < {phase['hr_min']}%")
    elif hr_pct is not None and hr_pct < phase["hr_min"]:
        reasons.append(
            f"HR baja ({hr_pct:.1f}% < {phase['hr_min']}%) pero extractor activo"
        )

    now_utc = datetime.now(timezone.utc)
    pump_on, pump_reason, irrigation = _evaluate_irrigation(
        phase,
        nodes["bomba"],
        nodes["suelo"],
        soil,
        now_utc,
        dry_run,
    )

    if irrigation["phase"] != "idle" or pump_on:
        reasons.append(pump_reason)

    evaluation = {
        "mode": storage.get_automation_mode(),
        "profile": profile["nombre"],
        "fase": phase["nombre"],
        "dia_cultivo": phase["dia_cultivo"],
        "sensors": {
            "temp_c": temp_c,
            "hr_pct": hr_pct,
            "mq135": mq135,
            "mq135_baseline": baseline,
            "soil_pct": soil,
        },
        "targets": {
            "hr_min": phase["hr_min"],
            "hr_max": phase["hr_max"],
            "temp_min": phase["temp_min"],
            "temp_max": phase["temp_max"],
            "suelo_min": phase["suelo_min"],
            "suelo_objetivo": phase["suelo_objetivo"],
        },
        "irrigation": irrigation,
        "light_on": relays[relay_map["luz"]],
        "light_schedule": get_light_schedule(profile, now_utc),
        "relays": relays,
        "relay_labels": RELAY_LABELS,
        "pump_on": pump_on,
        "reasons": reasons,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    storage.set_automation_value("last_evaluation", json.dumps(evaluation))
    return evaluation


def apply_evaluation(evaluation: dict) -> None:
    profile = load_profile()
    nodes = profile["nodos"]

    relay_state = storage.get_relay_state(nodes["relays"])
    wire_relays = storage.logical_to_wire(storage.normalize_relays(evaluation["relays"]))
    logical_relays = storage.normalize_relays(evaluation["relays"])
    if relay_state["relays"] != wire_relays:
        storage.set_relay_state(nodes["relays"], wire_relays)
        labels = evaluation.get("relay_labels") or RELAY_LABELS
        relay_log = " ".join(
            f"{labels[i]}:{'ON' if status else 'OFF'}"
            for i, status in enumerate(logical_relays)
            if i < len(labels)
        )
        message = f"AUTO relés -> {relay_log}"
        logger.info(message)
        storage.log_automation(message)


def tick() -> dict:
    _ensure_watering_success_flag()
    mode = storage.get_automation_mode()
    evaluation = evaluate(dry_run=(mode != "AUTO"))
    if mode == "AUTO":
        apply_evaluation(evaluation)
    return evaluation


PHASE_LABELS = {
    "germinacion": "Germinación",
    "plantula": "Plántula",
    "vegetativo": "Vegetativo",
    "floracion": "Floración",
    "floracion_tardia": "Floración tardía",
}


def _phase_label(name: str) -> str:
    return PHASE_LABELS.get(name, name.replace("_", " ").title())


def _range_status(value: float | None, low: float, high: float) -> str:
    if value is None:
        return "unknown"
    if value < low:
        return "low"
    if value > high:
        return "high"
    return "ok"


def _soil_status(value: float | None, trigger: float, target: float) -> str:
    if value is None:
        return "unknown"
    if value < trigger:
        return "low"
    if value > target:
        return "high"
    return "ok"


def _mq135_status(value: float | None, baseline: float, delta: float) -> str:
    if value is None or value <= 0:
        return "unknown"
    if value > baseline + delta:
        return "high"
    return "ok"


def get_cultivation_plan(
    profile: dict | None = None,
    evaluation: dict | None = None,
) -> dict:
    profile = profile or load_profile()
    day = get_cultivation_day(profile)
    current_phase = get_current_phase(profile)
    phases_config = profile["fases"]
    total_days = phases_config[-1]["dias"][1]
    light = profile.get("luz", {})
    iluminacion = profile.get("iluminacion", {})

    fases = []
    active_index = 0
    for index, phase in enumerate(phases_config):
        start_day, end_day = phase["dias"]
        is_active = start_day <= day <= end_day
        if is_active:
            active_index = index
        fases.append(
            {
                "nombre": phase["nombre"],
                "label": _phase_label(phase["nombre"]),
                "dias": [start_day, end_day],
                "duracion": end_day - start_day + 1,
                "activa": is_active,
                "caloventor": bool(phase.get("caloventor")),
                "targets": {
                    "temp_min": phase["temp_min"],
                    "temp_max": phase["temp_max"],
                    "hr_min": phase["hr_min"],
                    "hr_max": phase["hr_max"],
                    "suelo_min": phase["suelo_min"],
                    "suelo_objetivo": phase["suelo_objetivo"],
                },
            }
        )

    sensors = (evaluation or {}).get("sensors", {})
    mq135_cfg = profile.get("mq135", {})
    mq135_baseline = float(
        sensors.get("mq135_baseline")
        or mq135_cfg.get("baseline", 1200)
    )
    mq135_delta = float(mq135_cfg.get("delta", 300))
    sensors_vs_targets = {
        "temp_c": {
            "value": sensors.get("temp_c"),
            "min": current_phase["temp_min"],
            "max": current_phase["temp_max"],
            "status": _range_status(
                sensors.get("temp_c"),
                current_phase["temp_min"],
                current_phase["temp_max"],
            ),
            "unit": "°C",
        },
        "hr_pct": {
            "value": sensors.get("hr_pct"),
            "min": current_phase["hr_min"],
            "max": current_phase["hr_max"],
            "status": _range_status(
                sensors.get("hr_pct"),
                current_phase["hr_min"],
                current_phase["hr_max"],
            ),
            "unit": "%",
        },
        "mq135": {
            "value": sensors.get("mq135"),
            "baseline": mq135_baseline,
            "delta": mq135_delta,
            "max": mq135_baseline + mq135_delta,
            "status": _mq135_status(
                sensors.get("mq135"),
                mq135_baseline,
                mq135_delta,
            ),
            "unit": " ADC",
            "decimals": 0,
        },
        "soil_pct": {
            "value": sensors.get("soil_pct"),
            "min": current_phase["suelo_min"],
            "max": current_phase["suelo_objetivo"],
            "status": _soil_status(
                sensors.get("soil_pct"),
                current_phase["suelo_min"],
                current_phase["suelo_objetivo"],
            ),
            "unit": "%",
        },
    }

    progress_pct = round(min(100.0, max(0.0, (day / total_days) * 100)), 1)
    marker_pct = (
        round(min(100.0, max(0.0, ((day - 0.5) / total_days) * 100)), 1)
        if total_days
        else 0.0
    )

    return {
        "nombre": profile["nombre"],
        "variedad": profile.get("variedad"),
        "dia_inicio": profile["dia_inicio"],
        "dia_cultivo": day,
        "dia_total": total_days,
        "fase_actual": current_phase["nombre"],
        "fase_label": _phase_label(current_phase["nombre"]),
        "fase_index": active_index,
        "progress_pct": progress_pct,
        "marker_pct": marker_pct,
        "luz": {
            "horas_on": light.get("horas_on"),
            "inicio": light.get("inicio"),
            "tipo": iluminacion.get("tipo", "LED full spectrum"),
            "notas": iluminacion.get("notas"),
        },
        "fases": fases,
        "sensors_vs_targets": sensors_vs_targets,
    }


def _ensure_watering_success_flag() -> None:
    if storage.get_automation_value("last_watering_success") is not None:
        return
    for log in storage.get_automation_logs(40):
        message = log["message"]
        if message.startswith("Bomba: objetivo alcanzado"):
            storage.set_automation_value("last_watering_success", "true")
            return
        if message.startswith("Bomba: tope de ") and " pulsos" in message:
            storage.set_automation_value("last_watering_success", "false")
            return
    storage.set_automation_value("last_watering_success", "true")


def _load_cached_evaluation() -> dict:
    raw = storage.get_automation_value("last_evaluation", "{}")
    try:
        evaluation = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return evaluation if isinstance(evaluation, dict) else {}


def _overlay_live_sensors(evaluation: dict, profile: dict) -> dict:
    """Actualiza temp/HR/suelo en la UI sin re-evaluar relés (evita bloquear SQLite)."""
    nodes = profile.get("nodos", {})
    atmosphere = storage.get_latest_sensor_reading(nodes.get("atmosfera", ""))
    soil_reading = storage.get_latest_sensor_reading(nodes.get("suelo", ""))

    env = storage.parse_atmosphere(atmosphere["payload"]) if atmosphere else {}
    soil = storage.parse_soil(soil_reading["payload"]) if soil_reading else None

    synced = dict(evaluation)
    sensors = dict(synced.get("sensors") or {})
    sensors["temp_c"] = env.get("temp_c")
    sensors["hr_pct"] = env.get("hr_pct")
    sensors["mq135"] = env.get("mq135")
    sensors["soil_pct"] = soil
    synced["sensors"] = sensors
    return synced


def _sync_evaluation_with_profile(evaluation: dict, profile: dict) -> dict:
    """Mantiene sensores/relés cacheados pero actualiza metadatos del perfil activo."""
    phase = get_current_phase(profile)
    synced = dict(evaluation)
    synced["profile"] = profile["nombre"]
    synced["fase"] = phase["nombre"]
    synced["dia_cultivo"] = phase["dia_cultivo"]
    synced["targets"] = {
        "hr_min": phase["hr_min"],
        "hr_max": phase["hr_max"],
        "temp_min": phase["temp_min"],
        "temp_max": phase["temp_max"],
        "suelo_min": phase["suelo_min"],
        "suelo_objetivo": phase["suelo_objetivo"],
    }
    return synced


def get_status() -> dict:
    _ensure_watering_success_flag()
    mode = storage.get_automation_mode()
    profile = load_profile()

    evaluation = _load_cached_evaluation()
    if not evaluation:
        evaluation = evaluate(dry_run=(mode != "AUTO"))
    else:
        evaluation = _overlay_live_sensors(evaluation, profile)

    evaluation = _sync_evaluation_with_profile(evaluation, profile)
    plan = get_cultivation_plan(profile, evaluation)
    return {
        "mode": mode,
        "evaluation": evaluation,
        "logs": storage.get_automation_logs_balanced(),
        "cultivation_plan": plan,
    }


def _loop() -> None:
    logger.info("Motor de automatización iniciado (intervalo %ss)", AUTOMATION_INTERVAL_SEC)
    while True:
        try:
            tick()
        except Exception:
            logger.exception("Error en ciclo de automatización")
        time.sleep(AUTOMATION_INTERVAL_SEC)


def start_automation_loop() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        thread = threading.Thread(target=_loop, name="automation-loop", daemon=True)
        thread.start()
