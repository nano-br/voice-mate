"""Build `Config` dataclasses from parsed CLI args + tiny filesystem helpers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.config import (
    ClaudeChatConfig,
    ClaudeEffort,
    Config,
    TTSConfig,
    TTSDevice,
    VoiceSeedMode,
)

_DISABLED_SYSTEM_PROMPT = ""


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


def build_config(args: argparse.Namespace) -> Config:
    claude_chat_enabled = not args.no_claude_chat and args.input_method == "keyboard"
    if args.no_claude_chat is False and args.input_method == "mouse":
        print(
            "[VoiceMate] ⚠ Fluxo Claude requer input-method=keyboard. Desabilitando.",
            file=sys.stderr,
        )
    tts_device: TTSDevice = args.tts_device
    claude_effort: ClaudeEffort = args.claude_effort
    voice_seed_mode: VoiceSeedMode = args.tts_voice_seed_mode
    return Config(
        model_size=args.model,
        hotkey=args.hotkey,
        output_lang=args.output_lang,
        use_cpu=args.cpu,
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
            enabled=not args.no_tts,
            voice_description=args.tts_voice,
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
