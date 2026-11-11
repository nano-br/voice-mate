[English](configuration.md) | **Português** | [Español](configuration.es.md) | [Русский](configuration.ru.md) | [简体中文](configuration.zh-CN.md)

# Referência de configuração

Objetivo: toda opção do motor, chave de configurações, variável de ambiente e local de arquivo do VoiceMate. Voltar ao [README](../README.pt-BR.md).

## De onde vêm as configurações

| Parte | Origem | Precedência |
|---|---|---|
| Motor | Opções de linha de comando do `voice-mate` (`make run ARGS="..."`) e `~/.config/voicemate/config.toml` (escrito pelo `make setup` e pelo `make configure`) | A opção, depois o `config.toml`, depois a detecção ou o padrão embutido |
| Companion | `companion.toml`, editado pelas Configurações | O arquivo; valores inválidos voltam ao padrão |

As opções chegam ao motor pelo Makefile com `ARGS`, por exemplo `make run ARGS="--model medium --no-tts"`. O `make` não repassa palavras colocadas depois do alvo, então use sempre `ARGS`.

## Opções do motor

### Fala para texto e entrada

| Opção | Padrão | Significado |
|---|---|---|
| `--model {tiny,base,small,medium,large-v2,large-v3,large-v3-turbo}` | `large-v3-turbo` | Modelo Whisper |
| `--hotkey` | `ctrl+alt+v` | Atalho do fluxo da área de transferência |
| `--cpu` | desligado | Força a CPU (int8). Vale mais do que toda configuração de GPU |
| `--stt-mode {auto,cpu,gpu}` | escolha salva, senão `auto` | Onde transcrever. `cpu`: faster-whisper int8 no processador, decodificação gulosa com VAD, a GPU fica livre para outros trabalhos. `gpu`: o backend de GPU com decodificação gulosa, caindo para a CPU (com uma dica de instalação) quando não funciona. `auto`: a GPU quando o backend dela funciona, senão a CPU. Veja [Modos de reconhecimento de fala](#modos-de-reconhecimento-de-fala) |
| `--cpu-threads` | `0` (metade dos núcleos, no máximo 8) | Threads de CPU do faster-whisper na CPU |
| `--gpu-backend {auto,nvidia,amd,cpu}` | escolha salva, senão detectado | Fabricante da GPU; `auto` detecta a placa |
| `--whisper-backend {faster-whisper,whispercpp,openai-whisper}` | escolha salva, senão por fabricante | Motor de fala para texto |
| `--stt-strategy {auto,faster-whisper-rocm,whispercpp,openai-whisper}` | escolha salva, senão `auto` | Só AMD: o primeiro backend da cadeia de alternativas |
| `--whispercpp-mode {server,cli}` | `server` | `server` mantém o modelo do whisper.cpp carregado; `cli` o recarrega a cada gravação (mais lento) |
| `--transcription-language {auto,pt,en,es,fr,de,it,ja,ru,zh}` | derivado de `--output-lang` | Idioma fixado para a transcrição. `auto` o detecta a cada gravação (menos estável em gravações curtas) |
| `--input-method {keyboard,mouse}` | `keyboard` | `mouse` funciona só no Windows (gatilho `keyboard-hooks`) e só para o fluxo da área de transferência; ele desliga o fluxo do Claude |
| `--mouse-button` | `x` | Botão do mouse para `--input-method mouse` (`x` é um botão lateral) |
| `--max-recording-seconds` | `600` | Limite de gravação. Um aviso toca aos 80 %; no limite a gravação para e é transcrita |
| `--output-lang` | `pt-BR` | Código BCP-47. O idioma das respostas do Claude, o idioma de transcrição padrão (`pt-BR` dá `pt`, `en` dá `en`, `zh-CN` dá `zh`) e o idioma padrão das mensagens do motor. O padrão fixa o Whisper em português: passe `--output-lang en` (ou `--transcription-language en`) quando você dita em inglês. O companion o passa para o motor que ele inicia, a partir da configuração "Idioma do ditado:" |
| `--stt-engine {builtin,external}` | escolha salva, senão `builtin` | `builtin` é o motor de fala para texto escolhido acima. `external` é um serviço que você roda ([protocolo](stt-provider-protocol.pt-BR.md)) |
| `--stt-url` | `http://127.0.0.1:8766` | Só `external`: URL base do provedor. Uma URL inválida é registrada no log e o motor embutido é usado |
| `--stt-options` | nenhum | Só `external`: objeto JSON de opções repassado ao provedor como está |
| `--no-stt-fallback` | desligado | Só `external`: uma gravação que o provedor não consegue transcrever falha em vez de ir para o motor embutido |

O token do provedor de STT nunca é uma opção: ele é lido de `VOICEMATE_STT_TOKEN`, senão de `stt_external_token` no arquivo do motor, e nunca é registrado no log. O comando de início dele vem só de `VOICEMATE_STT_START_COMMAND`. O motor remove os dois do próprio ambiente depois de lidos. Veja o [protocolo de provedor de STT](stt-provider-protocol.pt-BR.md).

### Plataforma, gatilho e API

| Opção | Padrão | Significado |
|---|---|---|
| `--platform {windows,linux-x11,linux-wayland,wsl2}` | escolha salva, senão detectado | Ambiente de execução |
| `--trigger {keyboard-hooks,pynput,evdev,socket}` | por plataforma: `windows` usa `keyboard-hooks`, `linux-x11` usa `pynput`, `linux-wayland` usa `evdev`, `wsl2` usa `socket` | Como os atalhos chegam ao motor. `socket` significa que a API HTTP é o gatilho |
| `--daemon-port` | escolha salva, senão `47821` | Porta da API HTTP local |
| `--api` | desligado (sempre ligado com `--trigger socket`) | Serve a API HTTP ao lado do ouvinte nativo de atalhos |
| `--api-token` | desligado | Exige `Authorization: Bearer <token>` em toda rota, exceto `/health` (arquivo do token abaixo) |
| `--supervised` | desligado | Termina limpo quando o stdin chega ao fim do arquivo (o companion mantém o stdin aberto) |
| `--no-watchdog` | watchdog ligado | Desliga o watchdog que reinicia um motor travado |
| `--watchdog-timeout` | `120` | Tempo limite do watchdog, em segundos |
| `--no-listener-refresh` | renovação ligada | Só Windows: desliga a reinstalação periódica do hook de teclado |
| `--listener-refresh-seconds` | `60` | Só Windows: intervalo dessa reinstalação |

`make run-engine` roda `voice-mate --supervised --api --api-token`; é o que o companion inicia.

### Fluxo do Claude

| Opção | Padrão | Significado |
|---|---|---|
| `--claude-chat-hotkey` | `ctrl+alt+a` | Atalho do fluxo do Claude |
| `--no-claude-chat` | desligado | Desliga o fluxo do Claude (só área de transferência) |
| `--claude-system-prompt` | prompt embutido | Substitui o prompt de sistema |
| `--claude-no-system-prompt` | desligado | Não envia prompt de sistema. Vale mais do que `--claude-system-prompt` |
| `--claude-max-turns` | `50` | Turnos por conversa |
| `--claude-model` | `claude-haiku-5-5` | Modelo do Claude |
| `--claude-effort {low,medium,high,xhigh,max}` | `low` | Nível de esforço. Ignorado pelo Haiku 4.5 e anteriores |
| `--claude-enable-thinking` | desligado | Raciocínio estendido. O Sonnet 5.5, o Opus 5.5 e o Fable sempre raciocinam, e o Haiku 5.5 e o Opus 5 também com esforço `xhigh` ou `max` |
| `--claude-timeout-seconds` | `120.0` | Tempo limite de cada turno |

### Texto para fala (fluxo do Claude)

| Opção | Padrão | Significado |
|---|---|---|
| `--no-tts` | desligado | Sem resposta falada; a resposta ainda vai para a área de transferência |
| `--tts-engine {<default>,kokoro,voxcpm,external,none}` | escolha salva, senão o motor padrão | Motor de TTS. `external` é um serviço que você roda ([protocolo](tts-provider-protocol.md)). `none` desliga o TTS |
| `--tts-url` | `http://127.0.0.1:8765` | Só `external`: URL base do provedor |
| `--tts-external-voice` | padrão do provedor | Só `external`: id da voz (o seletor de voz do companion o preenche) |
| `--tts-options` | nenhum | Só `external`: um objeto JSON repassado ao provedor como `options` (velocidade, estilo). Qualquer outra coisa é ignorada, com um aviso |
| `--tts-kokoro-voice` | `pf_dora` | Voz do Kokoro. Vozes em português: `pf_dora` (feminina), `pm_alex` e `pm_santa` (masculinas) |
| `--tts-voice` | uma descrição em português da voz de uma senhora idosa brasileira | Só VoxCPM2: descrição em texto da voz, usada quando não há semente de voz |
| `--tts-cfg-value` | `2.0` | `cfg_value` do VoxCPM2 (1.0 a 3.0) |
| `--tts-inference-timesteps` | `10` | Passos de inferência do VoxCPM2 (4 a 30) |
| `--tts-device {auto,cuda,cpu,mps}` | `auto` | Dispositivo do TTS |
| `--tts-save-dir` | nenhum | Salva cada arquivo de áudio gerado nesta pasta |
| `--tts-no-streaming` | desligado | Gera de uma vez em vez de em fluxo (depuração) |
| `--tts-voice-seed-mode {auto,fixed,off}` | `off` | Como o motor padrão e o VoxCPM2 escolhem a voz (veja [usage.pt-BR.md](usage.pt-BR.md#modos-de-semente-de-voz)) |
| `--tts-voice-seed-path`, `--tts-voice-seed-text` | nenhum | WAV de referência e o texto exato dele, exigidos pelo `fixed` |
| `--tts-voice-seed-cache-dir` | `~/.cache/voicemate` | Onde o `auto` salva a referência dele |
| `--tts-reset-seed` | desligado | Apaga a semente automática do VoxCPM2 (`voice_seed.wav`) antes de iniciar |
| `--tts-show-progress` | desligado | Mostra a barra de progresso do VoxCPM2 |
| `--tts-drain-timeout-seconds` | `60.0` | Tempo limite do esvaziamento do player de áudio |
| `--tts-debug-vram` | desligado | Registra no log o uso de VRAM em volta de cada frase |

O token do provedor nunca é uma opção: ele é lido de `VOICEMATE_TTS_TOKEN`, senão de `tts_external_token` no arquivo do motor, e nunca é registrado no log. O companion o passa pelo ambiente, e o motor o remove do próprio ambiente depois de lido. Ele fica em texto simples no `companion.toml` do companion.

O comando de início do provedor também não é uma opção: o motor o lê de `VOICEMATE_TTS_START_COMMAND`, o remove do próprio ambiente depois de lido e nunca o registra no log. Veja [Starting the provider](tts-provider-protocol.md#starting-the-provider-with-voicemate).

O companion passa o endereço e a voz como `--tts-url=...` e `--tts-external-voice=...`, com um `=`, para que um valor que começa com `-` não seja lido como opção.

O TTS só inicia quando um fluxo de conversa está ligado, e o aquecimento dele também: a voz Kokoro embutida não é aquecida de outro modo. Se os pacotes do motor escolhido estiverem faltando, ou o motor falhar ao iniciar, o motor registra um aviso com a correção e roda sem TTS.

## Arquivo do motor: `~/.config/voicemate/config.toml`

Um arquivo TOML plano escrito pelo `make setup` e pelo `make configure`. Valores inválidos são ignorados; um arquivo ausente ou quebrado significa "nada salvo". Toda chave é opcional.

| Chave | Valores | Significado |
|---|---|---|
| `gpu_vendor` | `nvidia`, `amd`, `cpu` | Fabricante da GPU |
| `whisper_backend` | `faster-whisper`, `whispercpp`, `openai-whisper` | Motor de fala para texto |
| `tts_enabled` | `true`, `false` | TTS ligado ou desligado |
| `tts_engine` | o motor padrão, `kokoro`, `voxcpm`, `external`, `none` | Motor de TTS |
| `tts_external_url`, `tts_external_voice`, `tts_external_options` | texto | Configurações do motor `external` (endereço, id da voz, opções em JSON) |
| `tts_external_token` | texto | Token bearer do motor `external`. Texto simples neste arquivo; `VOICEMATE_TTS_TOKEN` vale mais |
| `stt_engine` | `builtin`, `external` | Motor de transcrição |
| `stt_external_url`, `stt_external_options` | texto | Configurações do motor de transcrição `external` (endereço, opções em JSON) |
| `stt_external_token` | texto | Token bearer do motor de transcrição `external`. Texto simples neste arquivo; `VOICEMATE_STT_TOKEN` vale mais |
| `stt_fallback` | `true`, `false` | `false` desliga a alternativa embutida do motor de transcrição `external` |
| `default_flow` | `clipboard`, `claude_chat` | `clipboard` desliga o fluxo do Claude |
| `platform` | `windows`, `linux-x11`, `linux-wayland`, `wsl2` | Ambiente de execução |
| `trigger` | `keyboard-hooks`, `pynput`, `evdev`, `socket` | Mecanismo dos atalhos |
| `stt_strategy` | `auto`, `faster-whisper-rocm`, `whispercpp`, `openai-whisper` | Cadeia de alternativas da AMD |
| `ct2_rocm_ok` | `true`, `false` | CTranslate2-ROCm validado pela configuração. O motor escreve `false` quando ele falha em execução; o `make configure` tenta de novo |
| `daemon_port` | inteiro | Porta da API HTTP |
| `stt_mode` | `auto`, `cpu`, `gpu` | Modo de transcrição padrão (perguntado pelo `make setup` e pelo `make configure`) |

## Arquivo do companion: `companion.toml`

Local: Windows `%APPDATA%\VoiceMate\companion.toml`; Linux `${XDG_CONFIG_HOME:-~/.config}/voicemate/companion.toml`. O desinstalador o mantém. Um arquivo que não pode ser lido é movido para o lado como `companion.toml.broken-<timestamp>` e a notificação "Configurações redefinidas" avisa. Um arquivo escrito por uma versão mais nova é mostrado somente para leitura.

| Chave | Padrão | Valores permitidos | Na interface |
|---|---|---|---|
| `version` | `1` | inteiro | |
| `client_key` | gerada na primeira execução | texto | |
| `language` | `auto` | `auto`, `pt-BR`, `en`, `es`, `ru`, `zh-CN` | Geral > "Idioma:" |
| `dictation_language` | `interface` | `interface` (segue `language`), `auto` (o Whisper detecta cada gravação), ou um idioma: `pt`, `en`, `es`, `ru`, `zh`, `fr`, `de`, `it`, `ja` | Geral > "Idioma do ditado:" ("Igual ao da interface", "Detectar automaticamente", depois um idioma) |
| `engine_mode` | `wsl2` no Windows, `local` no Linux | Windows: `wsl2`, `external`; Linux: `local`, `external` | Geral > "Modo:" ("Iniciar no WSL", "Iniciar neste computador", "Só conectar") |
| `wsl_distro` | `""` (a distro padrão do WSL) | nome da distro | Geral > "Distro do WSL:" |
| `engine_dir` | `""` (detectar) | caminho relativo a `$HOME`, ou absoluto; `"`, `$`, crase, barra invertida e quebra de linha são rejeitados | Geral > "Pasta do motor:" |
| `daemon_port` | `47821` | 1024 a 65535 | só no arquivo |
| `cues_enabled` | `true` | `true`, `false` | Sons > "Tocar sons de aviso"; menu da bandeja > "Silenciar sons" |
| `master_volume` | `0.8` | 0.0 a 1.0 | Sons > "Volume:" |
| `wsl_restart_policy` | `auto` | `auto`, `ask`, `never` | Geral > "Reiniciar o WSL se o áudio falhar:" ("Automaticamente", "Perguntar antes", "Nunca") |
| `notify_level` | `warnings` | `all`, `warnings`, `errors`, `none` | Geral > "Notificações:" ("Todas", "Avisos e erros", "Só erros", "Nenhuma") |
| `start_at_login` | `false` | `true`, `false` | Geral > "Iniciar o VoiceMate ao fazer login" |
| `tray_icon_visible` | `true` | `true`, `false` (só no Windows 11) | Geral > "Sempre mostrar o ícone do VoiceMate na barra de tarefas" |
| `stt_mode` | `auto` | `auto` (a configuração do próprio motor), `cpu`, `gpu` | Geral > Transcrição > "Rodar na:"; menu da bandeja > "Transcrição" |
| `stt_cpu_model` | `large-v3-turbo` | `large-v3-turbo`, `large-v3`, `medium`, `small` | Geral > Transcrição > "Modelo da CPU:" |
| `stt_gpu_model` | `large-v3-turbo` | igual a `stt_cpu_model` | Geral > Transcrição > "Modelo da GPU:" |
| `tts_engine` | `default` | `default` (a configuração do próprio motor), `external` | Voz > "Motor de voz:" ("Integrado (configuração do motor)", "Provedor personalizado (HTTP)") |
| `tts_url` | `http://127.0.0.1:8765` | um endereço `http://` ou `https://` sem espaços, aspas nem `$` | Voz > "Endereço do provedor:" |
| `tts_voice` | `""` (o padrão do provedor) | um id de voz sem espaços nem caracteres de shell | Voz > "Voz:" |
| `tts_token` | `""` | texto sem quebras de linha. Texto simples neste arquivo | Voz > "Token:" |
| `tts_start_command` | `""` (o VoiceMate não inicia o provedor) | uma única linha, executada pelo shell como digitada. Texto simples neste arquivo | Voz > "Comando de início:" |
| tabela `[assistant]` | veja [Configurações do assistente](#configurações-do-assistente) | | Aba Assistente |
| `[[hotkeys]]` com `flow` e `chord` | `clipboard` = `ctrl+alt+v`, `claude_chat` = `ctrl+alt+a` | Ctrl, Alt, Shift ou Win com uma tecla, ou uma tecla de função sozinha; F12 é reservada pelo Windows | Aba Atalhos |
| `[cues.<name>]` para `start`, `transcribing`, `ready`, `ai_ready`, `warning`, `error`, `answer` (uma resposta de outra conversa está esperando) | `enabled = true`, `source = "preset"`, `preset = "classic"`, `file = ""`, `volume = 1.0` | `source`: `preset`, `file`; `preset`: `classic`, `soft`, `click`; `file`: um WAV PCM (8 ou 16 bits) | Aba Sons |

A mudança de idioma pergunta "Reiniciar o VoiceMate?" ("Reiniciar agora" ou "Depois"); até a reinicialização, a aba Geral mostra "Passa a valer depois que o VoiceMate reiniciar.".

### Idioma do ditado

O companion transforma `dictation_language` em duas opções para o motor que ele inicia: `--transcription-language <code>` e `--output-lang <BCP-47>` (`pt` dá `pt-BR`, `zh` dá `zh-CN`, os outros o próprio código). `interface` usa o idioma da interface; `auto` passa `--transcription-language auto` e mantém `--output-lang` no idioma da interface, então o Claude ainda responde nele. Mudar a chave reinicia o motor. No modo "Só conectar" a chave é ignorada e o motor mantém as próprias opções, e o mesmo vale para um motor ao qual o companion se conecta (uma unidade do systemd, ou um iniciado à mão). Mais em [usage.pt-BR.md](usage.pt-BR.md#idioma-do-ditado).

## Modos de reconhecimento de fala

O motor transcreve no processador ou na placa de vídeo. O modo CPU existe
para que o VoiceMate continue funcionando enquanto a GPU está ocupada com outro trabalho (edição de vídeo, renderização, jogos):
ele nunca carrega nada na GPU.

| Modo | Backend | Decodificação | Quando |
|---|---|---|---|
| `cpu` | faster-whisper, int8 | beam 1, filtro VAD, `--cpu-threads` | a GPU está ocupada, ou não há uma |
| `gpu` | faster-whisper CUDA (NVIDIA), openai-whisper ROCm (AMD no WSL2), whisper.cpp Vulkan (AMD no Windows) | beam 1 | o mais rápido |
| `auto` | a escolha da configuração do motor | o padrão do backend (beam 5) | como antes de esta opção existir |

O companion mantém um modelo por modo (`stt_cpu_model`, `stt_gpu_model`) e passa
`--stt-mode` e `--model` ao motor que ele inicia. Trocar reinicia o motor, que é
o que libera (ou toma) a GPU. Um modo que não está instalado é instalado pelo companion
depois de perguntar, com o tamanho do download; as mesmas ferramentas rodadas à mão:

```bash
make stt-check
make stt-install MODE=gpu
make stt-install MODE=cpu ARGS="--model medium"
```

O `stt-install` mexe só no ambiente virtual do próprio motor (e em `~/.cache` para os
modelos): PyTorch ROCm e openai-whisper para a AMD, bibliotecas CUDA para a NVIDIA, a
compilação do whisper.cpp no Windows, o modelo do faster-whisper para a CPU.

### Benchmark

Medido em 27/10/2026 (CPU) e 28/10/2026 (GPU) com ditado em português do Common
Voice (gravações curtas de cerca de 6 s, longas de cerca de 65 s), pelos backends
do próprio motor numa máquina (Intel i5-13400F, AMD Radeon RX 9070 XT com ROCm no WSL2). WER é
a taxa de erro de palavras em relação ao texto de referência; latência é o tempo de transcrição por
gravação.

| Modo | Modelo | Beam | Curta: latência, WER | Longa: latência, WER |
|---|---|---|---|---|
| CPU (8 threads, int8) | large-v3-turbo | 1 | 4,7 s, 5,0 % | 21 s, 3,3 % |
| CPU (8 threads, int8) | medium | 1 | 3,2 s, 5,3 % | 15 s, 5,1 % |
| GPU (Radeon, ROCm) | large-v3-turbo | 5 | 0,45 s, 4,8 % | 4,6 s, 2,9 % |
| GPU (Radeon, ROCm) | large-v3-turbo | 1 | 0,18 s, 4,5 % | 1,9 s, 3,6 % |
| GPU (Radeon, ROCm) | medium | 5 | 0,83 s, 6,1 % | 10,1 s, 6,9 % |
| GPU (Radeon, ROCm) | medium | 1 | 0,42 s, 5,8 % | 4,9 s, 7,3 % |

Na CPU, o beam 5 não deu WER menor na mesma execução e só foi mais lento (não está
na tabela). Na GPU, a decodificação gulosa é cerca de 2,4 vezes mais rápida que o beam 5, por menos de
um ponto de WER, então o modo `gpu` decodifica de forma gulosa. O `medium` é menos preciso que o
`large-v3-turbo` nos dois, e na GPU também é mais lento, então o modelo turbo é o
padrão dos dois modos; `medium` e `small` existem para processadores mais lentos.

## Configurações do assistente

O assistente de voz ([usage.pt-BR.md](usage.pt-BR.md#assistente-de-voz)) guarda as escolhas dele na tabela `[assistant]` do `companion.toml`:

| Chave | Padrão | Valores permitidos | Na interface |
|---|---|---|---|
| `mode` | `off` | `off`, `own_session`, `desktop` | Assistente > "Modo do assistente:" ("Desligado", "Conversa própria do VoiceMate", "Uma conversa do Claude Desktop") |
| `desktop_conversation` | `""` | o título exato de uma conversa na barra lateral do Claude Desktop | Assistente > "Conversa do Desktop:" (o modo `desktop` não inicia sem ela) |
| `model` | `haiku` | `haiku`, `sonnet`, `opus` (os apelidos do Claude Code) | Assistente > "Modelo:" |
| `effort` | `low` | `low`, `medium`, `high` | Assistente > "Esforço:" |
| `new_conversation_model` | `sonnet` | igual a `model` | Assistente > "Novas conversas usam:" |
| `new_conversation_effort` | `medium` | igual a `effort` | Assistente > "Com o esforço:" |
| `permission_mode` | `acceptEdits` | `default`, `acceptEdits`, `bypassPermissions` | Assistente > "Permissões:" ("Perguntar antes de cada mudança", "Aceitar edições de arquivos, perguntar o resto", "Acesso total, nunca perguntar (arriscado)") |
| `resume_permission_mode` | `""` | vazio (nenhum modo passado), `default`, `acceptEdits`, `plan`; `bypassPermissions` é recusado, com uma linha no log | nenhuma: defina no arquivo. O modo de permissão que uma sessão de terminal, IDE ou Remote Control fechada recebe quando o assistente a retoma para entregar uma mensagem. Não pode ser mudado por voz |
| `answer_delivery` | `announce` | `speak`, `announce`, `sound`, `silent` | Assistente > "Respostas de outras conversas:" |
| `speak_replies` | `true` | `true`, `false` | Assistente > "Falar as respostas" |
| `voice_source` | `auto` | `auto`, `engine`, `windows` | Assistente > "Origem da voz:" ("Automática", "A voz do motor", "As vozes do Windows"). Com `engine`, uma frase que encontra o motor ainda carregando a voz espera por ele (até 90 segundos, uma vez por queda), e os avisos curtos ficam na tela em vez de cair para a fala do Windows |
| `voice` | `""` (a voz padrão da origem) | um id de voz do motor de voz do motor, ou um id `windows/...` de uma voz do Windows | Assistente > "Voz do assistente:" (**Carregar vozes**, **Testar**) |
| `voice_commands` | `true` | `true`, `false` | Assistente > "Entender comandos de voz no texto digitado" |
| `expressions` | `off` | `off`, `on` | Assistente > "Tags de expressão:" |
| `user_name` | `""` | texto | Assistente > "Seu nome:" |
| `personality_source` | `none` | `none`, `file`, `custom` | Assistente > Personalidade > "Origem:" ("Nenhuma", "Um arquivo Markdown (por exemplo, uma nota do seu cofre do Obsidian)", "Escrever a minha") |
| `personality_file` | `""` | vazio, ou um arquivo `.md` existente (necessário quando a origem é `file`) | Assistente > "Arquivo de personalidade:" |
| `obsidian_vault` | `""` | uma pasta | Assistente > "Pasta do cofre:" |
| `skill_language` | `auto` | `auto` (a skill em português quando a interface está em português, inglês nos outros casos), `en`, `pt-BR` | Assistente > "Idioma da skill:" |
| `custom_skill_dir` | `""` | vazio (a skill embutida), ou uma pasta que tem um `SKILL.md` | Assistente > "Skill personalizada:" |
| `desktop_profile_auto_update` | `true` | `true`, `false` | Assistente > "Procurar um perfil mais novo quando o assistente iniciar" |
| `desktop_profile_override` | `""` | um arquivo de perfil importado com **Importar...** | Assistente > **Importar...**, **Usar o padrão** ([desktop-profile.md](desktop-profile.md)) |
| `talk_hotkey` | `ctrl+alt+s` | uma combinação, ou `""` para nenhuma | Atalhos > "Falar com o assistente" |
| `window_hotkey` | `ctrl+alt+w` | igual | Atalhos > "Abrir a janela do assistente" |
| `digest_hotkey` | `ctrl+alt+r` | igual | Atalhos > "Ouvir o que está pronto" |
| `mode_hotkey` | `""` | igual | Atalhos > "Trocar o modo do assistente" |
| `back_hotkey` | `""` | igual | Atalhos > "Voltar ao assistente" |

Um valor inválido volta ao padrão, com uma linha no log. Um arquivo mais antigo com `enabled = true` (ou `mode = "on"`) é lido como `own_session`, e um `personality_file` sem `personality_source` é lido como a origem `file`. Os atalhos do assistente são conferidos junto com os outros: uma combinação não pode ser dada a duas ações.

O que uma mudança faz enquanto o assistente roda:

| Mudança | Efeito |
|---|---|
| `mode`, `skill_language`, `custom_skill_dir` e, no modo próprio, `effort` e `permission_mode` | Reinicia a conversa central |
| `desktop_conversation` (modo Desktop) | Reinicia a conversa central |
| `model` (modo próprio) | Vale a partir da próxima resposta, sem reiniciar |
| `model` ou `effort` (modo Desktop) | O VoiceMate não consegue mudar a conversa do Desktop; ele envia à central uma nota pedindo que os aplique em si mesma |
| `permission_mode` | Vale para as conversas que o assistente inicia daí em diante, e reinicia a conversa central na hora (modo próprio), então ela nunca mantém as permissões antigas |
| Nome, personalidade, cofre, tags, `answer_delivery` | Escrito em `assistant.json`, que a skill relê |

O texto que você escreve em "Escrever a minha" não está no `companion.toml`: o VoiceMate o guarda em `assistant-personality.md` na pasta de estado dele (até 20.000 caracteres) e dá esse arquivo à skill como `personality_file`.

> [!WARNING]
> `permission_mode = "bypassPermissions"` deixa toda conversa que o assistente inicia alterar qualquer arquivo e rodar qualquer comando neste computador sem perguntar. Só a janela de Configurações pode escolhê-lo: por voz o assistente só pode alternar entre `default` e `acceptEdits`. Ele nunca vale para uma sessão fechada que o assistente retoma: essa retomada não passa modo de permissão a menos que você defina `resume_permission_mode`, que recusa `bypassPermissions`.

As respostas do assistente são faladas pelo motor, com a voz configurada no motor (aba Voz, ou o `--tts-engine` do motor): o companion envia o bloco `<voice>` de cada resposta, frase por frase, ao endpoint `/tts` do motor ([tts-provider-protocol.md](tts-provider-protocol.md#the-engine-speaks-the-same-protocol)) e toca o áudio neste computador.

A voz Kokoro embutida e um provedor personalizado podem falar pelo assistente, e as vozes que o Windows já tem também (todo idioma instalado em Configurações, Hora e idioma, Fala), sem nada para baixar. "Origem da voz:" decide: `auto` usa a voz do motor e as vozes do Windows quando o motor não consegue falar (ou não tem voz), `engine` só o motor, `windows` só o Windows. **Carregar vozes** e **Testar** seguem a origem escolhida, antes de ela ser salva; um id de voz pertence a uma origem, e cada origem ignora os ids da outra. Quando nada pode falar, as respostas ficam só em texto e a janela do assistente mostra um aviso "Nenhuma voz disponível" com o motivo. "Para de falar", um novo prompt e uma troca de modo o silenciam na hora. Ele nunca fala por cima de uma gravação: o que chega enquanto o microfone grava, ou enquanto a gravação é transcrita, espera; uma frase que uma gravação cortou é dita de novo desde o começo, seguida das frases que já estavam na fila atrás dela, em ordem.

O VoiceMate também escreve `~/.voicemate/assistant.json` na sua pasta de usuário, que é o arquivo que a skill `voicemate-assistant` lê (`version`, `language`, `user_name`, `personality_file`, `notes_vault`, `expressions`, `mode`, `permission_mode`, `resume_permission_mode`, `answer_delivery`, `instructions`, `inbox` e o catálogo `settings`). Com a skill em português, `language` é `"pt-BR"` e a voz fala em pt-BR, seja qual for o idioma da interface. Com a skill em inglês (ou uma personalizada), o idioma segue a interface.

`pending` lista os pedidos que o assistente está esperando ou observando (id da sessão, título, motivo, desde quando, `watch`, `until`), e `instructions` as instruções permanentes que você deu a ele ([assistant.pt-BR.md](assistant.pt-BR.md#-instruções-permanentes)). O formato dele está em [usage.pt-BR.md](usage.pt-BR.md#a-skill). A skill em si é instalada em `<Claude home>/skills/voicemate-assistant`, onde o Claude home é `~/.claude` ou a pasta em `CLAUDE_CONFIG_DIR`.

### Configurações que o assistente pode mudar por voz

A lista `settings` do `assistant.json` é a única que o assistente pode mudar.

Chaves do assistente: `assistant.mode` (`own_session`, `desktop`), `assistant.model`, `assistant.effort`, `assistant.permission_mode`, `assistant.answer_delivery`, `assistant.new_conversation_model`, `assistant.new_conversation_effort`, `assistant.desktop_conversation`.

Chaves de voz, texto e som: `assistant.speak_replies`, `assistant.voice`, `assistant.voice_commands`, `assistant.expressions`, `assistant.user_name`, `assistant.personality_source`, `assistant.desktop_profile_auto_update`, `assistant.skill_language`, `cues.enabled`, `cues.volume` (0 a 100), `cues.answer.enabled`, `cues.answer.volume` (0 a 100), `general.dictation_language`, `general.stt_mode` e `general.notify_level`.

Ficam de fora de propósito: os atalhos; todo arquivo ou pasta (o arquivo e o texto da personalidade, o cofre, a pasta da skill personalizada, o perfil do Desktop importado); o motor de voz, o endereço e o token do provedor; o próprio motor (pasta, distro do WSL, porta, modo); o modo `off`; e os sinais sonoros além do de resposta. Um valor precisa ser um dos listados (texto é uma única linha de no máximo 200 caracteres), `desktop` precisa de uma conversa do Desktop escolhida, e `personality_source` como `file` precisa de um arquivo escolhido; caso contrário o VoiceMate recusa, com o motivo, e avisa o assistente.

## Variáveis de ambiente

| Variável | Lida por | Efeito |
|---|---|---|
| `VOICEMATE_LANG` | motor | Idioma só das mensagens do motor (`pt-BR`, `en`, `es`, `ru`, `zh-CN`); sem ela as mensagens seguem `--output-lang`, cujo padrão é o português. Defina-a no shell que roda o `make run` (bash dentro do WSL ou no Linux, `$env:VOICEMATE_LANG` no PowerShell no Windows). Vale mais do que `--output-lang`. Não muda o que o Whisper ouve nem o idioma das respostas do Claude. O companion a ignora |
| `VOICEMATE_TTS_TOKEN` | motor | Token bearer do provedor de TTS externo. Vale mais do que o salvo. O companion a define quando inicia o motor |
| `VOICEMATE_TTS_START_COMMAND` | motor | Linha de comando que inicia o provedor de TTS externo quando nada escuta no endereço dele. O companion a define quando inicia o motor e a lista em `WSLENV`, para que chegue ao motor dentro do WSL. Nunca registrada no log |
| `VOICEMATE_STT_TOKEN` | motor | Token bearer do provedor de STT externo. Vale mais do que o salvo |
| `VOICEMATE_STT_START_COMMAND` | motor | Linha de comando que inicia o provedor de STT externo quando nada escuta no endereço dele. Nunca registrada no log |
| `VOICEMATE_API_TOKEN` | motor, companion | Substitui o arquivo do token. Só para testes |
| `VOICEMATE_CLAUDE_CLI` | companion (assistente) | Caminho ou nome de comando da CLI do Claude Code que o assistente inicia. Sem ela, o VoiceMate procura `claude` no `PATH`, depois em `~/.local/bin` (`claude.exe`, o instalador nativo) e em `%APPDATA%\npm` (`claude.cmd`, npm). Defina com o caminho completo quando o `claude` estiver em outro lugar |
| `CLAUDE_CONFIG_DIR` | companion (assistente) | O Claude home, onde a skill é instalada e as conversas são lidas; padrão `~/.claude` |
| `PULSE_LATENCY_MSEC` | motor (Linux, WSL2) | Buffer de áudio do PulseAudio; definida como `200` quando ausente |
| `CT2_CUDA_ALLOCATOR` | motor (CTranslate2-ROCm) | Definida como `cub_caching` quando ausente (solução para uma falha de memória no gfx1201) |
| `MIOPEN_FIND_MODE`, `MIOPEN_USER_DB_PATH`, `PYTORCH_TUNABLEOP_*` | motor (só AMD) | Definidas quando ausentes; os caches vão para `~/.cache/voicemate` |
| `PULSE_SERVER` | `make doctor` | Conferida no WSL2 (esperado `unix:/mnt/wslg/PulseServer`) |
| `APPDATA`, `LOCALAPPDATA` (Windows); `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` (Linux) | companion | Pastas base dos arquivos do companion |
| `LC_ALL`, `LC_MESSAGES`, `LANG`, `LANGUAGE` | companion no Linux | Idioma da interface quando "Idioma:" é automático. No Windows, o idioma de exibição do Windows decide |

## Token da API e porta

- A API HTTP escuta só em `127.0.0.1`, porta `47821` por padrão. O WSL2 a encaminha para o loopback do Windows.
- Requisições com um cabeçalho `Origin` (navegadores) ou um `Host` que não é loopback recebem `403`. `/trigger` aceita só POST.
- Com `--api-token`, o motor cria `~/.config/voicemate/api-token` (modo 0600, 32 bytes aleatórios) quando ele não existe, e toda rota, exceto `/health`, precisa de `Authorization: Bearer <token>`. No Windows o companion lê o arquivo pelo `wsl.exe`, e o lê de novo depois de uma recusa, no máximo a cada 30 segundos.
- `make run` e o serviço do systemd rodam sem token; `make run-engine` (o companion) roda com ele. Os scripts de atalhos antigos só funcionam com um motor sem token.

## Arquivos e logs

| Arquivo | Windows | Linux |
|---|---|---|
| `companion.toml` | `%APPDATA%\VoiceMate\` | `${XDG_CONFIG_HOME:-~/.config}/voicemate/` |
| `companion.log`, `engine.log` (rotacionados, 5 MB, 3 cópias) | `%LOCALAPPDATA%\VoiceMate\logs\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/logs/` |
| `pending.json` (a lista **Não copiadas**; guarda texto de transcrição) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-session.json` e a pasta `assistant\` (id da sessão, pasta de trabalho do assistente) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-inbox.json` (respostas guardadas para "Ouvir o que está pronto"; guarda o texto delas), `assistant-conversations\` (as conversas que o assistente iniciou), `assistant-personality.md` (a personalidade que você escreve) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-waiting.json` (a lista "Aguardando", com prazos e até onde cada transcrição foi lida), `assistant-instructions.json` (as suas instruções permanentes; um arquivo que não pode ser lido é guardado como `.bad`) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `desktop-profile\` (os perfis do Claude Desktop baixados e importados) | `%LOCALAPPDATA%\VoiceMate\` | não usado |
| `assistant.json` (lido pela skill), a skill `voicemate-assistant` | `%USERPROFILE%\.voicemate\`, `%USERPROFILE%\.claude\skills\` | `~/.voicemate/`, `~/.claude/skills/` |
| Sinais sonoros renderizados | `%LOCALAPPDATA%\VoiceMate\cues\` | `${XDG_CACHE_HOME:-~/.cache}/voicemate/cues/` |
| Escolhas do motor e token da API | dentro do WSL: `~/.config/voicemate/config.toml` e `api-token` (motor nativo no Windows: `%USERPROFILE%\.config\voicemate\`) | `~/.config/voicemate/` |
| Caches do motor (binários e modelos do whisper.cpp, sementes de voz, caches da AMD) | dentro do WSL: `~/.cache/voicemate/` (motor nativo no Windows: `%USERPROFILE%\.cache\voicemate\`) | `~/.cache/voicemate/` |
| Log do serviço do systemd | `journalctl --user -u voicemate -f`, dentro do WSL | o mesmo comando |

Menu da bandeja > **Motor** > **Abrir logs** abre a pasta de logs.
