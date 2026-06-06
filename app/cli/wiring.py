"""Wire CLI args + Config into the runtime objects (handlers, listeners)."""

from __future__ import annotations

import sys

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


def _faster_whisper_cpu(config: Config) -> TranscriptionBackend:
    return FasterWhisperBackend(model_size=config.model_size, use_cpu=True, beam_size=config.beam_size)


def _try_openai_whisper(config: Config) -> TranscriptionBackend | None:
    if not openai_whisper_feature.is_available():
        return None
    try:
        return openai_whisper_feature.build_backend(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[VoiceMate] ⚠ Falha no openai-whisper: {exc}", file=sys.stderr)
        return None


def _try_whispercpp(config: Config) -> TranscriptionBackend | None:
    if not whispercpp_feature.is_available(config):
        print(
            "[VoiceMate] ⚠ whisper.cpp não encontrado (rode `make configure`); tentando fallback.",
            file=sys.stderr,
        )
        return _try_openai_whisper(config)
    try:
        return whispercpp_feature.build_backend(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[VoiceMate] ⚠ Falha no whisper.cpp; caindo para fallback: {exc}", file=sys.stderr)
        return _try_openai_whisper(config)


def build_transcriber(config: Config) -> TranscriptionBackend:
    """Escolhe o motor de transcrição a partir de `gpu_vendor`/`whisper_backend`.

    AMD → whisper.cpp + Vulkan (leve, estável, sem torch ROCm; o CTranslate2 do
    faster-whisper não acelera em ROCm e trava na gfx1201). NVIDIA →
    faster-whisper CUDA. CPU/`--cpu` → faster-whisper CPU. Cadeia de fallback
    segura: whisper.cpp → openai-whisper → faster-whisper CPU.
    """
    if not config.use_cpu:
        backend: TranscriptionBackend | None = None
        if config.whisper_backend == "whispercpp":
            backend = _try_whispercpp(config)
        elif config.whisper_backend == "openai-whisper":
            backend = _try_openai_whisper(config)
        if backend is not None:
            return backend

    # faster-whisper: NVIDIA acelera em CUDA; AMD/CPU/fallback vão para CPU (CT2 não tem ROCm).
    use_cpu = config.use_cpu or config.gpu_vendor in ("amd", "cpu")
    return FasterWhisperBackend(model_size=config.model_size, use_cpu=use_cpu, beam_size=config.beam_size)


def build_speaker(config: TTSConfig) -> TextToSpeech:
    if not config.enabled or config.engine == "none":
        return NullSpeaker()
    if not tts_feature.is_available():
        print(
            "[VoiceMate] ⚠ TTS desativado: extra 'tts' não instalado.",
            file=sys.stderr,
        )
        print(
            "[VoiceMate]   Instale com: poetry install --extras tts (ou rode com --no-tts).",
            file=sys.stderr,
        )
        return NullSpeaker()
    try:
        return tts_feature.build_default_speaker(config)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[VoiceMate] ⚠ TTS desativado (falha ao iniciar VoxCPM2): {exc}",
            file=sys.stderr,
        )
        print(
            "[VoiceMate]   Verifique `poetry install --extras tts`, GPU/CUDA disponíveis, ou rode com `--no-tts`.",
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
) -> tuple[dict[str, TranscriptionHandler], list[TranscriptionHandler]]:
    """Build a handler per flow and the list of owned handlers (for close())."""
    handlers: dict[str, TranscriptionHandler] = {}
    owned: list[TranscriptionHandler] = []
    for flow in flows:
        if flow.kind == "clipboard":
            handler: TranscriptionHandler = ClipboardHandler(audio)
        elif flow.kind == "claude_chat":
            if not claude_feature.is_available():
                print(
                    "[VoiceMate] ⚠ Fluxo Claude desabilitado: extra 'claude' não instalado.",
                    file=sys.stderr,
                )
                print(
                    "[VoiceMate]   Instale com: poetry install --extras claude",
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
                    f"[VoiceMate] ⚠ Falha ao iniciar Claude (rode `claude login`): {exc}",
                    file=sys.stderr,
                )
                continue
            handler = claude_feature.ClaudeChatHandler(
                runtime,
                audio,
                speaker,
                timeout_seconds=chat_cfg.timeout_seconds,
            )
        else:
            print(f"[VoiceMate] ⚠ Fluxo desconhecido ignorado: {flow.kind}", file=sys.stderr)
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


def build_listener(
    config: Config,
    flows: list[FlowConfig],
    session: RecordingSession,
) -> InputListener:
    if config.input_method == "mouse":
        flow_name = flows[0].name
        callback = _HotkeyCallback(session, flow_name)
        return MouseButtonListener(button=config.mouse_button, on_toggle=callback)  # type: ignore[return-value]

    bindings: dict[str, _HotkeyCallback] = {}
    for flow in flows:
        if flow.hotkey not in bindings:
            bindings[flow.hotkey] = _HotkeyCallback(session, flow.name)
    if len(bindings) == 1:
        hotkey, cb = next(iter(bindings.items()))
        return KeyboardHotkeyListener(hotkey=hotkey, on_toggle=cb)  # type: ignore[return-value]
    return MultiHotkeyListener({hk: cb for hk, cb in bindings.items()})  # type: ignore[return-value]
