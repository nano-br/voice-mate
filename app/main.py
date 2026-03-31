import argparse
import threading
import winsound

import keyboard
import numpy as np
import pyperclip
import sounddevice as sd
from numpy.typing import NDArray

from app.core.config import Config
from app.services.recorder import Recorder
from app.services.transcriber import Transcriber


def _stop_and_transcribe(
    recorder: Recorder,
    transcriber: Transcriber,
    sample_rate: int,
) -> None:
    audio: NDArray[np.float32] | None = recorder.stop()
    if audio is None:
        print("[VoiceMate] Nenhum áudio capturado.")
        return

    duration = len(audio) / sample_rate
    print(f"[VoiceMate] ⏳ Transcrevendo {duration:.1f}s de áudio...")

    text = transcriber.transcribe(audio)
    if text:
        pyperclip.copy(text)
        winsound.Beep(600, 100)
        winsound.Beep(900, 150)
        preview = text[:100] + ("..." if len(text) > 100 else "")
        print(f"[VoiceMate] ✓ Copiado: {preview}")
    else:
        print("[VoiceMate] Nenhuma fala detectada.")


def _on_hotkey(recorder: Recorder, transcriber: Transcriber, sample_rate: int) -> None:
    if recorder.start():
        winsound.Beep(800, 150)
        print("[VoiceMate] 🎙  Gravando... (pressione o hotkey novamente para parar)")
    else:
        threading.Thread(
            target=_stop_and_transcribe,
            args=(recorder, transcriber, sample_rate),
            daemon=True,
        ).start()


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
    args = parser.parse_args()

    config = Config(model_size=args.model, hotkey=args.hotkey, use_cpu=args.cpu)
    recorder = Recorder(sample_rate=config.sample_rate)
    transcriber = Transcriber(
        model_size=config.model_size,
        use_cpu=config.use_cpu,
        beam_size=config.beam_size,
    )

    print(f"\n[VoiceMate] Pronto. Hotkey: {config.hotkey}")
    print("[VoiceMate] Pressione o hotkey para iniciar/parar a gravação.")
    print("[VoiceMate] Ctrl+C para sair.\n")

    keyboard.add_hotkey(
        config.hotkey,
        lambda: _on_hotkey(recorder, transcriber, config.sample_rate),
    )

    with sd.InputStream(
        samplerate=config.sample_rate,
        channels=1,
        dtype="float32",
        callback=recorder.callback,
    ):
        try:
            keyboard.wait()
        except KeyboardInterrupt:
            print("\n[VoiceMate] Encerrando.")


if __name__ == "__main__":
    main()
