import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from config import (
    DB_PATH,
    DATA_DIR,
    DEFAULT_RELAYS_WIRE,
    PUMP_ID_DEFAULT,
    RELAY_ALWAYS_ON_INDICES,
    RELAY_COUNT,
    RELAY_LABELS,
    RELAY_TRUE_CUTS_POWER,
    SOIL_ADC_DRY,
    SOIL_ADC_WET,
    SOIL_SENSOR_PIN,
    SENSOR_RETENTION_DAYS,
)


def logical_to_wire(relays: list) -> list:
    if not RELAY_TRUE_CUTS_POWER:
        return [bool(value) for value in relays]
    return [not bool(value) for value in relays]


def wire_to_logical(relays: list) -> list:
    if not RELAY_TRUE_CUTS_POWER:
        return [bool(value) for value in relays]
    return [not bool(value) for value in relays]


def normalize_relays(relays: list, count: int = RELAY_COUNT) -> list:
    values = [bool(value) for value in relays]
    if len(values) < count:
        if len(values) == 4:
            # Expande módulo viejo de 4 → 8, resto apagado (wire true)
            values.extend([True] * (count - len(values)))
        else:
            values.extend([True] * (count - len(values)))
    return values[:count]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with _connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pump_states (
                box_id TEXT PRIMARY KEY,
                bomba INTEGER NOT NULL DEFAULT 1,
                status INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS relay_states (
                box_id TEXT PRIMARY KEY,
                relays TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                box_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                payload TEXT NOT NULL,
                received_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sensor_readings_box_id
                ON sensor_readings(box_id);
            CREATE INDEX IF NOT EXISTS idx_sensor_readings_received_at
                ON sensor_readings(received_at);

            CREATE INDEX IF NOT EXISTS idx_sensor_readings_box_id_id
                ON sensor_readings(box_id, id);

            CREATE TABLE IF NOT EXISTS automation_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS automation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                message TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS node_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                box_id TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                received_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_node_logs_box_id
                ON node_logs(box_id);
            CREATE INDEX IF NOT EXISTS idx_node_logs_received_at
                ON node_logs(received_at);
            """
        )
        _ensure_automation_defaults(conn)
        _ensure_schema_migrations(conn)

    try:
        prune_sensor_readings()
    except sqlite3.OperationalError:
        pass


def _ensure_automation_defaults(conn) -> None:
    defaults = {
        "mode": "MANUAL",
        "pump_pulse_started_at": "",
        "last_watering_at": "",
        "mq135_baseline": "",
        "last_evaluation": "{}",
        "soil_adc_dry": "",
        "soil_adc_wet": "",
    }
    for key, value in defaults.items():
        conn.execute(
            """
            INSERT INTO automation_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, value),
        )


def _ensure_schema_migrations(conn) -> None:
    pump_cols = {row[1] for row in conn.execute("PRAGMA table_info(pump_states)")}
    if "last_seen_at" not in pump_cols:
        conn.execute("ALTER TABLE pump_states ADD COLUMN last_seen_at TEXT")

    relay_cols = {row[1] for row in conn.execute("PRAGMA table_info(relay_states)")}
    if "last_seen_at" not in relay_cols:
        conn.execute("ALTER TABLE relay_states ADD COLUMN last_seen_at TEXT")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sensor_readings_box_received
            ON sensor_readings(box_id, received_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_automation_logs_created_at
            ON automation_logs(created_at)
        """
    )


@contextmanager
def _connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_pump_state(box_id: str) -> dict:
    with _connection() as conn:
        row = conn.execute(
            "SELECT bomba, status, updated_at, last_seen_at FROM pump_states WHERE box_id = ?",
            (box_id,),
        ).fetchone()

        if row:
            state = {
                "bomba": row["bomba"],
                "status": bool(row["status"]),
                "updated_at": row["updated_at"],
                "last_seen_at": row["last_seen_at"],
            }
        else:
            now = _utc_now()
            conn.execute(
                """
                INSERT INTO pump_states (box_id, bomba, status, updated_at, last_seen_at)
                VALUES (?, ?, 0, ?, NULL)
                """,
                (box_id, PUMP_ID_DEFAULT, now),
            )
            state = {
                "bomba": PUMP_ID_DEFAULT,
                "status": False,
                "updated_at": now,
                "last_seen_at": None,
            }

    pulse = get_pump_pulse_request(box_id)
    if pulse:
        state["pulse"] = {
            "request_id": pulse["request_id"],
            "seconds": pulse["seconds"],
        }
    return state


def touch_pump_seen(box_id: str) -> None:
    now = _utc_now()
    with _connection() as conn:
        conn.execute(
            """
            UPDATE pump_states
            SET last_seen_at = ?
            WHERE box_id = ?
            """,
            (now, box_id),
        )
        if conn.total_changes == 0:
            conn.execute(
                """
                INSERT INTO pump_states (box_id, bomba, status, updated_at, last_seen_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                (box_id, PUMP_ID_DEFAULT, now, now),
            )


def touch_relay_seen(box_id: str) -> None:
    now = _utc_now()
    with _connection() as conn:
        conn.execute(
            """
            UPDATE relay_states
            SET last_seen_at = ?
            WHERE box_id = ?
            """,
            (now, box_id),
        )
        if conn.total_changes == 0:
            relays = _enforce_always_on_wire(list(DEFAULT_RELAYS_WIRE))
            conn.execute(
                """
                INSERT INTO relay_states (box_id, relays, updated_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                """,
                (box_id, json.dumps(relays), now, now),
            )


def get_relay_last_seen(box_id: str) -> str | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT last_seen_at FROM relay_states WHERE box_id = ?",
            (box_id,),
        ).fetchone()
    return row["last_seen_at"] if row else None


def get_pump_pulse_request(box_id: str) -> dict | None:
    raw = get_automation_value(f"pump_pulse_request:{box_id}", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def queue_pump_pulse(box_id: str, seconds: int) -> dict:
    request = {
        "request_id": uuid.uuid4().hex[:12],
        "seconds": int(seconds),
        "created_at": _utc_now(),
    }
    set_automation_value(f"pump_pulse_request:{box_id}", json.dumps(request))
    return request


def clear_pump_pulse_request(box_id: str) -> None:
    set_automation_value(f"pump_pulse_request:{box_id}", "")


def complete_pump_pulse(box_id: str, request_id: str) -> bool:
    pending = get_pump_pulse_request(box_id)
    if not pending or pending.get("request_id") != request_id:
        return False
    clear_pump_pulse_request(box_id)
    set_pump_status(box_id, False)
    return True


def set_pump_status(box_id: str, status: bool) -> dict:
    state = get_pump_state(box_id)
    now = _utc_now()

    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO pump_states (box_id, bomba, status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(box_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (box_id, state["bomba"], int(status), now),
        )

    state["status"] = status
    if not status:
        clear_pump_pulse_request(box_id)
    return state


def _enforce_always_on_relays(relays: list) -> list:
    enforced = [bool(value) for value in relays]
    for index in RELAY_ALWAYS_ON_INDICES:
        if 0 <= index < len(enforced):
            enforced[index] = True
    return enforced


def _enforce_always_on_wire(relays: list) -> list:
    return logical_to_wire(_enforce_always_on_relays(wire_to_logical(relays)))


def get_relay_state(box_id: str) -> dict:
    """Estado wire enviado al ESP (True = corta corriente)."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT relays FROM relay_states WHERE box_id = ?",
            (box_id,),
        ).fetchone()

        if row:
            relays = _enforce_always_on_wire(normalize_relays(json.loads(row["relays"])))
            return {"relays": relays}

        relays = _enforce_always_on_wire(list(DEFAULT_RELAYS_WIRE))
        relays_json = json.dumps(relays)
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO relay_states (box_id, relays, updated_at)
            VALUES (?, ?, ?)
            """,
            (box_id, relays_json, now),
        )
        return {"relays": relays}


def get_relay_state_logical(box_id: str) -> dict:
    """Estado para UI (True = ON, pasa corriente)."""
    wire = get_relay_state(box_id)
    return {"relays": wire_to_logical(wire["relays"])}


def set_relay_state(box_id: str, relays: list) -> dict:
    """Guarda estado wire (True = corta corriente)."""
    relays = _enforce_always_on_wire(normalize_relays(relays))
    relays_json = json.dumps(relays)
    now = _utc_now()

    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO relay_states (box_id, relays, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(box_id) DO UPDATE SET
                relays = excluded.relays,
                updated_at = excluded.updated_at
            """,
            (box_id, relays_json, now),
        )

    return {"relays": relays}


def set_relay_state_logical(box_id: str, relays: list) -> dict:
    """Guarda estado lógico para UI (True = pasa corriente / ON)."""
    relays = _enforce_always_on_relays(normalize_relays(relays))
    wire = logical_to_wire(relays)
    set_relay_state(box_id, wire)
    return {"relays": relays}


def delete_pump_state(box_id: str) -> bool:
    with _connection() as conn:
        cursor = conn.execute("DELETE FROM pump_states WHERE box_id = ?", (box_id,))
    return cursor.rowcount > 0


def delete_relay_state(box_id: str) -> bool:
    with _connection() as conn:
        cursor = conn.execute("DELETE FROM relay_states WHERE box_id = ?", (box_id,))
    return cursor.rowcount > 0


_save_counter = 0


def prune_sensor_readings(days: int = SENSOR_RETENTION_DAYS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connection() as conn:
        cursor = conn.execute(
            "DELETE FROM sensor_readings WHERE received_at < ?",
            (cutoff,),
        )
    return cursor.rowcount


def save_sensor_reading(box_id: str, endpoint: str, payload: list) -> None:
    global _save_counter
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO sensor_readings (box_id, endpoint, payload, received_at)
            VALUES (?, ?, ?, ?)
            """,
            (box_id, endpoint, json.dumps(payload), _utc_now()),
        )

    _save_counter += 1
    if _save_counter % 100 == 0:
        deleted = prune_sensor_readings()
        if deleted:
            logger = __import__("logging").getLogger("nodos.storage")
            logger.info("Lecturas antiguas purgadas: %s filas", deleted)


def get_summary() -> dict:
    with _connection() as conn:
        pumps = conn.execute("SELECT COUNT(*) AS count FROM pump_states").fetchone()["count"]
        relays = conn.execute("SELECT COUNT(*) AS count FROM relay_states").fetchone()["count"]
        readings = conn.execute("SELECT COUNT(*) AS count FROM sensor_readings").fetchone()["count"]
        last_reading = conn.execute(
            "SELECT received_at FROM sensor_readings ORDER BY id DESC LIMIT 1"
        ).fetchone()

    return {
        "pump_nodes": pumps,
        "relay_nodes": relays,
        "sensor_readings": readings,
        "last_sensor_reading_at": last_reading["received_at"] if last_reading else None,
    }


def list_pump_states() -> list:
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT box_id, bomba, status, updated_at
            FROM pump_states
            ORDER BY updated_at DESC
            """
        ).fetchall()

    return [
        {
            "box_id": row["box_id"],
            "bomba": row["bomba"],
            "status": bool(row["status"]),
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def list_relay_states() -> list:
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT box_id, relays, updated_at
            FROM relay_states
            ORDER BY updated_at DESC
            """
        ).fetchall()

    results = []
    for row in rows:
        wire = normalize_relays(json.loads(row["relays"]))
        logical = wire_to_logical(wire)
        results.append(
            {
                "box_id": row["box_id"],
                "relays": logical,
                "relays_wire": wire,
                "updated_at": row["updated_at"],
            }
        )
    return results


def get_sensor_history(box_id: str, hours: int = 48, limit: int = 2000) -> list:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT box_id, endpoint, payload, received_at
            FROM sensor_readings
            WHERE box_id = ? AND received_at >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (box_id, since.isoformat(), limit),
        ).fetchall()

    rows = list(reversed(rows))
    return [
        {
            "box_id": row["box_id"],
            "endpoint": row["endpoint"],
            "payload": json.loads(row["payload"]),
            "received_at": row["received_at"],
        }
        for row in rows
    ]


def get_sensor_history_since(box_id: str, since: datetime, limit: int = 400) -> list:
    if not box_id:
        return []
    since_value = since.isoformat()
    if since.tzinfo is None:
        since_value = since.replace(tzinfo=timezone.utc).isoformat()
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT box_id, endpoint, payload, received_at
            FROM sensor_readings
            WHERE box_id = ? AND received_at >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (box_id, since_value, limit),
        ).fetchall()

    rows = list(reversed(rows))
    return [
        {
            "box_id": row["box_id"],
            "endpoint": row["endpoint"],
            "payload": json.loads(row["payload"]),
            "received_at": row["received_at"],
        }
        for row in rows
    ]


def get_recent_sensor_readings(limit: int = 20) -> list:
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT box_id, endpoint, payload, received_at
            FROM sensor_readings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    results = []
    for row in rows:
        payload = json.loads(row["payload"])
        results.append(
            {
                "box_id": row["box_id"],
                "endpoint": row["endpoint"],
                "payload": payload,
                "received_at": row["received_at"],
                "display": format_sensor_payload(payload),
            }
        )
    return results


def get_automation_value(key: str, default: str = "") -> str:
    with _connection() as conn:
        row = conn.execute(
            "SELECT value FROM automation_state WHERE key = ?",
            (key,),
        ).fetchone()
    return row["value"] if row else default


def set_automation_value(key: str, value: str) -> None:
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO automation_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_automation_mode() -> str:
    return get_automation_value("mode", "MANUAL")


def set_automation_mode(mode: str) -> None:
    set_automation_value("mode", mode)


def log_automation(message: str) -> None:
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO automation_logs (created_at, message)
            VALUES (?, ?)
            """,
            (_utc_now(), message),
        )


def get_automation_logs(limit: int = 100) -> list:
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT created_at, message
            FROM automation_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [{"created_at": row["created_at"], "message": row["message"]} for row in rows]


def get_automation_actuator_logs_since(since: datetime, limit: int = 120) -> list:
    """Solo eventos de relés y riego relevantes (evita miles de logs de estado Bomba)."""
    since_value = since.isoformat()
    if since.tzinfo is None:
        since_value = since.replace(tzinfo=timezone.utc).isoformat()
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT created_at, message
            FROM automation_logs
            WHERE created_at >= ?
              AND (
                message LIKE 'AUTO relés ->%'
                OR message LIKE 'MANUAL relés ->%'
                OR message LIKE 'Bomba: micropulso %'
                OR message LIKE 'Bomba: objetivo alcanzado%'
                OR message LIKE 'Bomba: tope de %'
                OR message LIKE 'Bomba: apagada%'
                OR message LIKE 'Bomba: manual %'
              )
            ORDER BY id DESC
            LIMIT ?
            """,
            (since_value, limit),
        ).fetchall()

    return [{"created_at": row["created_at"], "message": row["message"]} for row in rows]


def _is_relay_automation_log(message: str) -> bool:
    return message.startswith("AUTO relés ->") or message.startswith("MANUAL relés ->")


def _is_irrigation_automation_log(message: str) -> bool:
    return message.startswith("Bomba:")


def get_automation_logs_balanced(
    *,
    relay_limit: int = 40,
    irrigation_limit: int = 80,
    other_limit: int = 30,
    scan_limit: int = 500,
) -> list:
    """Reserva cupo por sección para que el riego no oculte relés ni sistema."""
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT created_at, message
            FROM automation_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (scan_limit,),
        ).fetchall()

    relay_logs: list[dict] = []
    irrigation_logs: list[dict] = []
    other_logs: list[dict] = []

    for row in rows:
        message = row["message"]
        entry = {"created_at": row["created_at"], "message": message}
        if _is_relay_automation_log(message):
            if len(relay_logs) < relay_limit:
                relay_logs.append(entry)
        elif _is_irrigation_automation_log(message):
            if len(irrigation_logs) < irrigation_limit:
                irrigation_logs.append(entry)
        elif len(other_logs) < other_limit:
            other_logs.append(entry)

        if (
            len(relay_logs) >= relay_limit
            and len(irrigation_logs) >= irrigation_limit
            and len(other_logs) >= other_limit
        ):
            break

    merged = relay_logs + irrigation_logs + other_logs
    merged.sort(key=lambda item: item["created_at"], reverse=True)
    return merged


def get_latest_sensor_reading(box_id: str) -> dict | None:
    with _connection() as conn:
        row = conn.execute(
            """
            SELECT id, box_id, endpoint, payload, received_at
            FROM sensor_readings
            WHERE box_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (box_id,),
        ).fetchone()

    if not row:
        return None

    return {
        "id": row["id"],
        "box_id": row["box_id"],
        "endpoint": row["endpoint"],
        "payload": json.loads(row["payload"]),
        "received_at": row["received_at"],
    }


def get_soil_readings_after(box_id: str, after_id: int, limit: int = 10) -> list:
    with _connection() as conn:
        rows = conn.execute(
            """
            SELECT id, box_id, endpoint, payload, received_at
            FROM sensor_readings
            WHERE box_id = ? AND id > ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (box_id, after_id, limit),
        ).fetchall()

    rows = list(reversed(rows))
    return [
        {
            "id": row["id"],
            "box_id": row["box_id"],
            "endpoint": row["endpoint"],
            "payload": json.loads(row["payload"]),
            "received_at": row["received_at"],
        }
        for row in rows
    ]


def parse_atmosphere(payload: list) -> dict:
    result = {}
    for item in payload:
        key = item.get("key", "").lower()
        raw = item.get("raw", 0)
        unit = item.get("unit", "")
        if unit == "C*100":
            result["temp_c"] = raw / 100.0
        elif unit == "%RH*100":
            result["hr_pct"] = raw / 100.0
        elif unit == "ADC" and "mq135" in key:
            result["mq135"] = raw
    return result


def get_soil_calibration() -> dict:
    wet, dry, custom = get_effective_soil_limits()
    return {
        "calibrated": True,
        "standard": not custom,
        "custom": custom,
        "pin": SOIL_SENSOR_PIN,
        "wet_adc": wet,
        "dry_adc": dry,
    }


def get_effective_soil_limits() -> tuple[int, int, bool]:
    dry_raw = get_automation_value("soil_adc_dry", "")
    wet_raw = get_automation_value("soil_adc_wet", "")
    dry = SOIL_ADC_DRY
    wet = SOIL_ADC_WET
    custom = False
    try:
        if dry_raw.strip():
            dry = int(dry_raw)
            custom = True
        if wet_raw.strip():
            wet = int(wet_raw)
            custom = True
    except ValueError:
        pass
    return wet, dry, custom


def set_soil_calibration(dry_adc: int, wet_adc: int) -> dict:
    if dry_adc == wet_adc:
        raise ValueError("Los valores seco y húmedo deben ser distintos")
    set_automation_value("soil_adc_dry", str(int(dry_adc)))
    set_automation_value("soil_adc_wet", str(int(wet_adc)))
    return get_soil_calibration()


def adc_to_soil_pct(
    adc: float,
    dry: int | None = None,
    wet: int | None = None,
) -> float:
    if dry is None or wet is None:
        wet, dry, _ = get_effective_soil_limits()
    if dry == wet:
        return 0.0
    pct = ((float(adc) - dry) / (wet - dry)) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def format_sensor_item(item: dict) -> str | None:
    unit = item.get("unit", "")
    raw = item.get("raw", 0)
    key = (item.get("key") or "lectura").replace("_", " ").title()

    if unit == "C*100":
        return f"{key}: {float(raw) / 100.0:.2f} °C"
    if unit == "%RH*100":
        return f"Humedad aire: {float(raw) / 100.0:.2f} %"
    if unit == "%":
        signal = float(raw)
        pct = adc_to_soil_pct(signal)
        pin = item.get("arduinoPin", "N/A")
        return f"Humedad suelo: {pct:.1f}% (ADC {signal:.0f}, Pin {pin})"
    if unit == "ph":
        return f"pH: {float(raw) / 100.0:.2f}"
    if unit == "ADC":
        return f"{key}: {raw} ADC"
    if unit:
        return f"{key}: {raw} {unit}"
    return f"{key}: {raw}"


def format_sensor_payload(payload: list) -> str:
    parts = []
    for item in payload:
        line = format_sensor_item(item)
        if line:
            parts.append(line)
    return " · ".join(parts) if parts else "—"


def extract_soil_signal(payload: list) -> dict | None:
    for item in payload:
        if item.get("arduinoPin") == SOIL_SENSOR_PIN and item.get("unit") == "%":
            return {
                "pin": item.get("arduinoPin"),
                "signal": float(item.get("raw", 0)),
            }
    for item in payload:
        if item.get("unit") == "%":
            return {
                "pin": item.get("arduinoPin"),
                "signal": float(item.get("raw", 0)),
            }
    return None


def parse_soil_item(item: dict) -> tuple[float | None, str]:
    if item.get("unit") != "%":
        return None, "ignored"

    raw = float(item.get("raw", 0))
    return adc_to_soil_pct(raw), "adc"


def parse_soil(payload: list) -> float | None:
    preferred = None
    preferred_kind = ""
    fallback = None
    fallback_kind = ""

    for item in payload:
        value, kind = parse_soil_item(item)
        if value is None:
            continue
        if item.get("arduinoPin") == SOIL_SENSOR_PIN:
            preferred = value
            preferred_kind = kind
            break
        if fallback is None:
            fallback = value
            fallback_kind = kind

    if preferred is not None:
        return preferred
    return fallback


def parse_soil_raw(payload: list) -> dict | None:
    for item in payload:
        if item.get("arduinoPin") == SOIL_SENSOR_PIN and item.get("unit") == "%":
            raw = float(item.get("raw", 0))
            value, kind = parse_soil_item(item)
            return {
                "pin": item.get("arduinoPin"),
                "raw": raw,
                "kind": kind,
                "pct": value,
            }
    for item in payload:
        if item.get("unit") == "%":
            raw = float(item.get("raw", 0))
            value, kind = parse_soil_item(item)
            return {
                "pin": item.get("arduinoPin"),
                "raw": raw,
                "kind": kind,
                "pct": value,
            }
    return None


def _reading_age_seconds(received_at: str) -> float | None:
    if not received_at:
        return None
    try:
        timestamp = datetime.fromisoformat(received_at)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds())
    except ValueError:
        return None


def _card_status(age_seconds: float | None, max_age: float) -> str:
    if age_seconds is None:
        return "offline"
    if age_seconds <= max_age:
        return "online"
    if age_seconds <= max_age * 3:
        return "stale"
    return "offline"


def _actuator_status(last_seen_at: str | None, max_age: float = 60.0) -> str:
    """En línea ≤1 min, demorado 1–3 min, sin señal >3 min (poll bomba ~2 s, relés ~3 s)."""
    if not last_seen_at:
        return "sin_datos"
    return _card_status(_reading_age_seconds(last_seen_at), max_age)


def get_live_sensor_cards(nodos: dict) -> list:
    cards = []

    atmosphere = get_latest_sensor_reading(nodos.get("atmosfera", ""))
    if atmosphere:
        parsed = parse_atmosphere(atmosphere["payload"])
        age = _reading_age_seconds(atmosphere["received_at"])
        cards.append(
            {
                "id": "atmosfera",
                "title": "Atmósfera",
                "box_id": atmosphere["box_id"],
                "received_at": atmosphere["received_at"],
                "age_seconds": age,
                "status": _card_status(age, 120),
                "metrics": [
                    {
                        "label": "Temperatura",
                        "value": parsed.get("temp_c"),
                        "unit": "°C",
                        "decimals": 1,
                    },
                    {
                        "label": "Humedad aire",
                        "value": parsed.get("hr_pct"),
                        "unit": "%",
                        "decimals": 1,
                    },
                    {
                        "label": "Calidad aire",
                        "value": parsed.get("mq135"),
                        "unit": "ADC",
                        "decimals": 0,
                    },
                ],
            }
        )

    soil = get_latest_sensor_reading(nodos.get("suelo", ""))
    if soil:
        parsed = parse_soil_raw(soil["payload"])
        soil_pct = parsed["pct"] if parsed else parse_soil(soil["payload"])
        age = _reading_age_seconds(soil["received_at"])
        metrics = [
            {
                "label": "Humedad",
                "value": soil_pct,
                "unit": "%",
                "decimals": 1,
            },
        ]
        if parsed:
            metrics.append(
                {
                    "label": "Señal",
                    "value": parsed["raw"],
                    "unit": "ADC",
                    "decimals": 0,
                }
            )
        cards.append(
            {
                "id": "suelo",
                "title": "Humedad suelo",
                "box_id": soil["box_id"],
                "received_at": soil["received_at"],
                "age_seconds": age,
                "status": _card_status(age, 120),
                "metrics": metrics,
            }
        )

    return cards

def save_node_log(box_id: str, level: str, message: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO node_logs (box_id, level, message, received_at)
            VALUES (?, ?, ?, ?)
            """,
            (box_id, level, message, now),
        )

def get_node_logs(box_id: str = None, limit: int = 50) -> list:
    with _connection() as conn:
        if box_id:
            cursor = conn.execute(
                "SELECT box_id, level, message, received_at FROM node_logs WHERE box_id = ? ORDER BY received_at DESC LIMIT ?",
                (box_id, limit)
            )
        else:
            cursor = conn.execute(
                "SELECT box_id, level, message, received_at FROM node_logs ORDER BY received_at DESC LIMIT ?",
                (limit,)
            )
        return [dict(row) for row in cursor.fetchall()]
