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

## Sumário

[Recursos](#recursos) · [Capturas de tela](#capturas-de-tela) · [Início rápido](#início-rápido) · [Uso](#uso) · [Configuração](#configuração) · [Arquitetura](#arquitetura) · [Solução de problemas](#solução-de-problemas) · [Documentação](#documentação) · [Como contribuir](#como-contribuir) · [Licença](#licença)

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

<a id="idiomas"></a>**Idiomas**: o companion, as mensagens do motor e o instalador falam inglês, português do Brasil, espanhol, russo e chinês simplificado.

<a id="plataformas-e-gpus"></a>**Plataformas e GPUs**
- Windows 10/11 com NVIDIA (CUDA): o caminho original, testado a fundo. O motor roda nativamente pela linha de comando.
- **Experimental:** o motor dentro do WSL2 (a configuração do companion), GPUs AMD (ROCm, Vulkan) no WSL2, no Linux e no Windows, e Linux (X11, Wayland) com um companion opcional.
- CPU como alternativa em qualquer lugar. O `make setup` detecta a plataforma e a GPU e instala o build do PyTorch e o backend de reconhecimento de fala correspondentes.

**Conversa por voz com o Claude (experimental)**
- `Ctrl+Alt+A` envia a sua fala ao Claude pela CLI do Claude Code (com o seu próprio login: o texto transcrito vai para a Anthropic, o áudio não). A resposta é copiada para a área de transferência e lida em voz alta.
- Motores de TTS: OmniVoice (o usado quando nada foi salvo), Kokoro (o que o `make setup` propõe) e VoxCPM2. A conversa continua entre os turnos; qualquer atalho interrompe a resposta e inicia uma nova gravação.

## Capturas de tela

| O menu da bandeja | "Reiniciar o WSL?" e "Reiniciar o VoiceMate?" |
|:---:|:---:|
| <img src="docs/assets/screenshots/pt-BR/tray-menu.png" width="279" alt="O menu da bandeja"> | <img src="docs/assets/screenshots/pt-BR/dialog-restart-wsl.png" width="516" alt="A pergunta Reiniciar o WSL?"><br><img src="docs/assets/screenshots/pt-BR/dialog-language.png" width="466" alt="A pergunta Reiniciar o VoiceMate?"> |

<p align="center"><img src="docs/assets/screenshots/pt-BR/settings-general.png" width="743" alt="A aba Geral de Configurações do VoiceMate"><br><sub>"Configurações do VoiceMate", aba "Geral". Veja também as abas <a href="docs/assets/screenshots/pt-BR/settings-hotkeys.png">"Atalhos"</a> e <a href="docs/assets/screenshots/pt-BR/settings-sounds.png">"Sons"</a>.</sub></p>

<p align="center"><img src="docs/assets/screenshots/tray-states.png" width="584" alt="O ícone da bandeja em cada estado, numa barra de tarefas escura e numa clara"><br><sub>O ícone da bandeja mostra o estado com um selo. O que cada selo significa: <a href="docs/usage.md#tray-icon-and-menu">Uso, ícone e menu da bandeja</a>.</sub></p>

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
   make setup    # só para ditado, responda 1 (clipboard) em "Qual flow principal?"
   make doctor   # todas as verificações devem mostrar ✓
   ```
   O companion procura o motor em `~/voice-mate` e em algumas outras pastas comuns ([a lista](docs/installation.md#2-install-the-engine)); em qualquer outro lugar, defina "Pasta do motor:" em Configurações. Com uma GPU AMD, instale também o driver da AMD e o ROCm para WSL ([docs/wsl2.md](docs/wsl2.md)).
3. Recomendado: ainda em `~/voice-mate`, rode `make run` uma vez e pare com `Ctrl+C` quando ele terminar de carregar. A primeira inicialização baixa o modelo Whisper, e o companion dá a uma inicialização do motor só 240 segundos (ele para de tentar depois de 3 estouros de tempo seguidos), o que uma conexão lenta pode ultrapassar.
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

**O motor usa português por padrão** (`--output-lang pt-BR`), o que define o idioma que o Whisper escuta, as respostas do Claude e as mensagens do motor. Passe `--output-lang en` (ou outro código) como acima; `--transcription-language` e `VOICEMATE_LANG` mudam só um deles ([configuração](docs/configuration.md)). O companion passa essas flags sozinho, veja [Uso](#uso).

Para rodar o companion a partir do código-fonte no Windows, com o motor no WSL2: `make companion-venv` uma vez, depois `make run-tray`. No Linux, rode `make setup` e `make run` como acima; para a bandeja e a janela de status opcionais, `poetry install --extras ui` e depois `make run-tray` ([detalhes](docs/installation.md#linux-experimental)).

## Uso

| Atalho | Ação no menu da bandeja | O que acontece |
|---|---|---|
| `Ctrl+Alt+V` | "Ditar" | Fala vira texto, copiado para a área de transferência |
| `Ctrl+Alt+A` | "Perguntar ao Claude" | Fala enviada ao Claude, resposta copiada e lida em voz alta (**experimental**) |

Aperte `Ctrl+Alt+V`, fale depois do sinal de início, aperte `Ctrl+Alt+V` de novo e cole com `Ctrl+V` em qualquer lugar. O atalho que para a gravação decide para onde o texto vai. Um texto que não conseguiu chegar à área de transferência espera em **Não copiadas** (menu da bandeja e janela de status), onde um clique o copia. Para sair, escolha **Sair do VoiceMate** no menu da bandeja; um motor que o companion não iniciou continua rodando.

**Idioma do ditado.** Em Configurações > **Geral**, "Idioma do ditado:" define o idioma que você fala. O padrão é "Igual ao da interface"; "Detectar automaticamente" deixa o Whisper detectar cada gravação (o Claude continua respondendo no idioma da interface; com o Kokoro, fixe um idioma); ou escolha um idioma. O companion repassa a escolha ao motor que ele inicia como `--transcription-language` e `--output-lang`, e mudar o idioma reinicia o motor. Um motor ao qual o companion só se conecta (uma unidade do systemd, ou um iniciado à mão) mantém as próprias flags.

A conversa por voz com o Claude precisa da CLI do Claude Code instalada e com login feito, e do módulo `claude` escolhido no `make setup`. Veja [docs/usage.md](docs/usage.md#claude-flow-experimental).

## Configuração

| O quê | Onde |
|---|---|
| Configurações do companion | Windows `%APPDATA%\VoiceMate\companion.toml`, Linux `~/.config/voicemate/companion.toml` |
| Escolhas do motor feitas no `make setup`, token da API | `~/.config/voicemate/` (dentro do WSL para o companion; `%USERPROFILE%\.config\voicemate\` para um motor rodando nativamente no Windows) |
| API HTTP do motor | `127.0.0.1:47821` |
| Logs (`companion.log`, `engine.log`) | Windows `%LOCALAPPDATA%\VoiceMate\logs`, Linux `~/.local/state/voicemate/logs` |
| Lista **Não copiadas** | `pending.json`, ao lado da pasta `logs` |

Todas as flags do motor, todas as chaves de configurações e todas as variáveis de ambiente: [docs/configuration.md](docs/configuration.md).

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
    U->>C: Atalho
    C->>E: Iniciar gravação
    E-->>C: Microfone ativo
    C->>U: Sinal de início
    U->>C: Atalho de novo
    C->>E: Parar gravação
    E->>E: O Whisper transcreve
    E-->>C: Texto para entregar
    C->>CB: Escrever, ler de volta, comparar
    alt Texto verificado
        C->>E: ACK entregue
        C->>U: Sinal de pronto
    else Área de transferência bloqueada por outro aplicativo
        C->>C: Guardar o texto em Não copiadas
        C->>E: ACK falhou
        C->>U: Sinal de erro e notificação
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
    state "Rodando" as Running
    state "Reiniciando" as Restarting
    state "Erro" as Error
    state "Ocioso" as Idle
    state "Gravando" as Recording
    state "Transcrevendo" as Transcribing
    state "Respondendo" as Answering
    state "Pronto" as Ready
    state "Alerta" as Warning
    [*] --> Stopped
    Stopped --> Starting : App inicia
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
        Idle --> Warning : Sem microfone ou áudio fora
        Warning --> Idle : Áudio de volta
    }
```

Visão geral. Diagrama completo: [docs/architecture.md, Estados da bandeja](docs/architecture.md#5-tray-states).

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
    healthy -->|"3 verificações sem resposta ou saída"| q_breaker{"Reinícios demais?"}
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
| Outro problema do lado do motor | Rode `make doctor` na pasta do motor. Ele mostra a correção para cada verificação que falhou. |

Mais problemas e as mensagens exatas: [docs/troubleshooting.md](docs/troubleshooting.md).

## Documentação

- [Installation](docs/installation.md) (em inglês): todos os caminhos de instalação, atualização e desinstalação.
- [Usage](docs/usage.md): fluxos, atalhos, ícone da bandeja, modelos e opções de voz.
- [Configuration](docs/configuration.md): flags do motor, arquivos de configurações, variáveis de ambiente.
- [Troubleshooting](docs/troubleshooting.md): `make doctor`, áudio do WSL, logs, mensagens conhecidas.
- [Architecture](docs/architecture.md): diagramas e decisões de projeto.
- [WSL2 and AMD](docs/wsl2.md) (experimental), [Companion design](docs/companion-app.md), [Brand](docs/brand.md), [Releasing](docs/releasing.md).

## Como contribuir

Issues e pull requests são bem-vindos. Todo comando de desenvolvimento passa pelo Makefile: `make format lint test` para o motor (Ruff e mypy estrito) e `make companion-lint companion-test` para o companion. Todo texto visível ao usuário existe em 5 idiomas via gettext. Os commits seguem o Conventional Commits e explicam o contexto. Leia o [CONTRIBUTING.md](CONTRIBUTING.md) primeiro.

## Agradecimentos

O VoiceMate se apoia no trabalho de muitos projetos: [Whisper](https://github.com/openai/whisper) da OpenAI, [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [CTranslate2-ROCm](https://github.com/arlo-phoenix/CTranslate2-rocm), [PySide6](https://doc.qt.io/qtforpython-6/), [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice), [Kokoro](https://github.com/hexgrad/kokoro), [VoxCPM](https://github.com/OpenBMB/VoxCPM), o [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) com o [Claude Code](https://docs.claude.com/en/docs/claude-code), o [Inno Setup](https://jrsoftware.org/isinfo.php) e o [PyInstaller](https://pyinstaller.org/).

## Licença

[MIT](LICENSE) © Álli Terhorst. Parte da [NanoBR](https://github.com/nano-br): utilitários de código aberto para a produtividade do dia a dia.
