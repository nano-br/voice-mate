import os
import signal
import sys
import threading
import traceback
from argparse import Namespace
from dataclasses import dataclass
from types import FrameType

from app.cli.args import parse_args
from app.cli.config_builder import build_config, delete_existing_auto_seed
from app.cli.wiring import build_handlers, build_listener, build_speaker, build_transcriber
from app.core.audio_feedback import AudioFeedback
from app.core.config import Config, FlowConfig
from app.core.console import force_utf8_stdio
from app.core.input_listener import InputListener
from app.core.listener_keepalive import ListenerKeepalive
from app.core.recorder import Recorder
from app.core.recording_session import RecordingSession
from app.core.rocm_env import configure_rocm_env
from app.core.session_status import SessionStatus
from app.core.transcription_backend import TranscriptionBackend
from app.core.transcription_handler import TranscriptionHandler
from app.core.watchdog import Watchdog
from app.daemon.auth import TOKEN_ENV, default_token_path, load_or_create_token
from app.daemon.lifecycle import Lifecycle, hard_exit, watch_stdin_eof
from app.daemon.server import ApiServer, EngineInfo
from app.features.tts.base import NullSpeaker, TextToSpeech
from app.features.whispercpp.server_backend import kill_live_servers
from app.i18n import _, setup_locale
from app.platform.audio_probe import AudioServerProbe
from app.platform.clipboard import create_clipboard_writer
from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import PlatformKind, TriggerKind
from app.protocol.models import FlowInfo, ShutdownReason
from app.setup.persisted_config import load_persisted


def _start_warmup_thread(transcriber: object, speaker: TextToSpeech) -> threading.Event:
    """Warm up STT and TTS in ONE thread, sequentially (avoids GPU contention).

    Returns an event set once the STT warmup is over (successful or not).
    """
    stt_warm = threading.Event()

    def _warmup_all() -> None:
        try:
            stt_warmup = getattr(transcriber, "warmup", None)
            if callable(stt_warmup):
                stt_warmup()
        finally:
            stt_warm.set()
        if speaker.is_active():
            speaker.warmup()

    threading.Thread(target=_warmup_all, daemon=True, name="Warmup").start()
    return stt_warm


def _exit_now(code: int) -> None:
    """Hard exit for a shutdown requested while the engine loads: no cleanup runs, so
    first kill the whisper-server the build may already have started (it holds VRAM)."""
    kill_live_servers()
    hard_exit(code)


def _install_sigterm(lifecycle: Lifecycle) -> None:
    """SIGTERM (`systemctl stop`, `kill`) shuts the engine down like stdin EOF.

    The handler only starts a thread: it runs between two bytecodes of whatever the
    main thread was doing, which may hold the lifecycle's (non-reentrant) lock.
    """

    def _on_sigterm(_signum: int, _frame: FrameType | None) -> None:
        threading.Thread(target=lifecycle.request_shutdown, args=("supervisor",), daemon=True, name="Sigterm").start()

    signal.signal(signal.SIGTERM, _on_sigterm)


class _ListenerThread(threading.Thread):
    """Runs the trigger listener off the main thread, which follows the lifecycle instead:
    the keyboard/mouse listeners never return from listen(), so a /shutdown, stdin EOF or
    SIGTERM could not end the process while the main thread sat inside it."""

    def __init__(self, listener: InputListener) -> None:
        super().__init__(daemon=True, name="Listener")
        self._listener = listener
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self._listener.listen()
        except BaseException as exc:  # noqa: BLE001 (re-raised on the main thread)
            self.error = exc


def _configure_audio_env(platform: PlatformKind) -> None:
    """On WSLg/Linux, give PulseAudio a larger buffer (avoids underrun crackle).

    PortAudio's `latency` is mostly ignored by the Pulse host; what controls the
    buffer is the PULSE_LATENCY_MSEC env var. Set early, before any use of
    sounddevice (TTS, beeps, mic). Respects a user override.
    """
    if platform in ("wsl2", "linux-x11", "linux-wayland"):
        os.environ.setdefault("PULSE_LATENCY_MSEC", "200")


@dataclass
class _Engine:
    """What the (slow) build produces: everything that needs the model."""

    transcriber: TranscriptionBackend
    speaker: TextToSpeech
    owned_handlers: list[TranscriptionHandler]
    flows: list[FlowConfig]
    session: RecordingSession


def _has_chat_flow(flows: list[FlowConfig]) -> bool:
    return any(flow.kind == "claude_chat" for flow in flows)


def _engine_info(platform: PlatformKind, trigger: TriggerKind, flows: list[FlowConfig], tts: bool) -> EngineInfo:
    return EngineInfo(
        platform=platform,
        trigger=trigger,
        flows=tuple(FlowInfo(name=flow.name, kind=flow.kind, hotkey=flow.hotkey) for flow in flows),
        tts=tts,
    )


def _load_token() -> str:
    try:
        token = load_or_create_token()
    except OSError as exc:
        print(
            _("[VoiceMate] ❌ Could not read or create the API token ({path}): {exc}").format(
                path=default_token_path(), exc=exc
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    if os.environ.get(TOKEN_ENV, "").strip():
        print(_("[VoiceMate] API token required (taken from {env}).").format(env=TOKEN_ENV))
    else:
        print(_("[VoiceMate] API token required (Bearer), stored in {path}.").format(path=default_token_path()))
    return token


def _start_api(
    config: Config, status: SessionStatus, lifecycle: Lifecycle, token: str | None, info: EngineInfo
) -> ApiServer:
    api = ApiServer(
        status,
        info=info,
        port=config.daemon_port,
        token=token,
        lifecycle=lifecycle,
        on_shutdown=lifecycle.request_shutdown,
    )
    try:
        api.start()
    except OSError as exc:
        print(
            _("[VoiceMate] ❌ Could not open the HTTP API on 127.0.0.1:{port}: {exc}").format(
                port=config.daemon_port, exc=exc
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    print(_("[VoiceMate] HTTP API on http://127.0.0.1:{port}; loading the engine...").format(port=api.port))
    return api


def _build_engine(args: Namespace, config: Config, flows: list[FlowConfig], status: SessionStatus) -> _Engine:
    recorder = Recorder(sample_rate=config.sample_rate)
    transcriber = build_transcriber(config)
    audio_feedback = AudioFeedback()

    if args.tts_reset_seed:
        delete_existing_auto_seed(config.tts)
    speaker: TextToSpeech = build_speaker(config.tts) if _has_chat_flow(flows) else NullSpeaker()
    # SERIALIZED warmup in the background (STT then TTS, never concurrent): both
    # pay for ROCm kernel tuning at startup. Running them at the same time stalls
    # the GPU (the 1st transcription once took 45s vs ~3s in isolation) and the
    # thrash glitches WSLg audio. In sequence each one runs fast.
    stt_warm = _start_warmup_thread(transcriber, speaker)

    # The handlers publish every result to the session hub (`status`): on WSL2 the
    # Windows side reads it there and sets the native clipboard itself.
    clipboard = create_clipboard_writer(config.platform or detect_platform())
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
        flow_kinds={flow.name: flow.kind for flow in flows},
        # The backends are not thread-safe: the first transcription waits for the warmup.
        stt_ready=stt_warm,
    )
    return _Engine(transcriber, speaker, owned_handlers, flows, session)


def _print_ready(config: Config, flows: list[FlowConfig], api: ApiServer | None) -> None:
    print(_("\n[VoiceMate] Ready. Input: {input_method}").format(input_method=config.input_method))
    print(
        _("[VoiceMate] Platform: {platform} (trigger: {trigger})").format(
            platform=config.platform, trigger=config.trigger
        )
    )
    if config.trigger == "socket" and api is not None:
        print(_("[VoiceMate] Daemon listening on http://127.0.0.1:{port} (POST /trigger).").format(port=api.port))
        print(_("[VoiceMate] Register the Windows-side hotkeys with scripts/windows/voicemate-hotkeys.ahk (or .ps1)."))
    else:
        for flow in flows:
            label = _("clipboard") if flow.kind == "clipboard" else _("Claude (multi-turn)")
            print(_("[VoiceMate] Hotkey {hotkey}: → {label}").format(hotkey=flow.hotkey, label=label))
        if api is not None:
            print(_("[VoiceMate] HTTP API on http://127.0.0.1:{port}.").format(port=api.port))
    print(_("[VoiceMate] Max recording: {seconds}s").format(seconds=config.max_recording_seconds))
    print(_("[VoiceMate] Ctrl+C to exit.\n"))


def _announced_reason(lifecycle: Lifecycle, quit_reason: ShutdownReason | None) -> ShutdownReason | None:
    """The shutdown reason to publish, or None when the engine stopped on its own (a crash)."""
    return lifecycle.shutdown_reason or quit_reason


def _close_engine(engine: _Engine) -> None:
    for handler in engine.owned_handlers:
        try:
            handler.close()
        except Exception as exc:  # noqa: BLE001
            print(_("[VoiceMate] Failed to close handler: {exc}").format(exc=exc), file=sys.stderr)
    # Shut down transcriber resources (e.g. the whisper-server subprocess). close()
    # is optional in the Protocol: only some backends (server) implement it.
    transcriber_close = getattr(engine.transcriber, "close", None)
    if callable(transcriber_close):
        try:
            transcriber_close()
        except Exception as exc:  # noqa: BLE001
            print(_("[VoiceMate] Failed to close transcriber: {exc}").format(exc=exc), file=sys.stderr)


def main() -> None:
    force_utf8_stdio()
    args = parse_args()
    config = build_config(args, load_persisted())
    setup_locale(config.output_lang)

    # Resolve platform/trigger once; downstream (wiring, keepalive) uses the resolved values.
    platform: PlatformKind = config.platform or detect_platform()
    trigger: TriggerKind = config.trigger or default_trigger(platform)
    config.platform = platform
    config.trigger = trigger

    # Env early, BEFORE touching torch/sounddevice: MIOpen/TunableOp (STT + TTS
    # inherit the FAST/cache) and the larger PulseAudio buffer on WSLg.
    configure_rocm_env(config.gpu_vendor)
    _configure_audio_env(platform)

    configured_flows = config.flows or config.build_default_flows()
    # Session state hub: live state, results and API v2 events, served to the
    # consumers (the Windows hotkeys script, the companion app).
    status = SessionStatus()
    lifecycle = Lifecycle(exit_now=_exit_now)
    _install_sigterm(lifecycle)

    # Order matters (docs/companion-app.md, "Startup and readiness"): the token file,
    # the stdin watcher, then the HTTP API, all BEFORE the model loads (10 to 60 s), so
    # /health answers `ready: false` and Quit works during the build.
    api_enabled = trigger == "socket" or bool(args.api)
    token = _load_token() if api_enabled and args.api_token else None
    if args.supervised:
        watch_stdin_eof(lambda: lifecycle.request_shutdown("supervisor"))
        print(_("[VoiceMate] Supervised: closing stdin shuts the engine down."))
    api: ApiServer | None = None
    if api_enabled:
        planned_tts = config.tts.enabled and config.tts.engine != "none" and _has_chat_flow(configured_flows)
        api = _start_api(
            config, status, lifecycle, token, _engine_info(platform, trigger, configured_flows, planned_tts)
        )
    # WSL2: report WSLg PulseAudio health in /health (and as `health` events), so the
    # Windows side can restart WSL when the audio bridge dies (nothing inside the
    # distro revives it).
    audio_probe: AudioServerProbe | None = None
    if platform == "wsl2" and api_enabled:
        audio_probe = AudioServerProbe(on_change=status.hub.publish_health)
        audio_probe.start()

    try:
        engine = _build_engine(args, config, configured_flows, status)
    except KeyboardInterrupt:  # Ctrl+C while the model loads: just leave
        kill_live_servers()
        print(_("\n[VoiceMate] Shutting down."))
        sys.exit(0)
    except SystemExit:
        kill_live_servers()
        raise
    except Exception as exc:  # noqa: BLE001 (a daemon that fails to build exits non-zero)
        print(_("[VoiceMate] ❌ The engine failed to start: {exc}").format(exc=exc), file=sys.stderr)
        traceback.print_exc()
        kill_live_servers()
        sys.exit(1)

    tts_active = engine.speaker.is_active() and _has_chat_flow(engine.flows)
    if api is not None:
        api.attach(engine.session, _engine_info(platform, trigger, engine.flows, tts_active))
    listener: InputListener = build_listener(config, engine.flows, engine.session, api)
    _print_ready(config, engine.flows, api)

    watchdog: Watchdog | None = None
    if config.watchdog_enabled:
        watchdog = Watchdog(timeout_seconds=config.watchdog_timeout_seconds)
        watchdog.start()

    # The keepalive works around Windows silently dropping WH_KEYBOARD_LL hooks
    # under load; it doesn't apply to the Linux/WSL2 listeners.
    keepalive: ListenerKeepalive | None = None
    if config.listener_refresh_enabled and platform == "windows":
        keepalive = ListenerKeepalive(
            listener,
            interval_seconds=float(config.listener_refresh_seconds),
        )
        keepalive.start()

    def _stop(reason: ShutdownReason) -> None:
        # The shutdown event first: it wakes every pending long poll before the API stops.
        status.hub.publish_shutdown(reason)
        listener.stop()

    quit_reason: ShutdownReason | None = None
    try:
        if lifecycle.mark_ready(_stop):
            runner = _ListenerThread(listener)
            runner.start()
            while runner.is_alive() and not lifecycle.wait_shutdown(0.5):
                pass
            if runner.error is not None:
                raise runner.error
    except KeyboardInterrupt:
        quit_reason = "user_quit"
    finally:
        # Only a requested stop (or Ctrl+C) is announced. A listener that crashed or
        # ended on its own publishes nothing: attached clients see a disconnect (a
        # crash to recover from), never a deliberate user_quit.
        reason = _announced_reason(lifecycle, quit_reason)
        if reason is not None:
            status.hub.publish_shutdown(reason)
        try:
            listener.stop()
        except Exception as exc:  # noqa: BLE001 (shutting down anyway)
            print(_("[VoiceMate] Failed to stop the listener: {exc}").format(exc=exc), file=sys.stderr)
        if api is not None:
            api.stop()
        if audio_probe is not None:
            audio_probe.stop()
        if keepalive is not None:
            keepalive.stop()
        if watchdog is not None:
            watchdog.stop()
        _close_engine(engine)
        # A whisper-server the build abandoned (it fell back to whisper-cli after the
        # server never became ready) is owned by nobody: never let it outlive us.
        kill_live_servers()
        print(_("\n[VoiceMate] Shutting down."))


if __name__ == "__main__":
    main()
