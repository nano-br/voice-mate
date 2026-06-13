"""Wire CLI args + Config into the runtime objects (handlers, listeners)."""

from __future__ import annotations

import sys
from collections.abc import Callable

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
from app.platform.clipboard import ClipboardWriter, create_clipboard_writer
from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import TriggerKind
from app.platform.listeners import (
    EvdevHotkeyListener,
    PynputHotkeyListener,
    SocketTriggerListener,
)


def _whisper_language(config: Config) -> str | None:
    """Idioma fixado p/ o faster-whisper ("auto" → None = detectar)."""
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
        print(f"[VoiceMate] ⚠ Falha no openai-whisper: {exc}", file=sys.stderr)
        return None


def _try_whispercpp(config: Config) -> TranscriptionBackend | None:
    if not whispercpp_feature.is_available(config):
        print(
            "[VoiceMate] ⚠ whisper.cpp não encontrado (rode `make configure`); tentando fallback.",
            file=sys.stderr,
        )
        return None
    try:
        return whispercpp_feature.build_backend(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[VoiceMate] ⚠ Falha no whisper.cpp; caindo para fallback: {exc}", file=sys.stderr)
        return None


def _try_faster_whisper_rocm(config: Config) -> TranscriptionBackend | None:
    """faster-whisper na GPU AMD via fork ROCm do CTranslate2 (HIP reporta "cuda").

    Falha aqui é persistida (`ct2_rocm_ok = false`) para a cadeia não pagar o
    custo de re-tentar a cada boot — `make configure` re-valida e re-arma.
    """
    import os

    # Workaround validado p/ gfx1201 (RX 9070 XT): o allocator default do CT2
    # causa "Memory access fault" — cub_caching evita. setdefault: respeita
    # quem já exportou outro valor no ambiente.
    os.environ.setdefault("CT2_CUDA_ALLOCATOR", "cub_caching")
    try:
        return _faster_whisper(config, use_cpu=False)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[VoiceMate] ⚠ faster-whisper (CT2-ROCm) falhou na GPU AMD: {exc}",
            file=sys.stderr,
        )
        print("[VoiceMate]   Desabilitando até o próximo `make configure`.", file=sys.stderr)
        try:
            from app.setup.persisted_config import update_persisted

            update_persisted(ct2_rocm_ok=False)
        except Exception as persist_exc:  # noqa: BLE001
            print(f"[VoiceMate] ⚠ Falha ao persistir ct2_rocm_ok: {persist_exc}", file=sys.stderr)
        return None


def _build_amd_transcriber(config: Config) -> TranscriptionBackend:
    """Cadeia AMD: faster-whisper-rocm → (GPU intermediários por plataforma) → CPU.

    A estratégia explícita (--stt-strategy/persisted) entra no topo da cadeia;
    com "auto", o CT2-ROCm só é tentado se o setup o validou (ct2_rocm_ok).

    A ordem dos intermediários depende da plataforma: no WSL2 o Vulkan só
    enxerga llvmpipe (implementação por software, CPU) — a GPU real só é
    alcançável via ROCm —, então o openai-whisper (torch ROCm) vem ANTES do
    whisper.cpp. No Linux nativo (RADV) e Windows, whisper.cpp+Vulkan acelera
    de verdade e segue na frente.
    """
    strategy = config.stt_strategy
    if strategy == "auto" and config.whisper_backend == "openai-whisper":
        strategy = "openai-whisper"  # --whisper-backend explícito continua respeitado
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
    print("[VoiceMate] ⚠ Nenhum backend GPU disponível na AMD; usando faster-whisper em CPU.", file=sys.stderr)
    return _faster_whisper(config, use_cpu=True)


def build_transcriber(config: Config) -> TranscriptionBackend:
    """Escolhe o motor de transcrição a partir de vendor/estratégia.

    `--cpu` → faster-whisper CPU. AMD → cadeia `_build_amd_transcriber` (meta:
    qualidade ≥ faster-whisper CUDA da main). NVIDIA → faster-whisper CUDA
    (comportamento da main), com whispercpp/openai-whisper opt-in via
    --whisper-backend. Sem GPU → faster-whisper CPU.
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
    extra = "voxcpm" if config.engine == "voxcpm" else "tts"
    if not tts_feature.is_available(config.engine):
        print(
            f"[VoiceMate] ⚠ TTS desativado: pacotes do engine '{config.engine}' não instalados.",
            file=sys.stderr,
        )
        print(
            f"[VoiceMate]   Instale com: poetry install --extras {extra} (ou rode com --no-tts).",
            file=sys.stderr,
        )
        return NullSpeaker()
    try:
        return tts_feature.build_default_speaker(config)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[VoiceMate] ⚠ TTS desativado (falha ao iniciar engine '{config.engine}'): {exc}",
            file=sys.stderr,
        )
        print(
            f"[VoiceMate]   Verifique `poetry install --extras {extra}`, GPU disponível, ou rode com `--no-tts`.",
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
                clipboard=clipboard,
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


def resolve_trigger(config: Config) -> TriggerKind:
    """Gatilho efetivo: explícito no config, senão o default da plataforma."""
    if config.trigger is not None:
        return config.trigger
    return default_trigger(config.platform or detect_platform())


def build_listener(
    config: Config,
    flows: list[FlowConfig],
    session: RecordingSession,
) -> InputListener:
    trigger = resolve_trigger(config)

    if config.input_method == "mouse":
        if trigger != "keyboard-hooks":
            print(
                f"[VoiceMate] ⚠ --input-method mouse só é suportado no Windows (trigger={trigger}); "
                "usando hotkeys de teclado.",
                file=sys.stderr,
            )
        else:
            flow_name = flows[0].name
            callback = _HotkeyCallback(session, flow_name)
            return MouseButtonListener(button=config.mouse_button, on_toggle=callback)  # type: ignore[return-value]

    if trigger == "socket":
        # Bindings por NOME do flow (o request diz {"flow": ...}); "stop decide
        # o destino" é preservado: cada request equivale ao hotkey daquele flow.
        flow_bindings: dict[str, Callable[[], None]] = {
            flow.name: _HotkeyCallback(session, flow.name) for flow in flows
        }
        return SocketTriggerListener(flow_bindings, port=config.daemon_port)  # type: ignore[return-value]

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
