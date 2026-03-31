# VoiceMate

Assistente pessoal por voz para Windows — grava, transcreve com Whisper e copia direto para o clipboard.

Pressione o hotkey, fale, pressione novamente. O texto aparece no seu clipboard, pronto para colar.

## Requisitos

- Windows 10/11
- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation)
- GPU NVIDIA com CUDA (opcional, mas recomendado)

## Instalacao

```bash
git clone https://github.com/seu-usuario/voice-mate.git
cd voice-mate
make setup_env
```

## Uso

```bash
make run
```

Hotkey padrao: **Ctrl+Alt+V**

1. Pressione `Ctrl+Alt+V` para iniciar a gravacao (beep de confirmacao)
2. Fale normalmente
3. Pressione `Ctrl+Alt+V` novamente para parar
4. O texto transcrito e copiado para o clipboard (beep duplo de confirmacao)
5. Cole com `Ctrl+V` onde quiser

### Opcoes

```bash
# Usar modelo especifico
poetry run voice-mate --model medium

# Alterar hotkey
poetry run voice-mate --hotkey "ctrl+shift+r"

# Forcar uso de CPU
poetry run voice-mate --cpu
```

### Modelos disponiveis

| Modelo | VRAM (GPU) | Velocidade | Qualidade |
|--------|-----------|------------|-----------|
| `tiny` | ~75 MB | Muito rapida | Basica |
| `base` | ~140 MB | Rapida | Boa |
| `small` | ~460 MB | Moderada | Muito boa |
| `medium` | ~1.0 GB | Moderada | Otima |
| `large-v3-turbo` | ~1.5 GB | Rapida | Excelente |
| `large-v3` | ~3.0 GB | Lenta | Maxima |

O padrao e `large-v3-turbo` — melhor equilibrio entre velocidade e qualidade, especialmente para transcrever audio com mistura de idiomas.

## Makefile

| Comando | Descricao |
|---------|-----------|
| `make setup_env` | Instala dependencias via Poetry |
| `make run` | Executa com modelo padrao (large-v3-turbo) |
| `make run-large` | Executa com large-v3 |
| `make run-turbo` | Executa com large-v3-turbo |
| `make format` | Formata codigo com Ruff |
| `make lint` | Verifica codigo com Ruff + Mypy |
| `make test` | Executa testes com pytest |
| `make clean` | Limpa caches |

## Stack

- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — Whisper otimizado com CTranslate2 (4-8x mais rapido que PyTorch)
- **[sounddevice](https://python-sounddevice.readthedocs.io/)** — Captura de audio do microfone
- **[keyboard](https://github.com/boppreh/keyboard)** — Hotkey global no Windows
- **[pyperclip](https://github.com/asweigart/pyperclip)** — Acesso ao clipboard

## Estrutura

```
voice-mate/
├── app/
│   ├── main.py              # Entry point, hotkey, fluxo principal
│   ├── core/
│   │   └── config.py        # Configuracoes (modelo, hotkey, sample rate)
│   └── services/
│       ├── recorder.py      # Captura de audio (toggle start/stop)
│       └── transcriber.py   # Transcricao com faster-whisper
├── Makefile
└── pyproject.toml
```
