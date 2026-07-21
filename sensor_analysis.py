from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from statistics import mean, median, pstdev

import storage
from config import SOIL_ADC_DRY, SOIL_ADC_WET, SOIL_SENSOR_PIN

_analysis_cache = {"key": None, "at": 0.0, "data": None}
_analysis_lock = threading.Lock()
ANALYSIS_CACHE_SEC = 45


def clear_analysis_cache() -> None:
    with _analysis_lock:
        _analysis_cache["key"] = None
        _analysis_cache["at"] = 0.0
        _analysis_cache["data"] = None


def _parse_ts(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _series(readings: list) -> list[dict]:
    points = []
    for reading in readings:
        parsed = storage.parse_soil_raw(reading["payload"])
        if parsed is None or parsed["pct"] is None:
            continue
        points.append(
            {
                "received_at": reading["received_at"],
                "value": float(parsed["pct"]),
                "raw": parsed["raw"],
                "raw_kind": parsed["kind"],
                "pin": parsed.get("pin"),
            }
        )
    points.sort(key=lambda item: item["received_at"])
    return points


def _values(points: list[dict]) -> list[float]:
    return [point["value"] for point in points]


def _find_step_change(points: list[dict], min_delta: float = 5.0) -> dict | None:
    if len(points) < 2:
        return None

    best = None
    for previous, current in zip(points, points[1:]):
        delta = current["value"] - previous["value"]
        if abs(delta) < min_delta:
            continue
        candidate = {
            "at": current["received_at"],
            "from": previous["value"],
            "to": current["value"],
            "delta": round(delta, 1),
        }
        if best is None or abs(candidate["delta"]) > abs(best["delta"]):
            best = candidate
    return best


def _hourly_buckets(points: list[dict]) -> list[dict]:
    buckets: dict[str, list[float]] = {}
    for point in points:
        hour = _parse_ts(point["received_at"]).strftime("%Y-%m-%d %H:00 UTC")
        buckets.setdefault(hour, []).append(point["value"])

    return [
        {
            "hour": hour,
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": round(mean(values), 1),
        }
        for hour, values in sorted(buckets.items())
    ]


def _chart_hourly_series(points: list[dict]) -> list[dict]:
    buckets: dict[datetime, list[float]] = {}
    for point in points:
        ts = _parse_ts(point["received_at"]).replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(ts, []).append(point["value"])

    return [
        {
            "t": ts.isoformat(),
            "value": round(mean(values), 1),
        }
        for ts, values in sorted(buckets.items())
    ]


def _chart_daily_series(points: list[dict]) -> list[dict]:
    buckets: dict[str, list[float]] = {}
    for point in points:
        day = _parse_ts(point["received_at"]).strftime("%Y-%m-%d")
        buckets.setdefault(day, []).append(point["value"])

    series = []
    for day, values in sorted(buckets.items()):
        ts = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        series.append(
            {
                "t": ts.isoformat(),
                "value": round(mean(values), 1),
            }
        )
    return series


def soil_chart(
    box_id: str,
    view: str = "hours",
    span: int = 24,
    suelo_min: float | None = None,
    suelo_objetivo: float | None = None,
) -> dict:
    view = view if view in {"hours", "days"} else "hours"
    if view == "days":
        span = max(1, min(span, 30))
        hours = span * 24
        history_limit = span * 96
    else:
        span = max(1, min(span, 168))
        hours = span
        history_limit = max(span * 12, 120)

    readings = storage.get_sensor_history(box_id, hours=hours, limit=history_limit)
    points = _series(readings)

    if not points:
        return {
            "box_id": box_id,
            "view": view,
            "span": span,
            "current": None,
            "series": [],
            "thresholds": {
                "min": suelo_min,
                "target": suelo_objetivo,
            },
        }

    if view == "days":
        series = _chart_daily_series(points)
    else:
        series = _chart_hourly_series(points)

    latest = points[-1]
    return {
        "box_id": box_id,
        "view": view,
        "span": span,
        "current": round(latest["value"], 1),
        "updated_at": latest["received_at"],
        "series": series,
        "thresholds": {
            "min": suelo_min,
            "target": suelo_objetivo,
        },
    }


def _recent_window(points: list[dict], minutes: int) -> list[dict]:
    if not points:
        return []
    cutoff = _parse_ts(points[-1]["received_at"]) - timedelta(minutes=minutes)
    return [point for point in points if _parse_ts(point["received_at"]) >= cutoff]


def analyze_soil(
    box_id: str,
    hours: int = 6,
    suelo_min: float = 28,
    suelo_objetivo: float = 38,
    dry_soil_expected_max: float = 25,
    history_limit: int = 180,
) -> dict:
    cache_key = (box_id, hours, suelo_min, suelo_objetivo, history_limit)
    now_mono = time.monotonic()
    with _analysis_lock:
        if (
            _analysis_cache["key"] == cache_key
            and _analysis_cache["data"] is not None
            and now_mono - _analysis_cache["at"] < ANALYSIS_CACHE_SEC
        ):
            return _analysis_cache["data"]

    readings = storage.get_sensor_history(box_id, hours=hours, limit=history_limit)
    points = _series(readings)
    values = _values(points)
    now = datetime.now(timezone.utc)

    if not points:
        return {
            "box_id": box_id,
            "hours": hours,
            "status": "offline",
            "summary": "Sin lecturas en el período analizado.",
            "diagnostics": ["No hay datos del sensor de suelo en SQLite."],
            "stats": {},
            "points": [],
            "hourly": [],
            "irrigation": {},
        }

    latest = points[-1]
    latest_age_min = round(
        max(0.0, (now - _parse_ts(latest["received_at"])).total_seconds()) / 60,
        1,
    )
    last_hour = _recent_window(points, 60)
    last_hour_values = _values(last_hour)
    step_change = _find_step_change(points)

    stats = {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "avg": round(mean(values), 1),
        "median": round(median(values), 1),
        "range": round(max(values) - min(values), 1),
        "latest": latest["value"],
        "latest_at": latest["received_at"],
        "latest_age_min": latest_age_min,
    }
    if len(values) > 1:
        stats["std"] = round(pstdev(values), 2)
    if last_hour_values:
        stats["last_hour_avg"] = round(mean(last_hour_values), 1)
        stats["last_hour_std"] = round(pstdev(last_hour_values), 2) if len(last_hour_values) > 1 else 0.0

    would_irrigate = latest["value"] < suelo_min
    calibration = storage.get_soil_calibration()
    irrigation = {
        "suelo_min": suelo_min,
        "suelo_objetivo": suelo_objetivo,
        "current": latest["value"],
        "would_trigger_now": would_irrigate,
        "distance_to_trigger": round(latest["value"] - suelo_min, 1),
        "distance_to_target": round(suelo_objetivo - latest["value"], 1),
    }

    diagnostics = []
    status = "ok"

    if latest_age_min > 5:
        status = "warning"
        diagnostics.append(f"Última lectura hace {latest_age_min:.0f} min (revisar conexión del nodo).")

    if len(last_hour_values) >= 5 and stats.get("last_hour_std", 1) < 1.0:
        status = "warning"
        diagnostics.append(
            f"En la última hora casi no varía ({stats.get('last_hour_avg')}% ± "
            f"{stats.get('last_hour_std', 0)}). Puede estar pegado o mal contacto con la sonda."
        )

    if latest["value"] > dry_soil_expected_max:
        status = "critical" if latest["value"] >= suelo_objetivo else "warning"
        diagnostics.append(
            f"Lectura actual {latest['value']:.0f}% es alta para tierra seca "
            f"(esperado ≤ {dry_soil_expected_max:.0f}%)."
        )

    diagnostics.append(
        f"Calibración estándar: 0% = {calibration['dry_adc']} ADC, "
        f"100% = {calibration['wet_adc']} ADC."
    )

    if latest.get("raw") is not None:
        diagnostics.append(
            f"Señal actual pin {latest.get('pin') or SOIL_SENSOR_PIN}: {latest.get('raw'):.0f}"
        )

    if step_change:
        diagnostics.append(
            f"Cambio notable {step_change['from']:.0f}% → {step_change['to']:.0f}% "
            f"({step_change['delta']:+.0f} pts) cerca de {step_change['at'][:19]} UTC."
        )
    else:
        diagnostics.append("No hubo saltos grandes de lectura en el período (variación suave o fija).")

    if would_irrigate:
        status = "warning"
        diagnostics.append(
            f"Con {latest['value']:.0f}% el AUTO dispararía riego (< {suelo_min:.0f}%)."
        )
    else:
        diagnostics.append(
            f"Con {latest['value']:.0f}% el AUTO no riega (umbral {suelo_min:.0f}%)."
        )

    if status == "ok":
        summary = "El sensor responde y la lectura es coherente con tierra seca."
    elif status == "warning":
        summary = "Hay señales de alerta: conviene revisar colocación o calibración."
    else:
        summary = "Lectura incompatible con tierra seca; no confiar en AUTO de riego todavía."

    chart_points = [
        {
            "t": point["received_at"],
            "v": point["value"],
            "raw": point.get("raw"),
        }
        for point in points[-60:]
    ]

    result = {
        "box_id": box_id,
        "hours": hours,
        "status": status,
        "summary": summary,
        "diagnostics": diagnostics,
        "stats": stats,
        "step_change": step_change,
        "hourly": _hourly_buckets(points[-120:]),
        "irrigation": irrigation,
        "calibration": storage.get_soil_calibration(),
        "points": chart_points,
    }

    with _analysis_lock:
        _analysis_cache["key"] = cache_key
        _analysis_cache["at"] = now_mono
        _analysis_cache["data"] = result

    return result
