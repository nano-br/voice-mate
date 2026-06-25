"""argparse layer — keeps `main.py` slim by isolating CLI definitions."""

from __future__ import annotations

import argparse

from app.core.config import DEFAULT_OUTPUT_LANG, DEFAULT_VOICE_DESCRIPTION


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VoiceMate — voice to clipboard or Claude")
    _add_core_args(parser)
    _add_claude_args(parser)
    _add_tts_args(parser)
    return parser.parse_args(argv)


def _add_core_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default="large-v3-turbo",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"],
        help="Whisper model (default: large-v3-turbo)",
    )
    parser.add_argument(
        "--hotkey",
        default="ctrl+alt+v",
        help="Global hotkey for the clipboard flow (default: ctrl+alt+v)",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Force CPU instead of GPU (uses int8)",
    )
    parser.add_argument(
        "--gpu-backend",
        default=None,
        choices=["auto", "nvidia", "amd", "cpu"],
        help=(
            "GPU vendor. 'auto' detects the card; if omitted, uses the saved config "
            "(~/.config/voicemate/config.toml) or detects it. Overrides the saved value."
        ),
    )
    parser.add_argument(
        "--whisper-backend",
        default=None,
        choices=["faster-whisper", "whispercpp", "openai-whisper"],
        help=(
            "Transcription engine. Omitted = chosen automatically by vendor "
            "(NVIDIA→faster-whisper/CUDA, AMD→whisper.cpp/Vulkan, CPU→faster-whisper)."
        ),
    )
    parser.add_argument(
        "--stt-strategy",
        default=None,
        choices=["auto", "faster-whisper-rocm", "whispercpp", "openai-whisper"],
        help=(
            "STT strategy on AMD (fallback chain). Omitted = saved config or "
            "'auto' (faster-whisper-rocm if validated by setup → whispercpp → "
            "openai-whisper → CPU). 'faster-whisper-rocm' forces CT2-ROCm on the GPU."
        ),
    )
    parser.add_argument(
        "--platform",
        default=None,
        choices=["windows", "linux-x11", "linux-wayland", "wsl2"],
        help="Runtime environment. Omitted = saved config or auto-detection.",
    )
    parser.add_argument(
        "--trigger",
        default=None,
        choices=["keyboard-hooks", "pynput", "evdev", "socket"],
        help=(
            "Trigger mechanism. Omitted = platform default "
            "(windows→keyboard-hooks, x11→pynput, wayland→evdev, wsl2→socket/daemon)."
        ),
    )
    parser.add_argument(
        "--daemon-port",
        type=int,
        default=None,
        help="Port of the local HTTP daemon when trigger=socket (default: 47821).",
    )
    parser.add_argument(
        "--whispercpp-mode",
        default="server",
        choices=["server", "cli"],
        help=(
            "whisper.cpp mode. 'server' (default): starts whisper-server.exe once "
            "(model kept warm in VRAM, realtime). 'cli': runs whisper-cli.exe per utterance "
            "(reloads the model every time — slower)."
        ),
    )
    parser.add_argument(
        "--transcription-language",
        default=None,
        choices=["auto", "pt", "en", "es", "fr", "de", "it", "ja", "zh"],
        help=(
            "Language pinned for transcription. Omitted = derived from --output-lang "
            "(pt-BR→pt, en→en). Pinning improves stability and still transcribes embedded "
            "foreign terms (code-switching). 'auto' detects per utterance "
            "(less stable on short utterances)."
        ),
    )
    parser.add_argument(
        "--input-method",
        default="keyboard",
        choices=["keyboard", "mouse"],
        help="Input method (default: keyboard)",
    )
    parser.add_argument(
        "--mouse-button",
        default="x",
        help="Mouse button to use as trigger (default: x = side button)",
    )
    parser.add_argument(
        "--max-recording-seconds",
        type=int,
        default=600,
        help="Maximum recording time in seconds (default: 600 = 10min)",
    )
    parser.add_argument(
        "--no-watchdog",
        action="store_true",
        help="Disable the auto-recovery watchdog",
    )
    parser.add_argument(
        "--watchdog-timeout",
        type=int,
        default=120,
        help="Watchdog timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--no-listener-refresh",
        action="store_true",
        help="Disable periodic reinstallation of the listener",
    )
    parser.add_argument(
        "--listener-refresh-seconds",
        type=int,
        default=60,
        help="Listener reinstallation interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--output-lang",
        default=DEFAULT_OUTPUT_LANG,
        help=(
            "BCP-47 language code for Claude's responses (default: pt-BR). "
            "Injected into the {output_lang} placeholder of the canonical prompt — does not "
            "translate logs or app messages."
        ),
    )


def _add_claude_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--claude-chat-hotkey",
        default="ctrl+alt+a",
        help="Global hotkey for the voice→Claude→clipboard flow (default: ctrl+alt+a)",
    )
    parser.add_argument(
        "--no-claude-chat",
        action="store_true",
        help="Disable the Claude chat flow (clipboard only)",
    )
    parser.add_argument(
        "--claude-system-prompt",
        default=None,
        help="Custom system prompt for the Claude flow (overrides the canonical one)",
    )
    parser.add_argument(
        "--claude-no-system-prompt",
        action="store_true",
        help="Disable the system prompt (takes precedence over --claude-system-prompt if both are passed)",
    )
    parser.add_argument(
        "--claude-max-turns",
        type=int,
        default=50,
        help="Maximum turns per Claude session (default: 50)",
    )
    parser.add_argument(
        "--claude-model",
        default="claude-haiku-4-5",
        help=(
            "Claude model (default: claude-haiku-4-5 — lowest latency for realtime voice). "
            "Use claude-sonnet-4-6 for more elaborate responses."
        ),
    )
    parser.add_argument(
        "--claude-effort",
        default="low",
        choices=["low", "medium", "high", "xhigh", "max"],
        help=(
            "Claude effort level (default: low — prioritizes speed). "
            "Ignored on Haiku models, which do not accept the parameter."
        ),
    )
    parser.add_argument(
        "--claude-enable-thinking",
        action="store_true",
        help="Enable Claude's extended thinking (default: disabled)",
    )
    parser.add_argument(
        "--claude-timeout-seconds",
        type=float,
        default=120.0,
        help="Timeout in seconds for each Claude turn (default: 120s — defense in depth)",
    )


def _add_tts_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Disable TTS (Claude's response goes to clipboard + beep only)",
    )
    parser.add_argument(
        "--tts-engine",
        default=None,  # None = use the engine saved during setup, or "omnivoice"
        choices=["omnivoice", "kokoro", "voxcpm", "none"],
        help=(
            "TTS engine. 'omnivoice' (default): diffusion-based, clones voices, but heavy. "
            "'kokoro': lightweight/realtime, low GPU, fixed voices (no cloning). "
            "'voxcpm': alternative (heavier). 'none': disables it."
        ),
    )
    parser.add_argument(
        "--tts-kokoro-voice",
        default="pf_dora",
        help=(
            "Fixed Kokoro voice (only with --tts-engine=kokoro). PT-BR: pf_dora (female), pm_alex / pm_santa (male)."
        ),
    )
    parser.add_argument(
        "--tts-voice",
        default=DEFAULT_VOICE_DESCRIPTION,
        help=(
            "Text description of the desired voice (VoxCPM2 voice design mode). "
            "Applied ONLY when there is no seed: with --tts-voice-seed-mode=off "
            "(every utterance) or on the FIRST utterance of --tts-voice-seed-mode=auto. "
            "In cloning mode (auto after the first / fixed), the voice comes from the seed "
            "WAV and this description is ignored."
        ),
    )
    parser.add_argument(
        "--tts-cfg-value",
        type=float,
        default=2.0,
        help="VoxCPM2 cfg_value (1.0–3.0, default: 2.0)",
    )
    parser.add_argument(
        "--tts-inference-timesteps",
        type=int,
        default=10,
        help="VoxCPM2 inference_timesteps (4–30, default: 10)",
    )
    parser.add_argument(
        "--tts-device",
        default="auto",
        choices=["auto", "cuda", "cpu", "mps"],
        help="VoxCPM2 device (default: auto)",
    )
    parser.add_argument(
        "--tts-save-dir",
        default=None,
        help="Directory to save generated audio (default: do not save)",
    )
    parser.add_argument(
        "--tts-no-streaming",
        action="store_true",
        help="Use one-shot generation instead of streaming (debug)",
    )
    parser.add_argument(
        "--tts-voice-seed-mode",
        default="off",
        choices=["auto", "fixed", "off"],
        help=(
            "How the TTS picks the voice. "
            "'off' (default): voice fixed by DESCRIPTION (--tts-voice) — voice design, "
            "no cloning; with a fixed seed the SAME voice comes out on every utterance (fast and "
            "consistent). 'fixed': cloning from the WAV in --tts-voice-seed-path "
            "+ --tts-voice-seed-text. 'auto': auto cloning — the 1st utterance becomes the reference "
            "and the following ones clone it (slower; the voice may drift mid-response)."
        ),
    )
    parser.add_argument(
        "--tts-voice-seed-path",
        default=None,
        help="Path to the WAV used as seed when --tts-voice-seed-mode=fixed",
    )
    parser.add_argument(
        "--tts-voice-seed-text",
        default=None,
        help="Text matching the seed WAV (required with --tts-voice-seed-mode=fixed)",
    )
    parser.add_argument(
        "--tts-voice-seed-cache-dir",
        default=None,
        help="Directory to write the auto-seed (default: ~/.cache/voicemate)",
    )
    parser.add_argument(
        "--tts-reset-seed",
        action="store_true",
        help="Delete the existing auto-seed before starting (forces regeneration on the first utterance)",
    )
    parser.add_argument(
        "--tts-show-progress",
        action="store_true",
        help="Show VoxCPM2's internal progress bar (default: suppressed)",
    )
    parser.add_argument(
        "--tts-drain-timeout-seconds",
        type=float,
        default=60.0,
        help="AudioPlayer.drain() timeout in seconds (default: 60s)",
    )
    parser.add_argument(
        "--tts-debug-vram",
        action="store_true",
        help="Log VRAM usage before and after each utterance (diagnostics)",
    )
