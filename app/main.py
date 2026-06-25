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
    """Warm up STT and TTS in ONE thread, sequentially (avoids GPU contention)."""

    def _warmup_all() -> None:
        stt_warmup = getattr(transcriber, "warmup", None)
        if callable(stt_warmup):
            stt_warmup()
        if speaker.is_active():
            speaker.warmup()

    threading.Thread(target=_warmup_all, daemon=True, name="Warmup").start()


def _configure_audio_env(platform: PlatformKind) -> None:
    """On WSLg/Linux, give PulseAudio a larger buffer (avoids underrun crackle).

    PortAudio's `latency` is mostly ignored by the Pulse host; what controls the
    buffer is the PULSE_LATENCY_MSEC env var. Set early, before any use of
    sounddevice (TTS, beeps, mic). Respects a user override.
    """
    if platform in ("wsl2", "linux-x11", "linux-wayland"):
        os.environ.setdefault("PULSE_LATENCY_MSEC", "200")


def main() -> None:
    force_utf8_stdio()
    args = parse_args()
    config = build_config(args, load_persisted())
    setup_locale(config.output_lang)

    # Resolve platform/trigger once; downstream (wiring, keepalive) uses the resolved values.
    config.platform = config.platform or detect_platform()
    config.trigger = config.trigger or default_trigger(config.platform)

    # Env early, BEFORE touching torch/sounddevice: MIOpen/TunableOp (STT + TTS
    # inherit the FAST/cache) and the larger PulseAudio buffer on WSLg.
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
    # SERIALIZED warmup in the background (STT then TTS, never concurrent): both
    # pay for ROCm kernel tuning at startup. Running them at the same time stalls
    # the GPU — the 1st transcription once took 45s (vs ~3s in isolation) and the
    # thrash glitches WSLg audio. In sequence each one runs fast.
    _start_warmup_thread(transcriber, speaker)

    # Session state hub: live state + last result, pollable by consumers (the
    # Windows hotkeys script). On WSL2 the native clipboard is set by the Windows
    # side (via /result) — the hub keeps the last transcription.
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

    # The keepalive works around Windows silently dropping WH_KEYBOARD_LL hooks
    # under load — it doesn't apply to the Linux/WSL2 listeners.
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
                print(_("[VoiceMate] Failed to close handler: {exc}").format(exc=exc), file=sys.stderr)
        # Shut down transcriber resources (e.g. the whisper-server subprocess). close()
        # is optional in the Protocol — only some backends (server) implement it.
        transcriber_close = getattr(transcriber, "close", None)
        if callable(transcriber_close):
            try:
                transcriber_close()
            except Exception as exc:  # noqa: BLE001
                print(_("[VoiceMate] Failed to close transcriber: {exc}").format(exc=exc), file=sys.stderr)
        print(_("\n[VoiceMate] Shutting down."))


if __name__ == "__main__":
    main()
