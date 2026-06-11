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
from app.core.watchdog import Watchdog
from app.features.tts.base import NullSpeaker, TextToSpeech
from app.i18n import _, setup_locale
from app.platform.clipboard import create_clipboard_writer
from app.platform.detect import default_trigger, detect_platform
from app.setup.persisted_config import load_persisted


def main() -> None:
    force_utf8_stdio()
    args = parse_args()
    config = build_config(args, load_persisted())
    setup_locale(config.output_lang)

    # Resolve plataforma/gatilho uma vez; downstream (wiring, keepalive) usa os valores resolvidos.
    config.platform = config.platform or detect_platform()
    config.trigger = config.trigger or default_trigger(config.platform)

    recorder = Recorder(sample_rate=config.sample_rate)
    transcriber = build_transcriber(config)
    audio_feedback = AudioFeedback()

    flows = config.flows or config.build_default_flows()
    has_chat_flow = any(flow.kind == "claude_chat" for flow in flows)
    if args.tts_reset_seed:
        delete_existing_auto_seed(config.tts)
    speaker: TextToSpeech = build_speaker(config.tts) if has_chat_flow else NullSpeaker()
    # Pré-aquece o TTS em background (carrega modelo + tuna kernels ROCm) para a
    # 1ª frase falada já sair realtime, em paralelo com o resto do startup.
    if speaker.is_active():
        threading.Thread(target=speaker.warmup, daemon=True, name="TTSWarmup").start()
    clipboard = create_clipboard_writer(config.platform)
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
    )
    listener = build_listener(config, flows, session)

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
