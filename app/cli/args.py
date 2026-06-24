"""argparse layer — keeps `main.py` slim by isolating CLI definitions."""

from __future__ import annotations

import argparse

from app.core.config import DEFAULT_OUTPUT_LANG, DEFAULT_VOICE_DESCRIPTION


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VoiceMate — voz para clipboard ou Claude")
    _add_core_args(parser)
    _add_claude_args(parser)
    _add_tts_args(parser)
    return parser.parse_args(argv)


def _add_core_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default="large-v3-turbo",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"],
        help="Modelo Whisper (padrão: large-v3-turbo)",
    )
    parser.add_argument(
        "--hotkey",
        default="ctrl+alt+v",
        help="Hotkey global do fluxo clipboard (padrão: ctrl+alt+v)",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Forçar uso de CPU em vez de GPU (usa int8)",
    )
    parser.add_argument(
        "--gpu-backend",
        default=None,
        choices=["auto", "nvidia", "amd", "cpu"],
        help=(
            "Vendor da GPU. 'auto' detecta a placa; omitido usa o config salvo "
            "(~/.config/voicemate/config.toml) ou detecta. Sobrescreve o salvo."
        ),
    )
    parser.add_argument(
        "--whisper-backend",
        default=None,
        choices=["faster-whisper", "whispercpp", "openai-whisper"],
        help=(
            "Motor de transcrição. Omitido = automático pelo vendor "
            "(NVIDIA→faster-whisper/CUDA, AMD→whisper.cpp/Vulkan, CPU→faster-whisper)."
        ),
    )
    parser.add_argument(
        "--stt-strategy",
        default=None,
        choices=["auto", "faster-whisper-rocm", "whispercpp", "openai-whisper"],
        help=(
            "Estratégia de STT na AMD (cadeia de fallback). Omitido = config salvo ou "
            "'auto' (faster-whisper-rocm se validado pelo setup → whispercpp → "
            "openai-whisper → CPU). 'faster-whisper-rocm' força o CT2-ROCm na GPU."
        ),
    )
    parser.add_argument(
        "--platform",
        default=None,
        choices=["windows", "linux-x11", "linux-wayland", "wsl2"],
        help="Ambiente de execução. Omitido = config salvo ou auto-detecção.",
    )
    parser.add_argument(
        "--trigger",
        default=None,
        choices=["keyboard-hooks", "pynput", "evdev", "socket"],
        help=(
            "Mecanismo do gatilho. Omitido = default da plataforma "
            "(windows→keyboard-hooks, x11→pynput, wayland→evdev, wsl2→socket/daemon)."
        ),
    )
    parser.add_argument(
        "--daemon-port",
        type=int,
        default=None,
        help="Porta do daemon HTTP local quando trigger=socket (padrão: 47821).",
    )
    parser.add_argument(
        "--whispercpp-mode",
        default="server",
        choices=["server", "cli"],
        help=(
            "Modo do whisper.cpp. 'server' (padrão): sobe o whisper-server.exe uma vez "
            "(modelo quente na VRAM, realtime). 'cli': roda whisper-cli.exe por fala "
            "(recarrega o modelo toda vez — mais lento)."
        ),
    )
    parser.add_argument(
        "--transcription-language",
        default=None,
        choices=["auto", "pt", "en", "es", "fr", "de", "it", "ja", "zh"],
        help=(
            "Idioma fixado na transcrição. Omitido = derivado do --output-lang "
            "(pt-BR→pt, en→en). Fixar dá estabilidade e ainda transcreve termos "
            "estrangeiros embutidos (code-switching). 'auto' detecta por fala "
            "(menos estável em falas curtas)."
        ),
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
    parser.add_argument(
        "--no-listener-refresh",
        action="store_true",
        help="Desabilitar reinstalação periódica do listener",
    )
    parser.add_argument(
        "--listener-refresh-seconds",
        type=int,
        default=60,
        help="Intervalo de reinstalação do listener em segundos (padrão: 60)",
    )
    parser.add_argument(
        "--output-lang",
        default=DEFAULT_OUTPUT_LANG,
        help=(
            "Código BCP-47 do idioma das respostas do Claude (padrão: pt-BR). "
            "Injetado no placeholder {output_lang} do prompt canônico — não "
            "traduz logs nem mensagens do app."
        ),
    )


def _add_claude_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--claude-chat-hotkey",
        default="ctrl+alt+a",
        help="Hotkey global do fluxo voz→Claude→clipboard (padrão: ctrl+alt+a)",
    )
    parser.add_argument(
        "--no-claude-chat",
        action="store_true",
        help="Desabilitar fluxo de chat com Claude (só clipboard)",
    )
    parser.add_argument(
        "--claude-system-prompt",
        default=None,
        help="System prompt customizado para o fluxo Claude (sobrescreve o canônico)",
    )
    parser.add_argument(
        "--claude-no-system-prompt",
        action="store_true",
        help="Desabilita o system prompt (sobrepõe --claude-system-prompt se ambos passados)",
    )
    parser.add_argument(
        "--claude-max-turns",
        type=int,
        default=50,
        help="Máximo de turnos por sessão Claude (padrão: 50)",
    )
    parser.add_argument(
        "--claude-model",
        default="claude-haiku-4-5",
        help=(
            "Modelo Claude (padrão: claude-haiku-4-5 — menor latência p/ voz realtime). "
            "Use claude-sonnet-4-6 para respostas mais elaboradas."
        ),
    )
    parser.add_argument(
        "--claude-effort",
        default="low",
        choices=["low", "medium", "high", "xhigh", "max"],
        help=(
            "Nível de esforço do Claude (padrão: low — prioriza velocidade). "
            "Ignorado em modelos Haiku, que não aceitam o parâmetro."
        ),
    )
    parser.add_argument(
        "--claude-enable-thinking",
        action="store_true",
        help="Habilita extended thinking do Claude (padrão: desabilitado)",
    )
    parser.add_argument(
        "--claude-timeout-seconds",
        type=float,
        default=120.0,
        help="Timeout em segundos para cada turno do Claude (padrão: 120s — defesa em profundidade)",
    )


def _add_tts_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Desabilitar TTS (a resposta do Claude só vai pro clipboard + beep)",
    )
    parser.add_argument(
        "--tts-engine",
        default=None,  # None = usar o engine salvo no setup, ou "omnivoice"
        choices=["omnivoice", "kokoro", "voxcpm", "none"],
        help=(
            "Engine de TTS. 'omnivoice' (padrão): difusão, clona voz, mas pesado. "
            "'kokoro': leve/realtime, GPU baixa, vozes fixas (sem clonagem). "
            "'voxcpm': alternativo (mais pesado). 'none': desliga."
        ),
    )
    parser.add_argument(
        "--tts-kokoro-voice",
        default="pf_dora",
        help=(
            "Voz fixa do Kokoro (só com --tts-engine=kokoro). PT-BR: pf_dora "
            "(feminina), pm_alex / pm_santa (masculinas)."
        ),
    )
    parser.add_argument(
        "--tts-voice",
        default=DEFAULT_VOICE_DESCRIPTION,
        help=(
            "Descrição textual da voz desejada (modo voice design do VoxCPM2). "
            "Aplicada APENAS quando não há seed: em --tts-voice-seed-mode=off "
            "(toda fala) ou na PRIMEIRA fala do --tts-voice-seed-mode=auto. "
            "Em modo cloning (auto após a primeira / fixed), a voz vem do WAV "
            "de seed e essa descrição é ignorada."
        ),
    )
    parser.add_argument(
        "--tts-cfg-value",
        type=float,
        default=2.0,
        help="cfg_value do VoxCPM2 (1.0–3.0, padrão: 2.0)",
    )
    parser.add_argument(
        "--tts-inference-timesteps",
        type=int,
        default=10,
        help="inference_timesteps do VoxCPM2 (4–30, padrão: 10)",
    )
    parser.add_argument(
        "--tts-device",
        default="auto",
        choices=["auto", "cuda", "cpu", "mps"],
        help="Device do VoxCPM2 (padrão: auto)",
    )
    parser.add_argument(
        "--tts-save-dir",
        default=None,
        help="Diretório onde salvar os áudios gerados (padrão: não salvar)",
    )
    parser.add_argument(
        "--tts-no-streaming",
        action="store_true",
        help="Usar geração one-shot em vez de streaming (debug)",
    )
    parser.add_argument(
        "--tts-voice-seed-mode",
        default="off",
        choices=["auto", "fixed", "off"],
        help=(
            "Como o TTS escolhe a voz. "
            "'off' (padrão): voz fixa por DESCRIÇÃO (--tts-voice) — voice design, "
            "sem clonagem; com seed fixo a MESMA voz sai em toda fala (rápido e "
            "consistente). 'fixed': clonagem a partir do WAV em --tts-voice-seed-path "
            "+ --tts-voice-seed-text. 'auto': clonagem auto — a 1ª fala vira referência "
            "e as seguintes a clonam (mais lento; pode variar a voz no meio da resposta)."
        ),
    )
    parser.add_argument(
        "--tts-voice-seed-path",
        default=None,
        help="Caminho do WAV usado como seed quando --tts-voice-seed-mode=fixed",
    )
    parser.add_argument(
        "--tts-voice-seed-text",
        default=None,
        help="Texto correspondente ao WAV de seed (obrigatório com --tts-voice-seed-mode=fixed)",
    )
    parser.add_argument(
        "--tts-voice-seed-cache-dir",
        default=None,
        help="Diretório onde gravar o auto-seed (padrão: ~/.cache/voicemate)",
    )
    parser.add_argument(
        "--tts-reset-seed",
        action="store_true",
        help="Apaga o auto-seed existente antes de subir (força regerar na primeira fala)",
    )
    parser.add_argument(
        "--tts-show-progress",
        action="store_true",
        help="Mostra a barra de progresso interna do VoxCPM2 (padrão: suprimida)",
    )
    parser.add_argument(
        "--tts-drain-timeout-seconds",
        type=float,
        default=60.0,
        help="Timeout do AudioPlayer.drain() em segundos (padrão: 60s)",
    )
    parser.add_argument(
        "--tts-debug-vram",
        action="store_true",
        help="Loga uso de VRAM antes e depois de cada fala (diagnóstico)",
    )
