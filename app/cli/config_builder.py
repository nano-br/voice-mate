"""Build `Config` dataclasses from parsed CLI args + tiny filesystem helpers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from app.core.config import (
    ClaudeChatConfig,
    ClaudeEffort,
    Config,
    GpuVendor,
    SttStrategy,
    TranscriptionLanguage,
    TTSConfig,
    TTSDevice,
    TTSEngine,
    VoiceSeedMode,
    WhisperBackend,
    WhispercppMode,
)
from app.platform.kinds import PlatformKind, TriggerKind
from app.setup.gpu_detect import detect_gpu
from app.setup.persisted_config import PersistedConfig

_DISABLED_SYSTEM_PROMPT = ""

# Idiomas que o enum TranscriptionLanguage suporta (menos "auto") — usado para
# derivar o idioma da transcrição a partir do output_lang (BCP-47).
_KNOWN_TRANSCRIPTION_LANGS = frozenset({"pt", "en", "es", "fr", "de", "it", "ja", "zh"})


def _resolve_gpu_vendor(args: argparse.Namespace, persisted: PersistedConfig) -> GpuVendor:
    """Precedência: --cpu > --gpu-backend > config salvo > auto-detecção."""
    if args.cpu:
        return "cpu"
    if args.gpu_backend is not None:
        if args.gpu_backend == "auto":
            return detect_gpu().vendor
        return cast(GpuVendor, args.gpu_backend)  # nvidia | amd | cpu (validado pelo argparse)
    if persisted.gpu_vendor is not None:
        return persisted.gpu_vendor
    return detect_gpu().vendor


def _default_backend_for(vendor: GpuVendor) -> WhisperBackend:
    # AMD não acelera no CTranslate2 (faster-whisper) → whisper.cpp + Vulkan
    # (leve, estável, sem torch ROCm). openai-whisper fica como fallback opcional.
    return "whispercpp" if vendor == "amd" else "faster-whisper"


def _resolve_whisper_backend(args: argparse.Namespace, persisted: PersistedConfig, vendor: GpuVendor) -> WhisperBackend:
    """Precedência: --cpu > --whisper-backend > config salvo > default do vendor."""
    if args.cpu:
        return "faster-whisper"  # melhor engine em CPU
    if args.whisper_backend is not None:
        return cast(WhisperBackend, args.whisper_backend)
    if persisted.whisper_backend is not None:
        return persisted.whisper_backend
    return _default_backend_for(vendor)


def _derive_transcription_language(output_lang: str) -> TranscriptionLanguage:
    """Deriva o idioma da transcrição do output_lang (BCP-47 → ISO 639-1)."""
    code = output_lang.replace("_", "-").split("-")[0].lower()
    if code in _KNOWN_TRANSCRIPTION_LANGS:
        return cast(TranscriptionLanguage, code)
    return "auto"


def _resolve_transcription_language(args: argparse.Namespace, output_lang: str) -> TranscriptionLanguage:
    """Precedência: --transcription-language explícito > derivado do output_lang."""
    if args.transcription_language is not None:
        return cast(TranscriptionLanguage, args.transcription_language)
    return _derive_transcription_language(output_lang)


def _resolve_tts_enabled(args: argparse.Namespace, persisted: PersistedConfig) -> bool:
    if args.no_tts:
        return False
    if persisted.tts_enabled is not None:
        return persisted.tts_enabled
    return True


def _resolve_tts_engine(args: argparse.Namespace, persisted: PersistedConfig) -> TTSEngine:
    """Precedência: --tts-engine explícito > engine salvo no setup > "omnivoice"."""
    if args.tts_engine is not None:  # flag default = None (não informado)
        return cast(TTSEngine, args.tts_engine)
    if persisted.tts_engine is not None:
        return persisted.tts_engine
    return "omnivoice"


def _resolve_claude_enabled(args: argparse.Namespace, persisted: PersistedConfig) -> bool:
    """CLI --no-claude-chat > fluxo salvo > default (ligado); exige keyboard."""
    if args.no_claude_chat:
        enabled = False
    elif persisted.default_flow == "clipboard":
        enabled = False
    else:
        enabled = True
    if enabled and args.input_method == "mouse":
        print(
            "[VoiceMate] ⚠ Fluxo Claude requer input-method=keyboard. Desabilitando.",
            file=sys.stderr,
        )
        return False
    return enabled and args.input_method == "keyboard"


def resolve_system_prompt(args: argparse.Namespace) -> str | None:
    """Resolve the user's system-prompt flag precedence.

    Returns:
      - "" (sentinel) → user explicitly disabled (--claude-no-system-prompt)
      - non-empty str → user override
      - None → use canonical prompt (resolved later with `output_lang`)
    """
    if args.claude_no_system_prompt:
        if args.claude_system_prompt is not None:
            print(
                "[VoiceMate] ⚠ --claude-no-system-prompt e --claude-system-prompt foram "
                "passados juntos; usando --claude-no-system-prompt.",
                file=sys.stderr,
            )
        return _DISABLED_SYSTEM_PROMPT
    if args.claude_system_prompt is not None:
        return str(args.claude_system_prompt)
    return None


def _resolve_stt_strategy(args: argparse.Namespace, persisted: PersistedConfig) -> SttStrategy:
    """Precedência: --stt-strategy > config salvo > auto."""
    flag = getattr(args, "stt_strategy", None)
    if flag is not None:
        return cast(SttStrategy, flag)
    if persisted.stt_strategy is not None:
        return persisted.stt_strategy
    return "auto"


def _resolve_daemon_port(args: argparse.Namespace, persisted: PersistedConfig) -> int:
    flag = getattr(args, "daemon_port", None)
    if flag is not None:
        return int(flag)
    if persisted.daemon_port is not None:
        return persisted.daemon_port
    return 47821


def build_config(args: argparse.Namespace, persisted: PersistedConfig | None = None) -> Config:
    persisted = persisted or PersistedConfig()
    gpu_vendor = _resolve_gpu_vendor(args, persisted)
    whisper_backend = _resolve_whisper_backend(args, persisted, gpu_vendor)
    claude_chat_enabled = _resolve_claude_enabled(args, persisted)
    tts_enabled = _resolve_tts_enabled(args, persisted)
    tts_device: TTSDevice = args.tts_device
    claude_effort: ClaudeEffort = args.claude_effort
    voice_seed_mode: VoiceSeedMode = args.tts_voice_seed_mode
    whispercpp_mode: WhispercppMode = cast(WhispercppMode, args.whispercpp_mode)
    transcription_language = _resolve_transcription_language(args, args.output_lang)
    return Config(
        model_size=args.model,
        hotkey=args.hotkey,
        output_lang=args.output_lang,
        use_cpu=args.cpu,
        gpu_vendor=gpu_vendor,
        whisper_backend=whisper_backend,
        stt_strategy=_resolve_stt_strategy(args, persisted),
        ct2_rocm_ok=persisted.ct2_rocm_ok,
        # Precedência: flag > config salvo > auto-detect (resolvido em main.py).
        platform=cast(PlatformKind, args.platform) if getattr(args, "platform", None) else persisted.platform,
        trigger=cast(TriggerKind, args.trigger) if getattr(args, "trigger", None) else persisted.trigger,
        daemon_port=_resolve_daemon_port(args, persisted),
        whispercpp_mode=whispercpp_mode,
        transcription_language=transcription_language,
        input_method=args.input_method,
        mouse_button=args.mouse_button,
        max_recording_seconds=args.max_recording_seconds,
        watchdog_enabled=not args.no_watchdog,
        watchdog_timeout_seconds=args.watchdog_timeout,
        listener_refresh_enabled=not args.no_listener_refresh,
        listener_refresh_seconds=args.listener_refresh_seconds,
        claude_chat_enabled=claude_chat_enabled,
        claude_chat_hotkey=args.claude_chat_hotkey,
        claude_chat=ClaudeChatConfig(
            system_prompt=resolve_system_prompt(args),
            max_turns=args.claude_max_turns,
            model=args.claude_model,
            effort=claude_effort,
            thinking_enabled=args.claude_enable_thinking,
            timeout_seconds=args.claude_timeout_seconds,
        ),
        tts=TTSConfig(
            enabled=tts_enabled,
            engine=_resolve_tts_engine(args, persisted),
            language=transcription_language,
            voice_description=args.tts_voice,
            kokoro_voice=args.tts_kokoro_voice,
            cfg_value=args.tts_cfg_value,
            inference_timesteps=args.tts_inference_timesteps,
            device=tts_device,
            streaming=not args.tts_no_streaming,
            save_audio_dir=args.tts_save_dir,
            voice_seed_mode=voice_seed_mode,
            voice_seed_path=args.tts_voice_seed_path,
            voice_seed_text=args.tts_voice_seed_text,
            voice_seed_cache_dir=args.tts_voice_seed_cache_dir,
            show_progress=args.tts_show_progress,
            drain_timeout_seconds=args.tts_drain_timeout_seconds,
            debug_vram=args.tts_debug_vram,
            gpu_vendor=gpu_vendor,
        ),
    )


def resolve_voice_seed_cache_dir(config: TTSConfig) -> str:
    if config.voice_seed_cache_dir:
        return config.voice_seed_cache_dir
    return str(Path.home() / ".cache" / "voicemate")


def delete_existing_auto_seed(config: TTSConfig) -> None:
    cache_dir = Path(resolve_voice_seed_cache_dir(config))
    seed_wav = cache_dir / "voice_seed.wav"
    seed_txt = cache_dir / "voice_seed.txt"
    for path in (seed_wav, seed_txt):
        if path.exists():
            try:
                path.unlink()
                print(f"[VoiceMate] Auto-seed removido: {path}")
            except OSError as exc:
                print(
                    f"[VoiceMate] ⚠ Falha ao remover {path}: {exc}",
                    file=sys.stderr,
                )
