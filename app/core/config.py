from dataclasses import dataclass, field


@dataclass
class Config:
    model_size: str = "large-v3-turbo"
    hotkey: str = "ctrl+alt+v"
    sample_rate: int = 16000
    use_cpu: bool = False
    beam_size: int = 5
    # modelos válidos para validação em runtime
    valid_models: list[str] = field(
        default_factory=lambda: ["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"]
    )
