import os
import re

filepath = '/Users/dariancampos/Documents/Arduino/box_automation_nodo_humedad_suelo/nodo_suelo_new/nodo_suelo_new.ino'
if not os.path.exists(filepath):
    # try the one I edited before
    filepath = '/Users/dariancampos/Documents/Arduino/box_automation_nodo_humedad_suelo/nodo_suelo_new.ino'

with open(filepath, 'r') as f:
    content = f.read()

old_init = "esp_task_wdt_init(&wdt_config);"
new_init = """esp_err_t err = esp_task_wdt_init(&wdt_config);
  if (err == ESP_ERR_INVALID_STATE) {
    esp_task_wdt_reconfigure(&wdt_config);
  }"""
content = content.replace(old_init, new_init)

match = re.search(r'(bool conectar_wifi\(\)\s*\{)(.*?)(^\})', content, re.MULTILINE | re.DOTALL)
if match:
    func_content = match.group(2)
    func_content = func_content.replace('delay(500);', 'esp_task_wdt_reset();\n    delay(500);')
    content = content[:match.start(2)] + func_content + content[match.end(2):]

with open(filepath, 'w') as f:
    f.write(content)

print("Fixed suelo")
