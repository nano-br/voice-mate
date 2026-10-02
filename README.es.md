[English](README.md) | [Português](README.pt-BR.md) | **Español**

# VoiceMate

> Presiona un atajo, habla, pega. Transcripción local con Whisper directo a tu portapapeles, o pasada por Claude y leída de vuelta con tu propia voz.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/types-mypy-blue.svg)](https://mypy-lang.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)

## Por qué

El dictado en la nube es rápido, hasta que deja de serlo. VoiceMate ejecuta Whisper **localmente** en tu GPU, así que tu audio nunca sale de tu máquina y no hay latencia de red, cuota mensual ni concesiones en privacidad. Presiona un atajo, habla y pega donde quieras.

## Características

- **Atajo en modo alternancia**: presiona una vez para empezar y otra vez para detener y transcribir
- **Dos flujos, un micrófono**: `Ctrl+Alt+V` deja la transcripción en el portapapeles; `Ctrl+Alt+A` la envía a Claude (multiturno) y lee la respuesta de la IA en voz alta mediante TTS
- **Transcripción local**: `faster-whisper` (CTranslate2) en NVIDIA/CPU, o `whisper.cpp` + Vulkan en GPUs AMD (~1.6 GB de VRAM, large-v3-turbo); el backend se elige automáticamente según la GPU
- **Aceleración por GPU, independiente del fabricante**: NVIDIA (CUDA) **y** AMD (Vulkan + ROCm) son compatibles, con alternativa automática a la CPU. VRAM en reposo ≈ 0 (el STT se ejecuta como subproceso; el TTS se carga bajo demanda en la primera locución)
- **TTS intercambiable**: la respuesta de Claude se lee en voz alta con [VoxCPM2](https://github.com/OpenBMB/VoxCPM) (2B parámetros, diseño de voz a partir de una descripción textual, streaming). La arquitectura aísla cada motor de TTS para que puedas cambiarlo o quitarlo sin tocar el resto
- **Doble portapapeles con Win+V**: el flujo de IA copia primero la transcripción y luego la respuesta, así que el historial del portapapeles de Windows muestra ambas una junto a la otra para revisarlas
- **Detener decide el destino**: empieza con cualquier atajo; el atajo que presionas para *detener* elige el handler (portapapeles o Claude)
- **Cancelación en pleno vuelo**: presionar cualquier atajo mientras Claude responde (o mientras el TTS habla) cancela al instante e inicia una nueva grabación, preservando la conversación
- **Listener autorreparable**: reinstala el atajo global periódicamente para recuperarse de la eliminación silenciosa del hook que hace Windows bajo carga
- **Watchdog**: monitor de salud a nivel de proceso con reinicio automático ante bloqueos
- **Grabación máxima configurable**: protege contra sesiones olvidadas (predeterminado: 10 min)
- **Feedback sonoro**: pitidos distintos para inicio, aviso, transcripción completada y respuesta de la IA lista
- **Soporte para disparo con mouse**: usa un botón lateral en lugar del teclado, si lo prefieres (solo flujo de portapapeles)

## Requisitos

- Uno de los entornos compatibles (la capa de plataforma elige las integraciones adecuadas automáticamente):
  - **Windows 10/11** nativo (se recomienda NVIDIA): el objetivo original, sin cambios
  - **Linux** nativo, X11 o Wayland
  - **WSL2** (Ubuntu) en Windows 11: la app se ejecuta **completamente dentro de WSL**, con un pequeño
    script de atajos del lado de Windows; es el camino recomendado para **GPUs AMD** (ROCm). Consulta [docs/wsl2.md](docs/wsl2.md)
- Python 3.12 (el flujo de TTS con VoxCPM2 todavía no es compatible con 3.13)
- [Poetry](https://python-poetry.org/docs/#installation)
- La GPU es opcional, pero muy recomendable (necesaria para un TTS con latencia aceptable):
  - **NVIDIA** con CUDA, **o**
  - **AMD** (RDNA, p. ej. RX 7000/9000) mediante ROCm (Linux/WSL2) o ROCm-on-Windows (Adrenalin ≥ 26.2.2)
  - ¿Sin GPU? Igual funciona en la CPU (más lento; considera `--no-tts`)
- **Solo para el flujo de Claude:** Node.js 18+ y el [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) autenticado localmente

## Instalación

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup
```

`make setup` **detecta tu GPU** (NVIDIA / AMD / ninguna), lo confirma contigo, instala la versión de PyTorch correspondiente (CUDA `cu128` para NVIDIA, ROCm para AMD, o CPU) junto con los módulos que elijas, y recuerda todo en `~/.config/voicemate/config.toml`. Vuelve a ejecutar el selector cuando quieras con **`make configure`** (p. ej. después de cambiar de GPU).

> **Nota para AMD:** `make setup` instala el PyTorch ROCm (para VoxCPM/TTS) y descarga **whisper.cpp + Vulkan** (para la transcripción). Los wheels de ROCm **no** están en PyPI y el driver AMD Adrenalin (≥ 26.2.2) ya debe estar instalado; el setup avisa si parece que falta el driver. Consulta "Backends de GPU" más abajo.

### Instalación modular (extras)

`make setup` pregunta qué módulos quieres. Si prefieres instalar de forma no interactiva, los targets granulares siguen funcionando (nota: estos no instalan la versión de PyTorch para GPU; ejecuta `make configure` después, o usa `make setup`):

| Comando                                        | Qué instala                                                               |
| ---------------------------------------------- | ------------------------------------------------------------------------- |
| `make setup_env_minimal`                       | Solo el **core**: voz → transcripción → portapapeles.                     |
| `make setup_env_claude`                        | Core + `claude-agent-sdk` (habilita el flujo de Claude con `Ctrl+Alt+A`). |
| `make setup_env_tts`                           | Core + `voxcpm` + `soundfile` (TTS, pesado: ~5 GB de pesos del modelo).   |
| `make setup_env` *(legado, asume NVIDIA)*      | Core + Claude + TTS + PyTorch CUDA (`--extras all`).                      |
| `make setup_env_custom EXTRAS="claude tts"`    | Combinación libre de extras.                                              |

Extras (se pasan a `poetry install --extras`): `claude`, `tts`, `whisper-gpu` (transcripción en GPU AMD mediante `openai-whisper`), `all`.

Si falta un extra, la app arranca de todos modos y simplemente desactiva el flujo correspondiente con una advertencia instructiva (`el extra 'claude' no está instalado`), nunca con un fallo fatal.

### Idiomas

Claude responde en PT-BR de forma predeterminada. Para cambiarlo:

```bash
# Cambiar el asistente a inglés
make run ARGS="--output-lang en"
```

Internamente, el prompt canónico (escrito en inglés) tiene un placeholder `{output_lang}` que se completa en tiempo de ejecución; no se mantienen copias traducidas del prompt.

**Los mensajes de la propia app** (logs, textos de ayuda del CLI) también están localizados mediante `gettext` + Babel. El idioma predeterminado es PT-BR; cámbialo con una variable de entorno:

```bash
# Logs de la app en inglés (o `es` para español)
VOICEMATE_LANG=en make run
```

Catálogos disponibles: `pt_BR`, `en` y `es`. Todo texto que ve el usuario debe existir en los tres; `pt_BR` y `es` lo traducen, y `en` deja el `msgstr` vacío (el msgid en inglés ya es el texto).

Para editar / regenerar el catálogo de traducciones:

```bash
make i18n-extract     # extrae las cadenas _() a voicemate.pot
make i18n-update      # propaga las claves nuevas a los .po existentes
make i18n-compile     # compila .po → .mo (gettext carga el .mo en tiempo de ejecución)
```

Los catálogos están en `app/i18n/locales/{pt_BR,en,es}/LC_MESSAGES/voicemate.po`.

### Convenciones de código

- **Identificadores, claves de configuración, docstrings, comentarios nuevos**: inglés (PEP 8).
- **Prompts de LLM**: inglés canónico con el placeholder `{output_lang}`. Sin copias traducidas del prompt.
- **Cadenas visibles para el usuario** (logs, mensajes, ayudas): inglés como `msgid`, traducciones en `app/i18n/locales/<lang>/LC_MESSAGES/voicemate.po`. PT-BR es el predeterminado. Agrega nuevas traducciones marcándolas con `_()` en el código + `make i18n-extract && make i18n-compile`.

### Configurar Claude Code (opcional, solo para el flujo de IA)

Si solo quieres el flujo de portapapeles (`Ctrl+Alt+V`), puedes saltarte esta sección y ejecutar con `--no-claude-chat`.

Para el flujo de IA (`Ctrl+Alt+A`), VoiceMate se comunica con Claude a través de `claude-agent-sdk`, que **reutiliza el CLI `claude` local y sus credenciales**; no necesitas ninguna API key adicional.

1. **Instala Node.js 18+** (sáltalo si ya lo tienes). Descárgalo desde [nodejs.org](https://nodejs.org/) o usa un gestor como `nvm-windows` / `fnm`.

2. **Instala Claude Code globalmente:**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

3. **Autentícate.** Ejecuta el CLI una vez y sigue el inicio de sesión interactivo (abre un navegador):
   ```bash
   claude
   ```
   Elige el método de autenticación que uses (cuenta de Anthropic o Claude Pro/Max). Una vez dentro, escribe `/exit` para salir del chat; las credenciales quedan guardadas localmente.

4. **Verifica que funciona:**
   ```bash
   claude --version
   claude -p "ping"
   ```
   Si `ping` devuelve una respuesta de Claude, ya está todo listo.

Una vez que Claude está autenticado, el flujo de IA de VoiceMate lo detecta automáticamente con `poetry run voice-mate`. Si `claude` no está instalado o no tiene la sesión iniciada, el flujo de IA se omite silenciosamente y el flujo de portapapeles sigue funcionando.

### Configurar el TTS (VoxCPM2)

De forma predeterminada, la respuesta de Claude se lee en voz alta con [VoxCPM2](https://github.com/OpenBMB/VoxCPM), un modelo multilingüe de 2B parámetros (compatible con PT-BR) que recibe una descripción textual de la voz en lugar de un audio de referencia.

- El paquete `voxcpm` se instala automáticamente cuando tu entorno usa Python 3.12. En la primera ejecución, los pesos del modelo se descargan de Hugging Face (algunos GB; tarda un poco).
- La voz predeterminada es "una mujer brasileña joven, natural y cálida, de ritmo tranquilo"; personalízala con `--tts-voice "..."`.
- Para desactivar el TTS, ejecuta con `--no-tts` (la respuesta igual llega al portapapeles y vuelve a sonar el pitido triple).
- Si VoxCPM2 no logra iniciarse (sin CUDA, poco espacio en disco, etc.), la app pasa silenciosamente a un estado sin TTS; no tienes que hacer nada.

#### Backends de GPU (NVIDIA / AMD / CPU)

`torch`/`torchaudio` **no** están fijados en `pyproject.toml`: la versión correcta depende de tu tarjeta y de tu sistema operativo. `make setup` (mediante `app.setup.gpu_bootstrap`) detecta la plataforma + GPU e instala la versión correcta:

| Plataforma × GPU     | Versión de PyTorch         | Transcripción (la mejor disponible primero)               | TTS (OmniVoice/VoxCPM) |
| -------------------- | -------------------------- | --------------------------------------------------------- | ---------------------- |
| Windows + NVIDIA     | CUDA `cu128`               | `faster-whisper` (CUDA)                                   | GPU (CUDA)             |
| Windows + AMD        | ROCm (`repo.radeon.com`)   | **whisper.cpp + Vulkan**                                  | GPU (ROCm)             |
| Linux/WSL2 + AMD     | ROCm (pytorch.org)         | **faster-whisper vía CTranslate2-ROCm** → whisper.cpp + Vulkan → openai-whisper | GPU (ROCm) |
| Linux + NVIDIA       | CUDA `cu128`               | `faster-whisper` (CUDA)                                   | GPU (CUDA)             |
| cualquiera, sin GPU  | CPU                        | `faster-whisper` (int8)                                   | CPU (lento)            |

**AMD en Linux/WSL2 (recomendado para AMD):** el setup ofrece compilar el
[fork CTranslate2-ROCm](https://github.com/arlo-phoenix/CTranslate2-rocm); con él, la transcripción usa
exactamente el mismo motor `faster-whisper` que en NVIDIA (calidad idéntica). Si lo omites (o la compilación falla), la
cadena recurre automáticamente a **whisper.cpp** compilado con Vulkan (el modo servidor mantiene el modelo cargado en
caliente, con arranque rápido) con silero-VAD, y luego a `openai-whisper`. La elección se recuerda (`ct2_rocm_ok` en la
configuración); `make configure` lo reintenta y `make stt-eval` mide la calidad de forma objetiva (WER + detector de palabras partidas).

**AMD en Windows:** la transcripción usa **whisper.cpp + Vulkan**, un pequeño binario nativo más un modelo GGUF
(large-v3-turbo fp16) descargado en `~/.cache/voicemate/whispercpp/` (verificado con SHA-256). El stack de PyTorch ROCm
se sigue instalando, pero solo para el TTS.

Para confirmar que la aceleración por GPU está activa:

```bash
poetry run python -c "import torch; print('GPU:', torch.cuda.is_available())"
```

Debe imprimir `GPU: True` (en ROCm, el HIP de AMD se reporta como `cuda`, así que `True` también es lo correcto en AMD). Si imprime `False`:

- **NVIDIA:** actualiza tu driver (`nvidia-smi`); los drivers recientes (≥ 545) cubren CUDA 12.8.
- **AMD:** instala/actualiza el driver Adrenalin (≥ 26.2.2) y luego ejecuta `make configure`.

Si no tienes GPU y solo quieres el flujo de portapapeles, ejecuta con `--no-tts`. VoxCPMSpeaker también muestra al iniciar una advertencia específica según el fabricante cuando detecta PyTorch sin aceleración.

Puedes forzar la detección en cada ejecución con `--gpu-backend {auto,nvidia,amd,cpu}`, `--whisper-backend {faster-whisper,whispercpp,openai-whisper}` y `--stt-strategy {auto,faster-whisper-rocm,whispercpp,openai-whisper}`.

### Plataformas y disparadores

La capa de plataforma (`app/platform/`) detecta dónde estás y elige el mecanismo de atajos y la integración
con el portapapeles adecuados; puedes forzarlos con `--platform` / `--trigger`:

| Plataforma      | Disparador de atajos (predeterminado)     | Portapapeles         | Notas |
| --------------- | ----------------------------------------- | -------------------- | ----- |
| `windows`       | `keyboard-hooks` (libs keyboard/mouse)    | pyperclip            | El mismo comportamiento de siempre (incl. listener keepalive) |
| `linux-x11`     | `pynput` (GlobalHotKeys)                  | pyperclip (xclip)    | `poetry install --extras linux` |
| `linux-wayland` | `evdev` (/dev/input, requiere el grupo `input`) | pyperclip (wl-copy) | `sudo usermod -aG input $USER` |
| `wsl2`          | `socket`: daemon HTTP local + un pequeño script de atajos del lado de Windows | sincronización de WSLg (alternativa `clip.exe`) | Consulta [docs/wsl2.md](docs/wsl2.md) |

Los atajos predeterminados son idénticos en todas partes: `Ctrl+Alt+V` (portapapeles) y `Ctrl+Alt+A` (Claude). En WSL2
los registra `scripts/windows/voicemate-hotkeys.ahk` (o `.ps1`), que hace POST al daemon, con la misma
semántica de "el atajo de detener elige el handler". Ejecuta `make doctor` para validar
micrófono/audio/disparador/GPU con correcciones concretas. En Windows, la [aplicación companion](#aplicación-companion-bandeja) reemplaza estos scripts.

## Uso

```bash
make run
```

Atajos predeterminados:

- **`Ctrl+Alt+V`**: flujo de portapapeles (transcripción → portapapeles)
- **`Ctrl+Alt+A`**: flujo de Claude (transcripción → Claude → respuesta de la IA en el portapapeles + TTS)

### Flujo de portapapeles

1. Presiona `Ctrl+Alt+V` para empezar a grabar (pitido de inicio)
2. Habla con naturalidad
3. Presiona `Ctrl+Alt+V` de nuevo para detener
4. La transcripción se copia a tu portapapeles (pitido doble)
5. Pégala con `Ctrl+V` donde quieras

### Flujo de Claude (multiturno con voz)

1. Presiona `Ctrl+Alt+A` para empezar a grabar
2. Di tu prompt
3. Presiona `Ctrl+Alt+A` de nuevo para detener: VoiceMate transcribe, copia la transcripción al portapapeles y la envía a Claude
4. La respuesta de la IA reemplaza el contenido del portapapeles y VoxCPM2 empieza a leerla en voz alta (en PT-BR de forma predeterminada)
5. Presiona `Ctrl+Alt+A` otra vez para hacer una pregunta de seguimiento; la conversación continúa en la misma sesión

**Detener decide el destino:** puedes empezar con `Ctrl+Alt+V` y detener con `Ctrl+Alt+A` (o al revés). El atajo que presionas para *detener* elige el handler.

**Cancela mientras Claude piensa o habla:** presionar cualquier atajo mientras la IA responde (o mientras el TTS lee en voz alta) cancela al instante e inicia una nueva grabación. El contexto de la conversación se conserva.

**Historial de Win+V:** como tanto la transcripción como la respuesta de la IA pasan por el portapapeles, el historial del portapapeles de Windows (`Win+V`) muestra ambas; es útil cuando quieres comparar lo que dijiste con lo que respondió Claude.

### Opciones

```bash
# Elegir otro modelo de Whisper
poetry run voice-mate --model medium

# Atajos personalizados
poetry run voice-mate --hotkey "ctrl+shift+r" --claude-chat-hotkey "ctrl+shift+c"

# Desactivar el flujo de Claude (solo portapapeles)
poetry run voice-mate --no-claude-chat

# Darle un system prompt a Claude
poetry run voice-mate --claude-system-prompt "Eres un asistente de productividad conciso."

# Limitar la sesión multiturno
poetry run voice-mate --claude-max-turns 20

# Desactivar el TTS (la respuesta va solo al portapapeles + pitido)
poetry run voice-mate --no-tts

# Personalizar el perfil de voz del TTS
poetry run voice-mate --tts-voice "Un hombre brasileño, de voz grave y pausada."

# Forzar la CPU para el TTS (más lento, pero funciona sin GPU)
poetry run voice-mate --tts-device cpu

# Guardar el audio generado por el TTS en un directorio
poetry run voice-mate --tts-save-dir ./tts_logs

# Forzar la CPU para la transcripción con Whisper (sin GPU disponible)
poetry run voice-mate --cpu

# Forzar la detección de GPU / backend de transcripción en esta ejecución
poetry run voice-mate --gpu-backend amd                       # fuerza AMD (ROCm)
poetry run voice-mate --gpu-backend nvidia --whisper-backend faster-whisper

# Usar un botón lateral del mouse (solo flujo de portapapeles)
poetry run voice-mate --input-method mouse --mouse-button x

# Ajustar el watchdog y el keepalive del listener
poetry run voice-mate --listener-refresh-seconds 30 --watchdog-timeout 60
```

### Modelos

| Modelo             | VRAM (GPU) | Velocidad    | Calidad    |
| ------------------ | ---------- | ------------ | ---------- |
| `tiny`             | ~75 MB     | Muy rápida   | Básica     |
| `base`             | ~140 MB    | Rápida       | Buena      |
| `small`            | ~460 MB    | Moderada     | Muy buena  |
| `medium`           | ~1.0 GB    | Moderada     | Muy alta   |
| `large-v3-turbo`   | ~1.5 GB    | Rápida       | Excelente  |
| `large-v3`         | ~3.0 GB    | Lenta        | Máxima     |

El predeterminado es `large-v3-turbo`: el mejor equilibrio entre velocidad y calidad, especialmente para audio con mezcla de idiomas.

## Aplicación companion (bandeja)

El companion es una pequeña aplicación de escritorio (PySide6) que vive en la bandeja del sistema. Inicia y supervisa el motor, registra los atajos, reproduce los sonidos de aviso, escribe cada transcripción en el portapapeles y comprueba que haya llegado, y reúne en un solo lugar el estado y el botón para salir de todo. En Windows reemplaza el script de PowerShell/AutoHotkey y controla el motor dentro de WSL2; en Linux es una interfaz opcional. El motor en sí no cambia: `make run` sigue funcionando sin el companion.

### Instalación en Windows

**Con el instalador (recomendado).** Ejecuta `VoiceMate-Setup-<versión>.exe`. Instala solo para tu usuario (sin pedir administrador) en `%LOCALAPPDATA%\Programs\VoiceMate` y agrega VoiceMate al menú Inicio; un acceso directo en el escritorio e iniciar junto con Windows son opcionales (la primera instalación ofrece lo segundo; después lo controla **Iniciar VoiceMate al iniciar sesión**, en la Configuración). El instalador habla español, inglés y portugués. Para generarlo tú mismo (Python 3.12+ e Inno Setup 6.3+, `winget install JRSoftware.InnoSetup`):

```powershell
make companion-venv        # una vez: .venv-companion con PySide6 y PyInstaller en las versiones fijadas
make companion-installer   # dist\VoiceMate (PyInstaller), luego dist\installer\VoiceMate-Setup-<versión>.exe
```

**Desde el código fuente.** Con Python 3.12+ en Windows:

```powershell
make companion-venv   # una vez
make run-tray
```

En ambos casos el motor sigue en WSL2 (instálalo como en [docs/wsl2.md](docs/wsl2.md)): el companion lo inicia por ti, o se conecta a uno que ya esté en ejecución (por ejemplo el servicio de systemd). Si uno de los scripts antiguos de atajos (PowerShell o AutoHotkey) sigue en ejecución, ciérralo y quita su acceso directo de `shell:startup`: ahora quien registra `Ctrl+Alt+V` y `Ctrl+Alt+A` es el companion.

### Anclar a la barra de tareas

Windows no permite que los instaladores anclen aplicaciones. Abre Inicio, busca VoiceMate, haz clic derecho sobre él y elige **Anclar a la barra de tareas**. Hacer clic en el icono anclado con VoiceMate en ejecución abre la ventana de estado; el clic derecho ofrece **Configuración**, **Reiniciar el motor**, **Reiniciar WSL...** y **Salir de VoiceMate**. Anclar usa el acceso directo del menú Inicio, así que aplica a la aplicación instalada. En la primera ejecución, una notificación **Ancla VoiceMate a la barra de tareas** recuerda estos pasos.

El icono de la bandeja (el micrófono que muestra grabando, transcribiendo y listo, como el indicador de micrófono en uso de Windows) es otra cosa: Windows 11 oculta los iconos nuevos de la bandeja detrás de la flecha junto al reloj, así que VoiceMate mantiene su icono en la barra de tareas de forma predeterminada. Para devolverlo detrás de la flecha, desmarca **Mostrar siempre el icono de VoiceMate en la barra de tareas** en Configuración > **General**.

### Salir

Menú del icono de la bandeja > **Salir de VoiceMate** (también es un botón en la ventana de estado y una opción del menú de clic derecho del icono anclado). Salir detiene el motor que inició el companion; un motor al que solo se conectó (por ejemplo el servicio de systemd) sigue en ejecución. Desde una terminal, para la aplicación instalada (PowerShell):

```powershell
& "$env:LOCALAPPDATA\Programs\VoiceMate\VoiceMate.exe" --command quit
```

Desde el código fuente: `make run-tray ARGS="--command quit"`. Un `--command` nunca inicia VoiceMate: si no está en ejecución, no pasa nada.

### Configuración

Menú del icono de la bandeja > **Configuración...**, en tres pestañas: **Atajos**; **Sonidos** (sonidos integrados o tus propios archivos WAV, volumen); **General**: idioma, notificaciones, **Iniciar VoiceMate al iniciar sesión**, **Mostrar siempre el icono de VoiceMate en la barra de tareas** (solo en Windows) y el motor (modo, distro de WSL, carpeta del motor y **Reiniciar WSL si falla el audio**: **Automáticamente**, **Preguntar antes** o **Nunca**). La configuración se guarda en `%APPDATA%\VoiceMate\companion.toml` (Linux: `~/.config/voicemate/companion.toml`) y se conserva al desinstalar. Los logs están en `%LOCALAPPDATA%\VoiceMate\logs` (Linux: `~/.local/state/voicemate/logs`); menú del icono de la bandeja > **Motor** > **Abrir registros** abre la carpeta.

### Linux (opcional)

La CLI sigue funcionando como antes. Para el icono de la bandeja y la ventana de estado, en el repositorio:

```bash
poetry install --extras ui
make run-tray
```

En Linux el motor mantiene sus propios atajos (la pestaña **Atajos** los muestra solo para lectura). Sin bandeja del sistema (por ejemplo GNOME sin la extensión AppIndicator), la ventana de estado es la ventana principal. Para agregar VoiceMate al menú de aplicaciones:

```bash
mkdir -p ~/.local/share/applications &&
  sed "s|@VOICEMATE_DIR@|$PWD|g" packaging/linux/voicemate-companion.desktop \
  > ~/.local/share/applications/voicemate-companion.desktop
```

### Solución de problemas

| Síntoma | Solución |
| ------- | -------- |
| "Iniciando el motor..." durante mucho tiempo | La primera carga del modelo tarda de 10 a 60 s. Revisa el `engine.log` (**Motor** > **Abrir registros**) y la distro de WSL y la carpeta del motor en la Configuración |
| Un atajo muestra "En uso por otra aplicación" en la primera ejecución | Un script antiguo de atajos (PowerShell o AutoHotkey) sigue en ejecución: ciérralo y quítalo de `shell:startup` |
| "El audio de WSL se detuvo" o "Sin micrófono" | Conecta un micrófono. WSL se reinicia según **Reiniciar WSL si falla el audio**; a mano: **Motor** > **Reiniciar WSL...** |
| "El motor es más antiguo que esta aplicación. Reinícialo o actualízalo." | Actualiza el checkout en WSL (`git pull`) y usa **Reiniciar el motor** |
| Una transcripción no se copió | Queda en **No copiadas**, en el menú de la bandeja (haz clic para copiarla) y en la ventana de estado (**Copiar**) |
| SmartScreen advierte sobre el instalador | El instalador no tiene firma de código: **Más información** > **Ejecutar de todas formas** |

## Makefile

| Comando            | Descripción                                   |
| ------------------ | --------------------------------------------- |
| `make setup`       | Detecta plataforma + GPU, instala el PyTorch correspondiente + módulos y recuerda la elección |
| `make configure`   | Vuelve a ejecutar el selector de GPU/módulos (p. ej. tras cambiar de GPU) |
| `make doctor`      | Diagnóstico del entorno (micrófono/audio, disparador, whisper.cpp, GPU) con correcciones |
| `make stt-eval`    | Control de calidad del STT: WER + detector de palabras partidas contra muestras locales |
| `make setup_env`   | Instalación legada (asume NVIDIA + todos los extras) |
| `make lock`        | Regenera `poetry.lock` (tras editar el pyproject) |
| `make run`         | Ejecuta con el modelo predeterminado (`large-v3-turbo`) |
| `make run-large`   | Ejecuta con `large-v3`                        |
| `make run-turbo`   | Ejecuta con `large-v3-turbo`                  |
| `make format`      | Formatea el código con Ruff                   |
| `make lint`        | Lint con Ruff + verificación de tipos con Mypy |
| `make test`        | Ejecuta la suite de pytest                    |
| `make run-tray`    | Ejecuta la aplicación companion (bandeja); `ARGS="..."` pasa flags |
| `make companion-venv` | Crea `.venv-companion` (entorno del companion en Windows, versiones fijadas) |
| `make companion-test` / `make companion-lint` | Tests / lint del companion |
| `make companion-build` | Congela el companion con PyInstaller (`dist\VoiceMate`) |
| `make companion-installer` | Genera el instalador de Windows con Inno Setup (`dist\installer`) |
| `make clean`       | Elimina las cachés                            |

## Arquitectura

```
app/
├── main.py                          # Punto de entrada + parseo del CLI + conexión de los flujos
├── core/
│   └── config.py                    # Dataclass de configuración + FlowConfig + TTSConfig
└── services/
    ├── recorder.py                  # Captura del micrófono (sounddevice)
    ├── transcriber.py               # Inferencia de Whisper (faster-whisper)
    ├── audio_feedback.py            # Pitidos multiplataforma
    ├── audio_player.py              # Reproductor de audio con cola para el streaming del TTS
    ├── recording_session.py         # Máquina de estados: idle → recording → processing
    ├── transcription_handler.py     # Protocol + ClipboardHandler
    ├── claude_chat_handler.py       # Flujo de Claude: envío + doble portapapeles + TTS + cancelación
    ├── claude_runtime.py            # Puente sync ↔ asyncio para claude-agent-sdk
    ├── tts.py                       # Protocol TextToSpeech + NullSpeaker
    ├── voxcpm_speaker.py            # Speaker de VoxCPM2 (streaming + cancelación)
    ├── input_listener.py            # Abstracción del disparador de teclado / mouse
    ├── multi_hotkey_listener.py     # Varios atajos globales con callbacks distintos
    ├── listener_keepalive.py        # Reinstalación periódica del hook (corrección para Windows)
    └── watchdog.py                  # Monitor de salud a nivel de proceso
```

### ¿Por qué el listener-keepalive?

En Windows, los hooks de bajo nivel (`WH_KEYBOARD_LL` / `WH_MOUSE_LL`) que usan las bibliotecas de atajos globales son **eliminados silenciosamente** por el sistema operativo si el callback del hook supera `LowLevelHooksTimeout` (máximo de 1000 ms en Windows 10+). Con la CPU bajo carga alta, esto ocurre sin ninguna notificación ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)). VoiceMate vuelve a registrar el atajo cada 60 s de forma predeterminada, así que, aunque el sistema operativo haya eliminado el hook, el siguiente ciclo lo reinstala.

### ¿Por qué un TTS intercambiable?

La arquitectura separa el **orquestador** (Protocol `TextToSpeech` en `tts.py`) de la **implementación concreta** (`VoxCPMSpeaker`). Esto facilita probar otras bibliotecas de TTS más adelante (edge-tts, ElevenLabs, Piper, etc.): basta con crear una nueva implementación del Protocol y conectarla mediante la configuración. Si una biblioteca no encaja, puedes borrar solo su archivo.

## Stack

- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)**: Whisper optimizado con CTranslate2
- **[sounddevice](https://python-sounddevice.readthedocs.io/)**: captura del micrófono
- **[keyboard](https://github.com/boppreh/keyboard)** / **[mouse](https://github.com/boppreh/mouse)**: hooks globales de entrada
- **[pyperclip](https://github.com/asweigart/pyperclip)**: acceso al portapapeles
- **[claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python)**: flujo de Claude, sobre el CLI `claude` local
- **[voxcpm](https://github.com/OpenBMB/VoxCPM)**: TTS multilingüe con diseño de voz a partir de una descripción textual
- **[soundfile](https://github.com/bastibe/python-soundfile)**: lectura/escritura de WAV (opcional, solo se usa al guardar el audio del TTS)

## Contribuir

Los issues y PRs son bienvenidos. Ejecuta `make all` (format + lint + test) antes de abrir un PR.

## Licencia

[MIT](LICENSE) © Álli Terhorst

Parte de [NanoBR](https://github.com/nano-br): utilidades de código abierto para la productividad diaria.
