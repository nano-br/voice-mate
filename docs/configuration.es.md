[English](configuration.md) | [Português](configuration.pt-BR.md) | **Español** | [Русский](configuration.ru.md) | [简体中文](configuration.zh-CN.md)

# Referencia de configuración

Propósito: cada opción del motor, clave de configuración, variable de entorno y ubicación de archivos de VoiceMate. Volver al [README](../README.es.md).

## De dónde vienen los ajustes

| Parte | Origen | Precedencia |
|---|---|---|
| Motor | Opciones de la línea de comandos de `voice-mate` (`make run ARGS="..."`) y `~/.config/voicemate/config.toml` (escrito por `make setup` y `make configure`) | La opción, luego `config.toml`, luego la detección o el valor integrado por defecto |
| Companion | `companion.toml`, editado desde Configuración | El archivo; los valores no válidos vuelven al valor por defecto |

Las opciones llegan al motor a través del Makefile con `ARGS`, por ejemplo `make run ARGS="--model medium --no-tts"`. `make` no reenvía las palabras que se ponen después del objetivo, así que usa siempre `ARGS`.

## Opciones del motor

### Reconocimiento de voz y entrada

| Opción | Por defecto | Significado |
|---|---|---|
| `--model {tiny,base,small,medium,large-v2,large-v3,large-v3-turbo}` | `large-v3-turbo` | Modelo de Whisper |
| `--hotkey` | `ctrl+alt+v` | Atajo del flujo del portapapeles |
| `--cpu` | desactivado | Fuerza la CPU (int8). Tiene prioridad sobre cualquier ajuste de GPU |
| `--stt-mode {auto,cpu,gpu}` | la elección guardada, si no `auto` | Dónde transcribir. `cpu`: faster-whisper int8 en el procesador, decodificación voraz con VAD, la GPU queda libre para otros trabajos. `gpu`: el backend de GPU con decodificación voraz, que recurre a la CPU (con una pista de instalación) cuando no funciona. `auto`: la GPU cuando su backend funciona, si no la CPU. Mira [Modos de reconocimiento de voz](#modos-de-reconocimiento-de-voz) |
| `--cpu-threads` | `0` (la mitad de los núcleos, como máximo 8) | Hilos de CPU de faster-whisper en la CPU |
| `--gpu-backend {auto,nvidia,amd,cpu}` | la elección guardada, si no la detectada | Fabricante de la GPU; `auto` detecta la tarjeta |
| `--whisper-backend {faster-whisper,whispercpp,openai-whisper}` | la elección guardada, si no según el fabricante | Motor de reconocimiento de voz |
| `--stt-strategy {auto,faster-whisper-rocm,whispercpp,openai-whisper}` | la elección guardada, si no `auto` | Solo AMD: el primer backend de la cadena de respaldo |
| `--whispercpp-mode {server,cli}` | `server` | `server` mantiene cargado el modelo de whisper.cpp; `cli` lo recarga en cada grabación (más lento) |
| `--transcription-language {auto,pt,en,es,fr,de,it,ja,ru,zh}` | derivado de `--output-lang` | Idioma fijado para la transcripción. `auto` lo detecta en cada grabación (menos estable en grabaciones cortas) |
| `--input-method {keyboard,mouse}` | `keyboard` | `mouse` funciona solo en Windows (disparador `keyboard-hooks`) y solo para el flujo del portapapeles; desactiva el flujo de Claude |
| `--mouse-button` | `x` | Botón del ratón para `--input-method mouse` (`x` es un botón lateral) |
| `--max-recording-seconds` | `600` | Límite de grabación. Suena un aviso al 80 %; al llegar al límite la grabación se detiene y se transcribe |
| `--output-lang` | `pt-BR` | Código BCP-47. El idioma de las respuestas de Claude, el idioma de transcripción por defecto (`pt-BR` da `pt`, `en` da `en`, `zh-CN` da `zh`) y el idioma por defecto de los mensajes del motor. El valor por defecto fija Whisper en portugués: pasa `--output-lang en` (o `--transcription-language en`) cuando dictes en inglés. El companion lo pasa para el motor que inicia, a partir del ajuste "Idioma de dictado:" |
| `--stt-engine {builtin,external}` | la elección guardada, si no `builtin` | `builtin` es el motor de reconocimiento de voz elegido arriba. `external` es un servicio que ejecutas tú ([protocolo](stt-provider-protocol.es.md)) |
| `--stt-url` | `http://127.0.0.1:8766` | Solo `external`: URL base del proveedor. Una URL no válida se registra y se usa el motor integrado |
| `--stt-options` | ninguno | Solo `external`: objeto JSON de opciones que se pasan tal cual al proveedor |
| `--no-stt-fallback` | desactivado | Solo `external`: una grabación que el proveedor no puede transcribir falla en lugar de ir al motor integrado |

El token del proveedor de STT nunca es una opción: se lee de `VOICEMATE_STT_TOKEN`, si no de `stt_external_token` en el archivo del motor, y nunca se registra. Su comando de inicio viene solo de `VOICEMATE_STT_START_COMMAND`. El motor elimina ambos de su entorno una vez leídos. Mira el [protocolo de proveedor de STT](stt-provider-protocol.es.md).

### Plataforma, disparador y API

| Opción | Por defecto | Significado |
|---|---|---|
| `--platform {windows,linux-x11,linux-wayland,wsl2}` | la elección guardada, si no la detectada | Entorno de ejecución |
| `--trigger {keyboard-hooks,pynput,evdev,socket}` | según la plataforma: `windows` usa `keyboard-hooks`, `linux-x11` usa `pynput`, `linux-wayland` usa `evdev`, `wsl2` usa `socket` | Cómo llegan los atajos al motor. `socket` significa que el disparador es la API HTTP |
| `--daemon-port` | la elección guardada, si no `47821` | Puerto de la API HTTP local |
| `--api` | desactivado (siempre activado con `--trigger socket`) | Sirve la API HTTP junto al oyente nativo de atajos |
| `--api-token` | desactivado | Exige `Authorization: Bearer <token>` en todas las rutas salvo `/health` (archivo de token más abajo) |
| `--supervised` | desactivado | Termina limpiamente cuando stdin llega al fin de archivo (el companion mantiene stdin abierto) |
| `--no-watchdog` | watchdog activado | Desactiva el watchdog que reinicia un motor colgado |
| `--watchdog-timeout` | `120` | Tiempo de espera del watchdog en segundos |
| `--no-listener-refresh` | renovación activada | Solo Windows: desactiva la reinstalación periódica del hook de teclado |
| `--listener-refresh-seconds` | `60` | Solo Windows: intervalo de esa reinstalación |

`make run-engine` ejecuta `voice-mate --supervised --api --api-token`; es lo que inicia el companion.

### Flujo de Claude

| Opción | Por defecto | Significado |
|---|---|---|
| `--claude-chat-hotkey` | `ctrl+alt+a` | Atajo del flujo de Claude |
| `--no-claude-chat` | desactivado | Desactiva el flujo de Claude (solo portapapeles) |
| `--claude-system-prompt` | prompt integrado | Sustituye el prompt de sistema |
| `--claude-no-system-prompt` | desactivado | No envía ningún prompt de sistema. Tiene prioridad sobre `--claude-system-prompt` |
| `--claude-max-turns` | `50` | Turnos por conversación |
| `--claude-model` | `claude-haiku-5-5` | Modelo de Claude |
| `--claude-effort {low,medium,high,xhigh,max}` | `low` | Nivel de esfuerzo. Lo ignoran Haiku 4.5 y anteriores |
| `--claude-enable-thinking` | desactivado | Razonamiento extendido. Sonnet 5.5, Opus 5.5 y Fable siempre razonan, y también Haiku 5.5 y Opus 5 con esfuerzo `xhigh` o `max` |
| `--claude-timeout-seconds` | `120.0` | Tiempo de espera de cada turno |

### Texto a voz (flujo de Claude)

| Opción | Por defecto | Significado |
|---|---|---|
| `--no-tts` | desactivado | Sin respuesta hablada; la respuesta sigue yendo al portapapeles |
| `--tts-engine {<default>,kokoro,voxcpm,external,none}` | la elección guardada, si no el motor por defecto | Motor de TTS. `external` es un servicio que ejecutas tú ([protocolo](tts-provider-protocol.md)). `none` desactiva el TTS |
| `--tts-url` | `http://127.0.0.1:8765` | Solo `external`: URL base del proveedor |
| `--tts-external-voice` | la del proveedor por defecto | Solo `external`: id de voz (el selector de voz del companion lo rellena) |
| `--tts-options` | ninguno | Solo `external`: un objeto JSON que se pasa al proveedor como `options` (velocidad, estilo). Cualquier otra cosa se ignora con un aviso |
| `--tts-kokoro-voice` | `pf_dora` | Voz de Kokoro. Voces en portugués: `pf_dora` (femenina), `pm_alex` y `pm_santa` (masculinas) |
| `--tts-voice` | una descripción en portugués de la voz de una anciana brasileña | Solo VoxCPM2: descripción en texto de la voz, que se usa cuando no hay semilla de voz |
| `--tts-cfg-value` | `2.0` | `cfg_value` de VoxCPM2 (de 1.0 a 3.0) |
| `--tts-inference-timesteps` | `10` | Pasos de inferencia de VoxCPM2 (de 4 a 30) |
| `--tts-device {auto,cuda,cpu,mps}` | `auto` | Dispositivo del TTS |
| `--tts-save-dir` | ninguno | Guarda cada archivo de audio generado en esta carpeta |
| `--tts-no-streaming` | desactivado | Genera de una sola vez en lugar de por streaming (depuración) |
| `--tts-voice-seed-mode {auto,fixed,off}` | `off` | Cómo eligen la voz el motor por defecto y VoxCPM2 (mira [usage.es.md](usage.es.md#modos-de-semilla-de-voz)) |
| `--tts-voice-seed-path`, `--tts-voice-seed-text` | ninguno | WAV de referencia y su texto exacto, obligatorios con `fixed` |
| `--tts-voice-seed-cache-dir` | `~/.cache/voicemate` | Dónde guarda su referencia `auto` |
| `--tts-reset-seed` | desactivado | Borra la semilla automática de VoxCPM2 (`voice_seed.wav`) antes de iniciar |
| `--tts-show-progress` | desactivado | Muestra la barra de progreso de VoxCPM2 |
| `--tts-drain-timeout-seconds` | `60.0` | Tiempo de espera del vaciado del reproductor de audio |
| `--tts-debug-vram` | desactivado | Registra el uso de VRAM alrededor de cada frase |

El token del proveedor nunca es una opción: se lee de `VOICEMATE_TTS_TOKEN`, si no de `tts_external_token` en el archivo del motor, y nunca se registra. El companion lo pasa en el entorno, y el motor lo elimina de su entorno una vez leído. Queda en texto sin formato en el `companion.toml` del companion.

El comando de inicio del proveedor tampoco es una opción: el motor lo lee de `VOICEMATE_TTS_START_COMMAND`, lo elimina de su entorno una vez leído y nunca lo registra. Mira [Starting the provider](tts-provider-protocol.md#starting-the-provider-with-voicemate) (en inglés).

El companion pasa la dirección y la voz como `--tts-url=...` y `--tts-external-voice=...`, con un `=`, para que un valor que empieza por `-` no se lea como una opción.

El TTS solo se inicia cuando un flujo de chat está activado, y lo mismo su calentamiento: la voz integrada de Kokoro no se calienta en otro caso. Si faltan los paquetes del motor elegido, o el motor no consigue iniciarse, el motor registra un aviso con la solución y funciona sin TTS.

## Archivo del motor: `~/.config/voicemate/config.toml`

Un archivo TOML plano escrito por `make setup` y `make configure`. Los valores no válidos se ignoran; un archivo ausente o roto significa "nada guardado". Todas las claves son opcionales.

| Clave | Valores | Significado |
|---|---|---|
| `gpu_vendor` | `nvidia`, `amd`, `cpu` | Fabricante de la GPU |
| `whisper_backend` | `faster-whisper`, `whispercpp`, `openai-whisper` | Motor de reconocimiento de voz |
| `tts_enabled` | `true`, `false` | TTS activado o desactivado |
| `tts_engine` | el motor por defecto, `kokoro`, `voxcpm`, `external`, `none` | Motor de TTS |
| `tts_external_url`, `tts_external_voice`, `tts_external_options` | texto | Ajustes del motor `external` (dirección, id de voz, opciones JSON) |
| `tts_external_token` | texto | Token bearer del motor `external`. Texto sin formato en este archivo; `VOICEMATE_TTS_TOKEN` tiene prioridad |
| `stt_engine` | `builtin`, `external` | Motor de transcripción |
| `stt_external_url`, `stt_external_options` | texto | Ajustes del motor de transcripción `external` (dirección, opciones JSON) |
| `stt_external_token` | texto | Token bearer del motor de transcripción `external`. Texto sin formato en este archivo; `VOICEMATE_STT_TOKEN` tiene prioridad |
| `stt_fallback` | `true`, `false` | `false` desactiva el respaldo integrado del motor de transcripción `external` |
| `default_flow` | `clipboard`, `claude_chat` | `clipboard` desactiva el flujo de Claude |
| `platform` | `windows`, `linux-x11`, `linux-wayland`, `wsl2` | Entorno de ejecución |
| `trigger` | `keyboard-hooks`, `pynput`, `evdev`, `socket` | Mecanismo de atajos |
| `stt_strategy` | `auto`, `faster-whisper-rocm`, `whispercpp`, `openai-whisper` | Cadena de respaldo de AMD |
| `ct2_rocm_ok` | `true`, `false` | CTranslate2-ROCm validado por la instalación. El motor escribe `false` cuando falla en ejecución; `make configure` lo intenta de nuevo |
| `daemon_port` | entero | Puerto de la API HTTP |
| `stt_mode` | `auto`, `cpu`, `gpu` | Modo de transcripción por defecto (lo pregunta `make setup` y `make configure`) |

## Archivo del companion: `companion.toml`

Ubicación: en Windows `%APPDATA%\VoiceMate\companion.toml`; en Linux `${XDG_CONFIG_HOME:-~/.config}/voicemate/companion.toml`. El desinstalador lo conserva. Un archivo que no se puede leer se aparta como `companion.toml.broken-<timestamp>` y la notificación "Configuración restablecida" lo dice. Un archivo escrito por una versión más nueva se muestra de solo lectura.

| Clave | Por defecto | Valores permitidos | En la interfaz |
|---|---|---|---|
| `version` | `1` | entero | |
| `client_key` | se genera en el primer inicio | cadena | |
| `language` | `auto` | `auto`, `pt-BR`, `en`, `es`, `ru`, `zh-CN` | General > "Idioma:" |
| `dictation_language` | `interface` | `interface` (sigue a `language`), `auto` (Whisper detecta cada grabación), o un idioma: `pt`, `en`, `es`, `ru`, `zh`, `fr`, `de`, `it`, `ja` | General > "Idioma de dictado:" ("Igual que la interfaz", "Detectar automáticamente", luego un idioma) |
| `engine_mode` | `wsl2` en Windows, `local` en Linux | Windows: `wsl2`, `external`; Linux: `local`, `external` | General > "Modo:" ("Iniciar en WSL", "Iniciar en este equipo", "Solo conectar") |
| `wsl_distro` | `""` (la distro de WSL por defecto) | nombre de la distro | General > "Distro de WSL:" |
| `engine_dir` | `""` (detectar) | ruta relativa a `$HOME`, o absoluta; se rechazan `"`, `$`, el acento grave, la barra invertida y el salto de línea | General > "Carpeta del motor:" |
| `daemon_port` | `47821` | de 1024 a 65535 | solo en el archivo |
| `cues_enabled` | `true` | `true`, `false` | Sonidos > "Reproducir sonidos de aviso"; menú de la bandeja > "Silenciar sonidos" |
| `master_volume` | `0.8` | de 0.0 a 1.0 | Sonidos > "Volumen:" |
| `wsl_restart_policy` | `auto` | `auto`, `ask`, `never` | General > "Reiniciar WSL si falla el audio:" ("Automáticamente", "Preguntar antes", "Nunca") |
| `notify_level` | `warnings` | `all`, `warnings`, `errors`, `none` | General > "Notificaciones:" ("Todas", "Advertencias y errores", "Solo errores", "Ninguna") |
| `start_at_login` | `false` | `true`, `false` | General > "Iniciar VoiceMate al iniciar sesión" |
| `tray_icon_visible` | `true` | `true`, `false` (solo Windows 11) | General > "Mostrar siempre el icono de VoiceMate en la barra de tareas" |
| `stt_mode` | `auto` | `auto` (la configuración propia del motor), `cpu`, `gpu` | General > Transcripción > "Ejecutar en:"; menú de la bandeja > "Transcripción" |
| `stt_cpu_model` | `large-v3-turbo` | `large-v3-turbo`, `large-v3`, `medium`, `small` | General > Transcripción > "Modelo de la CPU:" |
| `stt_gpu_model` | `large-v3-turbo` | igual que `stt_cpu_model` | General > Transcripción > "Modelo de la GPU:" |
| `tts_engine` | `default` | `default` (la configuración propia del motor), `external` | Voz > "Motor de voz:" ("Integrado (configuración del motor)", "Proveedor personalizado (HTTP)") |
| `tts_url` | `http://127.0.0.1:8765` | una dirección `http://` o `https://` sin espacios, comillas ni `$` | Voz > "Dirección del proveedor:" |
| `tts_voice` | `""` (la del proveedor por defecto) | un id de voz sin espacios ni caracteres del shell | Voz > "Voz:" |
| `tts_token` | `""` | texto sin saltos de línea. Texto sin formato en este archivo | Voz > "Token:" |
| `tts_start_command` | `""` (VoiceMate no inicia el proveedor) | una sola línea, que se ejecuta a través del shell tal como se escribe. Texto sin formato en este archivo | Voz > "Comando de inicio:" |
| tabla `[assistant]` | mira [Ajustes del asistente](#ajustes-del-asistente) | | Pestaña Asistente |
| `[[hotkeys]]` con `flow` y `chord` | `clipboard` = `ctrl+alt+v`, `claude_chat` = `ctrl+alt+a` | Ctrl, Alt, Mayús o Win con una tecla, o una tecla de función sola; F12 está reservada por Windows | Pestaña Atajos |
| `[cues.<name>]` para `start`, `transcribing`, `ready`, `ai_ready`, `warning`, `error`, `answer` (hay una respuesta de otra conversación esperando) | `enabled = true`, `source = "preset"`, `preset = "classic"`, `file = ""`, `volume = 1.0` | `source`: `preset`, `file`; `preset`: `classic`, `soft`, `click`; `file`: un WAV PCM (de 8 o 16 bits) | Pestaña Sonidos |

El cambio de idioma pregunta "¿Reiniciar VoiceMate?" ("Reiniciar ahora" o "Más tarde"); hasta el reinicio, la pestaña General muestra "Se aplica cuando VoiceMate se reinicie.".

### Idioma de dictado

El companion convierte `dictation_language` en dos opciones para el motor que inicia: `--transcription-language <code>` y `--output-lang <BCP-47>` (`pt` da `pt-BR`, `zh` da `zh-CN`, los demás su propio código). `interface` usa el idioma de la interfaz; `auto` pasa `--transcription-language auto` y mantiene `--output-lang` en el idioma de la interfaz, así que Claude sigue respondiendo en él. Cambiar la clave reinicia el motor. En el modo "Solo conectar" la clave se ignora y el motor conserva sus propias opciones, y lo mismo ocurre con un motor al que el companion se conecta (una unidad de systemd, o uno iniciado a mano). Más en [usage.es.md](usage.es.md#idioma-de-dictado).

## Modos de reconocimiento de voz

El motor transcribe con el procesador o con la tarjeta gráfica. El modo CPU existe
para que VoiceMate siga funcionando mientras la GPU está ocupada con otro trabajo (edición de vídeo, renderizado, juegos):
nunca carga nada en la GPU.

| Modo | Backend | Decodificación | Cuándo |
|---|---|---|---|
| `cpu` | faster-whisper, int8 | beam 1, filtro VAD, `--cpu-threads` | la GPU está ocupada, o no hay ninguna |
| `gpu` | faster-whisper CUDA (NVIDIA), openai-whisper ROCm (AMD en WSL2), whisper.cpp Vulkan (AMD en Windows) | beam 1 | el más rápido |
| `auto` | la elección de la instalación del motor | la del backend por defecto (beam 5) | como antes de que existiera esta opción |

El companion mantiene un modelo por modo (`stt_cpu_model`, `stt_gpu_model`) y le pasa
`--stt-mode` y `--model` al motor que inicia. Cambiar reinicia el motor, que es
lo que libera (o toma) la GPU. Un modo que no está instalado lo instala el companion
después de preguntar, con el tamaño de la descarga; las mismas herramientas, ejecutadas a mano:

```bash
make stt-check
make stt-install MODE=gpu
make stt-install MODE=cpu ARGS="--model medium"
```

`stt-install` solo toca el entorno virtual propio del motor (y `~/.cache` para los
modelos): PyTorch ROCm y openai-whisper para AMD, bibliotecas CUDA para NVIDIA, la
compilación de whisper.cpp en Windows, el modelo de faster-whisper para la CPU.

### Benchmark

Medido el 27/10/2026 (CPU) y el 28/10/2026 (GPU) con dictado en portugués de Common
Voice (grabaciones cortas de unos 6 s, largas de unos 65 s), a través de los backends
propios del motor en una sola máquina (Intel i5-13400F, AMD Radeon RX 9070 XT con ROCm en WSL2). WER es
la tasa de error de palabras frente al texto de referencia; la latencia es el tiempo de transcripción por
grabación.

| Modo | Modelo | Beam | Cortas: latencia, WER | Largas: latencia, WER |
|---|---|---|---|---|
| CPU (8 hilos, int8) | large-v3-turbo | 1 | 4,7 s, 5,0 % | 21 s, 3,3 % |
| CPU (8 hilos, int8) | medium | 1 | 3,2 s, 5,3 % | 15 s, 5,1 % |
| GPU (Radeon, ROCm) | large-v3-turbo | 5 | 0,45 s, 4,8 % | 4,6 s, 2,9 % |
| GPU (Radeon, ROCm) | large-v3-turbo | 1 | 0,18 s, 4,5 % | 1,9 s, 3,6 % |
| GPU (Radeon, ROCm) | medium | 5 | 0,83 s, 6,1 % | 10,1 s, 6,9 % |
| GPU (Radeon, ROCm) | medium | 1 | 0,42 s, 5,8 % | 4,9 s, 7,3 % |

En la CPU, beam 5 no dio un WER más bajo en la misma ejecución y solo fue más lento (no está en la
tabla). En la GPU, la decodificación voraz es unas 2,4 veces más rápida que beam 5 con menos de
un punto de WER, así que el modo `gpu` decodifica de forma voraz. `medium` es menos preciso que
`large-v3-turbo` en ambos, y en la GPU además es más lento, así que el modelo turbo es el
valor por defecto de los dos modos; `medium` y `small` están para procesadores más lentos.

## Ajustes del asistente

El asistente de voz ([usage.es.md](usage.es.md#asistente-de-voz)) guarda sus elecciones en la tabla `[assistant]` de `companion.toml`:

| Clave | Por defecto | Valores permitidos | En la interfaz |
|---|---|---|---|
| `mode` | `off` | `off`, `own_session`, `desktop` | Asistente > "Modo del asistente:" ("Desactivado", "Conversación propia de VoiceMate", "Una conversación de Claude Desktop") |
| `desktop_conversation` | `""` | el título exacto de una conversación de la barra lateral de Claude Desktop | Asistente > "Conversación de Desktop:" (el modo `desktop` no arranca sin él) |
| `model` | `haiku` | `haiku`, `sonnet`, `opus` (los alias de Claude Code) | Asistente > "Modelo:" |
| `effort` | `low` | `low`, `medium`, `high` | Asistente > "Esfuerzo:" |
| `new_conversation_model` | `sonnet` | igual que `model` | Asistente > "Las conversaciones nuevas usan:" |
| `new_conversation_effort` | `medium` | igual que `effort` | Asistente > "Con el esfuerzo:" |
| `permission_mode` | `acceptEdits` | `default`, `acceptEdits`, `bypassPermissions` | Asistente > "Permisos:" ("Preguntar antes de cada cambio", "Aceptar ediciones de archivos, preguntar lo demás", "Acceso total, nunca preguntar (arriesgado)") |
| `resume_permission_mode` | `""` | vacío (no se pasa ningún modo), `default`, `acceptEdits`, `plan`; `bypassPermissions` se rechaza con una línea de registro | ninguna: defínelo en el archivo. El modo de permisos que recibe una sesión de terminal, IDE o Remote Control cerrada cuando el asistente la reanuda para entregar un mensaje. No se puede cambiar por voz |
| `answer_delivery` | `announce` | `speak`, `announce`, `sound`, `silent` | Asistente > "Respuestas de otras conversaciones:" |
| `speak_replies` | `true` | `true`, `false` | Asistente > "Leer las respuestas en voz alta" |
| `voice_source` | `auto` | `auto`, `engine`, `windows` | Asistente > "Origen de la voz:" ("Automática", "La voz del motor", "Las voces de Windows"). Con `engine`, una frase que encuentra al motor todavía cargando su voz lo espera (hasta 90 segundos, una vez por caída), y los avisos cortos se quedan en pantalla en lugar de pasar a la voz de Windows |
| `voice` | `""` (la voz por defecto del origen) | un id de voz del motor de voz del motor, o un id `windows/...` de una voz de Windows | Asistente > "Voz del asistente:" (**Cargar voces**, **Probar**) |
| `voice_commands` | `true` | `true`, `false` | Asistente > "Entender comandos de voz en el texto escrito" |
| `expressions` | `off` | `off`, `on` | Asistente > "Etiquetas de expresión:" |
| `user_name` | `""` | texto | Asistente > "Tu nombre:" |
| `personality_source` | `none` | `none`, `file`, `custom` | Asistente > Personalidad > "Origen:" ("Ninguna", "Un archivo Markdown (por ejemplo, una nota de tu bóveda de Obsidian)", "Escribir la mía") |
| `personality_file` | `""` | vacío, o un archivo `.md` existente (necesario cuando el origen es `file`) | Asistente > "Archivo de personalidad:" |
| `obsidian_vault` | `""` | una carpeta | Asistente > "Carpeta de la bóveda:" |
| `skill_language` | `auto` | `auto` (la skill en portugués cuando la interfaz está en portugués, en inglés en otro caso), `en`, `pt-BR` | Asistente > "Idioma de la skill:" |
| `custom_skill_dir` | `""` | vacío (la skill incluida), o una carpeta que contiene un `SKILL.md` | Asistente > "Skill personalizada:" |
| `desktop_profile_auto_update` | `true` | `true`, `false` | Asistente > "Buscar un perfil más reciente al iniciar el asistente" |
| `desktop_profile_override` | `""` | un archivo de perfil importado con **Importar...** | Asistente > **Importar...**, **Usar el predeterminado** ([desktop-profile.md](desktop-profile.md)) |
| `talk_hotkey` | `ctrl+alt+s` | una combinación, o `""` para ninguna | Atajos > "Hablar con el asistente" |
| `window_hotkey` | `ctrl+alt+w` | igual | Atajos > "Abrir la ventana del asistente" |
| `digest_hotkey` | `ctrl+alt+r` | igual | Atajos > "Escuchar lo que está listo" |
| `mode_hotkey` | `""` | igual | Atajos > "Cambiar el modo del asistente" |
| `back_hotkey` | `""` | igual | Atajos > "Volver al asistente" |

Un valor no válido vuelve a su valor por defecto con una línea de registro. Un archivo antiguo con `enabled = true` (o `mode = "on"`) se lee como `own_session`, y un `personality_file` sin `personality_source` se lee como el origen `file`. Los atajos del asistente se comprueban junto con los demás: una combinación no puede asignarse a dos acciones.

Qué hace un cambio mientras el asistente está en marcha:

| Cambio | Efecto |
|---|---|
| `mode`, `skill_language`, `custom_skill_dir` y, en el modo propio, `effort` y `permission_mode` | Reinicia la conversación central |
| `desktop_conversation` (modo Desktop) | Reinicia la conversación central |
| `model` (modo propio) | Se aplica desde la siguiente respuesta, sin reinicio |
| `model` o `effort` (modo Desktop) | VoiceMate no puede cambiar la conversación de Desktop; le envía a la central una nota pidiéndole que se los aplique a sí misma |
| `permission_mode` | Se aplica a las conversaciones que el asistente inicie desde entonces, y reinicia la conversación central al instante (modo propio), así que nunca conserva los permisos antiguos |
| Nombre, personalidad, bóveda, etiquetas, `answer_delivery` | Se escribe en `assistant.json`, que la skill vuelve a leer |

El texto que escribes en "Escribir la mía" no está en `companion.toml`: VoiceMate lo guarda en `assistant-personality.md` en su carpeta de estado (hasta 20.000 caracteres) y le da ese archivo a la skill como `personality_file`.

> [!WARNING]
> `permission_mode = "bypassPermissions"` deja que cada conversación que inicia el asistente cambie cualquier archivo y ejecute cualquier comando en este equipo sin preguntar. Solo la ventana de Configuración puede elegirlo: por voz el asistente solo puede alternar entre `default` y `acceptEdits`. Nunca se aplica a una sesión cerrada que el asistente reanuda: esa reanudación no pasa ningún modo de permisos salvo que definas `resume_permission_mode`, que rechaza `bypassPermissions`.

Las respuestas del asistente las habla el motor, con la voz configurada en el motor (pestaña Voz, o el `--tts-engine` del motor): el companion envía el bloque `<voice>` de cada respuesta, frase por frase, al endpoint `/tts` del motor ([tts-provider-protocol.md](tts-provider-protocol.md#the-engine-speaks-the-same-protocol)) y reproduce el audio en este equipo.

La voz integrada de Kokoro y un proveedor personalizado pueden hablar por el asistente, y también las voces que Windows ya tiene (cada idioma instalado en Configuración, Hora e idioma, Voz), sin nada que descargar. "Origen de la voz:" lo decide: `auto` usa la voz del motor y las voces de Windows cuando el motor no puede hablar (o no tiene voz), `engine` solo el motor, `windows` solo Windows. **Cargar voces** y **Probar** siguen el origen elegido, antes de guardarlo; un id de voz pertenece a un origen, y cada origen ignora los ids del otro. Cuando nada puede hablar, las respuestas quedan solo en texto y la ventana del asistente muestra un único aviso "No hay voz disponible" con el motivo. "Deja de hablar", un prompt nuevo y un cambio de modo lo silencian al instante. Nunca habla por encima de una grabación: lo que llega mientras el micrófono graba, o mientras se transcribe la grabación, espera; una frase que una grabación cortó se dice otra vez desde su principio, seguida de las frases que ya estaban en cola detrás de ella, en orden.

VoiceMate también escribe `~/.voicemate/assistant.json` en tu carpeta de usuario, que es el archivo que lee la skill `voicemate-assistant` (`version`, `language`, `user_name`, `personality_file`, `notes_vault`, `expressions`, `mode`, `permission_mode`, `resume_permission_mode`, `answer_delivery`, `instructions`, `inbox` y el catálogo `settings`). Con la skill en portugués, `language` es `"pt-BR"` y la voz habla pt-BR, sea cual sea el idioma de la interfaz. Con la skill en inglés (o una personalizada), el idioma sigue a la interfaz.

`pending` lista las peticiones que el asistente espera o vigila (id de sesión, título, motivo, desde cuándo, `watch`, `until`), y `instructions` las instrucciones permanentes que le diste ([assistant.es.md](assistant.es.md#-instrucciones-permanentes)). Su formato está en [usage.es.md](usage.es.md#la-skill). La propia skill se instala en `<Claude home>/skills/voicemate-assistant`, donde el Claude home es `~/.claude` o la carpeta de `CLAUDE_CONFIG_DIR`.

### Ajustes que el asistente puede cambiar por voz

La lista `settings` de `assistant.json` es la única lista que el asistente puede cambiar.

Claves del asistente: `assistant.mode` (`own_session`, `desktop`), `assistant.model`, `assistant.effort`, `assistant.permission_mode`, `assistant.answer_delivery`, `assistant.new_conversation_model`, `assistant.new_conversation_effort`, `assistant.desktop_conversation`.

Claves de voz, texto y sonido: `assistant.speak_replies`, `assistant.voice`, `assistant.voice_commands`, `assistant.expressions`, `assistant.user_name`, `assistant.personality_source`, `assistant.desktop_profile_auto_update`, `assistant.skill_language`, `cues.enabled`, `cues.volume` (de 0 a 100), `cues.answer.enabled`, `cues.answer.volume` (de 0 a 100), `general.dictation_language`, `general.stt_mode` y `general.notify_level`.

Excluido a propósito: los atajos; todo archivo o carpeta (el archivo y el texto de personalidad, la bóveda, la carpeta de la skill personalizada, el perfil de Desktop importado); el motor de voz, la dirección y el token del proveedor; el propio motor (carpeta, distro de WSL, puerto, modo); el modo `off`; y las señales de sonido que no sean la de respuesta. Un valor debe ser uno de los listados (el texto es una sola línea de 200 caracteres como máximo), `desktop` necesita una conversación de Desktop elegida, y `personality_source` `file` necesita un archivo elegido; en otro caso VoiceMate lo rechaza con el motivo y se lo dice al asistente.

## Variables de entorno

| Variable | La lee | Efecto |
|---|---|---|
| `VOICEMATE_LANG` | motor | Idioma solo de los mensajes del motor (`pt-BR`, `en`, `es`, `ru`, `zh-CN`); sin ella los mensajes siguen a `--output-lang`, cuyo valor por defecto es el portugués. Defínela en el shell que ejecuta `make run` (bash dentro de WSL o en Linux, `$env:VOICEMATE_LANG` en PowerShell en Windows). Tiene prioridad sobre `--output-lang`. No cambia lo que oye Whisper ni el idioma de las respuestas de Claude. El companion la ignora |
| `VOICEMATE_TTS_TOKEN` | motor | Token bearer para el proveedor de TTS externo. Tiene prioridad sobre el guardado. El companion lo define cuando inicia el motor |
| `VOICEMATE_TTS_START_COMMAND` | motor | Línea de comandos que inicia el proveedor de TTS externo cuando nada escucha en su dirección. El companion lo define cuando inicia el motor y lo incluye en `WSLENV`, para que llegue al motor dentro de WSL. Nunca se registra |
| `VOICEMATE_STT_TOKEN` | motor | Token bearer para el proveedor de STT externo. Tiene prioridad sobre el guardado |
| `VOICEMATE_STT_START_COMMAND` | motor | Línea de comandos que inicia el proveedor de STT externo cuando nada escucha en su dirección. Nunca se registra |
| `VOICEMATE_API_TOKEN` | motor, companion | Sustituye el archivo de token. Solo para pruebas |
| `VOICEMATE_CLAUDE_CLI` | companion (asistente) | Ruta o nombre de comando de la CLI de Claude Code que inicia el asistente. Sin ella, VoiceMate busca `claude` en el `PATH`, luego en `~/.local/bin` (`claude.exe`, el instalador nativo) y en `%APPDATA%\npm` (`claude.cmd`, npm). Defínela con una ruta completa cuando `claude` esté en otro sitio |
| `CLAUDE_CONFIG_DIR` | companion (asistente) | Claude home, donde se instala la skill y se leen las conversaciones; por defecto `~/.claude` |
| `PULSE_LATENCY_MSEC` | motor (Linux, WSL2) | Búfer de audio de PulseAudio; se define como `200` cuando falta |
| `CT2_CUDA_ALLOCATOR` | motor (CTranslate2-ROCm) | Se define como `cub_caching` cuando falta (solución para un fallo de memoria en gfx1201) |
| `MIOPEN_FIND_MODE`, `MIOPEN_USER_DB_PATH`, `PYTORCH_TUNABLEOP_*` | motor (solo AMD) | Se definen cuando faltan; las cachés van a `~/.cache/voicemate` |
| `PULSE_SERVER` | `make doctor` | Se comprueba en WSL2 (se espera `unix:/mnt/wslg/PulseServer`) |
| `APPDATA`, `LOCALAPPDATA` (Windows); `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` (Linux) | companion | Carpetas base de los archivos del companion |
| `LC_ALL`, `LC_MESSAGES`, `LANG`, `LANGUAGE` | companion en Linux | Idioma de la interfaz cuando "Idioma:" es automático. En Windows lo decide el idioma de visualización de Windows |

## Token y puerto de la API

- La API HTTP escucha solo en `127.0.0.1`, puerto `47821` por defecto. WSL2 la reenvía al loopback de Windows.
- Las peticiones con una cabecera `Origin` (navegadores) o con un `Host` que no es loopback reciben `403`. `/trigger` acepta solo POST.
- Con `--api-token`, el motor crea `~/.config/voicemate/api-token` (modo 0600, 32 bytes aleatorios) cuando falta, y todas las rutas salvo `/health` necesitan `Authorization: Bearer <token>`. En Windows el companion lee el archivo a través de `wsl.exe`, y lo vuelve a leer tras un rechazo, como máximo cada 30 segundos.
- `make run` y el servicio de systemd se ejecutan sin token; `make run-engine` (el companion) se ejecuta con él. Los antiguos scripts de atajos solo funcionan con un motor sin token.

## Archivos y registros

| Archivo | Windows | Linux |
|---|---|---|
| `companion.toml` | `%APPDATA%\VoiceMate\` | `${XDG_CONFIG_HOME:-~/.config}/voicemate/` |
| `companion.log`, `engine.log` (rotados, 5 MB, 3 copias) | `%LOCALAPPDATA%\VoiceMate\logs\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/logs/` |
| `pending.json` (la lista **No copiadas**; contiene texto de transcripciones) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-session.json` y la carpeta `assistant\` (id de sesión, carpeta de trabajo del asistente) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-inbox.json` (respuestas retenidas para "Escuchar lo que está listo"; contiene su texto), `assistant-conversations\` (las conversaciones que inició el asistente), `assistant-personality.md` (la personalidad que escribes) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-waiting.json` (la lista "Esperando", con los plazos y hasta dónde se leyó cada transcripción), `assistant-instructions.json` (tus instrucciones permanentes; un archivo que no se puede leer se conserva como `.bad`) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `desktop-profile\` (los perfiles de Claude Desktop descargados e importados) | `%LOCALAPPDATA%\VoiceMate\` | no se usa |
| `assistant.json` (lo lee la skill), la skill `voicemate-assistant` | `%USERPROFILE%\.voicemate\`, `%USERPROFILE%\.claude\skills\` | `~/.voicemate/`, `~/.claude/skills/` |
| Señales de sonido renderizadas | `%LOCALAPPDATA%\VoiceMate\cues\` | `${XDG_CACHE_HOME:-~/.cache}/voicemate/cues/` |
| Elecciones del motor y token de la API | dentro de WSL: `~/.config/voicemate/config.toml` y `api-token` (motor nativo de Windows: `%USERPROFILE%\.config\voicemate\`) | `~/.config/voicemate/` |
| Cachés del motor (binarios y modelos de whisper.cpp, semillas de voz, cachés de AMD) | dentro de WSL: `~/.cache/voicemate/` (motor nativo de Windows: `%USERPROFILE%\.cache\voicemate\`) | `~/.cache/voicemate/` |
| Registro del servicio de systemd | `journalctl --user -u voicemate -f`, dentro de WSL | el mismo comando |

Menú de la bandeja > **Motor** > **Abrir registros** abre la carpeta de registros.
