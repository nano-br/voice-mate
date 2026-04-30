[English](README.md) | **Português**

# VoiceMate

> Pressione um atalho, fale, cole. Transcrição local com Whisper direto pro clipboard.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/types-mypy-blue.svg)](https://mypy-lang.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)

## Por quê

Ditado na nuvem é rápido — até parar de ser. O VoiceMate roda Whisper **localmente** na sua GPU: o áudio nunca sai da sua máquina, sem latência de rede, sem mensalidade, sem trade-off de privacidade. Aperta o atalho, fala, cola onde precisar.

## Recursos

- **Atalho toggle** — aperta uma vez pra começar, aperta de novo pra parar e transcrever
- **Transcrição local** — `faster-whisper` (backend CTranslate2, 4–8× mais rápido que PyTorch)
- **GPU acelerada** — CUDA float16 por padrão, com fallback CPU int8
- **Listener auto-curativo** — reinstala o hook globalmente em ciclos curtos para se recuperar de remoção silenciosa do hook pelo Windows sob carga
- **Watchdog** — monitor de saúde no nível do processo com auto-restart em travamentos
- **Limite de gravação configurável** — proteção contra sessões esquecidas (padrão: 10 min)
- **Feedback sonoro** — beeps de início, aviso e conclusão
- **Suporte a mouse** — use um botão lateral em vez do teclado, se preferir

## Requisitos

- Windows 10/11 (foco principal — Linux/macOS podem funcionar mas não são o alvo)
- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation)
- GPU NVIDIA com CUDA (opcional, mas recomendado)

## Instalação

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup_env
```

## Uso

```bash
make run
```

Atalho padrão: **Ctrl+Alt+V**

1. Pressione `Ctrl+Alt+V` para iniciar a gravação (beep de início)
2. Fale normalmente
3. Pressione `Ctrl+Alt+V` novamente para parar
4. A transcrição vai pro clipboard (beep duplo)
5. Cole com `Ctrl+V` onde quiser

### Opções

```bash
# Modelo específico
poetry run voice-mate --model medium

# Atalho customizado
poetry run voice-mate --hotkey "ctrl+shift+r"

# Forçar CPU (sem GPU disponível)
poetry run voice-mate --cpu

# Usar botão lateral do mouse
poetry run voice-mate --input-method mouse --mouse-button x

# Ajustar watchdog e keepalive do listener
poetry run voice-mate --listener-refresh-seconds 30 --watchdog-timeout 60
```

### Modelos

| Modelo             | VRAM (GPU) | Velocidade   | Qualidade  |
| ------------------ | ---------- | ------------ | ---------- |
| `tiny`             | ~75 MB     | Muito rápida | Básica     |
| `base`             | ~140 MB    | Rápida       | Boa        |
| `small`            | ~460 MB    | Moderada     | Muito boa  |
| `medium`           | ~1.0 GB    | Moderada     | Ótima      |
| `large-v3-turbo`   | ~1.5 GB    | Rápida       | Excelente  |
| `large-v3`         | ~3.0 GB    | Lenta        | Máxima     |

Padrão é `large-v3-turbo` — melhor equilíbrio entre velocidade e qualidade, especialmente para áudio com mistura de idiomas.

## Makefile

| Comando            | Descrição                                          |
| ------------------ | -------------------------------------------------- |
| `make setup_env`   | Instala dependências via Poetry                    |
| `make run`         | Executa com modelo padrão (`large-v3-turbo`)       |
| `make run-large`   | Executa com `large-v3`                             |
| `make run-turbo`   | Executa com `large-v3-turbo`                       |
| `make format`      | Formata código com Ruff                            |
| `make lint`        | Lint com Ruff + type-check com Mypy                |
| `make test`        | Executa testes com pytest                          |
| `make clean`       | Limpa caches                                       |

## Arquitetura

```
app/
├── main.py                          # Entry point + CLI
├── core/
│   └── config.py                    # Dataclass de configuração
└── services/
    ├── recorder.py                  # Captura do microfone (sounddevice)
    ├── transcriber.py               # Inferência Whisper (faster-whisper)
    ├── audio_feedback.py            # Beeps cross-platform
    ├── recording_session.py         # Ciclo da sessão + timeout máximo
    ├── input_listener.py            # Abstração de trigger (teclado/mouse)
    ├── listener_keepalive.py        # Reinstalação periódica do hook (fix Windows)
    └── watchdog.py                  # Monitor de saúde do processo
```

### Por que o listener-keepalive?

No Windows, hooks de baixo nível (`WH_KEYBOARD_LL` / `WH_MOUSE_LL`) usados pelas libs de hotkey global são **removidos silenciosamente** pelo SO se o callback exceder o `LowLevelHooksTimeout` (máx 1000 ms no Win10+). Sob CPU em carga isso acontece sem aviso ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)). O VoiceMate re-registra o atalho a cada 60 s por padrão — então mesmo que o SO tenha matado o hook, o próximo ciclo reinstala.

## Stack

- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — Whisper otimizado com CTranslate2
- **[sounddevice](https://python-sounddevice.readthedocs.io/)** — captura do microfone
- **[keyboard](https://github.com/boppreh/keyboard)** / **[mouse](https://github.com/boppreh/mouse)** — hooks globais
- **[pyperclip](https://github.com/asweigart/pyperclip)** — acesso ao clipboard

## Contribuindo

Issues e PRs são bem-vindos. Rode `make all` (format + lint + test) antes de abrir um PR.

## Licença

[MIT](LICENSE) © Álli Terhorst

Parte da [NanoBR](https://github.com/nano-br) — utilitários open-source para o dia a dia.
