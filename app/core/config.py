from dataclasses import dataclass, field
from typing import Literal

from app.platform.kinds import PlatformKind, TriggerKind

FlowKind = Literal["clipboard", "claude_chat"]
# TTS engine:
#   - omnivoice (k2-fsa, default): multilingual diffusion, 24 kHz, CLONES voice,
#     but heavy/compute-bound (saturates the GPU during synthesis).
#   - kokoro (hexgrad, Apache-2.0): ~82M, NON-diffusion, 24 kHz, FIXED voices (no
#     cloning), very low GPU usage and realtime — good for AMD/WSL2 without
#     saturating the GPU.
#   - voxcpm (OpenBMB, alternative): 2B, heavier.
#   - none: disabled.
TTSEngine = Literal["omnivoice", "kokoro", "voxcpm", "none"]
TTSDevice = Literal["auto", "cuda", "cpu", "mps"]
ClaudeEffort = Literal["low", "medium", "high", "xhigh", "max"]
VoiceSeedMode = Literal["auto", "fixed", "off"]

# GPU vendor resolved at runtime (detected, persisted, or via --gpu-backend).
# On PyTorch+ROCm the AMD card reports itself as "cuda" (HIP disguises as CUDA),
# so the torch device is "cuda" for both NVIDIA and AMD; the vendor serves to
# pick the transcription backend and the correct reinstall message.
GpuVendor = Literal["nvidia", "amd", "cpu"]
# Transcription engine:
#   - faster-whisper (CTranslate2): accelerates NVIDIA/CPU (no ROCm).
#   - whispercpp: whisper.cpp + Vulkan, preferred backend on AMD — lightweight
#     (~2-3 GB with turbo fp16/q8), stable and vendor-agnostic, no torch ROCm.
#   - openai-whisper: torch fallback (runs on the AMD GPU via ROCm, but ~4.8 GB).
WhisperBackend = Literal["faster-whisper", "whispercpp", "openai-whisper"]

# STT strategy on AMD (fallback chain; "auto" picks the best available):
#   - auto: faster-whisper-rocm (if CT2-ROCm is installed/validated) → whispercpp →
#     openai-whisper → faster-whisper CPU.
#   - faster-whisper-rocm: forces faster-whisper on the GPU via the ROCm fork of
#     CTranslate2 (quality identical to the faster-whisper CUDA on main).
#   - whispercpp / openai-whisper: forces the corresponding backend.
SttStrategy = Literal["auto", "faster-whisper-rocm", "whispercpp", "openai-whisper"]

# whisper.cpp backend mode:
#   - server: starts whisper-server.exe once (model warm in VRAM) and does a
#     POST /inference per utterance — eliminates the model reload on every
#     transcription that cli mode incurs. This is the default (realtime).
#   - cli: runs whisper-cli.exe per utterance (reloads the model from disk every time).
WhispercppMode = Literal["server", "cli"]

# Language pinned for transcription. Whisper accepts a single language (no
# secondary list), but does code-switching: with the primary language pinned,
# foreign terms embedded in speech (e.g. English technical terms in the middle
# of Portuguese) still come out correct. Pinning (instead of "auto") gives
# stability — it stops the detector from misclassifying a whole short utterance
# in the wrong language. The default is derived from output_lang in cli.config_builder.
TranscriptionLanguage = Literal["auto", "pt", "en", "es", "fr", "de", "it", "ja", "zh"]

# Default language code injected into LLM system prompts (placeholder
# `{output_lang}`). The assistant replies in this language — switching it
# is enough to change the response language without keeping translated
# copies of the prompt. Format follows BCP-47 (e.g. "pt-BR", "en", "es").
DEFAULT_OUTPUT_LANG = "pt-BR"

# Default voice description used by VoxCPM2 when there is no seed (modes
# voice_seed_mode="off" or the first utterance of "auto"). In cloning mode
# (auto after the first utterance, or fixed), the voice comes from
# prompt_wav_path and the description is ignored. We keep the constant here so
# it serves as both the dataclass default and the default of the --tts-voice CLI arg.
DEFAULT_VOICE_DESCRIPTION = "Uma mulher brasileira idosa, voz natural e calorosa, tom pausado e claro."


@dataclass
class ClaudeChatConfig:
    """Parameters for the voice → Claude → clipboard flow.

    `system_prompt = None` means: use the canonical prompt
    (`claude_cli_system_prompt(output_lang)`). Passing a string overrides it.
    """

    system_prompt: str | None = None
    max_turns: int | None = 50
    # Haiku 4.5: lower time-to-first-token, ideal for realtime voice with short
    # responses. Does NOT accept the `effort` parameter (the runtime omits it for Haiku models).
    model: str = "claude-haiku-4-5"
    effort: ClaudeEffort = "low"
    thinking_enabled: bool = False
    timeout_seconds: float = 120.0
    # placeholders for future evolution — not used yet
    pre_ai_heuristic_enabled: bool = False
    router_model: str | None = None


@dataclass
class TTSConfig:
    """Parameters for the Text-to-Speech orchestrator."""

    enabled: bool = True
    engine: TTSEngine = "omnivoice"
    # Language passed to the engine (OmniVoice uses it to improve phonemization/prosody).
    # Derived from output_lang in cli.config_builder; "auto" = engine decides.
    language: TranscriptionLanguage = "pt"
    voice_description: str = DEFAULT_VOICE_DESCRIPTION
    cfg_value: float = 2.0
    inference_timesteps: int = 10
    # Fixed Kokoro voice (only applies to engine="kokoro"; Kokoro does not clone voice).
    # pf_dora = female PT-BR; pm_alex/pm_santa = male PT-BR.
    kokoro_voice: str = "pf_dora"
    device: TTSDevice = "auto"
    streaming: bool = True
    save_audio_dir: str | None = None
    optimize: bool = True
    cache_dir: str | None = None
    normalize: bool = False
    denoise: bool = False
    voice_seed_mode: VoiceSeedMode = "off"

    voice_seed_path: str | None = None
    voice_seed_text: str | None = None
    voice_seed_cache_dir: str | None = None
    show_progress: bool = False
    drain_timeout_seconds: float = 60.0
    debug_vram: bool = False
    # GPU vendor (only to pick the correct reinstall hint in the
    # "torch without acceleration" warning). The device itself still comes from `device`.
    gpu_vendor: GpuVendor = "cpu"


@dataclass
class FlowConfig:
    """Describes a flow (hotkey → destination of the transcribed text)."""

    name: str
    kind: FlowKind
    hotkey: str
    claude_chat: ClaudeChatConfig | None = None


@dataclass
class Config:
    model_size: str = "large-v3-turbo"
    hotkey: str = "ctrl+alt+v"
    output_lang: str = DEFAULT_OUTPUT_LANG
    sample_rate: int = 16000
    use_cpu: bool = False
    beam_size: int = 5
    # GPU resolved at runtime: vendor + transcription engine. The default is the
    # safest (CPU + faster-whisper); setup/persistence/CLI override it.
    gpu_vendor: GpuVendor = "cpu"
    whisper_backend: WhisperBackend = "faster-whisper"
    # STT strategy on AMD (fallback chain). "auto" uses ct2_rocm_ok to
    # decide whether to try faster-whisper-rocm first.
    stt_strategy: SttStrategy = "auto"
    # Result of the CTranslate2-ROCm validation done by setup (persisted):
    # True = build validated (use faster-whisper on the AMD GPU); False = failed
    # (do not retry on every boot); None = never attempted.
    ct2_rocm_ok: bool | None = None
    # Environment and trigger resolved at runtime (None = auto-detect in main/builder).
    # platform decides the trigger/clipboard defaults; trigger picks the listener
    # (keyboard-hooks on Windows, pynput on X11, evdev on Wayland, socket on WSL2).
    platform: PlatformKind | None = None
    trigger: TriggerKind | None = None
    # Port of the local HTTP daemon when trigger == "socket" (WSL2). The
    # Windows-side script (scripts/windows/) does POST /trigger on this port.
    daemon_port: int = 47821
    # whisper.cpp mode (server = warm model, default; cli = subprocess/utterance).
    whispercpp_mode: WhispercppMode = "server"
    # Language pinned for transcription. Default "pt" (consistent with the default
    # output_lang pt-BR); cli.config_builder derives it from output_lang when there
    # is no explicit --transcription-language.
    transcription_language: TranscriptionLanguage = "pt"
    # Directory with whisper-cli.exe / whisper-server.exe + DLLs + GGUF model.
    # None → ~/.cache/voicemate/whispercpp (filled in by make setup).
    whispercpp_dir: str | None = None
    input_method: str = "keyboard"
    mouse_button: str = "x"
    max_recording_seconds: int = 600
    timeout_warning_percent: float = 0.8
    watchdog_enabled: bool = True
    watchdog_timeout_seconds: int = 120
    listener_refresh_enabled: bool = True
    listener_refresh_seconds: int = 60
    # claude chat
    claude_chat_enabled: bool = True
    claude_chat_hotkey: str = "ctrl+alt+a"
    claude_chat: ClaudeChatConfig = field(default_factory=ClaudeChatConfig)
    # tts
    tts: TTSConfig = field(default_factory=TTSConfig)
    # flows: if empty, build_default_flows() is used
    flows: list[FlowConfig] = field(default_factory=list)
    # valid models for runtime validation
    valid_models: list[str] = field(
        default_factory=lambda: ["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"]
    )

    def build_default_flows(self) -> list[FlowConfig]:
        """Build the default list of flows from the Config fields."""
        flows: list[FlowConfig] = [
            FlowConfig(name="clipboard", kind="clipboard", hotkey=self.hotkey),
        ]
        if self.claude_chat_enabled:
            flows.append(
                FlowConfig(
                    name="claude_chat",
                    kind="claude_chat",
                    hotkey=self.claude_chat_hotkey,
                    claude_chat=self.claude_chat,
                )
            )
        return flows
