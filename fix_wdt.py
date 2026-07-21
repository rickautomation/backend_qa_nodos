import os
import glob

base_dir = '/Users/dariancampos/Documents/Arduino'

files = [
    'box_automation_nodo_atmosfera/nodo_atmosfera/nodo_atmosfera.ino',
    'box_automation_nodo_bomba/nodo_bomba/nodo_bomba.ino',
    'box_automation_nodo_humedad_suelo/nodo_suelo_new.ino',
    'box_automation_nodo_relays/nodo_relays/nodo_relays.ino',
    'box_automation_nodo_calidad_agua/nodo_h2o.ino'
]

for rel_path in files:
    filepath = os.path.join(base_dir, rel_path)
    if not os.path.exists(filepath):
        print(f"Not found: {filepath}")
        continue
    
    with open(filepath, 'r') as f:
        content = f.read()

    # 1. Fix WDT init
    old_init = "esp_task_wdt_init(&wdt_config);"
    new_init = """esp_err_t err = esp_task_wdt_init(&wdt_config);
  if (err == ESP_ERR_INVALID_STATE) {
    esp_task_wdt_reconfigure(&wdt_config);
  }"""
    content = content.replace(old_init, new_init)

    # 2. Add WDT reset to conectar_wifi loop
    # Usually it's:
    # while (WiFi.status() != WL_CONNECTED && millis() - inicio < TIEMPO_MAX_CONEXION_WIFI) {
    #   delay(...)
    # or something similar.
    # We can just replace "delay(500);" with "esp_task_wdt_reset(); delay(500);" if it's inside conectar_wifi
    # but wait, let's do a regex to find the while loop in conectar_wifi
    import re
    # Find conectar_wifi function
    match = re.search(r'(bool conectar_wifi\(\)\s*\{)(.*?)(^\})', content, re.MULTILINE | re.DOTALL)
    if match:
        func_content = match.group(2)
        func_content = func_content.replace('delay(500);', 'esp_task_wdt_reset();\n    delay(500);')
        content = content[:match.start(2)] + func_content + content[match.end(2):]
    
    # Do the same for probarCredencialesWifi in nodo_relays
    match = re.search(r'(bool probarCredencialesWifi\(.*?\)\s*\{)(.*?)(^\})', content, re.MULTILINE | re.DOTALL)
    if match:
        func_content = match.group(2)
        func_content = func_content.replace('delay(300);', 'esp_task_wdt_reset();\n    delay(300);')
        content = content[:match.start(2)] + func_content + content[match.end(2):]

    with open(filepath, 'w') as f:
        f.write(content)
        
    print(f"Fixed {filepath}")
