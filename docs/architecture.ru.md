[English](architecture.md) | [Português](architecture.pt-BR.md) | [Español](architecture.es.md) | **Русский** | [简体中文](architecture.zh-CN.md)

# Архитектура

Назначение: как устроен VoiceMate, с диаграммами каждого уровня, HTTP API, деревом пакетов и причинами основных проектных решений. Назад к [README](../README.ru.md).

Эта страница описывает конфигурацию Windows + WSL2 (приложение-компаньон в Windows, движок внутри WSL2), для которой компаньон и создавался. Локальный режим `local` в Linux и режим командной строки упоминаются там, где они отличаются. Когда меняется код, эта страница меняется в том же pull request. Контракт протокола во всех подробностях описан в [companion-app.md](companion-app.md).

## 1. Контекст системы

```mermaid
flowchart TB
    user(["Пользователь<br/>диктует голосом"])

    subgraph pc["ПК пользователя"]
        vm["VoiceMate<br/>локальная голосовая диктовка"]
        win["Рабочий стол Windows<br/>горячие клавиши, буфер обмена, область уведомлений, звуки"]
        wsl["Виртуальная машина Linux WSL2<br/>аудиомост WSLg"]
        gpu["GPU<br/>AMD ROCm или NVIDIA CUDA"]
        audio["Микрофон и динамики"]
        cli["Claude Code CLI<br/>необязательно"]
        tts["Модели TTS<br/>необязательно"]
    end

    subgraph net["Интернет"]
        api["Anthropic API<br/>необязательно"]
        hf["Hugging Face Hub<br/>веса моделей"]
        cdn["CDN моделей OpenAI<br/>веса openai-whisper"]
    end

    user -->|"Горячие клавиши, меню в области уведомлений"| vm
    user -->|"Вставляет текст"| win
    vm -->|"Регистрирует горячие клавиши, пишет в буфер обмена, показывает значок"| win
    vm -->|"Запускает движок внутри"| wsl
    wsl -->|"Записывает речь, воспроизводит звук"| audio
    vm -->|"Расшифровывает на"| gpu
    vm -.->|"Отправляет вопросы ИИ"| cli
    cli -.->|"HTTPS"| api
    vm -.->|"Озвучивает ответы через"| tts
    vm -.->|"Скачивает веса моделей"| hf
    vm -.->|"Скачивает веса STT"| cdn

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#999999,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    class user person
    class vm system
    class win,wsl,gpu,audio,hf,cdn external
    class cli,tts,api optional
```

Пользователь нажимает глобальную горячую клавишу в Windows и вставляет текст туда, где стоит курсор. VoiceMate это локальная система, разделённая между двумя половинами одного и того же ПК. Рабочий стол Windows предоставляет горячие клавиши, буфер обмена, значок в области уведомлений и вывод звука для сигналов. Движок работает внутри виртуальной машины Linux WSL2, потому что именно там через ROCm доступна GPU AMD ([wsl2.md](wsl2.md)). Микрофон и динамики доходят до движка через аудиомост WSLg PulseAudio, который движок опрашивает командой `pactl info` каждые 20 секунд (`app/platform/audio_probe.py`).

Всё необязательное находится за пунктирными стрелками. Режим Claude запускает локальный Claude Code CLI через `claude-agent-sdk` (`app/features/claude/runtime.py`), который обращается к Anthropic API (модель по умолчанию `claude-haiku-5-5`). Библиотеки TTS озвучивают ответ (если ничего не сохранено, используется локальный движок по умолчанию; `make setup` предлагает `kokoro`). Веса моделей скачиваются из интернета при первом использовании:

| Что | Источник | Когда |
|---|---|---|
| Модель GGML для whisper.cpp, её вариант q8, Silero VAD для whisper.cpp | Hugging Face (`ggerganov/whisper.cpp`, `ggml-org/whisper-vad`) | `make setup` или `make configure` |
| Модель faster-whisper (по умолчанию `large-v3-turbo`) | Hugging Face, через библиотеку `faster-whisper` | Первая загрузка модели |
| Модель openai-whisper (обычный бэкенд в WSL2 + AMD без CTranslate2-ROCm) | CDN моделей OpenAI, через библиотеку `openai-whisper` | Первая загрузка модели |
| Веса движков TTS (движок по умолчанию, VoxCPM2 и Kokoro) | Hugging Face (`openbmb/VoxCPM2`, Kokoro через свою библиотеку, собственный репозиторий движка по умолчанию) | Первая загрузка TTS |
| Колёса PyTorch ROCm, исходники whisper.cpp, форк CTranslate2-ROCm | `repo.radeon.com`, GitHub | `make setup` |

## 2. Контейнеры

```mermaid
flowchart LR
    user(["Пользователь"])

    subgraph windows["Хост Windows"]
        direction TB
        installer["Установщик<br/>Inno Setup, PyInstaller"]
        subgraph companion["Приложение-компаньон, VoiceMate.exe"]
            direction TB
            ui["UI компаньона<br/>PySide6: значок, состояние, настройки"]
            core["Ядро компаньона<br/>Python, без Qt: супервизор, доставка, сигналы"]
            w32["Поток Win32<br/>горячие клавиши, проверенный буфер обмена"]
        end
        wslexe["wsl.exe<br/>запуск движка, keepalive"]
        desk["Рабочий стол Windows<br/>буфер обмена, горячие клавиши, звук"]
        toml[("companion.toml<br/>настройки")]
        pending[("pending.json<br/>список «Нескопированные»")]
        logs[("Журналы<br/>companion.log, engine.log")]
    end

    subgraph wsl2["Дистрибутив WSL2"]
        direction TB
        subgraph daemon["Демон движка, режим под управлением"]
            direction TB
            api["HTTP API v2<br/>127.0.0.1:47821, токен Bearer"]
            engine["Сессия записи<br/>рекордер, обработчики"]
            stt["Бэкенд STT<br/>faster-whisper или openai-whisper"]
            tts["Синтезатор TTS<br/>движок по умолчанию, Kokoro, VoxCPM2, необязательно"]
        end
        wserver["whisper-server<br/>whisper.cpp, необязательно"]
        claude["Claude Code CLI<br/>необязательно"]
        pulse["WSLg PulseAudio<br/>микрофон и динамики"]
        cfg[("config.toml<br/>выбор движка")]
        token[("api-token<br/>секрет Bearer")]
        unit["юнит systemd<br/>необязательно"]
    end

    gpu["GPU"]
    anthropic["Anthropic API"]

    user -->|"Меню в области уведомлений, настройки"| ui
    user -->|"Нажимает горячую клавишу"| desk
    installer -->|"Устанавливает"| companion
    ui <-->|"Команды, снимки"| core
    core -->|"Отправляет сообщения"| w32
    w32 <-->|"События горячих клавиш, запись и чтение обратно"| desk
    core -->|"Воспроизводит сигналы"| desk
    core -->|"HTTP JSON, длинный опрос GET /events"| api
    core -->|"Запускает, закрывает stdin, останавливает WSL"| wslexe
    wslexe -->|"bash -lc make run-engine"| daemon
    wslexe -->|"Читает токен"| token
    core -->|"Читает, записывает"| toml
    core -->|"Сохраняет при изменении"| pending
    core -->|"Записывает"| logs
    api --> engine
    engine -->|"Записывает звук"| pulse
    engine --> stt
    stt -->|"CUDA или ROCm"| gpu
    engine -.->|"HTTP POST /inference"| wserver
    wserver -.-> gpu
    engine -.->|"claude-agent-sdk"| claude
    claude -.->|"HTTPS"| anthropic
    engine -.->|"Озвучивает ответы"| tts
    tts -.-> pulse
    daemon -->|"Создаёт при запуске"| token
    daemon -->|"Читает"| cfg
    unit -.->|"Альтернативный запуск"| daemon

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef store fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#999999,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    class user person
    class installer,ui,core,w32,wslexe,api,engine,stt container
    class toml,pending,logs,cfg,token store
    class desk,pulse,gpu,anthropic external
    class wserver,claude,tts,unit optional
```

Два хоста, один контракт через loopback. В Windows компаньон (`VoiceMate.exe`, устанавливается для каждого пользователя через Inno Setup в `%LOCALAPPDATA%\Programs\VoiceMate`) разделён на **ядро** без Qt и **UI** на PySide6. UI только отрисовывает неизменяемые объекты `CompanionSnapshot` и вызывает команды `CompanionController` (`app/companion/contract.py`). Тест запрещает компаньону импортировать стек движка (`tests/test_import_boundary.py`).

Внутри ядра один выделенный **поток Win32** владеет `RegisterHotKey` и каждой записью в буфер обмена и чтением обратно (`app/companion/win/hotkeys_clipboard.py`). Ядро запускает движок командой `wsl.exe -d <distro> -e bash -lc 'cd ... && exec make run-engine ARGS="--daemon-port <port> --transcription-language <code> --output-lang <BCP-47>"'`, внутри Job Object, с открытым stdin и выводом, который сливается в `engine.log` (`app/companion/supervisor/wsl.py`, `app/companion/supervisor/backend.py`). Два языковых флага берутся из настройки `dictation_language` (`app/companion/dictation.py`; см. [companion-app.md](companion-app.md)). `make run-engine` это `voice-mate --supervised --api --api-token`.

Внутри WSL2 **демон движка** привязывает свой HTTP API к `127.0.0.1:47821` ДО загрузки модели, создаёт файл токена `~/.config/voicemate/api-token` и запускает наблюдателя за концом файла stdin (`app/main.py`). WSL2 пробрасывает этот порт на loopback Windows. Ядро читает токен через `wsl.exe ... cat` (`app/companion/supervisor/wsl.py`) и общается простым HTTP JSON через `urllib`, без системного прокси, с длинным опросом `GET /events` на 25 секунд (`app/companion/client.py`).

Распознавание речи и TTS работают в процессе демона. Отдельными процессами являются только whisper.cpp в режиме сервера (`whisper-server` на свободном порту loopback) и Claude Code CLI (запускаемый `claude-agent-sdk`). Локальное состояние в Windows: `companion.toml` в `%APPDATA%\VoiceMate`, `pending.json` и `logs\` в `%LOCALAPPDATA%\VoiceMate` (`app/companion/paths.py`). Демон может запускать и необязательная пользовательская служба systemd; тогда компаньон подключается к ней.

### HTTP API v2

Маршруты в `app/daemon/server.py`, структуры данных в `app/protocol/models.py`:

| Маршрут | Назначение |
|---|---|
| `GET /health` | Готовность, `api_version`, `instance`, `audio`, `flow_info`. Токен не нужен никогда |
| `POST /register`, `POST /unregister` | Идентификатор клиента, эксклюзивные аренды `clipboard` и `cues` (от 10 до 120 секунд, по умолчанию 40), снимок и курсор |
| `GET /events?client_id&instance&since&wait` | Длинный опрос (не дольше 25 секунд) по кольцу из 512 событий: `snapshot`, `state`, `result`, `error`, `warning`, `health`, `shutdown` |
| `POST /trigger` (`expect`: toggle, start, stop), `POST /cancel` | Управление сессией записи |
| `POST /results/ack`, `GET /results?state=unacked` или `recent` | Подтверждение доставки и сверка по последним 200 результатам |
| `POST /shutdown` | Корректная остановка, причина `user_quit`, `restart` или `supervisor` |
| `/tts/v1/...` | Протокол `voicemate-tts` ([tts-provider-protocol.md](tts-provider-protocol.md#the-engine-speaks-the-same-protocol)), обслуживается только для Kokoro и своего провайдера; компаньон использует его, чтобы озвучивать ответы ассистента |

Маршруты v1 старых скриптов Windows (`/status`, `/result`, `/register` с пустым телом, `POST /trigger`) по-прежнему работают с демоном без токена. Защита на каждом запросе: `403` для любого заголовка `Origin` или `Host`, не являющегося loopback, `401` без bearer-токена (кроме `/health`), `503` до готовности (кроме `/health`, `/shutdown`, `/unregister`).

## 3. Компоненты

### 3.1 Ядро компаньона

```mermaid
flowchart TB
    subgraph uig["UI компаньона, PySide6"]
        direction LR
        ui["Значок, состояние, настройки<br/>виджеты"]
        bridge["ControllerBridge<br/>переход в поток Qt"]
    end

    subgraph core["Ядро компаньона, без Qt"]
        direction TB
        ctrl["Controller<br/>поток диспетчера, владеет состоянием"]
        reducer["Reducer<br/>состояние значка, сигналы, уведомления"]
        policy["Политика супервизора<br/>правила перезапуска"]
        queue["Очередь доставки<br/>порядок, повторы, ACK"]
        poller["Опрос событий<br/>регистрация, длинный опрос"]
        client["Клиент демона<br/>HTTP, токен"]
        backend["Бэкенд движка<br/>WSL, локальный, внешний"]
        cues["Проигрыватель сигналов<br/>отрисованные файлы WAV"]
        pstore["Хранилище ожидающих<br/>pending.json"]
        sstore["Хранилище настроек<br/>companion.toml"]
        desktop["Службы рабочего стола<br/>горячие клавиши, буфер обмена, звук, микрофоны"]
    end

    api["API демона v2"]
    wslexe["wsl.exe"]

    ui <--> bridge
    bridge <-->|"Команды, снимки"| ctrl
    ctrl -->|"Входные данные"| reducer
    reducer -->|"Снимок, сигнал, уведомление"| ctrl
    ctrl -->|"Наблюдения"| policy
    policy -->|"Действия"| ctrl
    ctrl -->|"Результаты, вердикты"| queue
    queue -->|"Доставка, ACK, Нескопированные"| ctrl
    poller -->|"События"| ctrl
    poller --> client
    ctrl -->|"Триггер, ACK, проверка"| client
    client -->|"HTTP"| api
    ctrl -->|"Запуск, остановка, перезапуск WSL"| backend
    backend --> wslexe
    ctrl --> cues
    cues --> desktop
    ctrl -->|"Горячие клавиши, запись в буфер обмена"| desktop
    ctrl -->|"Сохраняет список"| pstore
    ctrl -->|"Читает, применяет"| sstore

    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef pure fill:#85bbf0,stroke:#2e6295,color:#000000,stroke-width:3px
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    class ctrl,poller,client,backend,cues,pstore,sstore,desktop component
    class reducer,policy,queue pure
    class api,wslexe external
    class ui,bridge container
```

Ядро построено как чистые конечные автоматы, которыми управляет один поток. Поток `Dispatcher` владеет единственным `CoreState` и по порядку выполняет отправленные ему задачи и таймеры (`app/companion/runtime.py`). Медленная работа уходит в последовательные исполнители (ввод-вывод, команды, супервизор, проверки, запись ожидающих), результаты которых отправляются обратно (`app/companion/controller.py`).

Три чистых модуля содержат правила и не выполняют ввод-вывод: **reducer** (`app/companion/model.py`), **политика супервизора** (`app/companion/supervisor/policy.py`) и **очередь доставки** (`app/companion/delivery.py`). Каждый возвращает эффекты или действия, которые выполняет контроллер. Поток `EventPoller` регистрируется, держит длинный опрос `/events` и регистрируется снова при `410`, при смене экземпляра или при потере аренды (`app/companion/client.py`). Хосты движка подключаемые: `WslBackend`, `LocalBackend`, `ExternalBackend`. Службы ОС скрыты за набором `Desktop`: горячие клавиши, буфер обмена, звук, число устройств записи (Core Audio в Windows), автозапуск и перенос значка на видное место (`app/companion/desktop.py`). UI обращается к ядру только через `ControllerBridge`, который переносит каждый обратный вызов в поток Qt (`app/companion/ui/bridge.py`).

### 3.2 Демон движка

```mermaid
flowchart TB
    companion["Ядро компаньона"]

    subgraph daemon["Демон движка"]
        direction TB
        server["ApiServer<br/>маршруты, аутентификация, защита"]
        life["Lifecycle<br/>барьер готовности, завершение"]
        hub["EventHub<br/>состояние операции, публикация"]
        journal["Журнал событий<br/>512 событий"]
        leases["Реестр аренд<br/>clipboard, cues"]
        tracker["Трекер доставки<br/>200 результатов"]
        session["Сессия записи<br/>toggle, cancel"]
        recorder["Рекордер<br/>sounddevice"]
        handlers["Обработчики режимов<br/>clipboard, claude_chat"]
        stt["Бэкенд STT<br/>цепочка резервных вариантов"]
        tts["Синтезатор TTS<br/>необязательно"]
        probe["Проверка звука<br/>pactl каждые 20 с"]
        clip["Запись в буфер обмена<br/>wl-copy, clip.exe"]
    end

    pulse["WSLg PulseAudio"]
    cli["Claude Code CLI"]

    companion -->|"HTTP"| server
    server --> life
    server -->|"Trigger, cancel"| session
    server -->|"Регистрация, опрос, ACK"| hub
    hub --> journal
    hub --> leases
    hub --> tracker
    session -->|"Состояние, mic live"| hub
    session --> recorder
    recorder --> pulse
    session --> stt
    session --> handlers
    handlers -->|"Результаты"| hub
    handlers -.->|"Только без держателя аренды"| clip
    handlers -.-> cli
    handlers -.-> tts
    tts -.-> pulse
    probe -->|"Health"| hub
    probe --> pulse

    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef optional fill:#85bbf0,stroke:#5d82a8,color:#000000,stroke-dasharray:5 5
    classDef external fill:#999999,stroke:#6b6b6b,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    class server,life,hub,journal,leases,tracker,session,recorder,handlers,stt,probe,clip component
    class tts optional
    class pulse,cli external
    class companion container
```

`ApiServer` лишь переводит HTTP в вызовы `EventHub`, единственного владельца состояния API v2 (`app/daemon/server.py`, `app/daemon/hub.py`). Хаб хранит текущую операцию, добавляет типизированные события в `EventJournal` (кольцо на 512), отслеживает результаты в `DeliveryTracker` (последние 200, их возраст считается по монотонным часам демона) и обращается к `LeaseRegistry`. Решения об арендах принимаются один раз на публикацию под блокировкой хаба: один и тот же снимок держателей выставляет флаги `needs_cue` и `needs_delivery` для каждого клиента и говорит движку, записывать ли буфер обмена и подавать ли сигнал самому. `Lifecycle` хранит барьер готовности и единственный запрос на завершение (`app/daemon/lifecycle.py`).

`RecordingSession` выполняет операцию: она публикует `recording` до того, как открывает микрофон, выставляет `mic_live`, когда поток действительно открыт, и расшифровывает речь в собственном потоке, в первый раз дожидаясь прогрева (`app/core/recording_session.py`). Обработчики по режимам: `ClipboardHandler` публикует расшифровку и сам пишет в буфер обмена, только если аренду никто не держит (через `wl-copy`, затем `pyperclip`, затем `clip.exe` в WSL2; `app/core/transcription_handler.py`, `app/platform/clipboard.py`). `ClaudeChatHandler` добавляет фазы `thinking` и `speaking`. Бэкенд распознавания речи выбирается один раз при запуске цепочкой резервных вариантов (`app/cli/wiring.py`); в WSL2 + AMD: faster-whisper на CTranslate2-ROCm, если он проверен, затем openai-whisper, затем whisper.cpp, затем CPU. С `--stt-engine external` каждая запись вместо этого уходит сервису, который следует [протоколу STT-провайдера](stt-provider-protocol.ru.md), а эта цепочка служит резервным вариантом (`app/features/stt_external/`). `AudioServerProbe` публикует события `health`, когда состояние WSLg PulseAudio меняется.

## 4. Выполнение: одна диктовка

```mermaid
sequenceDiagram
    autonumber
    actor U as Пользователь
    participant W as Поток Win32
    participant C as Ядро компаньона
    participant A as API демона v2
    participant S as Сессия записи
    participant T as Бэкенд STT
    participant CB as Буфер обмена Windows

    Note over C,A: Зарегистрирован с арендами clipboard и cues, длинный опрос GET /events открыт
    rect rgba(67,141,213,0.12)
        U->>W: Нажимает Ctrl+Alt+V
        W->>C: Горячая клавиша для режима clipboard
        C->>A: POST /trigger flow clipboard, expect toggle
        A->>S: toggle
        S-->>A: state recording, mic_live false
        A-->>C: action started, op_seq
        S->>S: Открывает микрофон в фоне
        S-->>A: state recording, mic_live true
        A-->>C: event state, needs_cue true
        C->>U: Сигнал start, значок recording
        U->>W: Нажимает Ctrl+Alt+V ещё раз
        W->>C: Горячая клавиша для режима clipboard
        C->>A: POST /trigger flow clipboard, expect toggle
        A->>S: toggle
        A-->>C: action stopped
        S-->>A: state processing, phase transcribing
        A-->>C: event state
        C->>U: Сигнал transcribing, значок transcribing
    end
    rect rgba(106,61,154,0.12)
        S->>T: расшифровать звук
        T-->>S: текст
        S->>A: publish_result transcript, final
        Note over A: Аренда clipboard удержана, доставка ожидает, демон не пишет в буфер обмена
        A-->>C: event result, needs_delivery true
        S-->>A: state idle
    end
    rect rgba(46,125,50,0.12)
        C->>W: Доставить текст
        loop До 5 попыток с интервалом от 60 до 300 мс
            W->>CB: Открыть, если свободен, записать, прочитать обратно, сравнить
        end
        alt Текст проверен
            W->>CB: Записать ещё раз через 250 мс для журнала Win+V
            W-->>C: Доставлено
            C->>A: POST /results/ack status delivered, verified true
            C->>U: Сигнал ready, значок ready на 3 с
        else Буфер обмена заблокирован другим приложением
            W-->>C: Не доставлено
            loop Повторы очереди, до 3, с интервалом 1 с
                C->>W: Доставить текст снова
                W-->>C: Не доставлено
            end
            C->>C: Оставить текст в Нескопированных, сохранить pending.json
            C->>A: POST /results/ack status failed
            C->>U: Сигнал error, уведомление о нескопированном, значок idle
        end
        opt Каждые 10 с и при переподключении
            C->>A: GET /results state unacked
            A-->>C: Результаты, всё ещё ожидающие
            alt Младше 30 с, его событие пропущено
                C->>W: Доставить текст, без сигнала
            else Старше 30 с
                C->>C: Добавить в Нескопированные, уведомить один раз
                C->>A: POST /results/ack status dismissed
            else Уже подтверждён здесь, дважды подряд в списке
                C->>A: POST /results/ack тот же статус снова
            end
        end
    end
```

Горячей клавишей и буфером обмена Windows владеет компаньон, а не движок. Горячая клавиша попадает в ядро из потока Win32. Ядро отказывается запускать запись, пока супервизор не в порядке (не `healthy` и не `degraded`) или движок не готов, и вместо этого показывает уведомление; в остальных случаях оно отправляет `POST /trigger` с `expect: "toggle"`. Сессия публикует `recording` до того, как открывает микрофон, и второе событие `state` с `mic_live: true`, когда поток открыт, так что сигнал начала означает «говорите».

Второе нажатие переводит сессию в `processing` с фазой `transcribing`; расшифровка публикуется как `result`. Поскольку компаньон держит аренду `clipboard`, результат сохраняется как ожидающий, и только этот клиент получает `needs_delivery: true`; демон пропускает собственную запись в буфер обмена и свой сигнал. Очередь доставки отправляет тексты в порядке `result_seq` в поток Win32, который запускает конечный автомат на таймерах: пропустить, пока другой процесс держит буфер обмена, записать, прочитать обратно, сравнить, до 5 попыток, затем записать ещё раз через 250 мс для журнала `Win+V`. Проверенная запись подтверждается как `delivered` и подаёт сигнал `ready`.

Ветка сбоя: после 3 повторов очереди с интервалом 1 секунда результат подтверждается как `failed`, остаётся в **Нескопированные** (сохраняется в `pending.json`) и объявляется сигналом `error` и уведомлением. Сверка выполняется каждые 10 секунд и при переподключении: неподтверждённые результаты младше 30 секунд доставляются без сигнала; более старые подтверждаются как `dismissed` и попадают в **Нескопированные**. Подтверждение, от которого компаньон отказался, отправляется снова, если два списка подряд всё ещё показывают этот результат. Доставка, которая всё ещё ждёт, когда экземпляр демона меняется, тоже попадает в **Нескопированные**: поздно она не записывается никогда.

## 5. Состояния значка в области уведомлений

```mermaid
stateDiagram-v2
    [*] --> stopped
    stopped --> starting : Приложение запускается
    starting --> healthy : Движок готов
    starting --> restarting : Таймаут запуска или выход
    starting --> error : Нет папки движка
    restarting --> healthy : Движок готов
    restarting --> error : Сработал предохранитель
    healthy --> restarting : Сбой, нет ответа или перезапуск WSL
    error --> restarting : Ручной перезапуск
    healthy --> stopped : Выход

    state healthy {
        [*] --> idle
        idle --> recording : Триггер запущен
        recording --> transcribing : Триггер остановлен
        recording --> idle : Отмена
        recording --> warning : Микрофон недоступен
        transcribing --> thinking : Режим Claude
        thinking --> speaking : TTS говорит
        transcribing --> ready : Текст скопирован
        thinking --> ready : Ответ скопирован
        speaking --> ready : Ответ скопирован
        transcribing --> idle : Нет речи или сбой
        ready --> idle : Через 3 с
        idle --> warning : Звук не работает, нет микрофона или пропущена проверка
        warning --> idle : Звук вернулся и микрофон есть
        warning --> recording : Триггер запущен
    }

    note right of error
        Состояние error показывает
        только состояние супервизора failed
    end note

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
    class stopped external
    class starting,restarting,warning warn
    class healthy,idle,ready ok
    class error bad
    class recording,transcribing,thinking,speaking container
```

Состояние значка нигде не хранится: reducer выводит его при каждом изменении из состояния супервизора, операции движка и двух таймеров (`app/companion/model.py`). Приоритет: сначала супервизор (`stopped`, `starting`, `restarting` и для `restarting`, и для `backoff`, `error` только для `failed`, `warning` для `degraded` или устаревшего движка), затем операция (`recording`, `transcribing`, `thinking`, `speaking`), затем `ready` на 3 секунды после того, как окончательный результат попал в буфер обмена, затем ожидающее предупреждение (нет микрофона, не работает звук, микрофон недоступен), иначе `idle`. Составной блок это состояние супервизора `healthy`. Одиннадцать названий соответствуют `TrayState` (`app/companion/contract.py`).

Сигналы подчиняются тому же reducer: вход в `transcribing`, `warning` или `error` подаёт свой сигнал, если только его не заменяет собственный сигнал события, а события демона подают сигнал, только когда `needs_cue` равно true. Сбойная расшифровка или запрос к Claude возвращает в `idle` с сигналом `error`, поэтому `error` на значке всегда означает, что супервизор сдался.

## 6. Супервизор в Windows и WSL2

```mermaid
flowchart TD
    launch(["Запуск"])
    q_health{"GET /health отвечает?"}
    attach["Подключение к работающему демону<br/>keepalive wsl.exe sleep infinity"]
    q_unit{"Служба systemd включена?"}
    unit["Режим подключения<br/>сообщить, как отключить службу"]
    q_dir{"Папка движка известна или найдена?"}
    spawn["Запуск wsl.exe bash -lc<br/>make run-engine, Job Object"]
    starting["starting<br/>проверка каждые 5 с"]
    q_ready{"ready true за 240 с?"}
    healthy["healthy<br/>проверка каждые 5 с"]
    degraded["degraded<br/>пропущена проверка"]
    q_miss{"3 пропущенные проверки или движок завершился?"}
    q_inuse{"Выход сообщил, что адрес занят, первый раз?"}
    q_breaker{"Больше 5 перезапусков за 15 мин<br/>или 3 таймаута запуска подряд?"}
    backoff["backoff<br/>2, 5, 15, 30, 60, 120 с"]
    restarting["restarting<br/>остановить движок, затем запустить"]
    failed["failed<br/>уведомить, ждать ручного перезапуска"]
    q_audio{"звук не работает дважды или ошибка микрофона<br/>при наличии микрофона и звуке не ok?"}
    q_mic{"В Windows есть устройство записи?"}
    waitmic["Ждать микрофон<br/>опрос каждые 3 с"]
    q_policy{"wsl_restart_policy"}
    nothing["Ничего не делать"]
    ask["Спросить пользователя<br/>pending_wsl_restart"]
    q_others{"Запущены другие дистрибутивы?"}
    q_idle["Ждать, пока движок освободится<br/>не больше 30 с"]
    q_wslbreaker{"Больше 3 перезапусков WSL за 1 ч?"}
    wslrestart["POST /shutdown<br/>затем wsl --shutdown"]
    quit(["Выход"])
    stop["POST /shutdown, ждать 8 с<br/>закрыть stdin, ждать 2 с, завершить job"]
    keep["Только отмена регистрации<br/>демон продолжает работать"]

    launch --> q_health
    q_health -- "Да" --> attach --> starting
    q_health -- "Нет" --> q_unit
    q_unit -- "Да" --> unit --> starting
    q_unit -- "Нет" --> q_dir
    q_dir -- "Нет" --> failed
    q_dir -- "Да" --> spawn --> starting
    starting --> q_ready
    q_ready -- "Да" --> healthy
    q_ready -- "Нет, таймаут запуска" --> q_breaker
    healthy --> q_miss
    q_miss -- "Пропущено 1 или 2" --> degraded
    degraded -- "Проверка успешна" --> healthy
    q_miss -- "Да" --> q_inuse
    q_inuse -- "Да" --> restarting
    q_inuse -- "Нет" --> q_breaker
    q_breaker -- "Нет" --> backoff --> restarting
    q_breaker -- "Да" --> failed
    restarting --> launch
    failed -- "Перезапустить движок или Перезапустить WSL" --> restarting

    healthy --> q_audio
    q_audio -- "Да" --> q_mic
    q_mic -- "Нет" --> waitmic
    waitmic -- "Микрофон появился, звук не ok" --> q_policy
    q_mic -- "Да" --> q_policy
    q_policy -- "never" --> nothing
    q_policy -- "ask" --> ask
    q_policy -- "auto" --> q_others
    q_others -- "Да" --> ask
    q_others -- "Нет" --> q_idle
    ask -- "Пользователь подтверждает" --> q_idle
    ask -- "Пользователь отказывается" --> nothing
    q_idle --> q_wslbreaker
    q_wslbreaker -- "Да" --> failed
    q_wslbreaker -- "Нет" --> wslrestart --> restarting

    quit -- "Движок, который запустили мы" --> stop
    quit -- "Подключённый демон" --> keep

    classDef state fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef bad fill:#c0392b,stroke:#922b21,color:#ffffff
    classDef term fill:#08427b,stroke:#052e56,color:#ffffff
    class starting,healthy,degraded,backoff,restarting state
    class failed bad
    class launch,quit term
```

`SupervisorPolicy` это чистый конечный автомат над `SupervisorState` (`stopped`, `starting`, `healthy`, `degraded`, `restarting`, `backoff`, `failed`); контроллер выполняет его действия в очереди супервизора. При запуске предпочтение отдаётся подключению: если `/health` отвечает, компаньон подключается и держит keepalive `wsl.exe ... sleep infinity`, чтобы WSL не останавливал дистрибутив; если включена пользовательская служба systemd `voicemate`, он подключается и показывает, как отключить службу; иначе он находит папку движка и запускает движок.

Проверки выполняются каждые 5 секунд: TCP-соединение за 500 мс, затем `/health` с таймаутом 2 секунды. Пороги: 240 секунд на запуск, 3 пропущенные проверки, паузы 2, 5, 15, 30, 60 и 120 секунд (сбрасываются после 10 минут исправной работы) и предохранители при более чем 5 перезапусках движка за 15 минут, более чем 3 перезапусках WSL за 1 час или 3 таймаутах запуска подряд (`app/companion/supervisor/policy.py`). Движок, завершившийся с «address in use», заставляет компаньон один раз подключиться вместо паузы.

Перезапуск WSL нужен, когда `audio` равно `down` два раза подряд, либо при `mic_unavailable`, пока в Windows есть устройство записи, а звук не `ok`; никогда без устройства записи. Настройка `wsl_restart_policy` принимает `auto`, `ask` или `never` (по умолчанию `auto`); `auto` всё равно спрашивает, если запущены другие дистрибутивы, поскольку `wsl --shutdown` останавливает их все. Перезапуск ждёт до 30 секунд, пока движок освободится, затем отправляет `POST /shutdown`, выполняет `wsl --shutdown` и запускает всё заново. Выход останавливает только то, что запустило приложение: `POST /shutdown`, ожидание 8 секунд, закрытие stdin, ожидание 2 секунды, завершение job. Подключённый демон получает только `/unregister`.

### 6.1 Голосовой ассистент

Ассистент живёт в компаньоне; движок участвует только в том, чтобы расшифровывать речь и озвучивать ответы. **Поток: как запрос доходит до центрального разговора в любом из режимов, как отвечают другие разговоры и как озвучивается ответ.** Тот же поток со стороны пользователя, шаг за шагом: [voice-assistant.md](voice-assistant.md); что ассистент с ним делает (маршрутизация, инициатива, наблюдения, постоянные инструкции): [assistant.md](assistant.ru.md).

```mermaid
flowchart LR
    user(["Пользователь"])
    subgraph comp["Компаньон, VoiceMate.exe"]
        hk["Горячие клавиши<br/>разговор, окно, режим, возврат, прослушивание"]
        win["Окно ассистента<br/>сообщения, ожидающие ответы"]
        svc["Сервис ассистента<br/>команды, прямой режим,<br/>настройки голосом"]
        own["Порт собственной сессии<br/>дочерний процесс stream-json"]
        tools["Инструменты voicemate<br/>перечислить, читать, отправить, запустить,<br/>модель, остановить"]
        resumer["Возобновление через CLI<br/>закрытые сессии терминала"]
        dport["Порт Desktop<br/>набор вспышкой"]
        watch["Наблюдатель за ответами<br/>reply, wait, observe"]
        inbox[("Входящие<br/>assistant-inbox.json")]
        waiting[("Список ожидания<br/>assistant-waiting.json")]
        rules[("Постоянные инструкции<br/>assistant-instructions.json")]
        voice["Голосовой вывод<br/>предложение за предложением"]
    end
    engine["Движок<br/>распознавание речи,<br/>/tts/v1"]
    provider["Свой TTS-провайдер"]
    cli["Claude Code CLI<br/>режим own_session"]
    desk["Claude Desktop<br/>режим desktop"]
    others["Другие разговоры<br/>Desktop, запущенные VoiceMate,<br/>терминал, IDE, Remote Control"]
    disk[("Расшифровки<br/>.claude/projects")]
    cfg[("assistant.json<br/>каталог настроек, входящие,<br/>pending, инструкции")]
    phone["Телефон"]

    user -->|"печатает"| win
    user -->|"говорит"| hk
    hk --> engine
    engine -->|"текст"| svc
    win --> svc
    svc -->|"mode own_session"| own
    own -->|"stdin"| cli
    cli --> tools
    tools -->|"отправить, запустить"| others
    tools -->|"закрытая сессия cli"| resumer
    resumer -->|"claude --resume -p,<br/>запрос через stdin"| others
    svc -->|"mode desktop"| dport
    dport -.->|"печатает около 2 с"| desk
    desk -->|"инструменты сессий Desktop"| others
    cli -->|"дописывает"| disk
    desk -->|"дописывает"| disk
    others -->|"дописывают"| disk
    svc -->|"читает ответы"| disk
    watch -->|"наблюдает"| disk
    watch -->|"ответ готов"| svc
    svc -->|"сохранённые ответы"| inbox
    svc -->|"follow_up, restart"| waiting
    svc -->|"remember, forget"| rules
    svc -->|"записывает"| cfg
    cli -->|"читает"| cfg
    svc -->|"блок voice"| voice
    voice -->|"предложения"| engine
    engine -.->|"ретранслирует"| provider
    voice -->|"звук"| user
    cli <-->|"Remote Control"| phone

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef agent fill:#6a3d9a,stroke:#4a2a6b,color:#ffffff
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    class user person
    class win,svc container
    class hk,own,tools,resumer,dport,watch,voice component
    class cli,desk,others agent
    class disk,cfg,inbox,waiting,rules store
    class engine,phone external
    class provider optional
```

**Режимы.** Сервис запускает один порт для центрального разговора, в зависимости от `mode`:

- **`own_session`**: `OwnSessionPort` запускает постоянный дочерний процесс `claude -p --input-format stream-json` с именем «VoiceMate» и инструментами `voicemate` (MCP-сервер SDK, который компаньон обслуживает через stdio дочернего процесса, `mcp_tools.py`). VoiceMate единственный, кто пишет; Claude Desktop и телефон это клиенты Remote Control для него. Инструменты перечисляют и читают разговоры по расшифровкам, отправляют сообщения в разговор (разговору, которым владеет VoiceMate, запрос подаётся на его stdin, разговору Desktop делается вспышка, закрытая сессия терминала, IDE или Remote Control возобновляется через `CliResumer`) и запускают, меняют модель и останавливают разговоры, которыми владеет VoiceMate (`conversations.py`: не больше 4 живых, считая те, что ещё запускаются; Remote Control включён, они запоминаются в `assistant-conversations\conversations.json` и возобновляются при следующем запуске).
- **`desktop`**: `DesktopConversationPort` находит разговор Desktop по его точному названию и пишет в него **вспышкой** (`app/companion/win/claude_desktop.py`): выводит окно Claude на передний план, стирает черновик, вставляет текст через буфер обмена (он сохраняется и восстанавливается, только если за время вспышки никто ничего не скопировал: порядковый номер буфера обмена проверяется перед вставкой и перед Enter), нажимает Enter и возвращает передний план, примерно за 2 секунды. Перед каждой клавишей проверяются три защиты (каждая клавиша это отдельное нажатие). Сбой вспышки Desktop всегда оформляется как типизированная ошибка (`DesktopError`): буфер обмена, занятый другой программой, превращается в `busy`, сбой UI Automation (перезагрузка или закрытие окна) превращается в `unavailable`, так что окно показывает «Не отправлено в ...», а сообщение не теряется. Где находятся элементы управления, берётся из [профиля](desktop-profile.md). Центральный разговор использует собственные инструменты сессий Desktop, поэтому инструментов `voicemate` здесь нет.

В обоих случаях ответы читаются из расшифровки на диске, а не из вывода процесса, так что запрос, набранный на телефоне или в Claude Desktop, тоже виден.

**Закрытые сессии терминала.** `cli_resume.py` пишет в сессию `cli`, только если её не держит открытой никакая программа: её нет в живом реестре, её расшифровка не менялась 120 секунд, а собственное возобновление этой сессии VoiceMate не выполняется и только что не завершилось. Он запускает `claude --resume <id> -p --output-format json` в папке сессии, с очищенным окружением и запросом на stdin за заголовком, который говорит, что текст пришёл от ассистента и ничего не одобряет.

`--permission-mode` не передаётся, если человек не задал `resume_permission_mode` (`default`, `acceptEdits` или `plan`; `bypassPermissions` отклоняется). Идентификатор должен быть UUID, текст ограничен 16 000 символов, а ход останавливается через 30 минут. Ненулевой код возврата, таймаут или разорванный канал восстанавливает прежнее наблюдение и отправляет центральному разговору заметку, что сообщение не доставлено. Используемая сессия остаётся для VoiceMate только для чтения; навык может достучаться до неё через собственные инструменты сессий клиента, когда они есть у центрального разговора.

**Другие разговоры и ответы.** Инструменты, которые пишут в разговор, никогда не ждут. Они регистрируют его в `AnswerWatcher` (`notifier.py`), который следит за концом его расшифровки и передаёт сервису завершённые ходы. У наблюдения один из трёх видов: `reply` (ответ на только что отправленное сообщение, доставляется один раз), `wait` (следующий ход, до 1 часа) и `observe` (каждый ход, до 12 часов, запрашивается через `follow_up` и `"watch": true`).

Наблюдаемые ходы, которые заканчиваются в пределах 15 секунд, группируются в одну заметку, а центральный разговор получает не больше 4 наблюдаемых заметок в минуту. Ответ на наблюдаемый разговор доставляется один раз, затем наблюдение продолжается. Список «В ожидании» сохраняется в `assistant-waiting.json` вместе с позицией чтения каждой расшифровки, поэтому ход, закончившийся, пока VoiceMate был закрыт, всё равно будет сообщён, а запись, у которой истекло время, уходит с одной заметкой.

Затем решает `answer_delivery` (наблюдение всегда идёт центральному разговору, если только человек не отошёл в прямом режиме или не следит за другим разговором: тогда оно идёт во входящие):

| Значение | Путь |
|---|---|
| `speak` | Ответ сразу уходит центральному разговору как заметка (ходы с пометкой `[voicemate:event]` скрыты из окна) |
| `announce`, `sound`, `silent` | Ответ сохраняется во входящих (`Inbox`, `inbox.py`, 50 элементов, сохраняется): `announce` и `sound` подают сигнал ответа, а `announce` ещё и произносит одну строку |

Сохранённые ответы никогда не подают уведомление во время записи или голосового ответа. «Прослушать готовое» отправляет центральному разговору одну сводную заметку со всеми ними. Прямой режим обходит центральный разговор: сервис отправляет текст человека дословно в один разговор до «back to the assistant», а затем сообщает центральному разговору, что там произошло.

**Голосовой путь.** Запись, остановленная горячей клавишей разговора, расшифровывается движком, а её текст идёт сервису, а не в буфер обмена. Блок `<voice>` каждого ответа разрезается на предложения и отправляется на эндпоинт `/tts/v1` движка (`voice.py`); звук, который приходит потоком, воспроизводится на этом компьютере, пока готовится следующее предложение. Движок обслуживает этот эндпоинт только для Kokoro или для своего провайдера, который он ретранслирует. Когда он говорить не может, ответы озвучивают голоса, которые в Windows уже есть (`app/companion/win/system_voice.py`, через SAPI), в источнике голоса «auto» по умолчанию (`assistant.voice_source`); если не может ни то, ни другое, ответы остаются текстом, а короткие уведомления переходят на речь Windows. С источником «engine» резервного варианта нет: предложение ждёт (до 90 секунд, один раз на сбой), пока движок ещё загружает свой голос, а короткие уведомления остаются на экране.

**Кнопка микрофона.** В окне ассистента есть кнопка **Говорить**, которая делает то же, что и горячая клавиша разговора: начинает запись, во время записи превращается в **Остановить и отправить** и показывает состояние значка в области уведомлений из окна состояния (тот же бейдж и ту же строку, включая прошедшее время), так что запись, начатая горячей клавишей, видна и там.

**Настройки и действия.** `settings_catalog.py` это список настроек, которые центральный разговор может менять. VoiceMate записывает его с текущими значениями в `assistant.json`; действие `set_setting` в ответе проверяется по нему и применяется тем же путём, что и из окна настроек. `read_inbox` и `clear_inbox` тоже выполняются.

`follow_up` добавляет (или обновляет) запись в списке «В ожидании» (разговор, название, причина, с какого времени и `watch` для наблюдения); `follow_up_done` удаляет её; `remember` и `forget` добавляют и удаляют постоянную инструкцию (`instructions.py`: 40 правил до 300 символов в `assistant-instructions.json`, копируются в `assistant.json`) и принимаются только в ответе на собственные слова человека; `focus` заставляет текстовый экран следовать за этим разговором, а `focus` без идентификатора сессии возвращает к центральному. `open_conversation` и `notify` только разбираются и записываются в журнал.

- **Один пишущий.** Процесс VoiceMate единственный пишет в свой собственный разговор. Перезапуск возобновляет сессию по идентификатору (`--resume`), только если её не владеет ни один живой процесс, иначе запускается новая.
- **Дочерний процесс запускается чистым.** Окружение отбрасывает переменные, которые Claude Desktop задаёт своим сессиям, потому что дочерний процесс, унаследовавший их, перезаписал файл учётных данных в лаборатории ([исследование](research/claude-code-client.md#2-authentication-is-the-first-blocker)).
- **Текст из других разговоров это данные.** Цитируемые ответы доходят до центрального разговора с пометкой «данные», а навык никогда не выполняет инструкции, найденные в них.

Замысел, риски и истории, которые ещё предстоит реализовать: [research/voice-assistant-design.md](research/voice-assistant-design.md).

## 7. Дерево пакетов

```text
app/
├── main.py              Точка входа движка (`voice-mate`): связывает всё и запускает цикл
├── cli/                 флаги argparse, построитель конфигурации, связывание обработчиков и слушателей
├── core/                Движок, не зависящий от ОС: рекордер, сессия записи, расшифровщик,
│   │                    обработчики, слушатели горячих клавиш, keepalive, сторожевой таймер, звуковые сигналы
│   └── prompts/         Канонический системный промпт с заполнителем {output_lang}
├── daemon/              HTTP API v2: сервер, хаб событий, журнал, аренды, доставка, аутентификация, жизненный цикл
├── features/            Необязательные модули (extras Poetry)
│   ├── claude/          Среда выполнения Claude Agent SDK, обработчик чата, буфер предложений
│   ├── openai_whisper/  Бэкенд openai-whisper и Silero VAD
│   ├── tts/             Протокол TTS, аудиоплееры, синтезаторы Kokoro, VoxCPM2 и по умолчанию
│   └── whispercpp/      Бэкенды whisper.cpp CLI и server
├── platform/            Определение платформы, средства записи в буфер обмена, проверка звука WSLg
│   └── listeners/       Триггеры evdev, pynput и HTTP socket
├── protocol/            Модели обмена демона и компаньона, разборщик ответов ассистента (только стандартная библиотека)
├── setup/               make setup / configure (подготовка и определение GPU), make doctor,
│                        сборка CTranslate2-ROCm, ~/.config/voicemate/config.toml
├── i18n/                обёртка gettext и каталоги (en, pt_BR, es, ru, zh_CN)
└── companion/           Приложение для области уведомлений: контроллер, клиент, reducer, доставка, настройки, пути
    ├── assistant/       Голосовой ассистент: сервис, собственная сессия, порт Desktop,
    │   │                инструменты voicemate, разговоры, возобновление через CLI, notifier, входящие, постоянные
    │   │                инструкции, чтение сессии, детектор команд, каталог настроек,
    │   │                личность, установка навыка
    │   └── skill/       Навык voicemate-assistant из поставки, в en/ и pt-BR/
    ├── supervisor/      Политика, бэкенды движка: WSL, локальный и внешний
    ├── ui/              PySide6: значок, окно состояния, настройки, окно ассистента, уведомления
    ├── win/             Windows: горячие клавиши и буфер обмена, Job Object, jump list, автозапуск,
    │   │                драйвер Claude Desktop (UI Automation) и его профиль
    │   └── profiles/    Профиль Claude Desktop из поставки
    └── linux/           Linux: инструменты буфера обмена, звук, автозапуск, аудиоустройства
```

## 8. Проектные решения

**Зачем API v2 с токеном и арендами.** WSL2 пробрасывает порт движка на loopback Windows, до которого может дотянуться любая веб-страница. Демон отклоняет запросы браузеров (заголовок `Origin`), заголовки `Host`, не являющиеся loopback (DNS rebinding), и `GET /trigger`, а с `--api-token` требует bearer-токен, хранящийся в файле с правами 0600. Эксклюзивные аренды решают, кто пишет в буфер обмена и кто подаёт сигналы: один держатель на задачу. Компаньон, который завис или завершился, теряет аренду, и демон снова пишет в буфер обмена и подаёт сигналы, так что диктовка никогда не теряется и никогда не сопровождается сигналом дважды. API привязывается до загрузки модели, поэтому «Выход» и «Перезапуск» работают в течение тех 10-60 секунд, что загружается модель.

**Зачем компаньон проверяет буфер обмена.** В WSL2 мост буфера обмена WSLg и `clip.exe` ненадёжны: иногда расшифровка так и не доходила до Windows. Компаньон сам пишет в буфер обмена Windows, читает его обратно и сравнивает, подтверждает каждый результат («побеждает последний статус»), сверяет пропущенные события, никогда не вставляет устаревший текст поверх буфера обмена пользователя и хранит каждый сбой в сохраняемом списке **Нескопированные** (запись атомарная: временный файл, `fsync`, `os.replace`).

**Зачем keepalive слушателя.** В Windows низкоуровневые перехватчики (`WH_KEYBOARD_LL`, `WH_MOUSE_LL`), которые используют библиотеки глобальных горячих клавиш, молча удаляются ОС, когда обратный вызов превышает `LowLevelHooksTimeout`, а это бывает при сильной нагрузке на CPU ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)). Нативный движок переустанавливает свой перехватчик каждые 60 секунд, так что потерянный перехватчик возвращается на следующем такте. Компаньон вместо этого использует `RegisterHotKey`, у которого такого таймаута нет.

**Зачем подключаемый TTS.** Протокол `TextToSpeech` (`app/features/tts/base.py`) отделяет режим Claude от каждого движка. Движок по умолчанию, Kokoro и VoxCPM2 это отдельные синтезаторы за отдельными extras, `NullSpeaker` подставляется, когда TTS выключен, а отсутствующий пакет или неудачный запуск приводят к работе без TTS с предупреждением. Добавление или удаление движка затрагивает только его собственный модуль.

**Остальные принципы.**
- Контракт прежде всего, в коде: `app/protocol/models.py` общий для обеих сторон и использует типы `Literal` для каждого дискретного параметра; `app/companion/contract.py` это единственный шов между ядром компаньона и его UI.
- Правила живут в чистых конечных автоматах, в которые время передаётся снаружи, так что каждое правило покрывается модульными тестами без потоков, HTTP и Win32.
- Надзор с ограниченными повторами: паузы, три предохранителя, лестница корректной остановки (HTTP, конец файла stdin, завершение процесса), Job Object как страховка и никакого автоматического `wsl --shutdown`, который удивил бы другие дистрибутивы.
- Дисциплина потоков: один диспетчер владеет состоянием, привязанные к потоку API Win32 живут в одном потоке на таймерах, а UI переходит в поток Qt через сигналы.
- Дисциплина часов: возраст результатов берётся по монотонным часам демона, потому что часы виртуальной машины WSL расходятся после сна хоста.
- Границы обеспечиваются тестами: `tests/test_import_boundary.py` не даёт компаньону зависеть от стека движка.
