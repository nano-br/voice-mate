import os
import sys
import threading

from app.cli.args import parse_args
from app.cli.config_builder import build_config, delete_existing_auto_seed
from app.cli.wiring import build_handlers, build_listener, build_speaker, build_transcriber
from app.core.audio_feedback import AudioFeedback
from app.core.console import force_utf8_stdio
from app.core.listener_keepalive import ListenerKeepalive
from app.core.recorder import Recorder
from app.core.recording_session import RecordingSession
from app.core.rocm_env import configure_rocm_env
from app.core.session_status import SessionStatus
from app.core.watchdog import Watchdog
from app.features.tts.base import NullSpeaker, TextToSpeech
from app.i18n import _, setup_locale
from app.platform.clipboard import create_clipboard_writer
from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import PlatformKind
from app.setup.persisted_config import load_persisted


def _start_warmup_thread(transcriber: object, speaker: TextToSpeech) -> None:
    """Aquece STT e TTS em UMA thread, em sequência (evita disputa de GPU)."""

    def _warmup_all() -> None:
        stt_warmup = getattr(transcriber, "warmup", None)
        if callable(stt_warmup):
            stt_warmup()
        if speaker.is_active():
            speaker.warmup()

    threading.Thread(target=_warmup_all, daemon=True, name="Warmup").start()


def _configure_audio_env(platform: PlatformKind) -> None:
    """No WSLg/Linux, dá ao PulseAudio um buffer maior (evita o chiado de underrun).

    O `latency` do PortAudio é quase ignorado pelo host Pulse; quem controla o
    buffer é a env var PULSE_LATENCY_MSEC. Setada cedo, antes de qualquer uso de
    sounddevice (TTS, beeps, mic). Respeita override do usuário.
    """
    if platform in ("wsl2", "linux-x11", "linux-wayland"):
        os.environ.setdefault("PULSE_LATENCY_MSEC", "200")


def main() -> None:
    force_utf8_stdio()
    args = parse_args()
    config = build_config(args, load_persisted())
    setup_locale(config.output_lang)

    # Resolve plataforma/gatilho uma vez; downstream (wiring, keepalive) usa os valores resolvidos.
    config.platform = config.platform or detect_platform()
    config.trigger = config.trigger or default_trigger(config.platform)

    # Env cedo, ANTES de tocar em torch/sounddevice: MIOpen/TunableOp (STT + TTS
    # herdam o FAST/cache) e o buffer maior do PulseAudio no WSLg.
    configure_rocm_env(config.gpu_vendor)
    _configure_audio_env(config.platform)

    recorder = Recorder(sample_rate=config.sample_rate)
    transcriber = build_transcriber(config)
    audio_feedback = AudioFeedback()

    flows = config.flows or config.build_default_flows()
    has_chat_flow = any(flow.kind == "claude_chat" for flow in flows)
    if args.tts_reset_seed:
        delete_existing_auto_seed(config.tts)
    speaker: TextToSpeech = build_speaker(config.tts) if has_chat_flow else NullSpeaker()
    # Warmup SERIALIZADO em background (STT depois TTS, nunca concorrente): ambos
    # pagam o tuning de kernels ROCm no startup. Rodar os dois ao mesmo tempo
    # trava a GPU — a 1ª transcrição chegou a levar 45s (vs ~3s isolada) e o
    # thrash glitcha o áudio do WSLg. Em sequência cada um roda rápido.
    _start_warmup_thread(transcriber, speaker)

    # Hub de estado da sessão: estado vivo + último resultado, consultável pelos
    # consumidores (o script de hotkeys do Windows). No WSL2 o clipboard nativo é
    # setado pelo lado Windows (via /result) — o hub guarda a última transcrição.
    status = SessionStatus()
    clipboard = create_clipboard_writer(config.platform, status)
    handlers, owned_handlers = build_handlers(flows, audio_feedback, speaker, config.output_lang, clipboard=clipboard)
    if not handlers:
        speaker.close()
        print(_("[VoiceMate] No handler available; exiting."), file=sys.stderr)
        sys.exit(1)

    flows = [f for f in flows if f.name in handlers]
    default_handler_id = "clipboard" if "clipboard" in handlers else flows[0].name

    session = RecordingSession(
        recorder=recorder,
        transcriber=transcriber,
        audio=audio_feedback,
        config=config,
        handlers=handlers,
        default_handler_id=default_handler_id,
        status=status,
    )
    listener = build_listener(config, flows, session, status)

    print(_("\n[VoiceMate] Ready. Input: {input_method}").format(input_method=config.input_method))
    print(
        _("[VoiceMate] Platform: {platform} (trigger: {trigger})").format(
            platform=config.platform, trigger=config.trigger
        )
    )
    if config.trigger == "socket":
        print(
            _("[VoiceMate] Daemon listening on http://127.0.0.1:{port} (POST /trigger).").format(
                port=config.daemon_port
            )
        )
        print(_("[VoiceMate] Register the Windows-side hotkeys with scripts/windows/voicemate-hotkeys.ahk (or .ps1)."))
    else:
        for flow in flows:
            label = _("clipboard") if flow.kind == "clipboard" else _("Claude (multi-turn)")
            print(_("[VoiceMate] Hotkey {hotkey}: → {label}").format(hotkey=flow.hotkey, label=label))
    print(_("[VoiceMate] Max recording: {seconds}s").format(seconds=config.max_recording_seconds))
    print(_("[VoiceMate] Ctrl+C to exit.\n"))

    watchdog: Watchdog | None = None
    if config.watchdog_enabled:
        watchdog = Watchdog(timeout_seconds=config.watchdog_timeout_seconds)
        watchdog.start()

    # O keepalive contorna a remoção silenciosa de hooks WH_KEYBOARD_LL pelo
    # Windows sob carga — não se aplica aos listeners de Linux/WSL2.
    keepalive: ListenerKeepalive | None = None
    if config.listener_refresh_enabled and config.platform == "windows":
        keepalive = ListenerKeepalive(
            listener,
            interval_seconds=float(config.listener_refresh_seconds),
        )
        keepalive.start()

    try:
        listener.listen()
    except KeyboardInterrupt:
        pass
    finally:
        if keepalive is not None:
            keepalive.stop()
        if watchdog is not None:
            watchdog.stop()
        for handler in owned_handlers:
            try:
                handler.close()
            except Exception as exc:  # noqa: BLE001
                print(f"[VoiceMate] Falha ao fechar handler: {exc}", file=sys.stderr)
        # Encerra recursos do transcriber (ex.: subprocess do whisper-server). close()
        # é opcional no Protocol — só alguns backends (server) o implementam.
        transcriber_close = getattr(transcriber, "close", None)
        if callable(transcriber_close):
            try:
                transcriber_close()
            except Exception as exc:  # noqa: BLE001
                print(f"[VoiceMate] Falha ao fechar transcriber: {exc}", file=sys.stderr)
        print("\n[VoiceMate] Encerrando.")


if __name__ == "__main__":
    main()
