"""Wire CLI args + Config into the runtime objects (handlers, listeners)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.core.audio_feedback import AudioFeedback
from app.core.config import ClaudeChatConfig, Config, FlowConfig, TTSConfig
from app.core.input_listener import (
    InputListener,
    KeyboardHotkeyListener,
    MouseButtonListener,
)
from app.core.multi_hotkey_listener import MultiHotkeyListener
from app.core.prompts import claude_cli_system_prompt
from app.core.recording_session import RecordingSession
from app.core.transcriber import FasterWhisperBackend
from app.core.transcription_backend import TranscriptionBackend
from app.core.transcription_handler import ClipboardHandler, TranscriptionHandler
from app.features import claude as claude_feature
from app.features import openai_whisper as openai_whisper_feature
from app.features import tts as tts_feature
from app.features import whispercpp as whispercpp_feature
from app.features.tts.base import NullSpeaker, TextToSpeech
from app.i18n import _
from app.platform.clipboard import ClipboardWriter, create_clipboard_writer

if TYPE_CHECKING:
    from app.core.session_status import SessionStatus, ToggleOutcome
from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import TriggerKind
from app.platform.listeners import (
    EvdevHotkeyListener,
    PynputHotkeyListener,
    SocketTriggerListener,
)


def _whisper_language(config: Config) -> str | None:
    """Pinned language for faster-whisper ("auto" → None = detect)."""
    return None if config.transcription_language == "auto" else config.transcription_language


def _faster_whisper(config: Config, use_cpu: bool) -> TranscriptionBackend:
    return FasterWhisperBackend(
        model_size=config.model_size,
        use_cpu=use_cpu,
        beam_size=config.beam_size,
        language=_whisper_language(config),
    )


def _try_openai_whisper(config: Config) -> TranscriptionBackend | None:
    if not openai_whisper_feature.is_available():
        return None
    try:
        return openai_whisper_feature.build_backend(config)
    except Exception as exc:  # noqa: BLE001
        print(_("[VoiceMate] ⚠ openai-whisper failed: {exc}").format(exc=exc), file=sys.stderr)
        return None


def _try_whispercpp(config: Config) -> TranscriptionBackend | None:
    if not whispercpp_feature.is_available(config):
        print(
            _("[VoiceMate] ⚠ whisper.cpp not found (run `make configure`); trying fallback."),
            file=sys.stderr,
        )
        return None
    try:
        return whispercpp_feature.build_backend(config)
    except Exception as exc:  # noqa: BLE001
        print(_("[VoiceMate] ⚠ whisper.cpp failed; falling back: {exc}").format(exc=exc), file=sys.stderr)
        return None


def _try_faster_whisper_rocm(config: Config) -> TranscriptionBackend | None:
    """faster-whisper on the AMD GPU via the ROCm fork of CTranslate2 (HIP reports "cuda").

    A failure here is persisted (`ct2_rocm_ok = false`) so the chain doesn't pay
    the cost of retrying on every boot — `make configure` re-validates and re-arms.
    """
    import os

    # Validated workaround for gfx1201 (RX 9070 XT): CT2's default allocator
    # causes a "Memory access fault" — cub_caching avoids it. setdefault: respects
    # anyone who already exported a different value in the environment.
    os.environ.setdefault("CT2_CUDA_ALLOCATOR", "cub_caching")
    try:
        return _faster_whisper(config, use_cpu=False)
    except Exception as exc:  # noqa: BLE001
        print(
            _("[VoiceMate] ⚠ faster-whisper (CT2-ROCm) failed on the AMD GPU: {exc}").format(exc=exc),
            file=sys.stderr,
        )
        print(_("[VoiceMate]   Disabling until the next `make configure`."), file=sys.stderr)
        try:
            from app.setup.persisted_config import update_persisted

            update_persisted(ct2_rocm_ok=False)
        except Exception as persist_exc:  # noqa: BLE001
            print(
                _("[VoiceMate] ⚠ Failed to persist ct2_rocm_ok: {persist_exc}").format(persist_exc=persist_exc),
                file=sys.stderr,
            )
        return None


def _build_amd_transcriber(config: Config) -> TranscriptionBackend:
    """AMD chain: faster-whisper-rocm → (per-platform intermediate GPU backends) → CPU.

    The explicit strategy (--stt-strategy/persisted) goes at the top of the chain;
    with "auto", CT2-ROCm is only tried if setup validated it (ct2_rocm_ok).

    The order of the intermediates depends on the platform: on WSL2 Vulkan only
    sees llvmpipe (a software, CPU implementation) — the real GPU is only
    reachable via ROCm — so openai-whisper (torch ROCm) comes BEFORE whisper.cpp.
    On native Linux (RADV) and Windows, whisper.cpp+Vulkan genuinely accelerates
    and stays ahead.
    """
    strategy = config.stt_strategy
    if strategy == "auto" and config.whisper_backend == "openai-whisper":
        strategy = "openai-whisper"  # an explicit --whisper-backend is still respected
    platform = config.platform or detect_platform()

    try_rocm = strategy == "faster-whisper-rocm" or (strategy == "auto" and config.ct2_rocm_ok is True)
    if try_rocm:
        backend = _try_faster_whisper_rocm(config)
        if backend is not None:
            return backend

    if strategy == "whispercpp":
        order = [_try_whispercpp, _try_openai_whisper]
    elif strategy == "openai-whisper":
        order = [_try_openai_whisper]
    elif platform == "wsl2":
        order = [_try_openai_whisper, _try_whispercpp]
    else:
        order = [_try_whispercpp, _try_openai_whisper]
    for factory in order:
        backend = factory(config)
        if backend is not None:
            return backend
    print(_("[VoiceMate] ⚠ No GPU backend available on AMD; using faster-whisper on CPU."), file=sys.stderr)
    return _faster_whisper(config, use_cpu=True)


def build_transcriber(config: Config) -> TranscriptionBackend:
    """Pick the transcription engine based on vendor/strategy.

    `--cpu` → faster-whisper CPU. AMD → the `_build_amd_transcriber` chain (goal:
    quality ≥ main's faster-whisper CUDA). NVIDIA → faster-whisper CUDA (main's
    behavior), with whispercpp/openai-whisper opt-in via --whisper-backend.
    No GPU → faster-whisper CPU.
    """
    if config.use_cpu:
        return _faster_whisper(config, use_cpu=True)

    if config.gpu_vendor == "amd":
        return _build_amd_transcriber(config)

    if config.whisper_backend == "whispercpp":
        backend = _try_whispercpp(config)
        if backend is not None:
            return backend
    elif config.whisper_backend == "openai-whisper":
        backend = _try_openai_whisper(config)
        if backend is not None:
            return backend

    return _faster_whisper(config, use_cpu=config.gpu_vendor == "cpu")


def build_speaker(config: TTSConfig) -> TextToSpeech:
    if not config.enabled or config.engine == "none":
        return NullSpeaker()
    # Poetry extra name per engine (for the install hint in the warning).
    extra = {"voxcpm": "voxcpm", "kokoro": "kokoro"}.get(config.engine, "tts")
    if not tts_feature.is_available(config.engine):
        print(
            _("[VoiceMate] ⚠ TTS disabled: engine '{engine}' packages not installed.").format(engine=config.engine),
            file=sys.stderr,
        )
        print(
            _("[VoiceMate]   Install with: poetry install --extras {extra} (or run with --no-tts).").format(
                extra=extra
            ),
            file=sys.stderr,
        )
        return NullSpeaker()
    try:
        return tts_feature.build_default_speaker(config)
    except Exception as exc:  # noqa: BLE001
        print(
            _("[VoiceMate] ⚠ TTS disabled (failed to start engine '{engine}'): {exc}").format(
                engine=config.engine, exc=exc
            ),
            file=sys.stderr,
        )
        print(
            _(
                "[VoiceMate]   Check `poetry install --extras {extra}`, GPU availability, or run with `--no-tts`."
            ).format(extra=extra),
            file=sys.stderr,
        )
        return NullSpeaker()


def _resolve_chat_system_prompt(chat_cfg: ClaudeChatConfig, output_lang: str) -> str | None:
    """Resolve the system prompt actually sent to the Claude runtime.

    Convention coming from `cli.config_builder.resolve_system_prompt`:
      - "" (sentinel) → user explicitly disabled → return None.
      - non-empty str → user override → return as-is.
      - None → use canonical prompt with the requested output language.
    """
    if chat_cfg.system_prompt == "":
        return None
    if chat_cfg.system_prompt is not None:
        return chat_cfg.system_prompt
    return claude_cli_system_prompt(output_lang)


def build_handlers(
    flows: list[FlowConfig],
    audio: AudioFeedback,
    speaker: TextToSpeech,
    output_lang: str,
    clipboard: ClipboardWriter | None = None,
) -> tuple[dict[str, TranscriptionHandler], list[TranscriptionHandler]]:
    """Build a handler per flow and the list of owned handlers (for close())."""
    handlers: dict[str, TranscriptionHandler] = {}
    owned: list[TranscriptionHandler] = []
    if clipboard is None:
        clipboard = create_clipboard_writer(detect_platform())
    for flow in flows:
        if flow.kind == "clipboard":
            handler: TranscriptionHandler = ClipboardHandler(audio, clipboard=clipboard)
        elif flow.kind == "claude_chat":
            if not claude_feature.is_available():
                print(
                    _("[VoiceMate] ⚠ Claude flow disabled: extra 'claude' not installed."),
                    file=sys.stderr,
                )
                print(
                    _("[VoiceMate]   Install with: poetry install --extras claude"),
                    file=sys.stderr,
                )
                continue
            chat_cfg = flow.claude_chat or ClaudeChatConfig()
            runtime = claude_feature.ClaudeRuntime(
                system_prompt=_resolve_chat_system_prompt(chat_cfg, output_lang),
                max_turns=chat_cfg.max_turns,
                model=chat_cfg.model,
                effort=chat_cfg.effort,
                thinking_enabled=chat_cfg.thinking_enabled,
            )
            try:
                runtime.start()
            except Exception as exc:  # noqa: BLE001
                print(
                    _("[VoiceMate] ⚠ Failed to start Claude (run `claude login`): {exc}").format(exc=exc),
                    file=sys.stderr,
                )
                continue
            handler = claude_feature.ClaudeChatHandler(
                runtime,
                audio,
                speaker,
                timeout_seconds=chat_cfg.timeout_seconds,
                clipboard=clipboard,
            )
        else:
            print(_("[VoiceMate] ⚠ Unknown flow ignored: {kind}").format(kind=flow.kind), file=sys.stderr)
            continue
        handlers[flow.name] = handler
        owned.append(handler)
    return handlers, owned


class _HotkeyCallback:
    """Stable (non-closure) wrapper that pins the handler_id per hotkey."""

    def __init__(self, session: RecordingSession, handler_id: str) -> None:
        self._session = session
        self._handler_id = handler_id

    def __call__(self) -> None:
        self._session.toggle(self._handler_id)


class _SocketTriggerCallback:
    """Socket binding: receives the client_id (who fired it) and returns the operation.

    Unlike `_HotkeyCallback` (local hotkey, fire-and-forget), the socket trigger
    needs to know WHAT the toggle did in order to answer the consumer.
    """

    def __init__(self, session: RecordingSession, handler_id: str) -> None:
        self._session = session
        self._handler_id = handler_id

    def __call__(self, client_id: str | None) -> ToggleOutcome | None:
        return self._session.toggle(self._handler_id, client_id=client_id)


def resolve_trigger(config: Config) -> TriggerKind:
    """Effective trigger: explicit in the config, otherwise the platform default."""
    if config.trigger is not None:
        return config.trigger
    return default_trigger(config.platform or detect_platform())


def build_listener(
    config: Config,
    flows: list[FlowConfig],
    session: RecordingSession,
    status: SessionStatus | None = None,
) -> InputListener:
    trigger = resolve_trigger(config)

    if config.input_method == "mouse":
        if trigger != "keyboard-hooks":
            print(
                _(
                    "[VoiceMate] ⚠ --input-method mouse is only supported on Windows (trigger={trigger}); "
                    "using keyboard hotkeys."
                ).format(trigger=trigger),
                file=sys.stderr,
            )
        else:
            flow_name = flows[0].name
            callback = _HotkeyCallback(session, flow_name)
            return MouseButtonListener(button=config.mouse_button, on_toggle=callback)  # type: ignore[return-value]

    if trigger == "socket":
        # Bindings keyed by flow NAME (the request says {"flow": ...}); "stop
        # decides the destination" is preserved: each request equals that flow's hotkey.
        flow_bindings: dict[str, Callable[[str | None], ToggleOutcome | None]] = {
            flow.name: _SocketTriggerCallback(session, flow.name) for flow in flows
        }
        return SocketTriggerListener(  # type: ignore[return-value]
            flow_bindings, port=config.daemon_port, status=status
        )

    bindings: dict[str, _HotkeyCallback] = {}
    for flow in flows:
        if flow.hotkey not in bindings:
            bindings[flow.hotkey] = _HotkeyCallback(session, flow.name)

    if trigger == "pynput":
        return PynputHotkeyListener(dict(bindings))  # type: ignore[return-value]
    if trigger == "evdev":
        return EvdevHotkeyListener(dict(bindings))  # type: ignore[return-value]

    if len(bindings) == 1:
        hotkey, cb = next(iter(bindings.items()))
        return KeyboardHotkeyListener(hotkey=hotkey, on_toggle=cb)  # type: ignore[return-value]
    return MultiHotkeyListener({hk: cb for hk, cb in bindings.items()})  # type: ignore[return-value]
