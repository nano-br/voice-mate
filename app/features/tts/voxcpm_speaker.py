from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import TTSConfig
from app.features.tts.audio_player import AudioSink, create_audio_player
from app.i18n import _

_AUTO_SEED_WAV_FILENAME = "voice_seed.wav"
_AUTO_SEED_TEXT_FILENAME = "voice_seed.txt"


class VoxCPMSpeaker:
    """Speaker baseado em VoxCPM2 (OpenBMB).

    Voice design por descrição em parênteses ("(jovem mulher...) texto"),
    streaming via `generate_streaming` quando disponível, fallback para
    geração one-shot. Cancelamento instantâneo via `stop()` (aborta player
    e para de consumir o gerador).

    Modos de consistência de voz (`voice_seed_mode`):
      - "auto": a primeira fala é salva como WAV de referência e usada
        nas falas seguintes (`prompt_wav_path` + `prompt_text`).
      - "fixed": usa um WAV externo fornecido pelo usuário.
      - "off": cada turno re-sorteia a voz (comportamento variado).
    """

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._player: AudioSink = create_audio_player()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._closed = False
        self._load_failed = False
        self._seed_path: str | None = None
        self._seed_text: str | None = None
        self._auto_seed_cache_dir: Path | None = None
        self._configure_voice_seed()
        # Lazy load: o modelo (~5-6 GB de VRAM) só carrega na PRIMEIRA fala.
        # Sessões que usam só o clipboard nunca pagam esse custo de VRAM/tempo.
        self._model: Any = None
        self._sample_rate: int = 16000

    def is_active(self) -> bool:
        return not self._closed and not self._load_failed

    def warmup(self) -> None:
        """Pré-carrega o modelo fora do 1º turno (chamado em background no startup)."""
        if self._closed or self._load_failed:
            return
        self._ensure_model()

    def wait_done(self, timeout: float | None = None) -> bool:  # noqa: ARG002 — speak() já bloqueia
        return True

    def _ensure_model(self) -> bool:
        """Carrega o modelo sob demanda (1ª fala). Retorna False se falhar."""
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        try:
            model = self._load_model()
            self._sample_rate = int(model.tts_model.sample_rate)
            self._model = model
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[VoxCPMSpeaker] ⚠ Falha ao carregar VoxCPM2: {exc}", file=sys.stderr)
            self._load_failed = True
            return False

    def speak(self, text: str) -> None:
        if self._closed or not text.strip():
            return
        if not self._ensure_model():
            return
        self._stop_event.clear()
        collected: list[NDArray[np.float32]] = []
        self._log_vram("antes")
        mode_label = _("cloning") if self._has_seed() else _("voice-design")
        print(_("[VoxCPMSpeaker] 🔊 Synthesizing voice ({mode})...").format(mode=mode_label))
        started_at = time.monotonic()
        try:
            if self._config.streaming and hasattr(self._model, "generate_streaming"):
                self._speak_streaming(text, collected)
            else:
                self._speak_oneshot(text, collected)
            drained = self._player.drain(timeout=self._config.drain_timeout_seconds)
            if not drained:
                print(
                    "[VoxCPMSpeaker] ⚠ drain do AudioPlayer estourou — "
                    "fluxo recuperado, áudio pode ter ficado incompleto.",
                    file=sys.stderr,
                )
                self._player.abort()
            else:
                elapsed = time.monotonic() - started_at
                print(_("[VoxCPMSpeaker] ✓ Playback finished ({elapsed:.1f}s).").format(elapsed=elapsed))
        except Exception as exc:  # noqa: BLE001
            print(f"[VoxCPMSpeaker] erro ao falar: {exc}", file=sys.stderr)
            self._player.abort()
            return
        finally:
            self._release_gpu_memory()
            self._log_vram("depois")
        if self._stop_event.is_set():
            return
        if collected:
            self._post_process_collected(text, collected)

    def stop(self) -> None:
        self._stop_event.set()
        self._player.abort()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._player.close()

    def _configure_voice_seed(self) -> None:
        mode = self._config.voice_seed_mode
        if mode == "off":
            return
        if mode == "fixed":
            path = self._config.voice_seed_path
            text = self._config.voice_seed_text
            if not path or not text:
                raise ValueError("voice_seed_mode='fixed' exige voice_seed_path e voice_seed_text")
            if not Path(path).exists():
                raise FileNotFoundError(f"voice_seed_path não existe: {path}")
            self._seed_path = path
            self._seed_text = text
            return
        # mode == "auto"
        cache_dir = self._resolve_auto_seed_cache_dir()
        self._auto_seed_cache_dir = cache_dir
        cached_wav = cache_dir / _AUTO_SEED_WAV_FILENAME
        cached_txt = cache_dir / _AUTO_SEED_TEXT_FILENAME
        if cached_wav.exists() and cached_txt.exists():
            try:
                self._seed_text = cached_txt.read_text(encoding="utf-8")
                self._seed_path = str(cached_wav)
                print(
                    f"[VoxCPMSpeaker] Auto-seed carregado de {cached_wav}",
                )
            except OSError as exc:
                print(
                    f"[VoxCPMSpeaker] ⚠ Falha ao ler auto-seed: {exc}",
                    file=sys.stderr,
                )

    def _resolve_auto_seed_cache_dir(self) -> Path:
        if self._config.voice_seed_cache_dir:
            return Path(self._config.voice_seed_cache_dir)
        return Path.home() / ".cache" / "voicemate"

    def _load_model(self) -> Any:  # noqa: ANN401 — modelo VoxCPM é dinâmico
        self._maybe_suppress_progress_bar()
        from voxcpm import VoxCPM

        self._warn_if_torch_lacks_cuda()
        kwargs: dict[str, Any] = {
            "load_denoiser": self._config.denoise,
            "optimize": self._config.optimize,
        }
        if self._config.device != "auto":
            kwargs["device"] = self._config.device
        if self._config.cache_dir is not None:
            kwargs["cache_dir"] = self._config.cache_dir
        print("[VoxCPMSpeaker] Carregando VoxCPM2 (primeira execução baixa pesos)...")
        model = VoxCPM.from_pretrained("openbmb/VoxCPM2", **kwargs)
        print("[VoxCPMSpeaker] Modelo pronto.")
        return model

    def _maybe_suppress_progress_bar(self) -> None:
        if self._config.show_progress:
            return
        try:
            import voxcpm.model.voxcpm2 as _vox2
        except ImportError:
            return

        def _identity(iterable: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            return iterable

        _vox2.tqdm = _identity  # type: ignore[assignment]

    def _warn_if_torch_lacks_cuda(self) -> None:
        try:
            import torch
        except ImportError:
            return
        # Em ROCm o torch reporta CUDA disponível (HIP se disfarça de cuda),
        # então isto cobre tanto NVIDIA quanto AMD acelerados.
        if torch.cuda.is_available():
            return
        print(
            "[VoxCPMSpeaker] ⚠ PyTorch sem aceleração de GPU — VoxCPM rodará em CPU "
            "(síntese ~50-100× mais lenta que GPU).",
            file=sys.stderr,
        )
        if self._config.gpu_vendor == "amd":
            print(
                "[VoxCPMSpeaker]   Para a GPU AMD, instale o torch ROCm "
                "(driver Adrenalin >= 26.2.2). Rode: make configure",
                file=sys.stderr,
            )
            print(
                "[VoxCPMSpeaker]   Wheels: https://repo.radeon.com/rocm/windows/ "
                "(torch/torchaudio +rocm, cp312, win_amd64)",
                file=sys.stderr,
            )
        else:
            print(
                "[VoxCPMSpeaker]   Para a GPU NVIDIA, reinstale o PyTorch com build CUDA:",
                file=sys.stderr,
            )
            print(
                "[VoxCPMSpeaker]   poetry run pip install --upgrade --index-url "
                "https://download.pytorch.org/whl/cu128 torch torchaudio",
                file=sys.stderr,
            )

    def _speak_streaming(self, text: str, collected: list[NDArray[np.float32]]) -> None:
        self._player.start(self._sample_rate)
        chunks = self._model.generate_streaming(**self._generate_kwargs(text))
        try:
            for chunk in chunks:
                if self._stop_event.is_set():
                    break
                arr = self._as_float32(chunk)
                collected.append(arr)
                self._player.feed(arr)
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[VoxCPMSpeaker] ⚠ falha ao fechar generator: {exc}",
                        file=sys.stderr,
                    )

    def _speak_oneshot(self, text: str, collected: list[NDArray[np.float32]]) -> None:
        self._player.start(self._sample_rate)
        wav = self._model.generate(**self._generate_kwargs(text))
        arr = self._as_float32(wav)
        collected.append(arr)
        if not self._stop_event.is_set():
            self._player.feed(arr)

    def _has_seed(self) -> bool:
        return self._seed_path is not None and self._seed_text is not None

    def _generate_kwargs(self, text: str) -> dict[str, Any]:
        """Monta os kwargs para `generate`/`generate_streaming`.

        Sem seed → modo voice design: a descrição entra entre parênteses no
        início do texto e o modelo a interpreta como instrução (não fala).

        Com seed (auto carregado ou fixed) → modo cloning: a voz é ditada
        pelo `prompt_wav_path` e o `text` precisa ser **somente a frase a
        ser sintetizada** — qualquer texto extra (incluindo a descrição
        entre parênteses) seria lido em voz alta como conteúdo.
        """
        has_seed = self._has_seed()
        if has_seed:
            synthesis_text = text
        else:
            synthesis_text = f"({self._config.voice_description}) {text}"
        kwargs: dict[str, Any] = {
            "text": synthesis_text,
            "cfg_value": self._config.cfg_value,
            "inference_timesteps": self._config.inference_timesteps,
            "normalize": self._config.normalize,
        }
        if has_seed:
            kwargs["prompt_wav_path"] = self._seed_path
            kwargs["prompt_text"] = self._seed_text
        return kwargs

    def _post_process_collected(
        self,
        text: str,
        collected: list[NDArray[np.float32]],
    ) -> None:
        if self._config.voice_seed_mode == "auto" and self._seed_path is None:
            self._persist_auto_seed(text, collected)
        if self._config.save_audio_dir:
            self._save_audio(collected)

    def _persist_auto_seed(
        self,
        text: str,
        collected: list[NDArray[np.float32]],
    ) -> None:
        if self._auto_seed_cache_dir is None:
            return
        try:
            import soundfile as sf
        except ImportError:
            print(
                "[VoxCPMSpeaker] soundfile não instalado — não foi possível salvar auto-seed.",
                file=sys.stderr,
            )
            return
        try:
            self._auto_seed_cache_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self._auto_seed_cache_dir / _AUTO_SEED_WAV_FILENAME
            txt_path = self._auto_seed_cache_dir / _AUTO_SEED_TEXT_FILENAME
            full = np.concatenate(collected)
            sf.write(str(wav_path), full, self._sample_rate)
            txt_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"[VoxCPMSpeaker] ⚠ Falha ao salvar auto-seed: {exc}", file=sys.stderr)
            return
        self._seed_path = str(wav_path)
        self._seed_text = text
        print(f"[VoxCPMSpeaker] Auto-seed salvo em {wav_path}")

    def _release_gpu_memory(self) -> None:
        try:
            import torch
        except ImportError:
            return
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _log_vram(self, label: str) -> None:
        if not self._config.debug_vram:
            return
        try:
            import torch
        except ImportError:
            return
        if not torch.cuda.is_available():
            return
        allocated = torch.cuda.memory_allocated() / 1024**3
        reserved = torch.cuda.memory_reserved() / 1024**3
        print(f"[VoxCPMSpeaker] VRAM ({label}): alloc={allocated:.2f}GB, reserved={reserved:.2f}GB")

    @staticmethod
    def _as_float32(audio: Any) -> NDArray[np.float32]:  # noqa: ANN401 — chunk vem do SDK
        arr = np.asarray(audio, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.reshape(-1)
        return arr

    def _save_audio(self, chunks: list[NDArray[np.float32]]) -> None:
        try:
            import soundfile as sf
        except ImportError:
            print(
                "[VoxCPMSpeaker] soundfile não instalado — não foi possível salvar áudio.",
                file=sys.stderr,
            )
            return
        directory = Path(self._config.save_audio_dir or ".")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"voxcpm_{int(time.time() * 1000)}.wav"
        full = np.concatenate(chunks)
        sf.write(str(path), full, self._sample_rate)
        print(f"[VoxCPMSpeaker] Áudio salvo em {path}")
