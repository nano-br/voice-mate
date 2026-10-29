[English](README.md) | **Português** | [Español](README.es.md) | [Русский](README.ru.md) | [简体中文](README.zh-CN.md)

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/banner-light.png">
  <img alt="VoiceMate" src="docs/assets/brand/banner-light.png" width="640">
</picture>

**Aperte um atalho, fale, cole. Ditado por voz local para a área de transferência, com o Whisper rodando no seu próprio computador.**

[![CI](https://github.com/nano-br/voice-mate/actions/workflows/ci.yml/badge.svg)](https://github.com/nano-br/voice-mate/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/nano-br/voice-mate?include_prereleases&sort=semver&style=flat)](https://github.com/nano-br/voice-mate/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?style=flat&logo=python&logoColor=white)](pyproject.toml)
[![Platforms](https://img.shields.io/badge/platforms-Windows%2010%2F11%20%7C%20Linux%20%26%20WSL2%20(experimental)-0078D6?style=flat)](#plataformas-e-gpus)
[![Languages](https://img.shields.io/badge/languages-en%20%7C%20pt--BR%20%7C%20es%20%7C%20ru%20%7C%20zh--CN-2EA44F?style=flat)](#idiomas)

[Baixar](https://github.com/nano-br/voice-mate/releases/latest) · [Início rápido](#início-rápido) · [Documentação](#documentação) · [Changelog](CHANGELOG.md)

<img src="docs/assets/screenshots/pt-BR/hero.png" width="763" alt="A janela de status do VoiceMate com as listas Não copiadas e Recentes, ao lado do menu da bandeja">

</div>

## Por que o VoiceMate

Criei o VoiceMate porque acredito numa coisa simples: quanto mais rápido e fácil é expressar uma ideia para uma IA, e quanto mais detalhes você dá, melhor o resultado. Falar é muito mais rápido do que digitar. Quando você pensa em voz alta, explica a ideia como explicaria a outra pessoa, com muito mais contexto do que jamais digitaria.

O VoiceMate transforma fala em texto na hora, pronto para qualquer ferramenta de IA: um prompt para um assistente de código, uma mensagem num chat, qualquer coisa que dê para colar. Aperte um atalho, fale naturalmente, aperte de novo e cole. O Whisper roda localmente, então o seu áudio fica no seu computador. O ditado não precisa de serviço na nuvem nem de mensalidade.

O ditado para a área de transferência é o coração do VoiceMate. Uso no trabalho de verdade, praticamente todo dia útil, desde março de 2026. Ele nasceu e foi testado a fundo no Windows com uma GPU NVIDIA. Quando troquei minha GPU por uma AMD, adaptei o VoiceMate para AMD via WSL2 e Linux, e hoje ele também é testado lá. Esse caminho ainda é **experimental**.

Um segundo módulo veio depois: uma conversa por voz com o Claude. Você fala, o Claude responde, e a resposta é lida em voz alta com síntese de voz (TTS). Esse módulo é opcional e **experimental**.

Um terceiro está começando: um **assistente de voz**. É uma conversa do Claude que sabe o que as minhas outras conversas estão fazendo, pergunta coisas a elas por mim e avisa, por voz, quando elas respondem. Ele vive numa conversa que o VoiceMate inicia ou numa que eu já tenho no Claude Desktop. É **experimental** e vem desligado.

## Sumário

[Recursos](#recursos) · [Capturas de tela](#capturas-de-tela) · [Início rápido](#início-rápido) · [Uso](#uso) · [Assistente de voz](#assistente-de-voz-experimental) · [Configuração](#configuração) · [Arquitetura](#arquitetura) · [Solução de problemas](#solução-de-problemas) · [Documentação](#documentação) · [Como contribuir](#como-contribuir) · [Licença](#licença)

## Recursos

**Ditado (o essencial)**
- Um único atalho inicia e para a gravação (`Ctrl+Alt+V` por padrão). O texto vai para a área de transferência, pronto para colar.
- Modelos Whisper locais, `large-v3-turbo` por padrão. Nenhum áudio sai do seu computador.
- O idioma do ditado fica fixado para dar resultados estáveis, e termos técnicos em inglês no meio de outros idiomas continuam sendo transcritos.
- Sons de aviso para início, transcrição, copiado, alerta e erro. Uma gravação para sozinha após 10 minutos (configurável).

**Aplicativo companion para Windows**
- Um ícone na bandeja que mostra o estado num relance, uma janela de status com as transcrições recentes e configurações para atalhos, sons de aviso, notificações, idioma e o motor.
- Entrega verificada na área de transferência: o aplicativo escreve o texto, lê de volta e compara. Um texto que não consegue chegar à área de transferência fica em **Não copiadas**, mesmo depois de reiniciar.
- Um supervisor que inicia o motor dentro do WSL2, reinicia o motor quando ele falha e reinicia o WSL quando o áudio dele para.
- Um instalador por usuário (sem pedir permissão de administrador) em 5 idiomas.
- Dois modos de reconhecimento de fala, que você troca no menu da bandeja ("Transcrição") ou em Configurações: **CPU** (deixa a GPU livre para outros trabalhos) e **GPU** (a mais rápida). Cada modo guarda o próprio modelo Whisper. Um modo que ainda não está instalado é instalado pelo aplicativo depois de perguntar, mostrando o tamanho do download, e só dentro do ambiente do próprio motor.

<a id="idiomas"></a>**Idiomas**: o companion, as mensagens do motor e o instalador falam inglês, português do Brasil, espanhol, russo e chinês simplificado.

<a id="plataformas-e-gpus"></a>**Plataformas e GPUs**
- Windows 10/11 com NVIDIA (CUDA): o caminho original, testado a fundo. O motor roda nativamente pela linha de comando.
- **Experimental:** o motor dentro do WSL2 (a configuração do companion), GPUs AMD (ROCm, Vulkan) no WSL2, no Linux e no Windows, e Linux (X11, Wayland) com um companion opcional.
- Em qualquer plataforma, a CPU serve de alternativa. O `make setup` detecta a plataforma e a GPU e instala o build do PyTorch e o backend de reconhecimento de fala correspondentes.

**Conversa por voz com o Claude (experimental)**
- `Ctrl+Alt+A` envia a sua fala ao Claude pela CLI do Claude Code (com o seu próprio login: o texto transcrito vai para a Anthropic, o áudio não). A resposta é copiada para a área de transferência e lida em voz alta.
- Motores de TTS: OmniVoice (o usado quando nada foi salvo), Kokoro (o que o `make setup` propõe) e VoxCPM2. Ou o seu: qualquer serviço que siga o [protocolo de provedor de TTS](docs/tts-provider-protocol.md) (Configurações > Voz > Provedor personalizado). A conversa continua entre os turnos; qualquer atalho interrompe a resposta e inicia uma nova gravação.

**Assistente de voz (experimental, desligado por padrão)**
- Uma conversa central do Claude em dois modos: a conversa própria do VoiceMate (um processo do Claude Code que o VoiceMate inicia, visível também no Claude Desktop e no celular) ou uma conversa do Claude Desktop que você já tem (escrita por um "flash" rápido de teclado, só no Windows). Ele lista, lê e envia mensagem às suas outras conversas e inicia novas em segundo plano.
- Fale com ele em `Ctrl+Alt+S` e ouça na voz do motor (Kokoro ou o seu provedor). Comandos de voz em português, inglês e espanhol leem a última resposta, trocam de conversa, enviam para ela ou falam direto com ela. Uma janela mostra o chat e as respostas esperando; um selo azul na bandeja e o som "Resposta pronta" avisam quando uma chega, e `Ctrl+Alt+R` lê o que está pronto.
- Ele muda por voz uma lista fixa de configurações do VoiceMate, aceita uma personalidade (um arquivo, uma nota num cofre do Obsidian ou um texto que você escreve) e usa uma skill em português ou inglês que também funciona sozinha em qualquer conversa do Claude Code. Veja [Assistente de voz](#assistente-de-voz-experimental).

## Capturas de tela

| O menu da bandeja | "Reiniciar o WSL?" e "Reiniciar o VoiceMate?" |
|:---:|:---:|
| <img src="docs/assets/screenshots/pt-BR/tray-menu.png" width="279" alt="O menu da bandeja"> | <img src="docs/assets/screenshots/pt-BR/dialog-restart-wsl.png" width="516" alt="A pergunta Reiniciar o WSL?"><br><img src="docs/assets/screenshots/pt-BR/dialog-language.png" width="479" alt="A pergunta Reiniciar o VoiceMate?"> |

<p align="center"><img src="docs/assets/screenshots/pt-BR/settings-general.png" width="770" alt="A aba Geral de Configurações do VoiceMate"><br><sub>"Configurações do VoiceMate", aba "Geral". Veja também as abas <a href="docs/assets/screenshots/pt-BR/settings-hotkeys.png">"Atalhos"</a> e <a href="docs/assets/screenshots/pt-BR/settings-sounds.png">"Sons"</a>.</sub></p>

<p align="center"><img src="docs/assets/screenshots/pt-BR/settings-voice.png" width="770" alt="A aba Voz de Configurações do VoiceMate, com o motor de voz e os campos do provedor personalizado"><br><sub>"Configurações do VoiceMate", aba "Voz": o motor de voz que fala as respostas, inclusive "Provedor personalizado (HTTP)".</sub></p>

<p align="center"><img src="docs/assets/screenshots/pt-BR/assistant.png" width="568" alt="A janela do assistente do VoiceMate com uma conversa de exemplo"><br><sub>A janela do assistente (**experimental**), aberta pelo item "Assistente" do menu da bandeja. Configurações: a aba <a href="docs/assets/screenshots/pt-BR/settings-assistant.png">"Assistente"</a>. A caixa "Transcrição", com a escolha entre CPU e GPU, fica na aba "Geral" acima.</sub></p>

<p align="center"><img src="docs/assets/screenshots/pt-BR/assistant-inbox.png" width="568" alt="A janela do assistente com o grupo Respostas esperando e a lista Aguardando"><br><sub>A mesma janela com o grupo "Respostas esperando" e a lista "Aguardando".</sub></p>

<p align="center"><img src="docs/assets/screenshots/pt-BR/settings-assistant-sections.png" width="770" alt="A aba Assistente de Configurações do VoiceMate, rolada até os grupos Personalidade, Skill e Perfil do Claude Desktop"><br><sub>A aba "Assistente" rolada: os grupos "Personalidade", "Skill" e "Perfil do Claude Desktop".</sub></p>

<p align="center"><img src="docs/assets/screenshots/tray-states.png" width="584" alt="O ícone da bandeja em cada estado, numa barra de tarefas escura e numa clara, e o selo mostrado quando há respostas esperando"><br><sub>O ícone da bandeja mostra o estado com um selo, e um selo azul quando há respostas esperando. O que cada selo significa: <a href="docs/usage.md#tray-icon-and-menu">Uso, ícone e menu da bandeja</a>.</sub></p>

## Início rápido

### Windows, com o instalador

O instalador traz só o aplicativo companion. O motor roda dentro de uma distro do WSL2, então configure isso primeiro (**experimental**; guia completo em [docs/installation.md](docs/installation.md)).

1. No PowerShell, instale o WSL2 com o Ubuntu: `wsl --install -d Ubuntu-24.04`, e depois abra o Ubuntu (se você já tinha outra distro do WSL, rode também `wsl --set-default Ubuntu-24.04`, ou defina "Distro do WSL:" em Configurações depois). Dentro dele, instale os pacotes de áudio; o Python 3.12 (o Ubuntu 24.04 já traz) e o [Poetry](https://python-poetry.org/docs/#installation) precisam estar no `PATH` de um shell de login também:
   ```bash
   sudo apt install -y libportaudio2 libasound2-plugins pulseaudio-utils wl-clipboard git make
   ```
2. Clone o motor e rode a configuração guiada:
   ```bash
   git clone https://github.com/nano-br/voice-mate.git ~/voice-mate
   cd ~/voice-mate
   make setup    # só para ditado, responda 1 (clipboard) em "Qual fluxo principal?"
   make doctor   # todas as verificações devem mostrar ✓
   ```
   O companion procura o motor em `~/voice-mate` e em algumas outras pastas comuns ([a lista](docs/installation.md#2-install-the-engine)); em qualquer outro lugar, defina "Pasta do motor:" em Configurações. Com uma GPU AMD, instale também o driver da AMD e o ROCm para WSL ([docs/wsl2.md](docs/wsl2.md)).
3. Recomendado: ainda em `~/voice-mate`, rode `make run` uma vez e pare com `Ctrl+C` quando ele terminar de carregar. A primeira inicialização baixa o modelo Whisper, e o companion dá só 240 segundos para o motor iniciar (ele para de tentar depois de 3 estouros de tempo seguidos), o que uma conexão lenta pode ultrapassar.
4. Baixe o `VoiceMate-Setup-x.y.z.exe` da [última versão](https://github.com/nano-br/voice-mate/releases/latest) e execute. O instalador não tem assinatura de código, então o Windows SmartScreen pode avisar: escolha **Mais informações** e depois **Executar assim mesmo**. Ele instala só para o seu usuário, sem pedir permissão de administrador.
5. Abra o VoiceMate. A bandeja mostra "Iniciando o motor..." enquanto o modelo carrega (de 10 a 60 segundos depois que o modelo foi baixado), e então "Aguardando Ctrl+Alt+V".
6. Opcional: fixe na barra de tarefas. Abra o Iniciar, pesquise VoiceMate, clique nele com o botão direito e escolha **Fixar na barra de tarefas**.

### A partir do código-fonte

Você precisa do Python 3.12, do [Poetry](https://python-poetry.org/docs/#installation), do `make` do GNU (no Windows, instale antes, por exemplo com Chocolatey ou Scoop) e do `git`.

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup                           # detecta plataforma e GPU, instala o PyTorch e os módulos que você escolher
make doctor                          # verifica microfone, áudio, atalhos e GPU, e mostra a correção para cada problema
make run ARGS="--output-lang en"     # inicia o motor com os próprios atalhos (Windows nativo ou Linux)
```

**O motor usa português por padrão** (`--output-lang pt-BR`), o que define o idioma que o Whisper escuta, as respostas do Claude e as mensagens do motor. Passe `--output-lang en` (ou outro código) como acima; `--transcription-language` e `VOICEMATE_LANG` mudam só um deles ([configuração](docs/configuration.md)). O companion passa essas opções sozinho, veja [Uso](#uso).

Para rodar o companion a partir do código-fonte no Windows, com o motor no WSL2: `make companion-venv` uma vez, depois `make run-tray`. No Linux, rode `make setup` e `make run` como acima; para a bandeja e a janela de status opcionais, `poetry install --extras ui` e depois `make run-tray` ([detalhes](docs/installation.md#linux-experimental)).

## Uso

| Atalho | Ação no menu da bandeja | O que acontece |
|---|---|---|
| `Ctrl+Alt+V` | "Ditar" | Fala vira texto, copiado para a área de transferência |
| `Ctrl+Alt+A` | "Perguntar ao Claude" | Fala enviada ao Claude, resposta copiada e lida em voz alta (**experimental**) |
| `Ctrl+Alt+S` | "Falar com o assistente" | Fala para o assistente em vez da área de transferência (**experimental**) |
| `Ctrl+Alt+W` | "Abrir a janela do assistente" | Abre ou fecha a janela do assistente |
| `Ctrl+Alt+R` | "Ouvir o que está pronto" | O assistente lê as respostas que estão esperando |

Aperte `Ctrl+Alt+V`, fale depois do sinal de início, aperte `Ctrl+Alt+V` de novo e cole com `Ctrl+V` em qualquer lugar. O atalho que para a gravação decide para onde o texto vai. Um texto que não conseguiu chegar à área de transferência espera em **Não copiadas** (menu da bandeja e janela de status), onde um clique o copia. Para sair, escolha **Sair do VoiceMate** no menu da bandeja; um motor que o companion não iniciou continua rodando.

**Idioma do ditado.** Em Configurações > **Geral**, "Idioma do ditado:" define o idioma que você fala. O padrão é "Igual ao da interface"; "Detectar automaticamente" deixa o Whisper detectar cada gravação (o Claude continua respondendo no idioma da interface; com o Kokoro, fixe um idioma); ou escolha um idioma. O companion repassa a escolha ao motor que ele inicia, usando `--transcription-language` e `--output-lang`, e mudar o idioma reinicia o motor. Um motor ao qual o companion só se conecta (uma unidade do systemd, ou um iniciado à mão) mantém as próprias opções.

A conversa por voz com o Claude precisa da CLI do Claude Code instalada e com login feito, e do módulo `claude` escolhido no `make setup`. Veja [docs/usage.md](docs/usage.md#claude-flow-experimental).

**Modo de transcrição.** Em Configurações > **Geral** > "Transcrição", "Rodar na:" oferece "CPU (deixa a GPU livre)", "GPU (mais rápida)" ou "Automático (configuração do motor)". O menu "Transcrição" da bandeja faz o mesmo. Cada modo tem o próprio modelo ("Modelo da CPU:", "Modelo da GPU:"). Trocar reinicia o motor, e se o modo ainda não estiver instalado o VoiceMate pergunta antes (dizendo o tamanho do download) e instala numa janela que mostra a saída. Pelo terminal: `make stt-check` e `make stt-install MODE=cpu` (ou `gpu`). Números de velocidade e precisão: [docs/configuration.md](docs/configuration.md#speech-to-text-modes).

### Assistente de voz (experimental)

O assistente é uma conversa do Claude, a central, que sabe o que as suas outras conversas estão fazendo e as conduz por você: lista, lê, manda mensagem, inicia novas em segundo plano e avisa quando elas respondem. Vem desligado. Em Configurações > **Assistente**, "Modo da assistente:" escolhe onde a conversa central mora.

| | Conversa própria do VoiceMate | Uma conversa do Claude Desktop |
|---|---|---|
| O que é | Um processo do Claude Code que o VoiceMate inicia e mantém | Uma conversa que você já tem, escolhida pelo título exato em "Conversa do Desktop:" |
| Como o VoiceMate escreve nela | Direto, pelo processo | Um "flash" rápido de UI Automation: a janela do Desktop vem para a frente, o rascunho é apagado, o texto é colado e enviado, e o foco volta (cerca de 2 segundos) |
| Ferramentas | As ferramentas `voicemate`: listar, ler e enviar para conversas, iniciar uma, ajustar o modelo, parar | As ferramentas de sessão do próprio Desktop |
| Precisa de | A CLI `claude` e um login do Claude | Claude Desktop aberto e tela desbloqueada; só no Windows |
| Celular | O Remote Control fica ligado | Fora do controle do VoiceMate |

1. **Requisitos:** a CLI do Claude Code (`claude` no `PATH` do Windows, ou o caminho dela em `VOICEMATE_CLAUDE_CLI`) e um login do Claude nesse computador (`claude auth login`, uma vez). Sem isso, a janela do assistente diz o que falta.
2. **Ligar:** em Configurações > **Assistente**, escolha "Conversa própria do VoiceMate" ou "Uma conversa do Claude Desktop" em "Modo da assistente:" (e digite o título exato dela) e clique em OK. O menu da bandeja ganha o item "Assistente".
3. **Usar:** aperte `Ctrl+Alt+S`, fale, aperte `Ctrl+Alt+S` de novo, e ele responde na voz do motor. Ou abra "Assistente" e escreva (Enter envia, Shift+Enter pula linha). A janela mostra as últimas 30 mensagens e um grupo "Respostas esperando" e uma lista "Aguardando" (o que o assistente espera de outras conversas).

`Ctrl+Alt+S` fala, `Ctrl+Alt+W` abre a janela e `Ctrl+Alt+R` lê o que está pronto. "Trocar o modo do assistente" e "Voltar ao assistente" não têm tecla padrão; defina em Configurações > **Atalhos**.

**Comandos de voz.** Algumas frases são detectadas sem nenhuma IA, em português, inglês e espanhol: ler a última resposta, parar de falar, ir para uma conversa, enviar para uma conversa, falar direto com uma conversa e voltar ao assistente. O modo direto manda o que você diz direto para essa conversa até você voltar. As frases estão em [uso](docs/usage.md#voice-commands).

**Respostas de outras conversas.** "Respostas de outras conversas:" oferece "Me contar a resposta assim que chegar", "Dar um aviso curto e guardar a resposta para depois" (padrão), "Só tocar um som e guardar a resposta" ou "Só guardar". As respostas guardadas esperam na caixa de entrada (50 no máximo, mantidas entre reinícios), na janela em "Respostas esperando", com um selo azul no ícone da bandeja e o som "Resposta pronta". Nada é anunciado enquanto você grava, transcreve ou ouve uma resposta.

**Conversas que o assistente inicia.** No máximo 4 ao mesmo tempo, com o Remote Control ligado e lembradas entre reinícios. "Novas conversas usam:" e "Com o esforço:" definem o modelo e o esforço delas, e "Permissões:" define o que elas podem fazer.

> [!WARNING]
> "Acesso total, nunca perguntar (arriscado)" (`bypassPermissions`) deixa essas conversas alterar qualquer arquivo e rodar qualquer comando sem perguntar. Mantenha o padrão, a menos que você confie no que pede a elas.

**O flash do modo Desktop.** Ele toma o primeiro plano por cerca de 2 segundos, apaga qualquer rascunho nesse campo e não digita nada se a tela estiver bloqueada ou se outra janela tomar o foco. Deixe o teclado em paz durante esses 2 segundos.

**Configurações por voz.** O assistente muda 23 configurações de uma lista fixa: o modo (entre os dois), modelo, esforço, permissões, entrega das respostas, modelo e esforço das conversas novas, a conversa do Desktop, voz, comandos de voz, tags de expressão, seu nome, a origem da personalidade, a atualização do perfil, o idioma da skill, os interruptores e volumes dos sons, o idioma do ditado, o modo de transcrição e as notificações.

Atalhos, arquivos e pastas, o motor de voz e suas configurações, e desligar o assistente ficam de fora de propósito. A lista: [uso](docs/usage.md#change-voicemate-by-voice).

**Personalidade e skill.** Em Personalidade, "Origem:" é nenhuma, um arquivo Markdown (por exemplo uma nota num cofre do Obsidian) ou um texto que você escreve; "Pasta do cofre:" diz onde estão suas notas, e o assistente só as lê quando você pede. "Idioma da skill:" é automático, inglês ou português do Brasil, e "Skill personalizada:" troca a skill por uma pasta sua com um `SKILL.md`.

A skill é opcional e compartilhável: copie `app/companion/assistant/skill/pt-BR/voicemate-assistant` (ou `en`) para `~/.claude/skills` e peça "o assistente" em qualquer conversa do Claude Code. As configurações dela ficam em `~/.voicemate/assistant.json`.

**Voz.** O assistente fala com a voz do motor: Kokoro ou o seu provedor (Configurações > **Voz**, `make tts-provider-check`, [protocolo](docs/tts-provider-protocol.md)). Se o motor não consegue falar, as respostas ficam em texto e os avisos curtos usam a voz do Windows.

**Perfil do Claude Desktop.** Um arquivo JSON diz ao modo Desktop onde estão os controles, então uma atualização do Claude Desktop se resolve com um perfil novo e não com uma versão. O VoiceMate procura um mais novo quando o assistente inicia (dá para desligar); Configurações > **Assistente** tem **Verificar agora**, **Importar...**, **Exportar...** e **Usar o padrão**. Veja [desktop-profile.md](docs/desktop-profile.md).

O desenho está em [docs/research/voice-assistant-design.md](docs/research/voice-assistant-design.md), os testes com Claude Desktop e celular por trás dele em [docs/research/claude-code-client.md](docs/research/claude-code-client.md), e cada passo em [docs/usage.md](docs/usage.md#voice-assistant-experimental).

## Configuração

| O quê | Onde |
|---|---|
| Configurações do companion | Windows `%APPDATA%\VoiceMate\companion.toml`, Linux `~/.config/voicemate/companion.toml` |
| Escolhas do motor feitas no `make setup`, token da API | `~/.config/voicemate/` (dentro do WSL para o companion; `%USERPROFILE%\.config\voicemate\` para um motor rodando nativamente no Windows) |
| API HTTP do motor | `127.0.0.1:47821` |
| Logs (`companion.log`, `engine.log`) | Windows `%LOCALAPPDATA%\VoiceMate\logs`, Linux `~/.local/state/voicemate/logs` |
| Lista **Não copiadas** | `pending.json`, ao lado da pasta `logs` |
| Configurações do assistente (a skill as lê) | `~/.voicemate/assistant.json`, na sua pasta de usuário |
| Sessão do assistente, caixa de entrada, conversas que ele iniciou, texto da personalidade e cache do perfil do Desktop | `assistant-session.json`, `assistant-inbox.json`, `assistant-conversations\`, `assistant-personality.md`, `desktop-profile\` e `assistant\` ao lado de `pending.json` |

Todas as opções do motor, todas as chaves de configurações e todas as variáveis de ambiente: [docs/configuration.md](docs/configuration.md).

## Arquitetura

O VoiceMate tem duas partes. O **motor** grava, transcreve e executa o fluxo do Claude; é um daemon em Python com uma API HTTP local. O **companion** é um aplicativo de bandeja em PySide6 no Windows que cuida dos atalhos e da área de transferência e supervisiona o motor dentro do WSL2. O motor também roda sozinho pela linha de comando. Os diagramas abaixo são visões gerais simplificadas; cada um aponta para o diagrama completo em [docs/architecture.md](docs/architecture.md), que traz também os diagramas de componentes, a API HTTP e as decisões de projeto.

<details>
<summary>Contexto do sistema (visão geral)</summary>

```mermaid
flowchart TB
    user(["Usuário"])
    subgraph pc["Seu PC"]
        vm["VoiceMate<br/>ditado local"]
        desk["Área de trabalho<br/>atalhos, área de transferência, bandeja"]
        audio["Microfone e alto-falantes"]
        gpu["GPU ou CPU<br/>executa o Whisper"]
    end
    subgraph net["Internet, opcional"]
        hub["Download de modelos"]
        claude["Claude<br/>pela CLI do Claude Code"]
    end
    user -->|"Atalho, fala"| vm
    user -->|"Cola o texto"| desk
    vm -->|"Escreve na área de transferência, mostra a bandeja"| desk
    vm -->|"Grava"| audio
    vm -->|"Transcreve"| gpu
    vm -.->|"Baixa modelos"| hub
    vm -.->|"Experimental: pergunta"| claude

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    classDef ok fill:#2e7d32,stroke:#1b5e20,color:#ffffff
    classDef warn fill:#f9a825,stroke:#c17900,color:#000000
    classDef bad fill:#c0392b,stroke:#922b21,color:#ffffff
    class user person
    class vm system
    class desk,audio,gpu external
    class hub,claude optional
```

Visão geral. Diagrama completo: [docs/architecture.md, Contexto do sistema](docs/architecture.md#1-system-context).

</details>

<details>
<summary>Contêineres, Windows com WSL2 (visão geral)</summary>

```mermaid
flowchart LR
    user(["Usuário"])
    subgraph win["Windows"]
        ui["Interface do companion<br/>bandeja, status, configurações"]
        core["Núcleo do companion<br/>atalhos, supervisor, entrega"]
        clip["Área de transferência do Windows"]
    end
    subgraph wsl["Distro do WSL2"]
        api["Daemon do motor<br/>API HTTP, 127.0.0.1:47821"]
        stt["Backend do Whisper"]
        tts["TTS, opcional"]
    end
    gpu["GPU"]
    user -->|"Menu da bandeja"| ui
    user -->|"Atalho"| core
    ui <--> core
    core -->|"Escreve e verifica"| clip
    core -->|"Inicia com wsl.exe"| api
    core <-->|"HTTP, token, eventos"| api
    api --> stt --> gpu
    api -.-> tts

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    classDef ok fill:#2e7d32,stroke:#1b5e20,color:#ffffff
    classDef warn fill:#f9a825,stroke:#c17900,color:#000000
    classDef bad fill:#c0392b,stroke:#922b21,color:#ffffff
    class user person
    class ui,core,api,stt container
    class clip,gpu external
    class tts optional
```

Visão geral. Diagrama completo: [docs/architecture.md, Contêineres](docs/architecture.md#2-containers).

</details>

<details>
<summary>Um ditado, passo a passo (visão geral)</summary>

```mermaid
sequenceDiagram
    autonumber
    actor U as Usuário
    participant C as Companion
    participant E as Motor
    participant CB as Área de transferência
    rect rgba(67,141,213,0.12)
        U->>C: Atalho
        C->>E: Iniciar gravação
        E-->>C: Microfone ativo
        C->>U: Sinal de início
    end
    rect rgba(106,61,154,0.12)
        U->>C: Atalho de novo
        C->>E: Parar gravação
        E->>E: O Whisper transcreve
        E-->>C: Texto para entregar
    end
    rect rgba(46,125,50,0.12)
        C->>CB: Escrever, ler de volta, comparar
        alt Texto verificado
            C->>E: ACK entregue
            C->>U: Sinal de pronto
        else Área de transferência bloqueada por outro aplicativo
            C->>C: Guardar o texto em Não copiadas
            C->>E: ACK falhou
            C->>U: Sinal de erro e notificação
        end
    end
```

Visão geral. Diagrama completo: [docs/architecture.md, Um ditado](docs/architecture.md#4-runtime-one-dictation).

</details>

<details>
<summary>Estados da bandeja (visão geral)</summary>

```mermaid
stateDiagram-v2
    state "Parado" as Stopped
    state "Iniciando" as Starting
    state "Em execução" as Running
    state "Reiniciando" as Restarting
    state "Erro" as Error
    state "Aguardando" as Idle
    state "Gravando" as Recording
    state "Transcrevendo" as Transcribing
    state "Respondendo" as Answering
    state "Pronto" as Ready
    state "Aviso" as Warning
    [*] --> Stopped
    Stopped --> Starting : O aplicativo inicia
    Starting --> Running : Motor pronto
    Starting --> Error : Sem pasta do motor
    Running --> Restarting : Motor falhou ou reinício do WSL
    Restarting --> Running : Motor pronto
    Restarting --> Error : Falhas demais
    Error --> Restarting : Reinício manual
    Running --> Stopped : Sair
    state Running {
        [*] --> Idle
        Idle --> Recording : Atalho
        Recording --> Transcribing : Atalho de novo
        Recording --> Idle : Cancelar
        Transcribing --> Ready : Texto copiado
        Transcribing --> Answering : Fluxo do Claude
        Answering --> Ready : Resposta copiada
        Ready --> Idle : Após 3 s
        Idle --> Warning : Sem microfone ou áudio parado
        Warning --> Idle : Áudio de volta
    }

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    classDef ok fill:#2e7d32,stroke:#1b5e20,color:#ffffff
    classDef warn fill:#f9a825,stroke:#c17900,color:#000000
    classDef bad fill:#c0392b,stroke:#922b21,color:#ffffff
    class Stopped external
    class Starting,Restarting,Warning warn
    class Running,Idle,Ready ok
    class Error bad
    class Recording,Transcribing,Answering container
```

Visão geral. Diagrama completo: [docs/architecture.md, Estados da bandeja](docs/architecture.md#5-tray-states).

</details>

<details>
<summary>Assistente de voz, experimental (visão geral)</summary>

```mermaid
flowchart LR
    user(["Você"])
    subgraph pc["Seu PC, Windows"]
        win["Janela do assistente<br/>chat, respostas esperando"]
        svc["Serviço do assistente<br/>dentro do companion"]
        own["Conversa própria<br/>Claude Code, ferramentas voicemate"]
        dsk["Conversa do Desktop<br/>escrita por um flash de teclado"]
        other["Suas outras<br/>conversas"]
        inbox[("Caixa de entrada<br/>respostas esperando")]
        eng["Motor<br/>fala entra, voz sai"]
    end
    phone["Celular"]
    user -->|"Atalho de falar"| eng
    eng -->|"Transcrição"| svc
    user --> win
    win --> svc
    svc -->|"Prompts"| own
    svc -->|"Flash, depois lê"| dsk
    own -->|"Listar, ler, enviar, iniciar"| other
    dsk -->|"Ferramentas de sessão do Desktop"| other
    other -->|"Respostas"| svc
    svc -->|"Guarda ou avisa"| inbox
    svc -->|"Bloco de voz"| eng
    eng -->|"Fala, som de resposta"| user
    own <-->|"Remote Control"| phone

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    class user person
    class win,svc,own,dsk,eng container
    class inbox store
    class other,phone external
```

Os dois modos seguem o mesmo ciclo: o assistente alcança suas outras conversas, as respostas voltam pelo serviço, e o serviço as fala ou as guarda na caixa de entrada. Azul: VoiceMate; verde-azulado: dados guardados; cinza: fora do VoiceMate. Diagrama completo: [docs/architecture.md](docs/architecture.md#61-voice-assistant-experimental).

</details>

<details>
<summary>Supervisor do motor e recuperação do áudio do WSL (visão geral)</summary>

```mermaid
flowchart TD
    launch(["Início"]) --> q_health{"O motor já responde?"}
    q_health -- "Sim" --> attach["Conectar a ele, esperar ficar pronto"] --> healthy
    q_health -- "Não" --> q_dir{"Pasta do motor encontrada?"}
    q_dir -- "Não" --> failed["Erro<br/>esperar um reinício manual"]
    q_dir -- "Sim" --> spawn["Iniciar make run-engine<br/>no WSL"]
    spawn --> q_ready{"Pronto em até 240 s?"}
    q_ready -- "Sim" --> healthy["Saudável<br/>verificar a cada 5 s"]
    q_ready -- "Não" --> q_breaker
    healthy -->|"3 verificações sem resposta ou o motor encerrou"| q_breaker{"Reinícios demais?"}
    q_breaker -- "Não" --> backoff["Esperar de 2 a 120 s"]
    backoff --> launch
    q_breaker -- "Sim" --> failed
    healthy -->|"Áudio do WSL parou"| q_policy{"Reiniciar o WSL se o áudio falhar"}
    q_policy -- "Nunca" --> nothing["Não fazer nada"]
    q_policy -- "Perguntar antes" --> ask["Perguntar ao usuário"]
    q_policy -- "Automaticamente" --> q_others{"Outras distros rodando?"}
    q_others -- "Sim" --> ask
    q_others -- "Não" --> wslrestart["wsl --shutdown"]
    ask -- "Reiniciar o WSL" --> wslrestart
    wslrestart --> launch

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    classDef ok fill:#2e7d32,stroke:#1b5e20,color:#ffffff
    classDef warn fill:#f9a825,stroke:#c17900,color:#000000
    classDef bad fill:#c0392b,stroke:#922b21,color:#ffffff
    class launch system
    class q_health,q_dir,q_ready,q_breaker,q_policy,q_others component
    class attach,healthy ok
    class failed bad
    class spawn,backoff,wslrestart container
    class ask warn
    class nothing external
```

Visão geral. Diagrama completo: [docs/architecture.md, Supervisor](docs/architecture.md#6-supervisor-on-windows-and-wsl2).

</details>

## Solução de problemas

| Sintoma | O que fazer |
|---|---|
| "Iniciando o motor..." por muito tempo | A primeira inicialização baixa o modelo, o que pode levar minutos (rode `make run` uma vez no WSL, veja [Início rápido](#windows-com-o-instalador)). As seguintes levam de 10 a 60 segundos. Abra **Motor** > **Abrir logs** e leia o `engine.log`. |
| "Pasta do motor não encontrada" | Clone o motor em uma das [pastas comuns](docs/installation.md#2-install-the-engine), ou defina "Pasta do motor:" em Configurações > **Geral**. |
| Um atalho diz "Em uso por outro aplicativo" | Um script antigo de atalhos do VoiceMate pode ainda estar rodando. Feche-o e remova-o de `shell:startup`, ou escolha outros atalhos. |
| "O áudio do WSL parou" ou "Sem microfone" | Conecte um microfone. O VoiceMate reinicia o WSL conforme definido em "Reiniciar o WSL se o áudio falhar:"; para fazer isso à mão, use **Motor** > **Reiniciar o WSL...**. |
| Um texto não chegou à área de transferência | Ele está em **Não copiadas** (menu da bandeja e janela de status). Clique nele para copiar de novo. |
| A fala em inglês sai errada, ou as mensagens do motor estão em português | Um motor iniciado à mão usa português por padrão: acrescente `ARGS="--output-lang en"` ao `make run`. |
| A GPU está ocupada com outro trabalho | Escolha o modo CPU: Configurações > **Geral** > "Transcrição", ou menu da bandeja > "Transcrição". O VoiceMate pergunta antes de instalar um modo que falta. |
| O assistente diz "O Claude Code não foi encontrado" ou "O Claude Code não está com login feito" | Instale o Claude Code (ou defina `VOICEMATE_CLAUDE_CLI`), ou rode `claude auth login` num terminal, e clique em **Reiniciar** na janela do assistente. Mais em [docs/troubleshooting.md](docs/troubleshooting.md#voice-assistant-experimental). |
| O modo Desktop diz "Nenhuma conversa do Claude Desktop foi escolhida" ou "Não foi possível preparar o assistente" | "Conversa do Desktop:" está vazia, ou nenhuma conversa do Claude Desktop tem esse título exato | Digite o título exatamente como na barra lateral do Claude Desktop, abra o Claude Desktop e clique em **Reiniciar**. Mais em [docs/troubleshooting.md](docs/troubleshooting.md#voice-assistant-experimental). |
| Outro problema do lado do motor | Rode `make doctor` na pasta do motor. Ele mostra a correção para cada verificação que falhou. |

Mais problemas e as mensagens exatas: [docs/troubleshooting.md](docs/troubleshooting.md).

## Documentação

- [Installation](docs/installation.md) (em inglês): todos os caminhos de instalação, atualização e desinstalação.
- [Usage](docs/usage.md): fluxos, atalhos, ícone da bandeja, modos de transcrição, modelos, opções de voz e o assistente de voz.
- [Configuration](docs/configuration.md): opções do motor, arquivos de configurações, variáveis de ambiente, modos de reconhecimento de fala e benchmark.
- [Troubleshooting](docs/troubleshooting.md): `make doctor`, áudio do WSL, logs, mensagens conhecidas.
- [Architecture](docs/architecture.md): diagramas e decisões de projeto.
- [WSL2 and AMD](docs/wsl2.md) (experimental), [Companion design](docs/companion-app.md), [Brand](docs/brand.md), [Releasing](docs/releasing.md).
- [Perfil do Claude Desktop](docs/desktop-profile.md) e [protocolo de provedor TTS](docs/tts-provider-protocol.md): o arquivo que diz ao modo Desktop onde estão os controles, e o contrato HTTP para a sua própria voz.
- Pesquisa (trabalho experimental, em inglês): [Projeto do assistente de voz](docs/research/voice-assistant-design.md) e [VoiceMate como cliente do Claude Code](docs/research/claude-code-client.md).

## Como contribuir

Issues e pull requests são bem-vindos. Todo comando de desenvolvimento passa pelo Makefile: `make format lint test` para o motor (Ruff e mypy estrito) e `make companion-lint companion-test` para o companion. Todo texto visível ao usuário existe em 5 idiomas via gettext. Os commits seguem o Conventional Commits e explicam o contexto. Leia o [CONTRIBUTING.md](CONTRIBUTING.md) primeiro.

## Agradecimentos

O VoiceMate se apoia no trabalho de muitos projetos: [Whisper](https://github.com/openai/whisper) da OpenAI, [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [CTranslate2-ROCm](https://github.com/arlo-phoenix/CTranslate2-rocm), [PySide6](https://doc.qt.io/qtforpython-6/), [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice), [Kokoro](https://github.com/hexgrad/kokoro), [VoxCPM](https://github.com/OpenBMB/VoxCPM), o [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) com o [Claude Code](https://docs.claude.com/en/docs/claude-code), o [Inno Setup](https://jrsoftware.org/isinfo.php) e o [PyInstaller](https://pyinstaller.org/).

## Licença

[MIT](LICENSE) © Álli Terhorst. Parte da [NanoBR](https://github.com/nano-br): utilitários de código aberto para a produtividade do dia a dia.
