from dataclasses import dataclass, field
from typing import Literal

FlowKind = Literal["clipboard", "claude_chat"]


@dataclass
class ClaudeChatConfig:
    """Parâmetros do fluxo voz → Claude → clipboard."""

    system_prompt: str | None = None
    max_turns: int | None = 50
    # placeholders para evolução futura — não usados ainda
    pre_ai_heuristic_enabled: bool = False
    router_model: str | None = None


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
    sample_rate: int = 16000
    use_cpu: bool = False
    beam_size: int = 5
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
