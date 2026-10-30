[English](README.md) | [Português](README.pt-BR.md) | [Español](README.es.md) | **Русский** | [简体中文](README.zh-CN.md)

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/banner-light.png">
  <img alt="VoiceMate" src="docs/assets/brand/banner-light.png" width="640">
</picture>

**Нажмите горячую клавишу, говорите, вставляйте. Локальная голосовая диктовка в буфер обмена на базе Whisper, который работает на вашем компьютере.**

[![CI](https://github.com/nano-br/voice-mate/actions/workflows/ci.yml/badge.svg)](https://github.com/nano-br/voice-mate/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/nano-br/voice-mate?include_prereleases&sort=semver&style=flat)](https://github.com/nano-br/voice-mate/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?style=flat&logo=python&logoColor=white)](pyproject.toml)
[![Platforms](https://img.shields.io/badge/platforms-Windows%2010%2F11%20%7C%20Linux%20%26%20WSL2-0078D6?style=flat)](#platforms-and-gpus)
[![Languages](https://img.shields.io/badge/languages-en%20%7C%20pt--BR%20%7C%20es%20%7C%20ru%20%7C%20zh--CN-2EA44F?style=flat)](#languages)

[Скачать](https://github.com/nano-br/voice-mate/releases/latest) · [Быстрый старт](#быстрый-старт) · [Документация](#документация) · [История изменений](CHANGELOG.md)

<img src="docs/assets/screenshots/ru/hero.png" width="866" alt="Окно состояния VoiceMate со списками «Нескопированные» и «Недавние» рядом с меню в области уведомлений">

</div>

## Зачем нужен VoiceMate

<!-- POSITIONING: aguardando texto alinhado -->
VoiceMate это персональный голосовой ассистент, который работает на вашем компьютере и интегрируется с Claude Desktop и Claude Code. Он начинался как диктовка: нажимаете горячую клавишу, говорите, и текст готов к вставке в любой ИИ-инструмент, причём Whisper работает локально, а звук не покидает ваш компьютер. Сегодня он умеет больше: персональный ИИ-агент, имя и характер которого выбираете вы, голосом оркестрирует ваши разговоры в Claude Code и меняет настройки самого VoiceMate в обычном разговоре. Для тех, кто программирует с Claude Code, это возможность говорить в том же разговоре, в котором вы работаете. Для тех, кому нужна доступность, это возможность пользоваться Claude Code, не завися от клавиатуры.

Я создал его, потому что верю в простую вещь: чем быстрее и проще донести мысль до ИИ и чем больше подробностей ему дать, тем лучше результат. Говорить намного быстрее, чем печатать, а когда думаешь вслух, контекста набирается гораздо больше, чем получилось бы набрать на клавиатуре.

**Диктовка в буфер обмена это основа.** Я пользуюсь ею в реальной работе практически каждый рабочий день с марта 2026 года, на Windows с видеокартой NVIDIA. После перехода на видеокарту AMD я адаптировал VoiceMate к AMD через WSL2 и ROCm 7.2, и там он тоже проверен (Radeon RX 9070 XT, `gfx1201`). Whisper работает локально: для диктовки не нужны ни облачный сервис, ни оплата.

**Голосовой ассистент это центральный разговор**, который оркестрирует ваши остальные разговоры в Claude Code: перечисляет их, читает, задаёт им вопросы и голосом сообщает, когда они ответили. Он работает в двух режимах: в собственной сессии (при закрытом Claude Desktop) или внутри Claude Desktop, с его встроенными инструментами. Вы выбираете его имя, его личность (файл Markdown, который может лежать в вашем хранилище Obsidian) и хранилище заметок, которые он может читать. Голосовой диалог с Claude (`Ctrl+Alt+A`) это более лёгкий вариант: речь на входе, ответ вслух.

VoiceMate это проект с открытым исходным кодом под лицензией MIT. Если попробуете, расскажите, как всё прошло.

## Демо

Демонстрация и живая проверка персонального ИИ-агента, пока на бразильском португальском. Другие видео, более проработанные и на других языках, появятся позже.

[VoiceMate e seu agente pessoal de IA: demonstração e teste ao vivo com o Claude Code](https://youtu.be/T9H30G-AbZo), на канале Prompt Falado.

## Содержание

[Демо](#демо) · [Возможности](#возможности) · [Скриншоты](#скриншоты) · [Быстрый старт](#быстрый-старт) · [Использование](#использование) · [Голосовой ассистент](#голосовой-ассистент) · [Конфигурация](#конфигурация) · [Архитектура](#архитектура) · [Решение проблем](#решение-проблем) · [Документация](#документация) · [Участие в проекте](#участие-в-проекте) · [Лицензия](#лицензия)

## Возможности

**Диктовка (основное)**
- Одна горячая клавиша начинает и останавливает запись (по умолчанию `Ctrl+Alt+V`). Текст попадает в буфер обмена, готовый к вставке.
- Локальные модели Whisper, по умолчанию `large-v3-turbo`. Звук не покидает ваш компьютер.
- Язык диктовки закреплён для стабильного результата, а английские технические термины внутри речи на других языках по-прежнему распознаются.
- Звуковые сигналы: начало записи, расшифровка, скопировано, предупреждение, ошибка. Запись сама останавливается через 10 минут (настраивается).

**Приложение-компаньон для Windows**
- Значок в области уведомлений, который показывает состояние с первого взгляда, окно состояния с недавними расшифровками и настройки горячих клавиш, звуковых сигналов, уведомлений, языка и движка.
- Проверенная запись в буфер обмена: приложение записывает текст, читает его обратно и сравнивает. Текст, который не удалось доставить в буфер обмена, остаётся в списке **Нескопированные**, даже после перезапуска.
- Компаньон сам следит за движком: запускает его внутри WSL2, перезапускает при сбое и перезапускает WSL, когда в нём пропадает звук.
- Установщик для текущего пользователя (без запроса прав администратора) на 5 языках.
- Два режима распознавания речи, которые переключаются в меню в области уведомлений («Распознавание») или в настройках: **CPU** (видеокарта остаётся свободной для другой работы) и **GPU** (быстрее всего). У каждого режима своя модель Whisper. Если режим ещё не установлен, приложение устанавливает его после вопроса, называет размер загрузки и ставит только в собственное окружение движка.

<a id="languages"></a>**Языки**: компаньон, сообщения движка и установщик доступны на английском, бразильском португальском, испанском, русском и упрощённом китайском.

<a id="platforms-and-gpus"></a>**Платформы и GPU**
- Windows 10/11 с NVIDIA (CUDA): исходный путь, проверенный в боевых условиях. Движок запускается нативно из командной строки.
- Движок внутри WSL2 (конфигурация компаньона) и GPU AMD (ROCm 7.2) через WSL2: проверено на Radeon RX 9070 XT (RDNA4, `gfx1201`).
- AMD с Vulkan в Windows и Linux (X11, Wayland) с необязательным компаньоном используют ту же конфигурацию.
- Запасной режим на CPU везде. `make setup` определяет платформу и GPU и устанавливает подходящую сборку PyTorch и бэкенд распознавания речи.

**Голосовой диалог с Claude**
- `Ctrl+Alt+A` отправляет вашу речь Claude через Claude Code CLI (используется ваша собственная учётная запись: расшифрованный текст уходит в Anthropic, а звук не отправляется). Ответ копируется в буфер обмена и зачитывается вслух.
- Движки TTS: OmniVoice (используется, если ничего не сохранено), Kokoro (его предлагает `make setup`) и VoxCPM2. Или свой: любой сервис, следующий [протоколу TTS-провайдера](docs/tts-provider-protocol.md) (Настройки > Голос > Свой провайдер). Диалог продолжается от реплики к реплике; любая горячая клавиша прерывает ответ и начинает новую запись.

**Голосовой ассистент (по умолчанию выключен)**
- Один центральный разговор Claude в двух режимах: собственная беседа VoiceMate (процесс Claude Code, который запускает VoiceMate; виден и в Claude Desktop, и на телефоне) или уже существующий у вас разговор Claude Desktop (в него пишет короткая «вспышка» клавиатуры, только Windows). Он перечисляет и читает ваши остальные разговоры, отправляет им сообщения и запускает новые в фоне.
- Говорите с ним через `Ctrl+Alt+S` или нажмите **Говорить** в его окне (во время записи кнопка превращается в **Остановить и отправить**). Он говорит голосами, которые уже есть в Windows, ничего не устанавливая, или голосом движка (Kokoro или ваш провайдер): Настройки > **Ассистент** > «Источник голоса:». Голосовые команды (английские, португальские и испанские фразы) читают последний ответ, переключаются на разговор, отправляют ему сообщение или говорят с ним напрямую. Окно показывает чат и ожидающие ответы; его голос ждёт, пока вы записываете и пока запись расшифровывается, а затем повторяет то, что вы прервали; синий значок в трее и звук «Ответ готов» сообщают о новом ответе, а `Ctrl+Alt+R` зачитывает готовое.
- По голосу он меняет фиксированный список настроек VoiceMate, принимает личность (файл, заметку из хранилища Obsidian или свой текст) и использует навык на английском или бразильском португальском, который работает и сам по себе в любом разговоре Claude Code. См. [Голосовой ассистент](#голосовой-ассистент) и [полное руководство](docs/voice-assistant.md) (на английском).

## Скриншоты

| Меню в области уведомлений | «Перезапустить WSL?» и «Перезапустить VoiceMate?» |
|:---:|:---:|
| <img src="docs/assets/screenshots/ru/tray-menu.png" width="320" alt="Меню в области уведомлений"> | <img src="docs/assets/screenshots/ru/dialog-restart-wsl.png" width="516" alt="Вопрос «Перезапустить WSL?»"><br><img src="docs/assets/screenshots/ru/dialog-language.png" width="516" alt="Вопрос «Перезапустить VoiceMate?»"> |

<p align="center"><img src="docs/assets/screenshots/ru/settings-general.png" width="752" alt="Вкладка «Общие» окна «Настройки VoiceMate»"><br><sub>«Настройки VoiceMate», вкладка «Общие». Также: вкладки <a href="docs/assets/screenshots/ru/settings-hotkeys.png">«Горячие клавиши»</a> и <a href="docs/assets/screenshots/ru/settings-sounds.png">«Звуки»</a>.</sub></p>

<p align="center"><img src="docs/assets/screenshots/ru/settings-voice.png" width="752" alt="Вкладка «Голос» окна «Настройки VoiceMate» с движком голоса и полями собственного провайдера"><br><sub>«Настройки VoiceMate», вкладка «Голос»: движок голоса, который озвучивает ответы, включая «Свой провайдер (HTTP)».</sub></p>

<p align="center"><img src="docs/assets/screenshots/ru/assistant.png" width="568" alt="Окно ассистента VoiceMate с примером беседы"><br><sub>Окно ассистента с кнопкой **Говорить**, его открывает пункт «Ассистент» в меню в области уведомлений. Настройки: вкладка <a href="docs/assets/screenshots/ru/settings-assistant.png">«Ассистент»</a>. Раздел «Распознавание» с выбором CPU или GPU находится на вкладке «Общие» выше.</sub></p>

<p align="center"><img src="docs/assets/screenshots/ru/assistant-inbox.png" width="568" alt="Окно ассистента с группой «Ожидающие ответы» и списком «В ожидании»"><br><sub>То же окно с группой «Ожидающие ответы» и списком «В ожидании».</sub></p>

<p align="center"><img src="docs/assets/screenshots/ru/settings-assistant-sections.png" width="752" alt="Вкладка «Ассистент» окна «Настройки VoiceMate», прокрученная до групп «Личность», «Навык» и «Профиль Claude Desktop»"><br><sub>Вкладка «Ассистент», прокрученная вниз: группы «Личность», «Навык» и «Профиль Claude Desktop».</sub></p>

<p align="center"><img src="docs/assets/screenshots/tray-states.png" width="584" alt="Значок в области уведомлений в каждом состоянии, на тёмной и светлой панели задач, и бейдж, который виден, когда есть ожидающие ответы"><br><sub>Состояние показывает значок с бейджем, а синий бейдж означает, что есть ожидающие ответы. Что означает каждый бейдж: <a href="docs/usage.md#tray-icon-and-menu">Usage, tray icon and menu</a> (на английском).</sub></p>

## Быстрый старт

### Windows, с установщиком

**Пошагово, со скриншотами установщика: [руководство по установке](docs/install-guide.ru.md)** ([English](docs/install-guide.md), [Português](docs/install-guide.pt-BR.md), [Español](docs/install-guide.es.md), [简体中文](docs/install-guide.zh-CN.md)). Ниже краткая версия; полное справочное описание в [docs/installation.md](docs/installation.md) (на английском).

В установщике только компаньон. Движок работает внутри дистрибутива WSL2, поэтому сначала настройте его.

1. В PowerShell установите WSL2 с Ubuntu: `wsl --install -d Ubuntu-24.04`, затем откройте Ubuntu (если у вас уже был другой дистрибутив WSL, выполните ещё и `wsl --set-default Ubuntu-24.04` или позже задайте «Дистрибутив WSL:» в настройках). Внутри него установите аудиопакеты; Python 3.12 (в Ubuntu 24.04 он есть) и [Poetry](https://python-poetry.org/docs/#installation) должны быть доступны через `PATH` и в login-оболочке:
   ```bash
   sudo apt install -y libportaudio2 libasound2-plugins pulseaudio-utils wl-clipboard git make
   ```
2. Клонируйте движок и запустите пошаговую настройку:
   ```bash
   git clone https://github.com/nano-br/voice-mate.git ~/voice-mate
   cd ~/voice-mate
   make setup    # если нужна только диктовка, на вопрос «Какой основной режим?» ответьте 1 (clipboard)
   make doctor   # каждая проверка должна показать галочку
   ```
   Компаньон ищет движок в `~/voice-mate` и ещё в нескольких обычных папках ([список](docs/installation.md#2-install-the-engine)); если он лежит в другом месте, задайте «Папка движка:» в настройках. С GPU AMD установите также драйвер AMD и ROCm для WSL ([docs/wsl2.md](docs/wsl2.md)).
3. Рекомендуется: всё ещё в `~/voice-mate` один раз выполните `make run` и остановите его через `Ctrl+C`, когда он загрузится. При самом первом запуске скачивается модель Whisper, а компаньон даёт запуску движка всего 240 секунд (после 3 неудачных запусков подряд по тайм-ауту он перестаёт пробовать), и при медленном соединении этого может не хватить.
4. Скачайте `VoiceMate-Setup-x.y.z.exe` со [страницы последнего релиза](https://github.com/nano-br/voice-mate/releases/latest) и запустите его. Установщик не имеет цифровой подписи, поэтому Windows SmartScreen может выдать предупреждение: выберите **Подробнее**, затем **Выполнить в любом случае**. Установка идёт только для вашего пользователя, без запроса прав администратора.
5. Запустите VoiceMate. Пока загружается модель, значок в области уведомлений показывает «Запуск движка...» (от 10 до 60 секунд после того, как модель скачана), затем «Ожидание нажатия Ctrl+Alt+V».
6. По желанию: закрепите приложение на панели задач. Откройте меню «Пуск», найдите VoiceMate, щёлкните по нему правой кнопкой мыши и выберите **Закрепить на панели задач**.

### Из исходников

Понадобятся Python 3.12, [Poetry](https://python-poetry.org/docs/#installation), GNU `make` (в Windows сначала установите его, например через Chocolatey или Scoop) и `git`.

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup                           # определяет платформу и GPU, устанавливает PyTorch и выбранные вами модули
make doctor                          # проверяет микрофон, звук, горячие клавиши и GPU и для каждой проблемы выводит способ её исправить
make run ARGS="--output-lang en"     # запускает движок с его собственными горячими клавишами (нативно в Windows или в Linux)
```

**По умолчанию движок использует португальский** (`--output-lang pt-BR`): от него зависят язык, который слышит Whisper, язык ответов Claude и язык сообщений движка. Передайте `--output-lang en` (или другой код), как выше; `--transcription-language` и `VOICEMATE_LANG` меняют только один из этих параметров ([конфигурация](docs/configuration.md)). Компаньон передаёт эти флаги сам, см. [Использование](#использование).

Чтобы запустить компаньон из исходников в Windows с движком в WSL2: один раз выполните `make companion-venv`, затем `make run-tray`. В Linux выполните `make setup` и `make run`, как выше; для необязательного значка в области уведомлений и окна состояния выполните `poetry install --extras ui`, а затем `make run-tray` ([подробности](docs/installation.md#linux)).

## Использование

| Горячая клавиша | Пункт меню в области уведомлений | Что происходит |
|---|---|---|
| `Ctrl+Alt+V` | «Диктовка» | Речь превращается в текст, он копируется в буфер обмена |
| `Ctrl+Alt+A` | «Спросить Claude» | Речь уходит Claude, ответ копируется и зачитывается вслух |
| `Ctrl+Alt+S` | «Говорить с ассистентом» | Речь уходит ассистенту, а не в буфер обмена |
| `Ctrl+Alt+W` | «Открыть окно ассистента» | Открывает или закрывает окно ассистента |
| `Ctrl+Alt+R` | «Прослушать готовое» | Ассистент зачитывает ожидающие ответы |

Нажмите `Ctrl+Alt+V`, говорите после сигнала начала записи, снова нажмите `Ctrl+Alt+V` и вставляйте через `Ctrl+V` где угодно. Куда пойдёт текст, определяет горячая клавиша, которой остановлена запись. Текст, который не удалось доставить в буфер обмена, ждёт в списке **Нескопированные** (меню в области уведомлений и окно состояния): один щелчок копирует его. Чтобы выйти, выберите **Выйти из VoiceMate** в меню в области уведомлений; движок, который компаньон не запускал, продолжает работать.

**Язык диктовки.** На вкладке **Общие** окна «Настройки VoiceMate» пункт «Язык диктовки:» задаёт язык, на котором вы говорите. По умолчанию выбрано «Как в интерфейсе»; «Определять автоматически» позволяет Whisper определять язык каждой записи (Claude по-прежнему отвечает на языке интерфейса; с Kokoro лучше закрепить язык); либо выберите один язык. Компаньон передаёт его запускаемому движку как `--transcription-language` и `--output-lang`, а смена языка перезапускает движок. Движок, к которому компаньон только подключается (юнит systemd или запущенный вручную), сохраняет свои флаги.

Для голосового диалога с Claude нужны установленный и авторизованный Claude Code CLI и модуль `claude`, выбранный в `make setup`. См. [docs/usage.md](docs/usage.md#claude-flow).

**Режим распознавания.** В «Настройки» > **Общие** > «Распознавание» пункт «Выполнять на:» предлагает «CPU (видеокарта остаётся свободной)», «GPU (быстрее всего)» или «Автоматически (настройка движка)». То же делает пункт «Распознавание» в меню в области уведомлений. У каждого режима своя модель («Модель для CPU:», «Модель для GPU:»). Переключение перезапускает движок, а если режим ещё не установлен, VoiceMate сначала спрашивает (с указанием размера загрузки), затем устанавливает его в окне, где виден вывод. Из терминала: `make stt-check` и `make stt-install MODE=cpu` (или `gpu`). Цифры скорости и точности: [docs/configuration.md](docs/configuration.md#speech-to-text-modes).

### Голосовой ассистент

Ассистент это один разговор Claude, центральный, который знает, что делают ваши остальные разговоры, и ведёт их за вас: перечисляет их, читает, отправляет им сообщения, запускает новые в фоне и сообщает, когда они ответили. По умолчанию он выключен.

**Весь процесс, со схемами: [docs/voice-assistant.md](docs/voice-assistant.md)** (на английском; как VoiceMate подключается к Claude, два режима, управление вашими разговорами, как возвращаются ответы, голос и безопасность).

| | Собственная беседа VoiceMate | Разговор Claude Desktop |
|---|---|---|
| Что это | Процесс Claude Code, который запускает и ведёт VoiceMate | Уже существующий разговор, выбранный по точному названию в «Разговор в Desktop:» |
| Как VoiceMate пишет в него | Напрямую, через процесс | Короткая «вспышка»: окно Desktop выходит вперёд, текст вставляется и отправляется, фокус возвращается (около 2 секунд) |
| Требуется | CLI `claude` и вход в Claude | Открытый Claude Desktop и разблокированный экран; только Windows |

1. **Требования:** Claude Code CLI (`claude` в `PATH` Windows, в `~/.local/bin` или в `%APPDATA%\npm`, либо путь к нему в `VOICEMATE_CLAUDE_CLI`) и вход в Claude на этом компьютере (один раз `claude auth login`).
2. **Включить:** нажмите **Ассистент** в окне состояния (если ассистент выключен, оно подскажет как) или откройте Настройки > **Ассистент** и задайте «Режим ассистента:».
3. **Пользоваться:** нажмите `Ctrl+Alt+S`, говорите, снова нажмите `Ctrl+Alt+S`, и он ответит голосом движка. Или пишите в его окне (`Ctrl+Alt+W`). `Ctrl+Alt+R` зачитывает ожидающие ответы.

> [!WARNING]
> «Полный доступ, никогда не спрашивать (рискованно)» (`bypassPermissions`) позволяет разговорам, которые запускает ассистент, менять любые файлы и выполнять любые команды без вопросов. Оставьте значение по умолчанию, если не уверены в том, о чём их просите.

Подробнее (на английском): [голосовые команды и прямой режим](docs/voice-assistant.md#6-direct-mode-and-voice-commands), [ответы из других разговоров](docs/voice-assistant.md#5-answers-coming-back), [настройки голосом](docs/voice-assistant.md#8-settings-by-voice), [каждая настройка по шагам](docs/usage.md#voice-assistant), [профиль Claude Desktop](docs/desktop-profile.md) и [протокол TTS-провайдера](docs/tts-provider-protocol.md).

## Конфигурация

| Что | Где |
|---|---|
| Настройки компаньона | Windows `%APPDATA%\VoiceMate\companion.toml`, Linux `~/.config/voicemate/companion.toml` |
| Параметры движка, выбранные в `make setup`, токен API | `~/.config/voicemate/` (внутри WSL для компаньона; `%USERPROFILE%\.config\voicemate\` для движка, запущенного нативно в Windows) |
| HTTP API движка | `127.0.0.1:47821` |
| Журналы (`companion.log`, `engine.log`) | Windows `%LOCALAPPDATA%\VoiceMate\logs`, Linux `~/.local/state/voicemate/logs` |
| Список **Нескопированные** | `pending.json` рядом с папкой `logs` |
| Настройки ассистента (их читает skill) | `~/.voicemate/assistant.json` в вашей папке пользователя |
| Сессия ассистента, входящие, запущенные им разговоры, текст личности и кэш профиля Desktop | `assistant-session.json`, `assistant-inbox.json`, `assistant-conversations\`, `assistant-personality.md`, `desktop-profile\` и `assistant\` рядом с `pending.json` |

Все флаги движка, все ключи настроек и все переменные окружения: [docs/configuration.md](docs/configuration.md).

## Архитектура

VoiceMate состоит из двух частей. **Движок** записывает звук, расшифровывает его и обслуживает режим Claude; это демон на Python с локальным HTTP API. **Компаньон**: приложение на PySide6 для области уведомлений в Windows. Оно отвечает за горячие клавиши и буфер обмена и следит за движком внутри WSL2. Движок можно запускать и отдельно, из командной строки. Диаграммы ниже: упрощённые обзоры; каждая ведёт к полной диаграмме в [docs/architecture.md](docs/architecture.md) (на английском), где есть также диаграммы компонентов, HTTP API и обоснование проектных решений.

<details>
<summary>Системный контекст (обзор)</summary>

```mermaid
flowchart TB
    user(["Пользователь"])
    subgraph pc["Ваш ПК"]
        vm["VoiceMate<br/>локальная диктовка"]
        desk["Рабочий стол<br/>горячие клавиши, буфер обмена, область уведомлений"]
        audio["Микрофон и колонки"]
        gpu["GPU или CPU<br/>запускает Whisper"]
    end
    subgraph net["Интернет, необязательно"]
        hub["Загрузка моделей"]
        claude["Claude<br/>через Claude Code CLI"]
    end
    user -->|"Горячая клавиша, речь"| vm
    user -->|"Вставляет текст"| desk
    vm -->|"Записывает в буфер обмена, показывает значок"| desk
    vm -->|"Записывает звук"| audio
    vm -->|"Расшифровывает"| gpu
    vm -.->|"Скачивает модели"| hub
    vm -.->|"Спрашивает"| claude

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

Обзор. Полная диаграмма: [docs/architecture.md, System context](docs/architecture.md#1-system-context).

</details>

<details>
<summary>Контейнеры, Windows с WSL2 (обзор)</summary>

```mermaid
flowchart LR
    user(["Пользователь"])
    subgraph win["Windows"]
        ui["Интерфейс компаньона<br/>значок, состояние, настройки"]
        core["Ядро компаньона<br/>горячие клавиши, управление движком, доставка"]
        clip["Буфер обмена Windows"]
    end
    subgraph wsl["Дистрибутив WSL2"]
        api["Демон движка<br/>HTTP API, 127.0.0.1:47821"]
        stt["Бэкенд Whisper"]
        tts["TTS, необязательно"]
    end
    gpu["GPU"]
    user -->|"Меню значка"| ui
    user -->|"Горячая клавиша"| core
    ui <--> core
    core -->|"Записывает и проверяет"| clip
    core -->|"Запускает через wsl.exe"| api
    core <-->|"HTTP, токен, события"| api
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

Обзор. Полная диаграмма: [docs/architecture.md, Containers](docs/architecture.md#2-containers).

</details>

<details>
<summary>Одна диктовка по шагам (обзор)</summary>

```mermaid
sequenceDiagram
    autonumber
    actor U as Пользователь
    participant C as Компаньон
    participant E as Движок
    participant CB as Буфер обмена
    rect rgba(67,141,213,0.12)
        U->>C: Горячая клавиша
        C->>E: Начать запись
        E-->>C: Микрофон включён
        C->>U: Сигнал начала
    end
    rect rgba(106,61,154,0.12)
        U->>C: Горячая клавиша ещё раз
        C->>E: Остановить запись
        E->>E: Whisper расшифровывает
        E-->>C: Текст для доставки
    end
    rect rgba(46,125,50,0.12)
        C->>CB: Записать, прочитать обратно, сравнить
        alt Текст подтверждён
            C->>E: ACK delivered
            C->>U: Сигнал готовности
        else Буфер обмена занят другим приложением
            C->>C: Оставить текст в списке «Нескопированные»
            C->>E: ACK failed
            C->>U: Сигнал ошибки и уведомление
        end
    end
```

Обзор. Полная диаграмма: [docs/architecture.md, One dictation](docs/architecture.md#4-runtime-one-dictation).

</details>

<details>
<summary>Состояния значка (обзор)</summary>

```mermaid
stateDiagram-v2
    state "Остановлен" as Stopped
    state "Запуск" as Starting
    state "Работает" as Running
    state "Ошибка" as Error
    state "Перезапуск" as Restarting
    [*] --> Stopped
    Stopped --> Starting : Приложение запущено
    Starting --> Running : Движок готов
    Starting --> Error : Нет папки движка
    Running --> Restarting : Сбой движка или перезапуск WSL
    Restarting --> Running : Движок готов
    Restarting --> Error : Слишком много сбоев
    Error --> Restarting : Ручной перезапуск
    Running --> Stopped : Выход
    state Running {
        state "Ожидание" as Idle
        state "Запись" as Recording
        state "Расшифровка" as Transcribing
        state "Claude отвечает" as Answering
        state "Готов к работе" as Ready
        state "Предупреждение" as Warning
        [*] --> Idle
        Idle --> Recording : Горячая клавиша
        Recording --> Transcribing : Горячая клавиша ещё раз
        Recording --> Idle : Отмена
        Transcribing --> Ready : Текст скопирован
        Transcribing --> Answering : Режим Claude
        Answering --> Ready : Ответ скопирован
        Ready --> Idle : Через 3 с
        Idle --> Warning : Нет микрофона или пропал звук
        Warning --> Idle : Звук вернулся
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

Обзор. Полная диаграмма: [docs/architecture.md, Tray states](docs/architecture.md#5-tray-states).

</details>

<details>
<summary>Голосовой ассистент (обзор)</summary>

```mermaid
flowchart LR
    user(["Вы"])
    subgraph pc["Ваш ПК, Windows"]
        win["Окно ассистента<br/>чат, ожидающие ответы"]
        svc["Сервис ассистента<br/>внутри companion"]
        own["Собственная беседа<br/>Claude Code, инструменты voicemate"]
        dsk["Разговор Desktop<br/>пишет вспышка клавиатуры"]
        other["Ваши другие<br/>разговоры"]
        inbox[("Входящие<br/>ожидающие ответы")]
        eng["Движок<br/>речь на входе, голос на выходе"]
    end
    phone["Телефон"]
    user -->|"Клавиша речи"| eng
    eng -->|"Транскрипт"| svc
    user --> win
    win --> svc
    svc -->|"Промпты"| own
    svc -->|"Вспышка, затем чтение"| dsk
    own -->|"Список, чтение, отправка, запуск"| other
    dsk -->|"Инструменты сессий Desktop"| other
    other -->|"Ответы"| svc
    svc -->|"Хранит или сообщает"| inbox
    svc -->|"Голосовой блок"| eng
    eng -->|"Говорит, звук ответа"| user
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

Оба режима идут по одному циклу: ассистент обращается к вашим остальным разговорам, ответы возвращаются через сервис, а сервис озвучивает их или кладёт во входящие. Синий: VoiceMate; бирюзовый: сохранённые данные; серый: вне VoiceMate. Полная схема: [docs/architecture.md](docs/architecture.md#61-voice-assistant). Весь процесс: [docs/voice-assistant.md](docs/voice-assistant.md).

</details>

<details>
<summary>Управление движком и восстановление звука в WSL (обзор)</summary>

```mermaid
flowchart TD
    launch(["Запуск"]) --> q_health{"Движок уже отвечает?"}
    q_health -- "Да" --> attach["Подключиться к нему, дождаться готовности"] --> healthy
    q_health -- "Нет" --> q_dir{"Папка движка найдена?"}
    q_dir -- "Нет" --> failed["Ошибка<br/>ждать ручного перезапуска"]
    q_dir -- "Да" --> spawn["Запустить make run-engine<br/>в WSL"]
    spawn --> q_ready{"Готов за 240 с?"}
    q_ready -- "Да" --> healthy["Работает штатно<br/>проверка каждые 5 с"]
    q_ready -- "Нет" --> q_breaker
    healthy -->|"3 пропущенные проверки или выход"| q_breaker{"Слишком много перезапусков?"}
    q_breaker -- "Нет" --> backoff["Пауза от 2 до 120 с"]
    backoff --> launch
    q_breaker -- "Да" --> failed
    healthy -->|"Звук в WSL остановлен"| q_policy{"Перезапускать WSL при сбое звука"}
    q_policy -- "Никогда" --> nothing["Ничего не делать"]
    q_policy -- "Сначала спрашивать" --> ask["Спросить пользователя"]
    q_policy -- "Автоматически" --> q_others{"Запущены другие дистрибутивы?"}
    q_others -- "Да" --> ask
    q_others -- "Нет" --> wslrestart["wsl --shutdown"]
    ask -- "Перезапустить WSL" --> wslrestart
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

Обзор. Полная диаграмма: [docs/architecture.md, Supervisor](docs/architecture.md#6-supervisor-on-windows-and-wsl2).

</details>

## Решение проблем

| Симптом | Что делать |
|---|---|
| «Запуск движка...» длится долго | Первый запуск скачивает модель, это может занять несколько минут (один раз выполните `make run` в WSL, см. [Быстрый старт](#windows-с-установщиком)). Следующие запуски занимают от 10 до 60 секунд. Откройте **Движок** > **Открыть журналы** и прочитайте `engine.log`. |
| «Папка движка не найдена» | Клонируйте движок в одну из [обычных папок](docs/installation.md#2-install-the-engine) или задайте «Папка движка:» на вкладке **Общие** окна «Настройки VoiceMate». |
| Горячая клавиша сообщает «Занято другим приложением» | Возможно, ещё работает старый скрипт горячих клавиш VoiceMate. Закройте его и удалите из `shell:startup` или выберите другие горячие клавиши. |
| «Звук в WSL остановлен» или «Нет микрофона» | Подключите микрофон. VoiceMate перезапускает WSL в соответствии с настройкой «Перезапускать WSL при сбое звука:»; чтобы сделать это вручную, выберите **Движок** > **Перезапустить WSL...**. |
| Текст не попал в буфер обмена | Он лежит в списке **Нескопированные** (меню в области уведомлений и окно состояния). Щёлкните по нему, чтобы скопировать снова. |
| Английская речь распознаётся неверно, или сообщения движка на португальском | Движок, запущенный вручную, по умолчанию использует португальский: добавьте `ARGS="--output-lang en"` к `make run`. |
| Видеокарта занята другой работой | Выберите режим CPU: «Настройки» > **Общие** > «Распознавание» или меню в области уведомлений > «Распознавание». Если режим не установлен, VoiceMate сначала спросит. |
| Ассистент пишет «Claude Code не найден» или «Вход в Claude Code не выполнен» | Выполните `where claude` в терминале. VoiceMate также ищет в `~/.local/bin` и в `%APPDATA%\npm`; если `claude` лежит в другом месте, задайте в `VOICEMATE_CLAUDE_CLI` полный путь. Если Claude Code не установлен, установите его. Для сообщения о входе выполните `claude auth login`. Затем нажмите **Перезапустить** в окне ассистента. Подробнее: [docs/troubleshooting.md](docs/troubleshooting.md#voice-assistant). |
| Режим Desktop пишет «Разговор Claude Desktop не выбран» или «Не удалось подготовить ассистента» | «Разговор в Desktop:» пусто или ни у одного разговора Claude Desktop нет точно такого названия: введите название точно как в боковой панели Claude Desktop, откройте Claude Desktop и нажмите **Перезапустить**. Подробнее в [docs/troubleshooting.md](docs/troubleshooting.md#voice-assistant). |
| Что-то ещё на стороне движка | Выполните `make doctor` в папке движка. Он выводит способ исправления для каждой непройденной проверки. |

Другие проблемы и точные тексты сообщений: [docs/troubleshooting.md](docs/troubleshooting.md).

## Документация

Подробные документы (на английском):

- [Руководство по установке](docs/install-guide.ru.md): пошагово, со скриншотами установщика.
- [Установка](docs/installation.md): все способы установки, обновление и удаление.
- [Голосовой ассистент и Claude](docs/voice-assistant.md): весь процесс ассистента со схемами, два режима и то, как он ведёт ваши остальные разговоры.
- [Usage](docs/usage.md): сценарии, горячие клавиши, значок в области уведомлений, режимы распознавания, модели, голосовые настройки и голосовой ассистент.
- [Configuration](docs/configuration.md): флаги движка, файлы настроек, переменные окружения, режимы распознавания речи и тест скорости.
- [Решение проблем](docs/troubleshooting.md): `make doctor`, звук в WSL, журналы, известные сообщения.
- [Архитектура](docs/architecture.md): диаграммы и проектные решения.
- [WSL2 и AMD](docs/wsl2.md), [Устройство компаньона](docs/companion-app.md), [Бренд](docs/brand.md), [Выпуск релизов](docs/releasing.md).
- [Профиль Claude Desktop](docs/desktop-profile.md) и [протокол TTS-провайдера](docs/tts-provider-protocol.md): файл, сообщающий режиму Desktop, где элементы управления, и HTTP-контракт для вашего собственного голоса.
- Заметки исследований (на английском): [Проект голосового ассистента](docs/research/voice-assistant-design.md) и [VoiceMate как клиент Claude Code](docs/research/claude-code-client.md).

## Участие в проекте

Issue и pull request'ы приветствуются. Все команды разработки идут через Makefile: `make format lint test` для движка (Ruff и строгий mypy) и `make companion-lint companion-test` для компаньона. Каждая строка интерфейса переведена на 5 языков через gettext. Коммиты оформляются по Conventional Commits и объясняют контекст. Сначала прочитайте [CONTRIBUTING.md](CONTRIBUTING.md).

## Благодарности

VoiceMate опирается на работу многих проектов: [Whisper](https://github.com/openai/whisper) от OpenAI, [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [CTranslate2-ROCm](https://github.com/arlo-phoenix/CTranslate2-rocm), [PySide6](https://doc.qt.io/qtforpython-6/), [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice), [Kokoro](https://github.com/hexgrad/kokoro), [VoxCPM](https://github.com/OpenBMB/VoxCPM), [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) вместе с [Claude Code](https://docs.claude.com/en/docs/claude-code), [Inno Setup](https://jrsoftware.org/isinfo.php) и [PyInstaller](https://pyinstaller.org/).

## Лицензия

[MIT](LICENSE) © Álli Terhorst. Часть [NanoBR](https://github.com/nano-br): утилиты с открытым исходным кодом для повседневной продуктивности.
