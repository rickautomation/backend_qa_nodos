# Greenbox Tablet (Android)

App Android para la **Lenovo Tab 10.1" (ZUI / Android 16)** que muestra el panel web de Greenbox en pantalla completa, conectado al backend en la Raspberry Pi.

No duplica la UI: es un **WebView** que carga `http://IP_DE_LA_PI:3000/` (el mismo dashboard que ya tenés en el navegador).

## Requisitos

- Android Studio **Ladybug** o más nuevo
- JDK **17**
- Tablet y Raspberry Pi en la **misma Wi‑Fi**
- Backend corriendo en la Pi (`nodos-backend`, puerto **3000**)

## Abrir el proyecto

1. Android Studio → **Open** → carpeta `android/greenbox-tablet`
2. Esperá que Gradle sincronice (descarga SDK 35 si hace falta)
3. Conectá la tablet por USB (depuración USB activada) o usá emulador
4. **Run** ▶️

## Primera configuración en la tablet

1. Abrí la app **Greenbox**
2. Menú ⚙️ (arriba a la derecha) → **Ajustes**
3. URL del backend, por defecto:

   `http://192.168.68.75:3000/`

4. Activá **Pantalla siempre encendida** si la tablet queda fija en el grow
5. **Guardar y abrir**

## Funciones

| Función | Descripción |
|---------|-------------|
| Landscape | Orientación horizontal (ideal para tablet en soporte) |
| Pull to refresh | Deslizá hacia abajo para recargar |
| Pantalla encendida | Opcional, para kiosk |
| HTTP local | Permite `http://` hacia la Pi en la LAN |
| Offline | Mensaje claro si la Pi no responde |

## Compilar APK para instalar sin USB

```bash
cd android/greenbox-tablet
./gradlew assembleRelease
```

APK: `app/build/outputs/apk/release/app-release-unsigned.apk`

Para firmar e instalar en la tablet (ADB o copiando el APK).

## Cambiar IP por defecto

Editá `app/build.gradle.kts`:

```kotlin
buildConfigField("String", "DEFAULT_BACKEND_URL", "\"http://TU_IP:3000/\"")
```

## Alternativa sin compilar: PWA

Si solo querés un acceso directo en la tablet:

1. Chrome → `http://192.168.68.75:3000/`
2. Menú → **Instalar aplicación** / **Añadir a pantalla de inicio**

El backend ya sirve `manifest.webmanifest` para eso.

## Deploy del backend (Pi)

La app **no** se sube con `deploy/raspberry-pi/sync.sh`. Solo hace falta desplegar el backend web:

```bash
./deploy/raspberry-pi/sync.sh
```

Los cambios de CSS/tablet en `static/` y `templates/` sí van a la Pi con ese script.

## Depurar (debug)

### 1. Activar depuración USB en la Lenovo (ZUI / Android 16)

1. **Ajustes** → **Acerca de la tablet**
2. Tocá **Número de compilación** 7 veces → modo desarrollador activado
3. **Ajustes** → **Sistema** → **Opciones de desarrollador**
4. Activá **Depuración USB**
5. Conectá la tablet al Mac/PC con cable USB (con datos)
6. En la tablet, aceptá **Permitir depuración USB** (marcá “Siempre desde este equipo”)

### 2. Correr en modo debug desde Android Studio

1. Abrí el proyecto `android/greenbox-tablet`
2. Arriba elegí la tablet conectada (no “Medium Phone”)
3. Run ▶️ con variante **debug** (por defecto)
4. Poné breakpoints en `MainActivity.kt` o `SettingsActivity.kt` si depurás Kotlin
5. **Logcat** (abajo en Android Studio): filtrá por `Greenbox` o `com.greenbox.tablet`

### 3. Depurar el panel web (HTML/JS/CSS) dentro del WebView

En builds **debug** la app habilita inspección remota del WebView.

1. Con la app abierta en la tablet, en el **Chrome del Mac/PC** entrá a:

   `chrome://inspect/#devices`

2. Marcá **Discover USB devices**
3. Debería aparecer tu tablet y **WebView in com.greenbox.tablet**
4. Clic en **inspect** → DevTools igual que en una web normal (Console, Network, Elements)

Ahí ves errores de `/api/dashboard`, CSS, etc.

### 4. Si no aparece la tablet en Android Studio

| Problema | Solución |
|----------|----------|
| “Unauthorized” | Revocá autorizaciones USB en Opciones de desarrollador y reconectá |
| Solo carga, no datos | Cable solo carga → probá otro cable/adaptador |
| Mac no detecta | `adb devices` en terminal; instalá platform-tools si hace falta |
| Wi‑Fi en vez de USB | Opciones de desarrollador → **Depuración inalámbrica** → emparejar |

### 5. Ver logs rápidos por terminal

```bash
adb devices
adb logcat -s GreenboxTablet:* chromium:* AndroidRuntime:E
```

(Reemplazá el tag si agregás logs propios con `Log.d("GreenboxTablet", "...")`.)

### 6. Depurar sin cable (Wi‑Fi)

1. Tablet y PC en la misma red
2. Opciones de desarrollador → **Depuración inalámbrica** → emparejar con código
3. Android Studio → Device Manager → **Pair using Wi‑Fi**

Útil si el puerto USB de la tablet es incómodo.

## Estructura

```
android/greenbox-tablet/
  app/src/main/java/com/greenbox/tablet/
    MainActivity.kt      → WebView + kiosk
    SettingsActivity.kt  → URL de la Pi
    Prefs.kt             → Preferencias locales
```
