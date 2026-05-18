[English](README.md) | **Português**

# VoiceMate

> Pressione um atalho, fale, cole. Transcrição local com Whisper direto pro clipboard — ou roteie pelo Claude pra um turno rápido com IA.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/types-mypy-blue.svg)](https://mypy-lang.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)

## Por quê

Ditado na nuvem é rápido — até parar de ser. O VoiceMate roda Whisper **localmente** na sua GPU: o áudio nunca sai da sua máquina, sem latência de rede, sem mensalidade, sem trade-off de privacidade. Aperta o atalho, fala, cola onde precisar.

## Recursos

- **Atalho toggle** — aperta uma vez pra começar, aperta de novo pra parar e transcrever
- **Dois fluxos, um microfone** — `Ctrl+Alt+V` joga a transcrição no clipboard; `Ctrl+Alt+A` envia pro Claude (multi-turn) e joga a resposta da IA no clipboard
- **Transcrição local** — `faster-whisper` (backend CTranslate2, 4–8× mais rápido que PyTorch)
- **GPU acelerada** — CUDA float16 por padrão, com fallback CPU int8
- **Clipboard duplo com Win+V** — o fluxo IA copia a transcrição primeiro e depois a resposta, então o histórico do clipboard do Windows mostra os dois lado a lado pra revisão
- **O atalho de parar decide o destino** — começa com qualquer atalho; o atalho que você usar pra *parar* escolhe o handler (clipboard ou Claude)
- **Cancelamento no voo** — apertar qualquer atalho enquanto a IA tá respondendo cancela a chamada e já inicia uma nova gravação, mantendo o contexto da conversa
- **Listener auto-curativo** — reinstala o hook globalmente em ciclos curtos para se recuperar de remoção silenciosa do hook pelo Windows sob carga
- **Watchdog** — monitor de saúde no nível do processo com auto-restart em travamentos
- **Limite de gravação configurável** — proteção contra sessões esquecidas (padrão: 10 min)
- **Feedback sonoro** — beeps distintos pra início, aviso, transcrição concluída e resposta da IA pronta
- **Suporte a mouse** — use um botão lateral em vez do teclado, se preferir (só fluxo clipboard)

## Requisitos

- Windows 10/11 (foco principal — Linux/macOS podem funcionar mas não são o alvo)
- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation)
- GPU NVIDIA com CUDA (opcional, mas recomendado)
- **Só pra o fluxo Claude:** Node.js 18+ e o [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) autenticado localmente

## Instalação

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup_env
```

### Configurando o Claude Code (opcional — só pra o fluxo de IA)

Se você só quer o fluxo clipboard (`Ctrl+Alt+V`), pode pular essa seção e rodar com `--no-claude-chat`.

Pra o fluxo IA (`Ctrl+Alt+A`), o VoiceMate conversa com o Claude através do `claude-agent-sdk`, que **reusa o `claude` CLI local e as credenciais dele** — não precisa de API key separada.

1. **Instale Node.js 18+** (pode pular se já tem). Baixe em [nodejs.org](https://nodejs.org/) ou use um gerenciador como `nvm-windows` / `fnm`.

2. **Instale o Claude Code globalmente:**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

3. **Autentique.** Rode o CLI uma vez e siga o login interativo (abre o navegador):
   ```bash
   claude
   ```
   Escolha o método de auth que você usa (conta Anthropic ou Claude Pro/Max). Digite `/exit` pra sair quando estiver dentro — as credenciais ficam salvas localmente.

4. **Confirme que está funcionando:**
   ```bash
   claude --version
   claude -p "ping"
   ```
   Se o `ping` voltar uma resposta do Claude, está pronto.

Uma vez autenticado, o fluxo de IA do VoiceMate detecta automaticamente quando você roda `poetry run voice-mate`. Se o `claude` estiver ausente ou deslogado, o fluxo de IA é silenciosamente desativado e o fluxo clipboard segue funcionando.

## Uso

```bash
make run
```

Atalhos padrão:

- **`Ctrl+Alt+V`** — fluxo clipboard (transcrição → clipboard)
- **`Ctrl+Alt+A`** — fluxo Claude (transcrição → Claude → resposta da IA no clipboard)

### Fluxo clipboard

1. Pressione `Ctrl+Alt+V` para iniciar a gravação (beep de início)
2. Fale normalmente
3. Pressione `Ctrl+Alt+V` novamente para parar
4. A transcrição vai pro clipboard (beep duplo)
5. Cole com `Ctrl+V` onde quiser

### Fluxo Claude (multi-turn)

1. Pressione `Ctrl+Alt+A` pra iniciar a gravação
2. Fale o seu pedido
3. Pressione `Ctrl+Alt+A` novamente pra parar — o VoiceMate transcreve, copia a transcrição pro clipboard, e envia pro Claude
4. A resposta da IA substitui o conteúdo do clipboard e toca uma tríade ascendente (C5–E5–G5)
5. Pressione `Ctrl+Alt+A` de novo pra fazer um follow-up — a conversa continua na mesma sessão

**O atalho de parar decide o destino:** você pode começar com `Ctrl+Alt+V` e parar com `Ctrl+Alt+A` (ou vice-versa). O atalho que você usa pra *parar* escolhe o handler.

**Cancelamento enquanto o Claude pensa:** apertar qualquer atalho enquanto a IA tá respondendo cancela a chamada e já inicia uma nova gravação. O contexto da conversa é preservado.

**Histórico Win+V:** como tanto a transcrição quanto a resposta da IA passam pelo clipboard, o histórico do Windows (`Win+V`) mostra os dois — útil quando você quer comparar o que falou com o que o Claude respondeu.

### Opções

```bash
# Modelo Whisper específico
poetry run voice-mate --model medium

# Atalhos customizados
poetry run voice-mate --hotkey "ctrl+shift+r" --claude-chat-hotkey "ctrl+shift+c"

# Desabilita o fluxo Claude (só clipboard)
poetry run voice-mate --no-claude-chat

# System prompt pro Claude
poetry run voice-mate --claude-system-prompt "Você é um assistente de produtividade conciso."

# Limita os turnos da sessão multi-turn
poetry run voice-mate --claude-max-turns 20

# Forçar CPU (sem GPU disponível)
poetry run voice-mate --cpu

# Usar botão lateral do mouse (só fluxo clipboard)
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
├── main.py                          # Entry point + CLI + montagem dos fluxos
├── core/
│   └── config.py                    # Dataclass de configuração + FlowConfig
└── services/
    ├── recorder.py                  # Captura do microfone (sounddevice)
    ├── transcriber.py               # Inferência Whisper (faster-whisper)
    ├── audio_feedback.py            # Beeps cross-platform
    ├── recording_session.py         # Máquina de estado: idle → recording → processing
    ├── transcription_handler.py     # Protocol + ClipboardHandler
    ├── claude_chat_handler.py       # Fluxo Claude: envio + clipboard duplo + cancel
    ├── claude_runtime.py            # Ponte sync ↔ asyncio para claude-agent-sdk
    ├── input_listener.py            # Abstração de trigger (teclado/mouse)
    ├── multi_hotkey_listener.py     # Múltiplos atalhos globais com callbacks distintos
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
- **[claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python)** — fluxo Claude, em cima do `claude` CLI local

## Contribuindo

Issues e PRs são bem-vindos. Rode `make all` (format + lint + test) antes de abrir um PR.

## Licença

[MIT](LICENSE) © Álli Terhorst

Parte da [NanoBR](https://github.com/nano-br) — utilitários open-source para o dia a dia.
