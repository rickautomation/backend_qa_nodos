from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURACIÓN GLOBAL ---
PUMP_ID_DEFAULT = 1
HOST = '0.0.0.0'
PORT = 3000

# --- ESTADOS GLOBALES ---
# Formato Bombas: {"MAC_ID": {"bomba": 1, "status": False}}
pump_states = {}

# Formato Relays: {"MAC_ID": {"relays": [False, False, False, False]}}
relay_states = {}

# ==========================================================
# ENDPOINT: RECEPCIÓN DE DATOS DE SENSORES (TODOS LOS NODOS)
# ==========================================================
@app.route('/sensor-data/arduino/batch', methods=['POST'])
@app.route('/sensor-data/agua/batch', methods=['POST'])
@app.route('/sensor-data/humedad/batch', methods=['POST'])
@app.route('/sensor-data/atmosfera/batch', methods=['POST'])
def receive_sensor_data():
    if not request.is_json:
        return jsonify({"message": "Content-Type must be application/json"}), 400

    data = request.get_json()
    box_id = data.get("boxSerialId", "ID desconocido")
    
    print("\n" + "="*60)
    print(f"📩 REPORTE RECIBIDO | ORIGEN: {request.path}")
    print(f"📡 DISPOSITIVO ID: {box_id}")
    print("-" * 60)
    
    sensor_data = data.get("data", [])
    for item in sensor_data:
        pin = item.get("arduinoPin", "N/A")
        raw = item.get("raw", 0)
        unit = item.get("unit", "")
        key = item.get("key", "N/A")
        
        label = key.replace('_', ' ').title()

        if unit == "C*100":
            print(f"   🌡️ {label}: {raw/100.0:.2f} °C")
        elif unit == "%RH*100":
            print(f"   💧 Humedad Aire: {raw/100.0:.2f} %")
        elif unit == "%":
            print(f"   🌱 Humedad Suelo: {raw}% (Pin: {pin})")
        elif unit == "ph":
            print(f"   🧪 Nivel pH: {raw/100.0:.2f}")
        elif unit == "ADC":
            print(f"   ☁️ {label}: {raw} (Valor ADC)")
        else:
            print(f"   📊 {label}: {raw} {unit}")

    print("="*60)
    return jsonify({
        "status": "success",
        "message": f"Datos de {box_id} procesados"
    }), 200

# ==========================================================
# SECCIÓN: NODO BOMBA (Cualquier dispositivo de 1 Relay)
# ==========================================================
@app.route('/bomba/status/<string:box_id>', methods=['GET'])
def get_pump_status(box_id):
    if box_id not in pump_states:
        print(f"🆕 Registrando nuevo Nodo Bomba: {box_id}")
        pump_states[box_id] = {"bomba": PUMP_ID_DEFAULT, "status": False}
    return jsonify(pump_states[box_id]), 200

@app.route('/bomba/control/<string:box_id>', methods=['POST'])
def control_pump(box_id):
    if not request.is_json:
        return jsonify({"message": "JSON required"}), 400
    data = request.get_json()
    new_status = data.get("status")
    
    if new_status is None:
        return jsonify({"error": "Missing 'status' field"}), 400

    if box_id not in pump_states:
        pump_states[box_id] = {"bomba": PUMP_ID_DEFAULT, "status": False}
    
    pump_states[box_id]["status"] = new_status
    print(f"\n💧 CONTROL BOMBA | ID: {box_id} -> {'🟢 ON' if new_status else '🔴 OFF'}")
    return jsonify({"message": "Bomba actualizada", "state": pump_states[box_id]}), 200

# ==========================================================
# SECCIÓN: NODO RELAYS (Dispositivo de 4 Canales)
# ==========================================================
@app.route('/relays/status/<string:box_id>', methods=['GET'])
def get_relays_status(box_id):
    if box_id not in relay_states:
        print(f"🆕 Registrando nuevo Nodo 4 Relays: {box_id}")
        # Inicializa los 4 relays en falso (apagados)
        relay_states[box_id] = {"relays": [False, False, False, False]}
    return jsonify(relay_states[box_id]), 200

@app.route('/relays/control/<string:box_id>', methods=['POST'])
def control_relays(box_id):
    if not request.is_json:
        return jsonify({"message": "JSON required"}), 400
    data = request.get_json()
    new_relays = data.get("relays") # Se espera una lista: [True, False, True, False]
    
    if new_relays is None or not isinstance(new_relays, list) or len(new_relays) != 4:
        return jsonify({"error": "Se requiere lista 'relays' con 4 valores booleanos"}), 400

    if box_id not in relay_states:
        relay_states[box_id] = {"relays": [False, False, False, False]}
    
    relay_states[box_id]["relays"] = new_relays
    
    # Log visual de los 4 canales
    log_estados = " ".join([f"[{i+1}:{'ON' if st else 'OFF'}]" for i, st in enumerate(new_relays)])
    print(f"\n⚡ CONTROL 4-RELAYS | ID: {box_id} -> {log_estados}")
    
    return jsonify({"message": "Relays actualizados", "state": relay_states[box_id]}), 200

# ==========================================================
# INICIO DEL SERVIDOR
# ==========================================================
if __name__ == '__main__':
    print(f"\n" + "*"*60)
    print(f"🚀 SISTEMA INTEGRADO DE MONITOREO Y CONTROL")
    print(f"📍 URL LOCAL: http://127.0.0.1:{PORT}")
    print(f"📡 URL RED:   http://{HOST}:{PORT}")
    print(f"📂 Endpoints Activos:")
    print(f"   - Sensores: /sensor-data/... (Atmosfera, Agua, Suelo)")
    print(f"   - Bomba (1 CH): /bomba/status/ID | /bomba/control/ID")
    print(f"   - Relays (4 CH): /relays/status/ID | /relays/control/ID")
    print(f"*"*60 + "\n")
    
    app.run(host=HOST, port=PORT, debug=False)