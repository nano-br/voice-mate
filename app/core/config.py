from dataclasses import dataclass, field
from typing import Literal

FlowKind = Literal["clipboard", "claude_chat"]
TTSEngine = Literal["voxcpm", "none"]
TTSDevice = Literal["auto", "cuda", "cpu", "mps"]
ClaudeEffort = Literal["low", "medium", "high", "xhigh", "max"]
VoiceSeedMode = Literal["auto", "fixed", "off"]

# Vendor da GPU resolvido em runtime (detectado, persistido ou via --gpu-backend).
# No PyTorch+ROCm a placa AMD se reporta como "cuda" (HIP se disfarça de CUDA),
# então o device do torch é "cuda" tanto p/ NVIDIA quanto p/ AMD; o vendor serve
# para escolher o backend de transcrição e a mensagem de reinstalação correta.
GpuVendor = Literal["nvidia", "amd", "cpu"]
# Motor de transcrição:
#   - faster-whisper (CTranslate2): acelera NVIDIA/CPU (não tem ROCm).
#   - whispercpp: whisper.cpp + Vulkan, backend preferido na AMD — leve
#     (~2-3 GB c/ turbo fp16/q8), estável e vendor-agnóstico, sem torch ROCm.
#   - openai-whisper: fallback torch (roda na GPU AMD via ROCm, mas ~4,8 GB).
WhisperBackend = Literal["faster-whisper", "whispercpp", "openai-whisper"]

# Default language code injected into LLM system prompts (placeholder
# `{output_lang}`). The assistant replies in this language — switching it
# is enough to change the response language without keeping translated
# copies of the prompt. Format follows BCP-47 (e.g. "pt-BR", "en", "es").
DEFAULT_OUTPUT_LANG = "pt-BR"

# Descrição de voz default usada pelo VoxCPM2 quando não há seed (modos
# voice_seed_mode="off" ou primeira fala do "auto"). Em modo cloning
# (auto após primeira fala ou fixed), a voz vem do prompt_wav_path e a
# descrição é ignorada. Mantemos a constante aqui para servir tanto como
# default do dataclass quanto como default do CLI arg --tts-voice.
DEFAULT_VOICE_DESCRIPTION = "Uma mulher brasileira idosa, voz natural e calorosa, tom pausado e claro."


@dataclass
class ClaudeChatConfig:
    """Parâmetros do fluxo voz → Claude → clipboard.

    `system_prompt = None` significa: usar o prompt canônico
    (`claude_cli_system_prompt(output_lang)`). Passar uma string sobrescreve.
    """

    system_prompt: str | None = None
    max_turns: int | None = 50
    model: str = "claude-sonnet-4-6"
    effort: ClaudeEffort = "low"
    thinking_enabled: bool = False
    timeout_seconds: float = 120.0
    # placeholders para evolução futura — não usados ainda
    pre_ai_heuristic_enabled: bool = False
    router_model: str | None = None


@dataclass
class TTSConfig:
    """Parâmetros do orquestrador de Text-to-Speech."""

    enabled: bool = True
    engine: TTSEngine = "voxcpm"
    voice_description: str = DEFAULT_VOICE_DESCRIPTION
    cfg_value: float = 2.0
    inference_timesteps: int = 10
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
    # Vendor da GPU (só p/ escolher a dica de reinstalação correta no aviso
    # de "torch sem aceleração"). O device em si continua vindo de `device`.
    gpu_vendor: GpuVendor = "cpu"


@dataclass
class FlowConfig:
    """Descreve um fluxo (atalho → destino do texto transcrito)."""

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
    # GPU resolvida em runtime: vendor + motor de transcrição. O default é o
    # mais seguro (CPU + faster-whisper); o setup/persistência/CLI sobrescrevem.
    gpu_vendor: GpuVendor = "cpu"
    whisper_backend: WhisperBackend = "faster-whisper"
    # Diretório com whisper-cli.exe + DLLs + modelo GGUF (backend whispercpp).
    # None → ~/.cache/voicemate/whispercpp (preenchido pelo make setup).
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
    # fluxos: se vazio, build_default_flows() é usado
    flows: list[FlowConfig] = field(default_factory=list)
    # modelos válidos para validação em runtime
    valid_models: list[str] = field(
        default_factory=lambda: ["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"]
    )

    def build_default_flows(self) -> list[FlowConfig]:
        """Constrói a lista padrão de fluxos a partir dos campos de Config."""
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
