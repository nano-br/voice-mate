[English](troubleshooting.md) | **Português** | [Español](troubleshooting.es.md) | [Русский](troubleshooting.ru.md) | [简体中文](troubleshooting.zh-CN.md)

# Solução de problemas

Objetivo: como diagnosticar problemas do VoiceMate, com as mensagens exatas que o aplicativo mostra e o que fazer em cada uma. Voltar ao [README](../README.pt-BR.md).

## Primeiros passos

1. **Leia os logs.** Menu da bandeja > **Motor** > **Abrir logs** abre a pasta com `companion.log` e `engine.log` (Windows `%LOCALAPPDATA%\VoiceMate\logs`, Linux `~/.local/state/voicemate/logs`). O `engine.log` guarda tudo o que o motor imprimiu.
2. **Rode `make doctor`** na pasta do motor (dentro do WSL, para o companion). Ele nunca para no primeiro problema: cada verificação imprime ✓ ou ✗, e cada verificação que falha imprime uma linha `fix:` com o comando a rodar.
3. **Reinicie o motor:** **Motor** > **Reiniciar o motor**. Isso também zera o supervisor depois de "O VoiceMate parou de tentar".

### O que o `make doctor` verifica

Nesta ordem: a plataforma detectada; só no WSL2, o socket do PulseAudio do WSLg, `PULSE_SERVER`, o plugin de ALSA para PulseAudio, as ferramentas nativas da área de transferência (`wl-copy`, `xclip`) e a interoperabilidade do WSL para o `clip.exe`; a biblioteca de áudio, o dispositivo de entrada e o de saída; o gatilho dos atalhos (acesso a `/dev/input` para o `evdev`, `DISPLAY` para o `pynput`, o daemon HTTP no WSL2, ou os hooks do Windows); o binário, o modelo e o VAD do whisper.cpp; o dispositivo Vulkan (Linux e WSL2); a CLI do Claude (`node` e `claude`, ignorada quando o fluxo salvo é `clipboard`); o `espeak-ng` quando o Kokoro está instalado; o PyTorch com GPU.

## Mensagens do companion

| Mensagem (título da notificação) | Significado | O que fazer |
|---|---|---|
| "O VoiceMate está iniciando" | Um atalho foi apertado enquanto o motor ainda carrega o modelo (de 10 a 60 segundos, bem mais na primeiríssima inicialização, que baixa o modelo) | Espere e aperte de novo |
| "Pasta do motor não encontrada" | Nenhuma cópia do motor nas pastas em que o companion procura | Clone o motor numa delas ([installation.md](installation.md#2-install-the-engine)) ou defina "Pasta do motor:" em Configurações > **Geral** |
| "Não foi possível iniciar o motor" | O `wsl.exe` ou o `make run-engine` falhou | Veja o `engine.log`; confira se `make` e `poetry` funcionam num shell de login (`wsl -e bash -lc "make --version && poetry --version"`) |
| "O VoiceMate parou de tentar" | O motor falhou vezes demais (mais de 5 reinicializações em 15 minutos, 3 esgotamentos de tempo de início seguidos, ou mais de 3 reinicializações do WSL em 1 hora) | Leia o `engine.log`, corrija a causa e depois use **Reiniciar o motor**. Na primeiríssima inicialização, um download lento do modelo pode esgotar os 240 segundos de um início do motor três vezes seguidas: rode `make run` uma vez na pasta do motor (dentro do WSL), pare com `Ctrl+C` quando ele terminar de carregar e então use **Reiniciar o motor** |
| "Motor desatualizado" / "O motor é mais antigo que este aplicativo. Reinicie ou atualize o motor." | A cópia do motor é mais antiga que o companion | Atualize-a (`git pull`, depois `make setup`) e então **Reiniciar o motor** |
| "Motor executado pelo systemd" | O serviço `voicemate` do systemd roda o motor, então o companion se conecta a ele | Mantenha assim, ou rode `systemctl --user disable --now voicemate` no WSL para deixar o VoiceMate gerenciar o motor |
| "Acesso ao motor negado" | O motor recusou o token da API | O VoiceMate lê `~/.config/voicemate/api-token` de novo a cada 30 segundos; se persistir, reinicie o motor |
| "Motor inacessível" / "Motor ocupado" | O motor não respondeu a um atalho (a tempo) | O VoiceMate continua tentando reconectar; veja o `engine.log` se durar |
| "Atalho indisponível" / "Em uso por outro aplicativo" (aba Atalhos) | Outro programa está com a combinação. Muitas vezes é o script antigo de atalhos do VoiceMate | Feche o script e remova-o de `shell:startup`, ou escolha outros atalhos |
| "Sem microfone" | O Windows não tem microfone | Conecte um |
| "Microfone indisponível" | O motor não conseguiu abrir o microfone | No Windows: permita que aplicativos de desktop usem o microfone nas configurações de privacidade do Windows, e feche qualquer aplicativo que o esteja usando com exclusividade. Se o áudio do WSL travou, reinicie o WSL |
| "O áudio do WSL parou" | O PulseAudio do WSLg não responde | Veja [Áudio do WSL](#áudio-do-wsl-e-o-microfone) |
| "Nada transcrito" / "Nada gravado" | Nenhuma fala foi detectada, ou a gravação não captou áudio | Confira o microfone e o nível de entrada |
| "Transcrição lenta" | A transcrição roda bem mais devagar que o áudio, em geral sem GPU | `make doctor` (PyTorch com GPU, dispositivo Vulkan); veja [wsl2.md](wsl2.md#speech-to-text-performance-on-wsl2) |
| "Falha na transcrição" | O backend de fala para texto levantou um erro | Leia o `engine.log` |
| "O Claude não respondeu" | A requisição ao Claude falhou | Leia o `engine.log`; confira se o `claude` funciona no WSL (`claude -p "ping"`) |
| "Não copiado para a área de transferência" | Outro aplicativo manteve a área de transferência travada | O texto está em **Não copiadas**: clique nele lá para copiar |
| "Falha ao copiar" | Copiar de Recentes ou de Não copiadas falhou | Tente de novo |
| "Configurações redefinidas" | O `companion.toml` não pôde ser lido; o VoiceMate iniciou com os padrões e guardou o arquivo antigo como `companion.toml.broken-<timestamp>` | Compare e corrija o arquivo antigo, ou fique com os padrões |
| 'Lista "Não copiadas" redefinida' | O `pending.json` não pôde ser lido; o arquivo antigo (com os textos) foi guardado como `pending.json.broken-<timestamp>` | Recupere os textos do arquivo antigo se precisar deles |

## Modo de transcrição

| Mensagem | Significado | O que fazer |
|---|---|---|
| "Instalar o modo CPU?" / "Instalar o modo GPU?" | O modo que você escolheu não está instalado no ambiente do motor | **Instalar** o baixa (o tamanho está na pergunta) e mostra a saída. Cancelar mantém o modo atual |
| "A instalação falhou. Os detalhes estão abaixo; o motor continua no modo atual." | O `make stt-install` terminou com erro | Leia a saída na janela, corrija a causa (rede, espaço em disco, ROCm) e tente de novo, ou rode `make stt-install MODE=gpu` na pasta do motor |
| "Sem modo GPU neste computador" | A verificação não achou placa de vídeo que o VoiceMate possa usar | Use o modo CPU |
| "O modo GPU não está funcionando. Instale pelo menu Transcrição." (janela de status) | O modo GPU caiu para a CPU | Menu da bandeja > "Transcrição" > "GPU (mais rápida)" oferece a instalação; o `make doctor` mostra as verificações da GPU |
| "Troca de modo de transcrição em andamento" | Uma verificação ou uma instalação ainda está rodando | Espere terminar e tente de novo |
| "Não foi possível trocar o modo de transcrição" | O motor não conseguiu reiniciar com o modo novo | Leia o `engine.log` |

## Assistente de voz

| Sintoma | Causa | O que fazer |
|---|---|---|
| "O Claude Code não foi encontrado", e um prompt de voz responde "O assistente não conseguiu iniciar" | O VoiceMate, iniciado no login, recebe o `PATH` guardado no registro do Windows. O instalador nativo do Claude Code põe o `claude.exe` em `%USERPROFILE%\.local\bin`, uma pasta que muitas vezes só um perfil de shell adiciona ao `PATH`, então um terminal encontra o `claude` e o VoiceMate não | Nada a fazer numa versão atual: o VoiceMate também procura em `~/.local/bin` e em `%APPDATA%\npm`. Para conferir, rode `where claude` num terminal: se não imprimir nada, instale o Claude Code. Se o `claude` estiver em outro lugar, defina `VOICEMATE_CLAUDE_CLI` com o caminho completo ([configuration.pt-BR.md](configuration.pt-BR.md#variáveis-de-ambiente)) e aperte **Reiniciar** na janela do assistente |
| "Você não está logado no Claude Code" | Nenhum login do Claude neste computador | Rode `claude auth login` num terminal e depois **Reiniciar**. O login do Claude Desktop não conta |
| "O assistente não conseguiu iniciar" ou "O assistente parou" | O processo falhou ou terminou | Abra o `companion.log` (menu da bandeja > **Motor** > **Abrir logs**), corrija a causa e depois **Reiniciar** |
| "O assistente não pôde ser preparado" | O VoiceMate não conseguiu instalar a skill nem escrever `~/.voicemate/assistant.json` | Confira se a sua pasta de usuário e `~/.claude` aceitam escrita; o log tem os detalhes |
| Sem o link "Abrir no claude.ai", nada no celular | O Remote Control não pôde ser ligado. O assistente continua funcionando neste computador | Reinicie a conversa: Configurações > **Assistente**, defina "Modo do assistente:" como desligado, aplique e ligue de novo. O `companion.log` diz o que a requisição respondeu. A requisição não é documentada e pode mudar entre versões do Claude Code (veja a [pesquisa](research/claude-code-client.md#61-a-voicemate-owned-session-in-the-desktop-tested-works)) |
| O assistente responde no idioma errado, ou sem nome | A skill não conseguiu ler `~/.voicemate/assistant.json` e diz que as configurações do assistente do VoiceMate não foram encontradas | Reinicie a conversa (Configurações > **Assistente**, desligado, aplique e ligue de novo): o VoiceMate escreve o arquivo de novo quando a conversa inicia. Confira se o arquivo está na sua pasta de usuário (`~/.voicemate/`) |
| Não há a entrada "Assistente" no menu da bandeja | O assistente está desligado | Configurações > **Assistente** > "Modo do assistente:" > "Conversa própria do VoiceMate" |
| "Mais de uma conversa combina com ..." ou "Nenhuma conversa combina com ..." | Um comando escrito não conseguiu escolher um título | Escreva mais do título, ou desligue "Entender comandos de voz no texto digitado" |
| Um arquivo de personalidade é recusado | Não é um arquivo `.md` existente | Escolha um arquivo Markdown com "Procurar..." |
| "Nenhuma conversa do Claude Desktop foi escolhida" | O modo é "Uma conversa do Claude Desktop" e "Conversa do Desktop:" está vazia | Digite o título exato da barra lateral do Claude Desktop em Configurações > **Assistente** |
| "O assistente não pôde ser preparado" no modo Desktop, e o log diz "no Claude Desktop conversation titled ..." | Nenhuma conversa do Desktop com transcrição tem esse título exato | Confira o título letra por letra na barra lateral do Claude Desktop; uma conversa que nunca teve mensagem ainda não tem transcrição |
| "O assistente não conseguiu iniciar" no modo Desktop, e o log diz `window_missing`, `conversation_missing` ou `ambiguous` | O Claude Desktop não está aberto, a conversa não está na barra lateral dele, ou duas têm esse título | Abra o Claude Desktop, renomeie um de dois títulos iguais e aperte **Reiniciar** |
| O log diz `locked` ou `focus_lost`, e uma mensagem não chegou ao Desktop | A tela estava bloqueada, ou outra janela tomou o foco enquanto o VoiceMate digitava. Nada foi digitado | Desbloqueie a tela, não toque no teclado durante os 2 segundos que uma mensagem leva e envie de novo |
| Um rascunho que você digitava numa conversa do Desktop sumiu | Uma mensagem do assistente apaga o rascunho da conversa em que escreve | Não digite na conversa que o assistente usa (o modo Desktop), nem numa em que ele está prestes a escrever |
| Depois de uma atualização do Claude Desktop o modo Desktop deixa de achar os controles dele | A atualização mudou a janela, então o perfil não combina mais | Configurações > **Assistente** > **Verificar agora** para um perfil mais novo; se não houver, veja [desktop-profile.md](desktop-profile.md#writing-a-profile-for-a-new-desktop-version) |
| "Problema: ..." sob o perfil do Claude Desktop | O perfil importado ou baixado foi recusado (o motivo nomeia o campo) | Corrija o arquivo e **Importar...** de novo, ou aperte **Usar o padrão** |
| O assistente não consegue navegar, abrir pull requests nem usar outras ferramentas só do Desktop | Em "Conversa própria do VoiceMate" ele só tem as ferramentas `voicemate` | Peça que ele passe a tarefa a uma conversa do Desktop, ou troque para o modo Desktop |
| O assistente está mudo, e a janela diz "Nenhuma voz disponível" | Nada pode falar: o motor está parado ou sem voz (o motor de voz dele não é o Kokoro nem um provedor personalizado, ou o provedor está fora do ar) e o Windows não tem voz para o idioma, ou "Origem da voz:" é "A voz do motor" | Em Configurações > **Assistente**, defina "Origem da voz:" como "As vozes do Windows" (ou "Automática") e aperte **Testar**; acrescente uma voz em Configurações do Windows > Hora e idioma > Fala. Ou confira Configurações > **Voz** e o motor de voz do motor ([protocolo](tts-provider-protocol.md#the-engine-speaks-the-same-protocol)); "Falar as respostas" precisa estar ligado. As respostas continuam legíveis na janela |
| A voz do assistente para quando eu gravo e volta depois | É de propósito: a voz nunca fala por cima do microfone. Ela espera enquanto você grava e enquanto a gravação é transcrita, e depois diz de novo a frase que foi cortada e o que estava na fila | Nada a corrigir. Um novo prompt ao assistente, "para de falar" ou um atalho do assistente a silencia de vez |
| O botão **Falar** na janela do assistente está desativado | O assistente não está pronto (desligado, iniciando, respondendo ou com problema), ou o motor ainda está transcrevendo a última gravação | Espere "Pronto" na janela. Depois de um erro, aperte **Reiniciar** |
| Uma resposta de outra conversa nunca chegou | Só são acompanhadas as conversas em que o assistente escreveu, que ele iniciou ou que ele foi pedido para observar. Uma espera termina depois de uma hora sem turno novo, uma observação que você pediu depois de 12 horas, e o assistente recebe uma nota quando qualquer uma termina | Pergunte ao assistente o que está pendente. Com "Só guardar" ou "Só tocar um som" a resposta espera em **Respostas aguardando**, não na conversa |
| O assistente diz que não conseguiu enviar a uma sessão de terminal ou IDE (a central recebeu `not sent (in use)` ou `not sent (busy)`) | `in use`: a sessão está aberta num programa, ou a transcrição dela mudou nos últimos 2 minutos. `busy`: a última mensagem do VoiceMate para ela ainda está sendo respondida, ou foi respondida agora há pouco | Feche a sessão lá ou espere, e peça de novo. Mais em [assistant.pt-BR.md](assistant.pt-BR.md#-problemas-comuns) |
| O ícone da bandeja tem um selo azul | Há respostas de outras conversas aguardando | Use "Ouvir o que está pronto" (menu da bandeja, `Ctrl+Alt+R`) ou abra a janela |
| Um atalho do assistente diz "Em uso por outro aplicativo" | `Ctrl+Alt+S`, `Ctrl+Alt+W` ou `Ctrl+Alt+R` está ocupado | Configurações > **Atalhos**: mude-o ou apague-o |
| O assistente recusa mudar uma configuração por voz | Ela não está no catálogo (atalhos, arquivos, pastas e o motor ficam de fora), ou o valor não é um dos permitidos | Mude nas Configurações. A lista está em [usage.pt-BR.md](usage.pt-BR.md#mudar-o-voicemate-por-voz) |

## Áudio do WSL e o microfone

- Abrir o microfone nunca trava um atalho: roda em segundo plano. Se ele não abrir em 6 segundos, o motor toca o sinal de erro, volta ao repouso e informa "Microfone indisponível".
- No WSL2 o motor sonda o PulseAudio do WSLg (`pactl info`) a cada 20 segundos e informa o áudio como funcionando, não funcionando ou desconhecido ("Áudio:" na janela de status).
- Quando o Windows perde o microfone, o PulseAudio do WSLg muitas vezes trava de vez, mesmo depois de o microfone voltar (`pactl info` dá `Connection failure: Timeout`). Só o `wsl --shutdown` o traz de volta.

O companion lida com isso com a configuração "Reiniciar o WSL se o áudio falhar:":

| Configuração | Comportamento |
|---|---|
| "Automaticamente" (padrão) | Reinicia o WSL sozinho, a menos que outras distros do WSL estejam rodando: o `wsl --shutdown` para todas elas, o Docker inclusive, então nesse caso ele pergunta antes |
| "Perguntar antes" | Mostra "O WSL precisa ser reiniciado" e o item de menu "Reiniciar o WSL agora..." |
| "Nunca" | Não faz nada; reinicie à mão com **Motor** > **Reiniciar o WSL...** |

Uma reinicialização do WSL é desejada quando o áudio está fora em duas sondagens seguidas, ou quando o microfone falha enquanto o Windows tem um dispositivo de captura e o áudio não está funcionando. Ela nunca acontece enquanto o Windows não tem microfone: o companion espera por um (confere a cada 3 segundos). Antes de reiniciar, espera até 30 segundos o motor ficar em repouso, depois para o motor, roda `wsl --shutdown` e inicia o motor de novo.

Sem o companion: rode `wsl --shutdown` no PowerShell e depois `make run` de novo.

## Atalhos

- **Windows com o companion:** o companion registra `Ctrl+Alt+V` e `Ctrl+Alt+A`. Se uma combinação mostra "Em uso por outro aplicativo", outro programa é dono dela.
- **Motor nativo no Windows (`make run`):** o Windows pode remover em silêncio o hook de teclado de baixo nível quando o sistema está sob carga pesada. O motor o reinstala a cada 60 segundos (`--listener-refresh-seconds`), e um watchdog reinicia um motor travado (`--watchdog-timeout`).
- **Wayland:** o gatilho `evdev` precisa do seu usuário no grupo `input` (`sudo usermod -aG input $USER`, e depois entre de novo na sessão).
- **X11:** o gatilho `pynput` precisa do `DISPLAY`.

## Problemas do motor

As mensagens do motor citadas neste guia estão em inglês. O motor as imprime no idioma de `--output-lang`, cujo padrão é o português (`pt-BR`), ou de `VOICEMATE_LANG`, que vale mais. Um motor que você inicia com um `make run` simples portanto registra em português: acrescente `ARGS="--output-lang en"` (ou defina `VOICEMATE_LANG=en` no shell que o roda) para ler o texto em inglês. O companion passa a opção sozinho, a partir de "Idioma do ditado:" nas Configurações.

| Sintoma | Causa | Correção |
|---|---|---|
| "Could not open the HTTP API on 127.0.0.1:{port}" no log do motor | A porta está ocupada, muitas vezes por outro motor | Pare o outro motor, ou escolha outra porta (`--daemon-port`, e `daemon_port` no `companion.toml`) |
| "Claude flow disabled: extra 'claude' not installed." | Falta o módulo `claude` | `poetry install --extras claude`, ou `make configure` |
| "Failed to start Claude (run `claude login`)" | A CLI do Claude não existe ou está sem login | Instale-a e faça o login ([installation.md](installation.md#claude-code-cli-for-the-claude-flow)) |
| "TTS disabled: engine '{engine}' packages not installed." | Falta o módulo do motor de TTS | Instale o módulo nomeado na linha seguinte do log, ou rode com `--no-tts` |
| A fala em inglês é transcrita errado (como se fosse português) | Um motor iniciado à mão fixa o Whisper em português por padrão | `make run ARGS="--output-lang en"` ou `--transcription-language en`. Com o companion, defina "Idioma do ditado:" em Configurações > **Geral** |
| A transcrição está de 10 a 50 vezes mais lenta que o esperado | O backend caiu para a CPU | `make doctor` (PyTorch com GPU); na AMD, o `rocminfo` precisa listar a GPU |
| O CTranslate2-ROCm falhou na compilação | Pacotes de desenvolvimento do ROCm incompletos, ou (ROCm 7) uma compilação que escolheu o gcc: "language hip not recognized" | `make doctor`; instale o HIP SDK do ROCm; `git pull` e `make configure` tentam de novo, agora com o clang do ROCm. Veja [wsl2.md](wsl2.md#ctranslate2-rocm-faster-whisper-on-the-gpu-optional-best-quality) |
| "Conv1D on GPU currently requires the cuDNN library" no log do motor, e a transcrição na GPU AMD falha | Um CTranslate2-ROCm compilado sem o MIOpen (o equivalente AMD do cuDNN) no ROCm 7 | `git pull`, depois `make configure` e aceite a compilação do CTranslate2-ROCm de novo: a compilação agora inclui o MIOpen. Até lá o motor cai para o openai-whisper (`ct2_rocm_ok = false`) |
| O idioma da interface não é o que você escolheu | O idioma muda depois de uma reinicialização | Responda "Reiniciar agora" em "Reiniciar o VoiceMate?" |

## Scripts de atalhos antigos (sem o companion)

O script PowerShell `scripts/windows/voicemate-hotkeys.ps1` imprime "daemon offline" quando nada responde na porta: inicie o motor no WSL com `make run` e aperte o atalho de novo. Esses scripts só funcionam com um motor iniciado sem token (`make run`), nunca com `make run-engine`.
