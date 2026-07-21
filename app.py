import logging
import socket
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request, send_from_directory

from config import (
    HOST,
    LOG_LEVEL,
    PORT,
    RELAY_COUNT,
    RELAY_LABELS,
    RELAY_TRUE_CUTS_POWER,
    SOIL_SENSOR_PIN,
)
import automation
import readings_hub
import sensor_analysis
import storage

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("nodos")


class QuietPollingFilter(logging.Filter):
    """Oculta el ruido del polling constante de bomba/relays."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (
            "GET /bomba/status/" in message
            or "GET /relays/status/" in message
            or "GET /api/dashboard" in message
        )


logging.getLogger("werkzeug").addFilter(QuietPollingFilter())

app = Flask(__name__)
storage.init_db()
automation.reload_profile()
automation.start_automation_loop()

STARTED_AT = datetime.now(timezone.utc)


@app.after_request
def force_close_connection(response):
    """Evita acumular sockets en CLOSE-WAIT con clientes que no cierran keep-alive."""
    response.headers["Connection"] = "close"
    return response


def _format_sensor_line(item: dict) -> str:
    return storage.format_sensor_item(item) or "lectura desconocida"


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


@app.route("/health", methods=["GET"])
def health():
    summary = storage.get_summary()
    return jsonify(
        {
            "status": "ok",
            "started_at": STARTED_AT.isoformat(),
            "local_ip": _local_ip(),
            "port": PORT,
            **summary,
        }
    ), 200


@app.route("/", methods=["GET"])
def dashboard():
    return render_template("index.html")


@app.route("/manifest.webmanifest", methods=["GET"])
def web_manifest():
    return send_from_directory(
        app.static_folder,
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@app.route("/api/sensors/history/suelo", methods=["GET"])
def soil_sensor_history():
    profile = automation.load_profile()
    box_id = profile["nodos"]["suelo"]
    hours = request.args.get("hours", default=24, type=int)
    hours = max(1, min(hours, 168))
    limit = request.args.get("limit", default=180, type=int)
    limit = max(10, min(limit, 500))

    readings = storage.get_sensor_history(box_id, hours=hours, limit=limit)
    points = []
    for reading in readings:
        parsed = storage.parse_soil_raw(reading["payload"])
        if not parsed:
            continue
        points.append(
            {
                "t": reading["received_at"],
                "signal": parsed["raw"],
                "pct": parsed["pct"],
            }
        )

    calibration = storage.get_soil_calibration()
    return jsonify(
        {
            "box_id": box_id,
            "hours": hours,
            "count": len(points),
            "calibration": calibration,
            "points": points,
        }
    ), 200


@app.route("/api/sensors/chart/suelo", methods=["GET"])
def soil_sensor_chart():
    profile = automation.load_profile()
    phase = automation.get_current_phase(profile)
    box_id = profile["nodos"]["suelo"]
    view = request.args.get("view", default="hours", type=str)
    span = request.args.get("span", default=24, type=int)

    chart = sensor_analysis.soil_chart(
        box_id,
        view=view,
        span=span,
        suelo_min=phase["suelo_min"],
        suelo_objetivo=phase["suelo_objetivo"],
    )
    return jsonify(chart), 200


@app.route("/api/sensors/analysis/suelo", methods=["GET"])
def soil_sensor_analysis():
    profile = automation.load_profile()
    phase = automation.get_current_phase(profile)
    box_id = profile["nodos"]["suelo"]
    hours = request.args.get("hours", default=6, type=int)
    hours = max(1, min(hours, 168))

    analysis = sensor_analysis.analyze_soil(
        box_id,
        hours=hours,
        suelo_min=phase["suelo_min"],
        suelo_objetivo=phase["suelo_objetivo"],
    )
    analysis["fase"] = phase["nombre"]
    analysis["dia_cultivo"] = phase["dia_cultivo"]
    analysis["calibration"] = storage.get_soil_calibration()
    return jsonify(analysis), 200


@app.route("/api/sensors/calibration/suelo", methods=["GET", "POST"])
def soil_calibration():
    if request.method == "GET":
        return jsonify(storage.get_soil_calibration()), 200

    if not request.is_json:
        return jsonify({"message": "JSON required"}), 400

    data = request.get_json()
    dry_adc = data.get("dry_adc")
    wet_adc = data.get("wet_adc")
    if dry_adc is None or wet_adc is None:
        return jsonify({"error": "Se requieren dry_adc y wet_adc"}), 400

    try:
        calibration = storage.set_soil_calibration(int(dry_adc), int(wet_adc))
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400

    logger.info(
        "Calibración suelo actualizada: seco=%s ADC, húmedo=%s ADC",
        calibration["dry_adc"],
        calibration["wet_adc"],
    )
    return jsonify(calibration), 200


@app.route("/api/readings/hub", methods=["GET"])
def readings_hub_api():
    profile = automation.load_profile()
    scope = request.args.get("scope", default="etapa", type=str)
    return jsonify(readings_hub.get_readings_hub(profile, scope=scope)), 200


@app.route("/api/dashboard", methods=["GET"])
def dashboard_api():
    profile = automation.load_profile()
    return jsonify(
        {
            "summary": storage.get_summary(),
            "pumps": storage.list_pump_states(),
            "relays": storage.list_relay_states(),
            "relay_config": {
                "count": RELAY_COUNT,
                "wire_true_cuts_power": RELAY_TRUE_CUTS_POWER,
                "labels": RELAY_LABELS,
            },
            "sensors": storage.get_recent_sensor_readings(),
            "live_sensors": storage.get_live_sensor_cards(profile["nodos"]),
            "automation": automation.get_status(),
            "light_schedule": automation.get_light_schedule(profile),
            "soil_config": {
                "dry_adc": storage.get_soil_calibration()["dry_adc"],
                "wet_adc": storage.get_soil_calibration()["wet_adc"],
                "pin": SOIL_SENSOR_PIN,
            },
        }
    ), 200


@app.route("/api/automation/status", methods=["GET"])
def automation_status():
    return jsonify(automation.get_status()), 200


@app.route("/api/automation/mode", methods=["POST"])
def automation_mode():
    if not request.is_json:
        return jsonify({"message": "JSON required"}), 400

    mode = request.get_json().get("mode", "").upper()
    if mode not in {"AUTO", "MANUAL"}:
        return jsonify({"error": "mode debe ser AUTO o MANUAL"}), 400

    storage.set_automation_mode(mode)
    storage.log_automation(f"Modo cambiado a {mode}")
    logger.info("Automatización en modo %s", mode)

    if mode == "AUTO":
        evaluation = automation.tick()
    else:
        evaluation = automation.evaluate()

    return jsonify({"mode": mode, "evaluation": evaluation}), 200


@app.route("/sensor-data/arduino/batch", methods=["POST"])
@app.route("/sensor-data/agua/batch", methods=["POST"])
@app.route("/sensor-data/humedad/batch", methods=["POST"])
@app.route("/sensor-data/atmosfera/batch", methods=["POST"])
def receive_sensor_data():
    if not request.is_json:
        return jsonify({"message": "Content-Type must be application/json"}), 400

    data = request.get_json()
    box_id = data.get("boxSerialId", "ID desconocido")
    sensor_data = data.get("data", [])

    storage.save_sensor_reading(box_id, request.path, sensor_data)

    lines = [_format_sensor_line(item) for item in sensor_data]
    logger.info(
        "Sensor %s | %s | %s",
        box_id,
        request.path,
        " | ".join(lines) if lines else "sin lecturas",
    )

    return jsonify(
        {
            "status": "success",
            "message": f"Datos de {box_id} procesados",
        }
    ), 200


@app.route("/sensor-data/logs", methods=["POST"])
def receive_sensor_logs():
    if not request.is_json:
        return jsonify({"message": "Content-Type must be application/json"}), 400

    data = request.get_json()
    box_id = data.get("boxSerialId", "UNKNOWN")
    level = data.get("level", "INFO")
    message = data.get("message", "")

    storage.save_node_log(box_id, level, message)
    logger.info("RemoteLog [%s] %s: %s", box_id, level, message)

    return jsonify({"status": "success"}), 200


@app.route("/bomba/status/<string:box_id>", methods=["GET"])
def get_pump_status(box_id):
    storage.touch_pump_seen(box_id)
    state = storage.get_pump_state(box_id)
    return jsonify(state), 200


@app.route("/bomba/control/<string:box_id>", methods=["POST"])
def control_pump(box_id):
    if not request.is_json:
        return jsonify({"message": "JSON required"}), 400

    data = request.get_json()
    event = (data.get("event") or "").lower()

    if event == "pulse_completed":
        request_id = data.get("request_id")
        if not request_id:
            return jsonify({"error": "Se requiere request_id"}), 400
        if not storage.complete_pump_pulse(box_id, str(request_id)):
            return jsonify({"error": "request_id inválido o ya procesado"}), 400
        automation.on_pulse_completed(box_id)
        logger.info("Bomba %s micropulso confirmado (%s)", box_id, request_id)
        return jsonify(
            {
                "message": "Micropulso confirmado",
                "state": storage.get_pump_state(box_id),
            }
        ), 200

    new_status = data.get("status")

    if new_status is None:
        return jsonify({"error": "Missing 'status' field"}), 400

    state = storage.set_pump_status(box_id, bool(new_status))
    storage.log_automation(f"Bomba: manual {'ON' if new_status else 'OFF'}")
    logger.info(
        "Bomba %s -> %s",
        box_id,
        "ON" if new_status else "OFF",
    )

    return jsonify({"message": "Bomba actualizada", "state": state}), 200


@app.route("/relays/status/<string:box_id>", methods=["GET"])
def get_relays_status(box_id):
    storage.touch_relay_seen(box_id)
    state = storage.get_relay_state(box_id)
    return jsonify(state), 200


@app.route("/relays/control/<string:box_id>", methods=["POST"])
def control_relays(box_id):
    if not request.is_json:
        return jsonify({"message": "JSON required"}), 400

    if storage.get_automation_mode() == "AUTO":
        return jsonify({"error": "Pasá a MANUAL para controlar relés manualmente"}), 409

    data = request.get_json()
    new_relays = data.get("relays")

    if new_relays is None or not isinstance(new_relays, list):
        return jsonify({"error": f"Se requiere lista 'relays' con {RELAY_COUNT} valores booleanos"}), 400

    if len(new_relays) != RELAY_COUNT:
        return jsonify({"error": f"Se requiere lista 'relays' con {RELAY_COUNT} valores booleanos"}), 400

    previous = storage.get_relay_state_logical(box_id)
    state = storage.set_relay_state_logical(box_id, [bool(value) for value in new_relays])
    if previous.get("relays") != state.get("relays"):
        relay_log = " ".join(
            f"{RELAY_LABELS[index]}:{'ON' if status else 'OFF'}"
            for index, status in enumerate(state["relays"])
            if index < len(RELAY_LABELS)
        )
        storage.log_automation(f"MANUAL relés -> {relay_log}")
    relay_log = " ".join(
        f"{RELAY_LABELS[index]}:{'ON' if status else 'OFF'}"
        for index, status in enumerate(state["relays"])
        if index < len(RELAY_LABELS)
    )
    logger.info("Relays %s -> %s", box_id, relay_log)

    return jsonify({"message": "Relays actualizados", "state": state}), 200


def _print_banner() -> None:
    local_ip = _local_ip()
    logger.info("Backend nodos IoT iniciado")
    logger.info("Local:  http://127.0.0.1:%s", PORT)
    logger.info("Red LAN: http://%s:%s", local_ip, PORT)
    logger.info("Panel web: http://%s:%s/", local_ip, PORT)
    logger.info("Health: http://%s:%s/health", local_ip, PORT)
    logger.info("Firebase remote_config.backend_host debe apuntar a %s", local_ip)


if __name__ == "__main__":
    _print_banner()
    app.run(host=HOST, port=PORT, debug=False)
