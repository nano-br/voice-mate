import sys

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
from app.setup.persisted_config import load_persisted


def main() -> None:
    force_utf8_stdio()
    args = parse_args()
    config = build_config(args, load_persisted())
    setup_locale(config.output_lang)

    recorder = Recorder(sample_rate=config.sample_rate)
    transcriber = build_transcriber(config)
    audio_feedback = AudioFeedback()

    flows = config.flows or config.build_default_flows()
    has_chat_flow = any(flow.kind == "claude_chat" for flow in flows)
    if args.tts_reset_seed:
        delete_existing_auto_seed(config.tts)
    speaker: TextToSpeech = build_speaker(config.tts) if has_chat_flow else NullSpeaker()
    handlers, owned_handlers = build_handlers(flows, audio_feedback, speaker, config.output_lang)
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
    for flow in flows:
        label = _("clipboard") if flow.kind == "clipboard" else _("Claude (multi-turn)")
        print(_("[VoiceMate] Hotkey {hotkey}: → {label}").format(hotkey=flow.hotkey, label=label))
    print(_("[VoiceMate] Max recording: {seconds}s").format(seconds=config.max_recording_seconds))
    print(_("[VoiceMate] Ctrl+C to exit.\n"))

    watchdog: Watchdog | None = None
    if config.watchdog_enabled:
        watchdog = Watchdog(timeout_seconds=config.watchdog_timeout_seconds)
        watchdog.start()

    keepalive: ListenerKeepalive | None = None
    if config.listener_refresh_enabled:
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
        print("\n[VoiceMate] Encerrando.")


if __name__ == "__main__":
    main()
