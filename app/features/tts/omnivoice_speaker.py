"""Speaker baseado em OmniVoice (k2-fsa) — TTS multilíngue leve (0.6B, 24 kHz).

Substitui o VoxCPM2 como engine padrão: mais leve (~4 GB bf16) e rápido. A API é
`OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=..., dtype=...)` +
`model.generate(text=..., ref_audio=..., ref_text=...)`, que retorna uma lista de
`np.ndarray` a 24 kHz.

OmniVoice não tem streaming nativo: a granularidade realtime vem do handler, que
chama `speak()` **frase a frase** (ver `claude.chat_handler`). Cada `speak()` gera
o áudio de uma frase e o reproduz pelo `AudioPlayer` (fila), com cancelamento
instantâneo via `stop()`.

Consistência de voz reaproveita o mesmo esquema do VoxCPM (`voice_seed_mode`):
  - "auto": a primeira fala vira referência (WAV+texto) e é clonada nas seguintes.
  - "fixed": usa um WAV externo do usuário como referência.
  - "off": cada turno usa a voz default do modelo.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import TTSConfig
from app.features.tts.audio_player import AudioPlayer

# Nomes específicos do OmniVoice p/ não colidir com o seed do VoxCPM (16 kHz) —
# o OmniVoice opera a 24 kHz e usa outro modelo de referência.
_AUTO_SEED_WAV_FILENAME = "voice_seed_omnivoice.wav"
_AUTO_SEED_TEXT_FILENAME = "voice_seed_omnivoice.txt"
_MODEL_ID = "k2-fsa/OmniVoice"
_SAMPLE_RATE = 24000
_FADE_MS = 6  # fade-in/out curto nas bordas de cada frase — mata cliques de descontinuidade

# Mapeia o código do config (output_lang derivado) → nome de idioma do OmniVoice.
# "auto" (ou desconhecido) → não passa language (o modelo detecta pelo texto).
_OMNI_LANG_NAMES = {
    "pt": "Portuguese",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "zh": "Chinese",
}


class OmniVoiceSpeaker:
    """Speaker OmniVoice — lazy load, geração one-shot por frase, streaming via AudioPlayer."""

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._player = AudioPlayer()
        self._stop_event = threading.Event()
        self._load_lock = threading.Lock()  # serializa o load (warmup vs 1ª fala)
        self._closed = False
        self._load_failed = False
        self._seed_path: str | None = None
        self._seed_text: str | None = None
        self._auto_seed_cache_dir: Path | None = None
        self._configure_voice_seed()
        # Otimizações de ROCm (TunableOp + MIOpen FAST) precisam das env vars setadas
        # ANTES de o torch importar — por isso aqui no __init__, não no _load_model.
        self._configure_rocm_env()
        # Lazy load: o modelo (~4 GB bf16) só carrega na PRIMEIRA fala — sessões
        # que usam só o clipboard não pagam o custo de VRAM/tempo.
        self._model: Any = None
        self._sample_rate = _SAMPLE_RATE

    def is_active(self) -> bool:
        return not self._closed and not self._load_failed

    def speak(self, text: str) -> None:
        """Sintetiza UMA frase e a enfileira no player persistente (não bloqueia até o fim).

        Não fecha/reabre o stream nem drena por frase — o player segue tocando enquanto
        a próxima frase é gerada (pipeline). O handler chama `wait_done()` no fim do turno.
        """
        if self._closed or not text.strip():
            return
        if not self._ensure_model():
            return
        self._stop_event.clear()
        try:
            self._player.ensure_started(self._sample_rate)
            audio = self._model.generate(**self._generate_kwargs(text))
            arr = self._finalize_audio(self._as_float32(audio))
            if self._stop_event.is_set() or arr.size == 0:
                return
            self._player.feed(arr)
            self._maybe_persist_auto_seed(text, arr)
        except Exception as exc:  # noqa: BLE001
            print(f"[OmniVoiceSpeaker] erro ao falar: {exc}", file=sys.stderr)
            self._player.abort()

    def wait_done(self, timeout: float | None = None) -> bool:
        """Espera a fila de áudio esvaziar (fim do turno) sem fechar o stream."""
        limit = timeout if timeout is not None else self._config.drain_timeout_seconds
        ok = self._player.drain(timeout=limit)
        if not ok:
            print(
                "[OmniVoiceSpeaker] ⚠ drain do AudioPlayer estourou — áudio pode ter ficado incompleto.",
                file=sys.stderr,
            )
            self._player.abort()
        self._release_gpu_memory()
        self._log_vram("fim do turno")
        return ok

    def warmup(self) -> None:
        """Carrega o modelo e tuna os kernels (TunableOp) fora do 1º turno real.

        Chamado em background no startup (ver main) — a 1ª síntese paga o tuning
        dos GEMMs na RDNA4 (segundos, uma vez por processo; o resultado já fica
        persistido em ~/.cache/voicemate). Sem isso, esse custo cairia na 1ª frase
        falada da conversa. Usa generate() direto (não speak()) p/ NÃO virar seed.
        """
        if self._closed or self._load_failed:
            return
        if not self._ensure_model():
            return
        try:
            print("[OmniVoiceSpeaker] Warmup (tuna kernels ROCm; só a 1ª vez é lenta)...")
            self._model.generate(**self._generate_kwargs("Olá."))
            print("[OmniVoiceSpeaker] Warmup concluído — síntese pronta (realtime).")
        except Exception as exc:  # noqa: BLE001 — warmup é otimização, nunca quebra a fala
            print(f"[OmniVoiceSpeaker] ⚠ warmup falhou (seguindo): {exc}", file=sys.stderr)
        finally:
            self._release_gpu_memory()

    def stop(self) -> None:
        self._stop_event.set()
        self._player.abort()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._player.close()

    # ── modelo ────────────────────────────────────────────────────────────────

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        with self._load_lock:  # warmup (background) e a 1ª fala podem competir
            if self._model is not None:
                return True
            if self._load_failed:
                return False
            try:
                self._model = self._load_model()
                return True
            except Exception as exc:  # noqa: BLE001
                print(f"[OmniVoiceSpeaker] ⚠ Falha ao carregar OmniVoice: {exc}", file=sys.stderr)
                self._load_failed = True
                return False

    def _load_model(self) -> Any:  # noqa: ANN401 — modelo OmniVoice é dinâmico
        import torch
        from omnivoice import OmniVoice

        self._warn_if_torch_lacks_gpu(torch)
        if getattr(torch.version, "hip", None):
            print(
                "[OmniVoiceSpeaker] ROCm — TunableOp ativo: cada comprimento de frase novo tem "
                "um pequeno ajuste (~1-2s) na 1ª vez; depois fica realtime (RTF ~0.7)."
            )
        device_map, dtype = self._resolve_device_and_dtype(torch)
        print(f"[OmniVoiceSpeaker] Carregando OmniVoice ({device_map}, primeira execução baixa pesos)...")
        model = OmniVoice.from_pretrained(_MODEL_ID, device_map=device_map, dtype=dtype)
        print("[OmniVoiceSpeaker] Modelo pronto.")
        return model

    def _configure_rocm_env(self) -> None:
        """Configura ROCm (MIOpen + TunableOp) via env vars — chave p/ realtime na AMD.

        **MIOpen `FIND_MODE=FAST`** é a correção principal: sem ele, as convoluções do
        codec de áudio caem no solver `GemmFwdRest` com workspace=0 (bug do PyTorch
        ROCm/Windows) — inundando o console com warnings e degradando sob contenção.
        FAST usa heurística, **zera os warnings** e dá síntese estável (~RTF 0.4 em
        frases variadas, sem freezes por comprimento). `MIOPEN_USER_DB_PATH` persiste
        a find-db p/ não re-procurar a cada run.

        TunableOp (complementar) tuna os GEMMs do transformer; limites baixos
        (DURATION/ITERATIONS) evitam congelar a fala em comprimentos novos.

        Precisa rodar ANTES de o torch importar (daí o __init__). `setdefault` respeita
        overrides do usuário. Só na AMD (gpu_vendor).
        """
        if self._config.gpu_vendor != "amd":
            return
        cache = self._resolve_auto_seed_cache_dir()
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        # MIOpen — correção do workspace=0/GemmFwdRest (convoluções do codec).
        os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")
        os.environ.setdefault("MIOPEN_USER_DB_PATH", str(cache / "miopen"))
        # TunableOp — GEMMs do transformer; limites baixos = sem freeze por comprimento.
        os.environ.setdefault("PYTORCH_TUNABLEOP_ENABLED", "1")
        os.environ.setdefault("PYTORCH_TUNABLEOP_FILENAME", str(cache / "tunableop.csv"))
        os.environ.setdefault("PYTORCH_TUNABLEOP_MAX_TUNING_DURATION_MS", "15")
        os.environ.setdefault("PYTORCH_TUNABLEOP_MAX_TUNING_ITERATIONS", "5")

    def _resolve_device_and_dtype(self, torch: Any) -> tuple[str, Any]:  # noqa: ANN401
        """Mapeia config.device → device_map do OmniVoice + dtype (bf16 na GPU)."""
        configured = self._config.device
        if configured == "auto":
            device_map = "cuda:0" if torch.cuda.is_available() else "cpu"
        elif configured == "cuda":
            device_map = "cuda:0"
        else:
            device_map = configured  # "cpu" | "mps"
        # bf16 = melhor relação qualidade/velocidade/VRAM na GPU; fp32 na CPU.
        accelerated = device_map.startswith("cuda") or device_map == "mps"
        dtype = torch.bfloat16 if accelerated else torch.float32
        return device_map, dtype

    def _warn_if_torch_lacks_gpu(self, torch: Any) -> None:  # noqa: ANN401
        # Em ROCm o torch reporta CUDA disponível (HIP se disfarça de cuda).
        if torch.cuda.is_available() or self._config.device == "cpu":
            return
        print(
            "[OmniVoiceSpeaker] ⚠ PyTorch sem aceleração de GPU — OmniVoice rodará em CPU "
            "(síntese bem mais lenta). Rode `make configure` para instalar o torch da sua GPU.",
            file=sys.stderr,
        )

    # ── geração / voz ───────────────────────────────────────────────────────────

    def _generate_kwargs(self, text: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"text": text}
        language = _OMNI_LANG_NAMES.get(self._config.language)
        if language:  # "auto"/desconhecido → omite (OmniVoice detecta pelo texto)
            kwargs["language"] = language
        if self._has_seed():  # "fixed"/"auto"(com seed) → clonagem por referência
            kwargs["ref_audio"] = self._seed_path
            kwargs["ref_text"] = self._seed_text
        # default "off": plain (sem ref) — sem clonagem. NÃO usamos voice-design
        # (`instruct`) porque o pool de multiprocessing dele trava no Windows/ROCm.
        return kwargs

    def _finalize_audio(self, arr: NDArray[np.float32]) -> NDArray[np.float32]:
        """Clipa para [-1,1] e aplica fade-in/out curto — remove cliques nas bordas."""
        if arr.size == 0:
            return arr
        np.clip(arr, -1.0, 1.0, out=arr)
        n = min(int(_SAMPLE_RATE * _FADE_MS / 1000), arr.size // 2)
        if n > 0:
            ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
            arr[:n] *= ramp
            arr[-n:] *= ramp[::-1]
        return arr

    def _has_seed(self) -> bool:
        return self._seed_path is not None and self._seed_text is not None

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
                print(f"[OmniVoiceSpeaker] Auto-seed carregado de {cached_wav}")
            except OSError as exc:
                print(f"[OmniVoiceSpeaker] ⚠ Falha ao ler auto-seed: {exc}", file=sys.stderr)

    def _resolve_auto_seed_cache_dir(self) -> Path:
        if self._config.voice_seed_cache_dir:
            return Path(self._config.voice_seed_cache_dir)
        return Path.home() / ".cache" / "voicemate"

    def _maybe_persist_auto_seed(self, text: str, arr: NDArray[np.float32]) -> None:
        # Só nos modos de clonagem opt-in; o default ("off"/voice-design) nunca passa aqui.
        if self._config.voice_seed_mode == "auto" and self._seed_path is None:
            self._persist_auto_seed(text, [arr])
        if self._config.save_audio_dir:
            self._save_audio([arr])

    def _persist_auto_seed(self, text: str, collected: list[NDArray[np.float32]]) -> None:
        if self._auto_seed_cache_dir is None:
            return
        try:
            import soundfile as sf
        except ImportError:
            print("[OmniVoiceSpeaker] soundfile não instalado — não foi possível salvar auto-seed.", file=sys.stderr)
            return
        try:
            self._auto_seed_cache_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self._auto_seed_cache_dir / _AUTO_SEED_WAV_FILENAME
            txt_path = self._auto_seed_cache_dir / _AUTO_SEED_TEXT_FILENAME
            sf.write(str(wav_path), np.concatenate(collected), self._sample_rate)
            txt_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"[OmniVoiceSpeaker] ⚠ Falha ao salvar auto-seed: {exc}", file=sys.stderr)
            return
        self._seed_path = str(wav_path)
        self._seed_text = text
        print(f"[OmniVoiceSpeaker] Auto-seed salvo em {wav_path}")

    def _save_audio(self, chunks: list[NDArray[np.float32]]) -> None:
        try:
            import soundfile as sf
        except ImportError:
            print("[OmniVoiceSpeaker] soundfile não instalado — não foi possível salvar áudio.", file=sys.stderr)
            return
        directory = Path(self._config.save_audio_dir or ".")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"omnivoice_{int(time.time() * 1000)}.wav"
        sf.write(str(path), np.concatenate(chunks), self._sample_rate)
        print(f"[OmniVoiceSpeaker] Áudio salvo em {path}")

    # ── utilidades ──────────────────────────────────────────────────────────────

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
        print(f"[OmniVoiceSpeaker] VRAM ({label}): alloc={allocated:.2f}GB, reserved={reserved:.2f}GB")

    @staticmethod
    def _as_float32(audio: Any) -> NDArray[np.float32]:  # noqa: ANN401 — saída do SDK
        """Normaliza a saída do OmniVoice (lista de np.ndarray a 24 kHz) em 1 vetor mono."""
        items = audio if isinstance(audio, (list, tuple)) else [audio]
        arrays = [OmniVoiceSpeaker._coerce(a) for a in items]
        arrays = [a for a in arrays if a.size > 0]
        if not arrays:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(arrays)

    @staticmethod
    def _coerce(a: Any) -> NDArray[np.float32]:  # noqa: ANN401
        if hasattr(a, "detach"):  # tensor torch → numpy
            a = a.detach().to("cpu").numpy()
        arr = np.asarray(a, dtype=np.float32)
        return arr.reshape(-1) if arr.ndim > 1 else arr
