[English](README.md) | **Português**

# VoiceMate

> Pressione um atalho, fale, cole. Transcrição local com Whisper direto no clipboard — ou passe pelo Claude e ouça a resposta da IA em voz alta.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/types-mypy-blue.svg)](https://mypy-lang.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)

## Por quê

Ditado na nuvem é rápido — até deixar de ser. O VoiceMate roda o Whisper **localmente** na sua GPU: o áudio nunca sai da sua máquina, sem latência de rede, sem mensalidade e sem abrir mão da privacidade. Você pressiona o atalho, fala e cola onde precisar.

## Recursos

- **Atalho em modo toggle** — pressione uma vez para começar, pressione de novo para parar e transcrever
- **Dois fluxos, um microfone** — `Ctrl+Alt+V` leva a transcrição para o clipboard; `Ctrl+Alt+A` manda o texto para o Claude (conversa multi-turn) e deixa a resposta da IA no clipboard
- **Transcrição local** — `faster-whisper` (backend CTranslate2, 4–8× mais rápido que PyTorch)
- **GPU acelerada** — CUDA float16 por padrão, com fallback CPU int8
- **TTS plugável** — a resposta do Claude é lida em voz alta com [VoxCPM2](https://github.com/OpenBMB/VoxCPM) (2B params, voice design por descrição em PT-BR, streaming). Arquitetura preparada para trocar facilmente por outras libs
- **Histórico de clipboard via Win+V** — o fluxo de IA copia primeiro a transcrição e depois a resposta, então o histórico do Windows mostra as duas lado a lado para você conferir
- **O atalho usado para parar decide o destino** — você pode começar com qualquer atalho; quem decide para onde o texto vai é o atalho que você pressiona para parar
- **Cancelamento em pleno voo** — pressionar qualquer atalho enquanto a IA está respondendo (ou o TTS está falando) cancela na hora e já começa uma nova gravação, mantendo o contexto da conversa
- **Listener auto-recuperável** — reinstala o hook global periodicamente para se recuperar da remoção silenciosa que o Windows faz com a máquina sob carga
- **Watchdog** — monitor de saúde do processo que reinicia automaticamente em caso de travamento
- **Limite de gravação configurável** — protege contra sessões esquecidas (padrão: 10 min)
- **Feedback sonoro** — beeps diferentes para início, aviso, transcrição concluída e resposta da IA pronta
- **Suporte a mouse** — dá para usar um botão lateral no lugar do teclado, se preferir (só no fluxo clipboard)

## Requisitos

- Windows 10/11 (foco principal — Linux/macOS podem funcionar, mas não são o alvo)
- Python 3.12 (o fluxo TTS via VoxCPM2 ainda não suporta 3.13)
- [Poetry](https://python-poetry.org/docs/#installation)
- GPU NVIDIA com CUDA (opcional, mas recomendado — necessário se você quiser usar TTS com qualidade)
- **Só para o fluxo Claude:** Node.js 18+ e o [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) autenticado localmente

## Instalação

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup_env
```

### Configurando o Claude Code (opcional — só para o fluxo de IA)

Se você só quer o fluxo clipboard (`Ctrl+Alt+V`), pode pular esta seção e rodar com `--no-claude-chat`.

Para o fluxo de IA (`Ctrl+Alt+A`), o VoiceMate conversa com o Claude pelo `claude-agent-sdk`, que **reaproveita o `claude` CLI local e as credenciais que já estão salvas nele** — você não precisa de uma API key separada.

1. **Instale o Node.js 18+** (pode pular se você já tem). Baixe em [nodejs.org](https://nodejs.org/) ou use um gerenciador como `nvm-windows` / `fnm`.

2. **Instale o Claude Code globalmente:**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

3. **Faça o login.** Rode o CLI uma vez e siga o fluxo de login interativo (ele abre o navegador):
   ```bash
   claude
   ```
   Escolha o método de autenticação que você usa (conta Anthropic ou Claude Pro/Max). Quando entrar no chat, digite `/exit` para sair — as credenciais ficam salvas localmente.

4. **Confirme que está tudo certo:**
   ```bash
   claude --version
   claude -p "ping"
   ```
   Se o `ping` devolver uma resposta do Claude, está pronto.

Depois disso, o fluxo de IA do VoiceMate detecta o Claude automaticamente quando você roda `poetry run voice-mate`. Se o `claude` não estiver instalado ou estiver deslogado, o fluxo de IA é desativado silenciosamente e o fluxo clipboard continua funcionando normalmente.

### Configurando o TTS (VoxCPM2)

Por padrão, a resposta do Claude é lida em voz alta usando o [VoxCPM2](https://github.com/OpenBMB/VoxCPM) — um modelo de 2B parâmetros, multilíngue (com PT-BR), com voice design por descrição textual.

- A lib `voxcpm` já entra como dependência quando o ambiente é Python 3.12. Na primeira execução, o modelo é baixado automaticamente do Hugging Face (alguns GB, leva um tempinho).
- A voz padrão é "uma jovem mulher brasileira, voz natural e calorosa, tom pausado e claro" — você pode customizar com `--tts-voice "..."`.
- Se você não quiser TTS, rode com `--no-tts` (a resposta continua indo para o clipboard e o beep da tríade volta a tocar).
- Se a inicialização do VoxCPM2 falhar (sem CUDA, sem espaço em disco, etc.), o app cai automaticamente para o fluxo sem TTS — você não precisa fazer nada.

#### Sobre PyTorch e CUDA

O `pyproject.toml` configura o `torch` e o `torchaudio` para virem da source oficial do PyTorch com build **CUDA 12.8** — então o `make setup_env` já instala a versão com GPU corretamente, sem passo extra.

Para confirmar:

```bash
poetry run python -c "import torch; print('CUDA:', torch.cuda.is_available())"
```

Tem que devolver `CUDA: True`. Se devolver `False`, verifique se o driver NVIDIA está atualizado (`nvidia-smi` mostra a versão) — drivers recentes (≥ 545) já cobrem CUDA 12.8.

Se você não tem GPU NVIDIA e quiser usar o app só com clipboard (sem TTS), rode com `--no-tts`. O VoxCPMSpeaker também avisa no console se detectar PyTorch sem CUDA na inicialização — fique de olho nessa mensagem.

## Uso

```bash
make run
```

Atalhos padrão:

- **`Ctrl+Alt+V`** — fluxo clipboard (transcrição → clipboard)
- **`Ctrl+Alt+A`** — fluxo Claude (transcrição → Claude → resposta da IA no clipboard + TTS)

### Fluxo clipboard

1. Pressione `Ctrl+Alt+V` para começar a gravar (beep de início)
2. Fale normalmente
3. Pressione `Ctrl+Alt+V` de novo para parar
4. A transcrição cai no clipboard (beep duplo)
5. Cole com `Ctrl+V` onde precisar

### Fluxo Claude (multi-turn com voz)

1. Pressione `Ctrl+Alt+A` para começar a gravar
2. Faça a sua pergunta ou comando
3. Pressione `Ctrl+Alt+A` de novo para parar — o VoiceMate transcreve, copia a transcrição para o clipboard e envia o texto para o Claude
4. A resposta da IA substitui o conteúdo do clipboard e o VoxCPM2 começa a ler em voz alta (em PT-BR)
5. Pressione `Ctrl+Alt+A` de novo para fazer um follow-up — a conversa continua na mesma sessão

**O atalho de parar decide o destino:** dá pra começar com `Ctrl+Alt+V` e parar com `Ctrl+Alt+A` (ou o contrário). Quem escolhe o destino é sempre o atalho que você usa para *parar*.

**Cancelamento enquanto o Claude pensa ou fala:** pressionar qualquer atalho enquanto a IA está processando — ou enquanto o TTS está falando — corta na hora e já começa uma nova gravação. O contexto da conversa é preservado, então o próximo turno vê tudo o que já foi conversado.

**Histórico Win+V:** como a transcrição e a resposta da IA passam pelo clipboard, o histórico do Windows (`Win+V`) mostra as duas — útil para você conferir o que falou e comparar com o que o Claude respondeu.

### Opções

```bash
# Escolher outro modelo Whisper
poetry run voice-mate --model medium

# Atalhos personalizados
poetry run voice-mate --hotkey "ctrl+shift+r" --claude-chat-hotkey "ctrl+shift+c"

# Desabilitar o fluxo do Claude (só clipboard)
poetry run voice-mate --no-claude-chat

# Definir um system prompt para o Claude
poetry run voice-mate --claude-system-prompt "Você é um assistente de produtividade conciso."

# Limitar os turnos da sessão multi-turn
poetry run voice-mate --claude-max-turns 20

# Desligar o TTS (resposta só vai pro clipboard + beep)
poetry run voice-mate --no-tts

# Mudar o perfil de voz do TTS
poetry run voice-mate --tts-voice "Um homem brasileiro, voz grave e pausada."

# Forçar CPU pro TTS (mais lento mas funciona sem GPU)
poetry run voice-mate --tts-device cpu

# Salvar os áudios gerados pelo TTS em um diretório
poetry run voice-mate --tts-save-dir ./tts_logs

# Forçar CPU pra transcrição Whisper (sem GPU)
poetry run voice-mate --cpu

# Usar botão lateral do mouse (só no fluxo clipboard)
poetry run voice-mate --input-method mouse --mouse-button x

# Ajustar watchdog e keepalive do listener
poetry run voice-mate --listener-refresh-seconds 30 --watchdog-timeout 60
```

### Modelos Whisper

| Modelo             | VRAM (GPU) | Velocidade   | Qualidade  |
| ------------------ | ---------- | ------------ | ---------- |
| `tiny`             | ~75 MB     | Muito rápida | Básica     |
| `base`             | ~140 MB    | Rápida       | Boa        |
| `small`            | ~460 MB    | Moderada     | Muito boa  |
| `medium`           | ~1.0 GB    | Moderada     | Ótima      |
| `large-v3-turbo`   | ~1.5 GB    | Rápida       | Excelente  |
| `large-v3`         | ~3.0 GB    | Lenta        | Máxima     |

O padrão é `large-v3-turbo` — melhor equilíbrio entre velocidade e qualidade, especialmente quando o áudio tem mistura de idiomas.

## Makefile

| Comando            | Descrição                                          |
| ------------------ | -------------------------------------------------- |
| `make setup_env`   | Instala as dependências via Poetry                 |
| `make lock`        | Regenera o `poetry.lock` (após mexer no pyproject) |
| `make run`         | Executa com o modelo padrão (`large-v3-turbo`)     |
| `make run-large`   | Executa com `large-v3`                             |
| `make run-turbo`   | Executa com `large-v3-turbo`                       |
| `make format`      | Formata o código com Ruff                          |
| `make lint`        | Roda Ruff + type-check com Mypy                    |
| `make test`        | Executa os testes com pytest                       |
| `make clean`       | Limpa os caches                                    |

## Arquitetura

```
app/
├── main.py                          # Entry point + CLI + montagem dos fluxos
├── core/
│   └── config.py                    # Dataclass de configuração + FlowConfig + TTSConfig
└── services/
    ├── recorder.py                  # Captura do microfone (sounddevice)
    ├── transcriber.py               # Inferência Whisper (faster-whisper)
    ├── audio_feedback.py            # Beeps cross-platform
    ├── audio_player.py              # Player de áudio com fila (TTS streaming)
    ├── recording_session.py         # Máquina de estado: idle → recording → processing
    ├── transcription_handler.py     # Protocol + ClipboardHandler
    ├── claude_chat_handler.py       # Fluxo Claude: envio + clipboard duplo + TTS + cancel
    ├── claude_runtime.py            # Ponte sync ↔ asyncio para claude-agent-sdk
    ├── tts.py                       # Protocol TextToSpeech + NullSpeaker
    ├── voxcpm_speaker.py            # Speaker baseado em VoxCPM2 (streaming + cancel)
    ├── input_listener.py            # Abstração de trigger (teclado/mouse)
    ├── multi_hotkey_listener.py     # Múltiplos atalhos globais com callbacks distintos
    ├── listener_keepalive.py        # Reinstalação periódica do hook (fix Windows)
    └── watchdog.py                  # Monitor de saúde do processo
```

### Por que o listener-keepalive?

No Windows, hooks de baixo nível (`WH_KEYBOARD_LL` / `WH_MOUSE_LL`) usados pelas libs de atalho global são **removidos silenciosamente** pelo SO quando o callback ultrapassa o `LowLevelHooksTimeout` (máximo de 1000 ms no Win10+). Sob CPU em carga isso acontece sem aviso ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)). O VoiceMate re-registra o atalho a cada 60 s por padrão — então, mesmo que o SO tenha matado o hook, o próximo ciclo reinstala.

### Por que TTS plugável?

A arquitetura separa o **orquestrador** (`TextToSpeech` Protocol em `tts.py`) da **implementação concreta** (`VoxCPMSpeaker`). Isso facilita testar outras libs de TTS no futuro (edge-tts, ElevenLabs, Piper, etc.) — basta criar uma nova implementação do Protocol e plugar via configuração. Se uma lib não te agradar, você descarta só o arquivo dela.

## Stack

- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — Whisper otimizado com CTranslate2
- **[sounddevice](https://python-sounddevice.readthedocs.io/)** — captura do microfone e reprodução do TTS
- **[keyboard](https://github.com/boppreh/keyboard)** / **[mouse](https://github.com/boppreh/mouse)** — hooks globais
- **[pyperclip](https://github.com/asweigart/pyperclip)** — acesso ao clipboard
- **[claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python)** — fluxo Claude, em cima do `claude` CLI local
- **[voxcpm](https://github.com/OpenBMB/VoxCPM)** — TTS multilíngue com voice design por descrição
- **[soundfile](https://github.com/bastibe/python-soundfile)** — leitura/escrita de WAV (opcional, para salvar áudios TTS)

## Contribuindo

Issues e PRs são bem-vindos. Rode `make all` (format + lint + test) antes de abrir um PR.

## Licença

[MIT](LICENSE) © Álli Terhorst

Faz parte da [NanoBR](https://github.com/nano-br) — utilitários open-source para o dia a dia.
