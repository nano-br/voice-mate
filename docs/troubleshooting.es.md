[English](troubleshooting.md) | [Português](troubleshooting.pt-BR.md) | **Español** | [Русский](troubleshooting.ru.md) | [简体中文](troubleshooting.zh-CN.md)

# Solución de problemas

Propósito: cómo diagnosticar los problemas de VoiceMate, con los mensajes exactos que muestra la aplicación y qué hacer ante cada uno. Volver al [README](../README.es.md).

## Primeros pasos

1. **Lee los registros.** Menú de la bandeja > **Motor** > **Abrir registros** abre la carpeta con `companion.log` y `engine.log` (en Windows `%LOCALAPPDATA%\VoiceMate\logs`, en Linux `~/.local/state/voicemate/logs`). `engine.log` contiene todo lo que imprimió el motor.
2. **Ejecuta `make doctor`** en la carpeta del motor (dentro de WSL para el companion). Nunca se detiene en el primer problema: cada comprobación imprime ✓ o ✗, y cada comprobación fallida imprime una línea `fix:` con el comando que debes ejecutar.
3. **Reinicia el motor:** **Motor** > **Reiniciar el motor**. También reinicia el supervisor después de "VoiceMate dejó de reintentar".

### Qué comprueba `make doctor`

En este orden: la plataforma detectada; solo en WSL2, el socket de PulseAudio de WSLg, `PULSE_SERVER`, el complemento de ALSA a PulseAudio, las herramientas nativas del portapapeles (`wl-copy`, `xclip`) y la interoperabilidad de WSL para `clip.exe`; la biblioteca de audio, el dispositivo de entrada y el dispositivo de salida; el disparador de atajos (acceso a `/dev/input` para `evdev`, `DISPLAY` para `pynput`, el daemon HTTP en WSL2 o los hooks de Windows); el binario, el modelo y el VAD de whisper.cpp; el dispositivo Vulkan (Linux y WSL2); la CLI de Claude (`node` y `claude`, se omite cuando el flujo guardado es `clipboard`); `espeak-ng` cuando Kokoro está instalado; PyTorch con GPU.

## Mensajes del companion

| Mensaje (título de la notificación) | Significado | Qué hacer |
|---|---|---|
| "VoiceMate se está iniciando" | Se pulsó un atajo mientras el motor aún carga el modelo (de 10 a 60 segundos, mucho más en el primer inicio, que descarga el modelo) | Espera y vuelve a pulsar |
| "No se encontró la carpeta del motor" | No hay ninguna copia del motor en las carpetas donde busca el companion | Clona el motor en una de ellas ([installation.md](installation.md#2-install-the-engine)) o define "Carpeta del motor:" en Configuración > **General** |
| "No se pudo iniciar el motor" | Falló `wsl.exe` o `make run-engine` | Revisa `engine.log`; comprueba que `make` y `poetry` funcionan en un shell de login (`wsl -e bash -lc "make --version && poetry --version"`) |
| "VoiceMate dejó de reintentar" | El motor falló demasiadas veces (más de 5 reinicios en 15 minutos, 3 tiempos de espera de inicio seguidos o más de 3 reinicios de WSL en 1 hora) | Lee `engine.log`, corrige la causa y luego usa **Reiniciar el motor**. En el primer inicio, una descarga lenta del modelo puede agotar tres veces seguidas los 240 segundos de un inicio del motor: ejecuta `make run` una vez en la carpeta del motor (dentro de WSL), detenlo con `Ctrl+C` cuando haya cargado y luego usa **Reiniciar el motor** |
| "Motor desactualizado" / "El motor es más antiguo que esta aplicación. Reinícialo o actualízalo." | La copia del motor es más antigua que el companion | Actualízalo (`git pull`, luego `make setup`), y luego **Reiniciar el motor** |
| "Motor ejecutado por systemd" | El servicio `voicemate` de systemd ejecuta el motor, así que el companion se conecta a él | Déjalo así, o ejecuta `systemctl --user disable --now voicemate` en WSL para que VoiceMate gestione el motor |
| "Acceso al motor denegado" | El motor rechazó el token de la API | VoiceMate vuelve a leer `~/.config/voicemate/api-token` cada 30 segundos; si persiste, reinicia el motor |
| "Motor inaccesible" / "Motor ocupado" | El motor no respondió a un atajo (a tiempo) | VoiceMate sigue reconectando; revisa `engine.log` si dura |
| "Atajo no disponible" / "En uso por otra aplicación" (pestaña Atajos) | Otro programa tiene esa combinación. A menudo, el antiguo script de atajos de VoiceMate | Cierra el script y quítalo de `shell:startup`, o elige otros atajos |
| "Sin micrófono" | Windows no tiene micrófono | Conecta uno |
| "Micrófono no disponible" | El motor no pudo abrir el micrófono | En Windows: permite que las aplicaciones de escritorio usen el micrófono en la configuración de privacidad de Windows, y cierra cualquier aplicación que lo retenga en exclusiva. Si el audio de WSL está bloqueado, reinicia WSL |
| "El audio de WSL se detuvo" | PulseAudio de WSLg no responde | Mira [Audio de WSL](#audio-de-wsl-y-el-micrófono) |
| "Nada transcrito" / "Nada grabado" | No se detectó voz, o la grabación no capturó audio | Revisa el micrófono y el nivel de entrada |
| "Transcripción lenta" | La transcripción corre mucho más lenta que el audio, normalmente sin GPU | `make doctor` (PyTorch con GPU, dispositivo Vulkan); mira [wsl2.md](wsl2.md#speech-to-text-performance-on-wsl2) |
| "Falló la transcripción" | El backend de reconocimiento de voz lanzó un error | Lee `engine.log` |
| "Claude no respondió" | Falló la petición a Claude | Lee `engine.log`; comprueba que `claude` funciona en WSL (`claude -p "ping"`) |
| "No se copió al portapapeles" | Otra aplicación mantuvo bloqueado el portapapeles | El texto está en **No copiadas**: haz clic ahí para copiarlo |
| "No se pudo copiar" | Falló la copia desde Recientes o No copiadas | Inténtalo de nuevo |
| "Configuración restablecida" | No se pudo leer `companion.toml`; VoiceMate arrancó con los valores por defecto y guardó el archivo antiguo como `companion.toml.broken-<timestamp>` | Compara y corrige el archivo antiguo, o quédate con los valores por defecto |
| 'Lista "No copiadas" restablecida' | No se pudo leer `pending.json`; el archivo antiguo (con sus textos) se guardó como `pending.json.broken-<timestamp>` | Recupera los textos del archivo antiguo si los necesitas |

## Modo de transcripción

| Mensaje | Significado | Qué hacer |
|---|---|---|
| "¿Instalar el modo CPU?" / "¿Instalar el modo GPU?" | El modo que elegiste no está instalado en el entorno del motor | **Instalar** lo descarga (el tamaño está en la pregunta) y muestra la salida. Cancelar mantiene el modo actual |
| "La instalación falló. Los detalles están abajo; el motor sigue en el modo actual." | `make stt-install` terminó con un error | Lee la salida en la ventana, corrige la causa (red, espacio en disco, ROCm), inténtalo de nuevo, o ejecuta `make stt-install MODE=gpu` en la carpeta del motor |
| "No hay modo GPU en este equipo" | La comprobación no encontró ninguna tarjeta gráfica que VoiceMate pueda usar | Usa el modo CPU |
| "El modo GPU no está funcionando. Instálalo desde el menú Transcripción." (ventana de estado) | El modo GPU recurrió a la CPU | Menú de la bandeja > "Transcripción" > "GPU (la más rápida)" ofrece la instalación; `make doctor` muestra las comprobaciones de la GPU |
| "Cambio de modo de transcripción en curso" | Aún hay una comprobación o una instalación en marcha | Espera a que termine y vuelve a intentarlo |
| "No se pudo cambiar el modo de transcripción" | El motor no pudo reiniciarse con el modo nuevo | Lee `engine.log` |

## Asistente de voz

| Síntoma | Causa | Qué hacer |
|---|---|---|
| "No se encontró Claude Code", y un prompt de voz responde "El asistente no pudo iniciarse" | VoiceMate, iniciado al entrar en la sesión, recibe el `PATH` guardado en el registro de Windows. El instalador nativo de Claude Code pone `claude.exe` en `%USERPROFILE%\.local\bin`, una carpeta que a menudo solo añade al `PATH` un perfil del shell, así que un terminal encuentra `claude` y VoiceMate no | Nada que hacer en una versión actual: VoiceMate también busca en `~/.local/bin` y en `%APPDATA%\npm`. Para comprobarlo, ejecuta `where claude` en un terminal: si no imprime nada, instala Claude Code. Si `claude` está en otro sitio, define `VOICEMATE_CLAUDE_CLI` con su ruta completa ([configuration.es.md](configuration.es.md#variables-de-entorno)) y pulsa **Reiniciar** en la ventana del asistente |
| "Claude Code no ha iniciado sesión" | No hay ninguna sesión de Claude en este equipo | Ejecuta `claude auth login` en un terminal y luego **Reiniciar**. El inicio de sesión de Claude Desktop no cuenta |
| "El asistente no pudo iniciarse" o "El asistente se detuvo" | El proceso falló o terminó | Abre `companion.log` (menú de la bandeja > **Motor** > **Abrir registros**), corrige la causa y luego **Reiniciar** |
| "No se pudo preparar el asistente" | VoiceMate no pudo instalar la skill ni escribir `~/.voicemate/assistant.json` | Comprueba que tu carpeta de usuario y `~/.claude` admiten escritura; el registro tiene los detalles |
| No hay enlace "Abrir en claude.ai", nada en el móvil | No se pudo activar Remote Control. El asistente sigue funcionando en este equipo | Reinicia la conversación: Configuración > **Asistente**, pon "Modo del asistente:" en desactivado, aplica, y vuelve a activarlo. `companion.log` dice qué respondió la petición. La petición no está documentada y puede cambiar entre versiones de Claude Code (mira la [investigación](research/claude-code-client.md#61-a-voicemate-owned-session-in-the-desktop-tested-works)) |
| El asistente responde en el idioma equivocado, o sin nombre | La skill no pudo leer `~/.voicemate/assistant.json` y dice "VoiceMate's assistant settings were not found" | Reinicia la conversación (Configuración > **Asistente**, desactivar, aplicar y volver a activar): VoiceMate vuelve a escribir el archivo cuando empieza la conversación. Comprueba que el archivo está en tu carpeta de usuario (`~/.voicemate/`) |
| No hay una entrada "Asistente" en el menú de la bandeja | El asistente está desactivado | Configuración > **Asistente** > "Modo del asistente:" > "Conversación propia de VoiceMate" |
| "Más de una conversación coincide con ..." o "Ninguna conversación coincide con ..." | Un comando escrito no pudo elegir un solo título | Escribe más del título, o desactiva "Entender comandos de voz en el texto escrito" |
| Se rechaza un archivo de personalidad | No es un archivo `.md` existente | Elige un archivo Markdown con "Examinar..." |
| "No se ha elegido ninguna conversación de Claude Desktop" | El modo es "Una conversación de Claude Desktop" y "Conversación de Desktop:" está vacía | Escribe el título exacto de la barra lateral de Claude Desktop en Configuración > **Asistente** |
| "No se pudo preparar el asistente" en el modo Desktop, y el registro dice "no Claude Desktop conversation titled ..." | Ninguna conversación de Desktop con transcripción tiene ese título exacto | Comprueba el título letra por letra en la barra lateral de Claude Desktop; una conversación que nunca tuvo un mensaje aún no tiene transcripción |
| "El asistente no pudo iniciarse" en el modo Desktop, y el registro dice `window_missing`, `conversation_missing` o `ambiguous` | Claude Desktop no está abierto, la conversación no está en su barra lateral, o dos tienen ese título | Abre Claude Desktop, cambia el nombre de uno de dos títulos iguales y luego pulsa **Reiniciar** |
| El registro dice `locked` o `focus_lost`, y un mensaje no llegó a Desktop | La pantalla estaba bloqueada, o otra ventana tomó el foco mientras VoiceMate escribía. No se escribió nada | Desbloquea la pantalla, deja el teclado quieto durante los 2 segundos que tarda un mensaje y vuelve a enviarlo |
| Desapareció un borrador que escribías en una conversación de Desktop | Un mensaje del asistente borra el borrador de la conversación en la que escribe | No escribas en la conversación que usa el asistente (el modo Desktop), ni en una en la que esté a punto de escribir |
| Tras una actualización de Claude Desktop el modo Desktop deja de encontrar sus controles | La actualización cambió la ventana, así que el perfil ya no coincide | Configuración > **Asistente** > **Comprobar ahora** para buscar un perfil más reciente; si no hay ninguno, mira [desktop-profile.md](desktop-profile.md#writing-a-profile-for-a-new-desktop-version) |
| "Problema: ..." bajo el perfil de Claude Desktop | Se rechazó el perfil importado o descargado (el motivo nombra el campo) | Corrige el archivo y vuelve a importarlo con **Importar...**, o pulsa **Usar el predeterminado** |
| El asistente no puede navegar, abrir pull requests ni usar otras herramientas exclusivas de Desktop | En "Conversación propia de VoiceMate" solo tiene las herramientas `voicemate` | Pídele que pase la tarea a una conversación de Desktop, o cambia al modo Desktop |
| El asistente está en silencio, y la ventana dice "No hay voz disponible" | Nada puede hablar: el motor está detenido o no tiene voz (su motor de voz no es Kokoro ni un proveedor personalizado, o el proveedor no funciona) y Windows no tiene voz para el idioma, o "Origen de la voz:" es "La voz del motor" | En Configuración > **Asistente**, pon "Origen de la voz:" en "Las voces de Windows" (o "Automática") y pulsa **Probar**; añade una voz en Configuración de Windows > Hora e idioma > Voz. O revisa Configuración > **Voz** y el motor de voz del motor ([protocolo](tts-provider-protocol.md#the-engine-speaks-the-same-protocol)); "Leer las respuestas en voz alta" debe estar activado. Las respuestas siguen siendo legibles en la ventana |
| La voz del asistente se detiene cuando grabo y vuelve más tarde | Es a propósito: la voz nunca habla por encima del micrófono. Espera mientras grabas y mientras se transcribe la grabación, y luego vuelve a decir la frase que se cortó y lo que estaba en cola | Nada que corregir. Un prompt nuevo al asistente, "deja de hablar" o un atajo del asistente lo silencia del todo |
| El botón **Hablar** de la ventana del asistente está desactivado | El asistente no está listo (desactivado, iniciándose, respondiendo o averiado), o el motor aún transcribe la última grabación | Espera a "Listo" en la ventana. Tras un error, pulsa **Reiniciar** |
| Nunca llegó una respuesta de otra conversación | Solo se siguen las conversaciones a las que el asistente escribió, que inició o que le pediste vigilar. Una espera termina tras una hora sin un turno nuevo, una vigilancia que pediste tras 12 horas, y el asistente recibe una nota cuando termina cualquiera de las dos | Pregúntale al asistente qué está pendiente. Con "Solo guardarla" o "Solo reproducir un sonido y guardar la respuesta", la respuesta espera en **Respuestas en espera**, no en la conversación |
| El asistente dice que no pudo enviar a una sesión de terminal o IDE (la central recibió `not sent (in use)` o `not sent (busy)`) | `in use`: la sesión está abierta en un programa, o su transcripción cambió en los últimos 2 minutos. `busy`: el último mensaje de VoiceMate a esa sesión aún se está respondiendo, o se respondió hace un momento | Cierra la sesión allí o espera, y vuelve a pedirlo. Más en [assistant.es.md](assistant.es.md#-problemas-comunes) |
| El icono de la bandeja tiene una insignia azul | Hay respuestas de otras conversaciones esperando | Usa "Escuchar lo que está listo" (menú de la bandeja, `Ctrl+Alt+R`) o abre la ventana |
| Un atajo del asistente dice "En uso por otra aplicación" | `Ctrl+Alt+S`, `Ctrl+Alt+W` o `Ctrl+Alt+R` está ocupado | Configuración > **Atajos**: cámbialo o bórralo |
| El asistente se niega a cambiar un ajuste por voz | No está en el catálogo (los atajos, los archivos, las carpetas y el motor quedan fuera), o el valor no es uno permitido | Cámbialo en Configuración. La lista está en [usage.es.md](usage.es.md#cambiar-voicemate-por-voz) |

## Audio de WSL y el micrófono

- Abrir el micrófono nunca bloquea un atajo: se hace en segundo plano. Si no se abre en 6 segundos, el motor reproduce la señal de error, vuelve a inactivo e informa "Micrófono no disponible".
- En WSL2 el motor sondea PulseAudio de WSLg (`pactl info`) cada 20 segundos e informa el audio como funcionando, no funcionando o desconocido ("Audio:" en la ventana de estado).
- Cuando Windows pierde su micrófono, PulseAudio de WSLg a menudo se bloquea para siempre, incluso después de que el micrófono vuelva (`pactl info` da `Connection failure: Timeout`). Solo `wsl --shutdown` lo recupera.

El companion lo gestiona con el ajuste "Reiniciar WSL si falla el audio:":

| Ajuste | Comportamiento |
|---|---|
| "Automáticamente" (por defecto) | Reinicia WSL por sí mismo, salvo que corran otras distros de WSL: `wsl --shutdown` las detiene todas, Docker incluido, así que entonces pregunta antes |
| "Preguntar antes" | Muestra "WSL necesita reiniciarse" y el elemento de menú "Reiniciar WSL ahora..." |
| "Nunca" | No hace nada; reinicia a mano con **Motor** > **Reiniciar WSL...** |

Se quiere un reinicio de WSL cuando el audio falla en dos sondeos seguidos, o cuando el micrófono falla mientras Windows tiene un dispositivo de captura y el audio no funciona. Nunca ocurre mientras Windows no tiene micrófono: el companion espera a que haya uno (lo comprueba cada 3 segundos). Antes del reinicio espera hasta 30 segundos a que el motor esté inactivo, luego detiene el motor, ejecuta `wsl --shutdown` y vuelve a iniciar el motor.

Sin el companion: ejecuta `wsl --shutdown` en PowerShell y luego `make run` de nuevo.

## Atajos

- **Windows con el companion:** el companion registra `Ctrl+Alt+V` y `Ctrl+Alt+A`. Si una combinación muestra "En uso por otra aplicación", otro programa la tiene.
- **Motor nativo de Windows (`make run`):** Windows puede quitar en silencio el hook de teclado de bajo nivel cuando el sistema está muy cargado. El motor lo reinstala cada 60 segundos (`--listener-refresh-seconds`), y un watchdog reinicia un motor colgado (`--watchdog-timeout`).
- **Wayland:** el disparador `evdev` necesita que tu usuario esté en el grupo `input` (`sudo usermod -aG input $USER`, y luego vuelve a iniciar sesión).
- **X11:** el disparador `pynput` necesita `DISPLAY`.

## Problemas del motor

Los mensajes del motor citados en esta guía están en inglés. El motor los imprime en el idioma de `--output-lang`, cuyo valor por defecto es el portugués (`pt-BR`), o de `VOICEMATE_LANG`, que tiene prioridad. Un motor que inicias con un simple `make run` registra, por tanto, en portugués: añade `ARGS="--output-lang en"` (o define `VOICEMATE_LANG=en` en el shell que lo ejecuta) para leer el texto en inglés. El companion pasa la opción por sí solo, a partir de "Idioma de dictado:" en Configuración.

| Síntoma | Causa | Solución |
|---|---|---|
| "Could not open the HTTP API on 127.0.0.1:{port}" en el registro del motor | El puerto está ocupado, a menudo por otro motor | Detén el otro motor, o elige otro puerto (`--daemon-port`, y `daemon_port` en `companion.toml`) |
| "Claude flow disabled: extra 'claude' not installed." | Falta el extra `claude` | `poetry install --extras claude`, o `make configure` |
| "Failed to start Claude (run `claude login`)" | Falta la CLI de Claude o su sesión está cerrada | Instálala e inicia sesión ([installation.md](installation.md#claude-code-cli-for-the-claude-flow)) |
| "TTS disabled: engine '{engine}' packages not installed." | Falta el extra del motor de TTS | Instala el extra que nombra la siguiente línea del registro, o ejecuta con `--no-tts` |
| El habla en inglés se transcribe mal (como si fuera portugués) | Un motor iniciado a mano fija Whisper en portugués por defecto | `make run ARGS="--output-lang en"` o `--transcription-language en`. Con el companion, define "Idioma de dictado:" en Configuración > **General** |
| La transcripción es de 10 a 50 veces más lenta de lo esperado | El backend recurrió a la CPU | `make doctor` (PyTorch con GPU); en AMD, `rocminfo` debe listar la GPU |
| Falló la compilación de CTranslate2-ROCm | Paquetes de desarrollo de ROCm incompletos, o (ROCm 7) una compilación que eligió gcc: "language hip not recognized" | `make doctor`; instala el HIP SDK de ROCm; `git pull` y `make configure` lo intentan de nuevo, ahora con el clang de ROCm. Mira [wsl2.md](wsl2.md#ctranslate2-rocm-faster-whisper-on-the-gpu-optional-best-quality) |
| "Conv1D on GPU currently requires the cuDNN library" en el registro del motor, y falla la transcripción en la GPU AMD | Un CTranslate2-ROCm compilado sin MIOpen (el equivalente de AMD a cuDNN) en ROCm 7 | `git pull`, luego `make configure` y acepta de nuevo la compilación de CTranslate2-ROCm: ahora la compilación incluye MIOpen. Hasta entonces el motor recurre a openai-whisper (`ct2_rocm_ok = false`) |
| El idioma de la interfaz no es el que elegiste | El idioma cambia tras un reinicio | Responde "Reiniciar ahora" en "¿Reiniciar VoiceMate?" |

## Antiguos scripts de atajos (sin el companion)

El script de PowerShell `scripts/windows/voicemate-hotkeys.ps1` imprime "daemon offline" cuando nada responde en el puerto: inicia el motor en WSL con `make run` y vuelve a pulsar el atajo. Estos scripts solo funcionan con un motor iniciado sin token (`make run`), nunca con `make run-engine`.
