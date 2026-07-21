import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "3000"))

DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "nodos.db"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

PROFILE_PATH = Path(os.getenv("PROFILE_PATH", BASE_DIR / "profiles" / "dark_purple_65d.json"))
AUTOMATION_INTERVAL_SEC = int(os.getenv("AUTOMATION_INTERVAL_SEC", "30"))
PUMP_PULSE_ACK_TIMEOUT_SEC = int(os.getenv("PUMP_PULSE_ACK_TIMEOUT_SEC", "45"))
PUMP_MAX_RUN_EXTRA_SEC = int(os.getenv("PUMP_MAX_RUN_EXTRA_SEC", "15"))

PUMP_ID_DEFAULT = 1
RELAY_COUNT = int(os.getenv("RELAY_COUNT", "8"))
RELAY_LABELS = [
    "Luz",
    "R2",
    "Humidificador",
    "Caloventor",
    "R5",
    "R6",
    "Ventilador 12V",
    "Extractor",
]
# Wire al ESP: true = corta corriente (OFF). false = pasa corriente (ON).
# UI y AUTO usan estado lógico (true = ON).
RELAY_TRUE_CUTS_POWER = os.getenv("RELAY_TRUE_CUTS_POWER", "true").lower() == "true"
DEFAULT_RELAYS_WIRE = [True] * RELAY_COUNT
RELAY_ALWAYS_ON_INDICES = []
# Calibración estándar humedad suelo: 0% = 2100 ADC, 100% = 1000 ADC
SOIL_ADC_DRY = int(os.getenv("SOIL_ADC_DRY", "2100"))
SOIL_ADC_WET = int(os.getenv("SOIL_ADC_WET", "1000"))
SOIL_SENSOR_PIN = os.getenv("SOIL_SENSOR_PIN", "A0")
SENSOR_RETENTION_DAYS = int(os.getenv("SENSOR_RETENTION_DAYS", "7"))
TIMEZONE = os.getenv("TIMEZONE", "America/Argentina/Buenos_Aires")
