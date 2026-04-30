import argparse

from app.core.config import Config
from app.services.audio_feedback import AudioFeedback
from app.services.input_listener import create_listener
from app.services.recorder import Recorder
from app.services.recording_session import RecordingSession
from app.services.transcriber import Transcriber
from app.services.watchdog import Watchdog


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceMate — voz para clipboard")
    parser.add_argument(
        "--model",
        default="large-v3-turbo",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"],
        help="Modelo Whisper (padrão: large-v3-turbo)",
    )
    parser.add_argument(
        "--hotkey",
        default="ctrl+alt+v",
        help="Hotkey global para iniciar/parar (padrão: ctrl+alt+v)",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Forçar uso de CPU em vez de GPU (usa int8)",
    )
    parser.add_argument(
        "--input-method",
        default="keyboard",
        choices=["keyboard", "mouse"],
        help="Método de input (padrão: keyboard)",
    )
    parser.add_argument(
        "--mouse-button",
        default="x",
        help="Botão do mouse para usar como trigger (padrão: x = botão lateral)",
    )
    parser.add_argument(
        "--max-recording-seconds",
        type=int,
        default=600,
        help="Tempo máximo de gravação em segundos (padrão: 600 = 10min)",
    )
    parser.add_argument(
        "--no-watchdog",
        action="store_true",
        help="Desabilitar watchdog de auto-recovery",
    )
    parser.add_argument(
        "--watchdog-timeout",
        type=int,
        default=120,
        help="Timeout do watchdog em segundos (padrão: 120)",
    )
    args = parser.parse_args()

    config = Config(
        model_size=args.model,
        hotkey=args.hotkey,
        use_cpu=args.cpu,
        input_method=args.input_method,
        mouse_button=args.mouse_button,
        max_recording_seconds=args.max_recording_seconds,
        watchdog_enabled=not args.no_watchdog,
        watchdog_timeout_seconds=args.watchdog_timeout,
    )

    recorder = Recorder(sample_rate=config.sample_rate)
    transcriber = Transcriber(
        model_size=config.model_size,
        use_cpu=config.use_cpu,
        beam_size=config.beam_size,
    )
    audio_feedback = AudioFeedback()
    session = RecordingSession(recorder, transcriber, audio_feedback, config)
    listener = create_listener(config.input_method, config.hotkey, config.mouse_button)

    print(f"\n[VoiceMate] Pronto. Input: {config.input_method}")
    if config.input_method == "keyboard":
        print(f"[VoiceMate] Hotkey: {config.hotkey}")
    else:
        print(f"[VoiceMate] Botão do mouse: {config.mouse_button}")
    print(f"[VoiceMate] Tempo máximo de gravação: {config.max_recording_seconds}s")
    print("[VoiceMate] Pressione para iniciar/parar a gravação.")
    print("[VoiceMate] Ctrl+C para sair.\n")

    watchdog: Watchdog | None = None
    if config.watchdog_enabled:
        watchdog = Watchdog(timeout_seconds=config.watchdog_timeout_seconds)
        watchdog.start()

    try:
        listener.listen(session.toggle)
    except KeyboardInterrupt:
        if watchdog is not None:
            watchdog.stop()
        print("\n[VoiceMate] Encerrando.")


if __name__ == "__main__":
    main()
