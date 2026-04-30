from dataclasses import dataclass, field


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
    # modelos válidos para validação em runtime
    valid_models: list[str] = field(
        default_factory=lambda: ["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"]
    )
