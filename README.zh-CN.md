[English](README.md) | [Português](README.pt-BR.md) | [Español](README.es.md) | [Русский](README.ru.md) | **简体中文**

# VoiceMate

> 按下快捷键，说话，粘贴。本地 Whisper 转写直接进入剪贴板，也可以交给 Claude 处理，再用你自己的声音朗读出来。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/types-mypy-blue.svg)](https://mypy-lang.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)

## 为什么

云端听写很快，但并非一直如此。VoiceMate 在你的 GPU 上**本地**运行 Whisper，音频永远不会离开你的电脑，没有网络延迟，没有月费，也无需在隐私上做出妥协。按下快捷键，说话，随处粘贴。

## 功能

- **切换式快捷键**：按一次开始，再按一次停止并转写
- **两种流程，一个麦克风**：`Ctrl+Alt+V` 把转写结果放进剪贴板；`Ctrl+Alt+A` 把它发送给 Claude（多轮对话），并通过 TTS 朗读 AI 的回复
- **本地转写**：在 NVIDIA/CPU 上使用 `faster-whisper`（CTranslate2），在 AMD GPU 上使用 `whisper.cpp` + Vulkan（约 1.6 GB 显存，large-v3-turbo）；后端会根据 GPU 自动选择
- **GPU 加速，不限厂商**：同时支持 NVIDIA（CUDA）**和** AMD（Vulkan + ROCm），并可自动回退到 CPU。空闲时显存占用 ≈ 0（STT 作为子进程运行；TTS 在第一次朗读时才延迟加载）
- **可插拔的 TTS**：Claude 的回复由 [VoxCPM2](https://github.com/OpenBMB/VoxCPM) 朗读（20 亿参数，可根据文字描述设计声音，支持流式输出）。架构将每个 TTS 引擎隔离开来，替换或移除时无需改动其他部分
- **配合 Win+V 的双剪贴板**：AI 流程先复制转写结果，再复制回复，因此 Windows 剪贴板历史记录会把两者并排显示，方便核对
- **由停止键决定去向**：用任意快捷键开始；*停止*时按下的快捷键决定由哪个处理程序接手（剪贴板或 Claude）
- **随时取消**：在 Claude 回复期间（或 TTS 朗读期间）按下任意快捷键，会立即取消并开始新的录音，对话上下文保持不变
- **自我修复的快捷键监听器**：定期重新安装全局快捷键，以便在高负载下 Windows 悄悄移除钩子后恢复
- **看门狗**：进程级健康监控，卡死时自动重启
- **可配置的最长录音时间**：防止忘记结束录音（默认：10 分钟）
- **声音反馈**：开始、警告、转写完成和 AI 回复就绪各有不同的提示音
- **支持鼠标触发**：如果你愿意，可以用鼠标侧键代替键盘（仅限剪贴板流程）

## 系统要求

- 以下任一受支持的环境（平台层会自动选择合适的集成方式）：
  - **Windows 10/11** 原生（推荐 NVIDIA）：最初的目标平台，保持不变
  - **Linux** 原生，X11 或 Wayland
  - Windows 11 上的 **WSL2**（Ubuntu）：应用**完全在 WSL 内**运行，Windows 端只需一个很小的快捷键脚本；这是 **AMD GPU**（ROCm）的推荐方案。参见 [docs/wsl2.md](docs/wsl2.md)
- Python 3.12（通过 VoxCPM2 的 TTS 流程尚不支持 3.13）
- [Poetry](https://python-poetry.org/docs/#installation)
- GPU 是可选的，但强烈推荐（TTS 要达到可接受的延迟则必须有 GPU）：
  - 支持 CUDA 的 **NVIDIA**，**或**
  - **AMD**（RDNA，例如 RX 7000/9000），通过 ROCm（Linux/WSL2）或 ROCm-on-Windows（Adrenalin ≥ 26.2.2）
  - 没有 GPU？仍然可以在 CPU 上运行（速度较慢，可以考虑 `--no-tts`）
- **仅 Claude 流程需要：** Node.js 18+ 以及已在本地完成认证的 [Claude Code CLI](https://docs.claude.com/en/docs/claude-code)

## 安装

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup
```

`make setup` 会**检测你的 GPU**（NVIDIA / AMD / 无），在你确认后安装对应的 PyTorch 版本（NVIDIA 用 CUDA `cu128`，AMD 用 ROCm，或 CPU 版）以及你选择的模块，并把所有选择记录在 `~/.config/voicemate/config.toml` 中。随时可以用 **`make configure`** 重新运行选择程序（例如更换 GPU 之后）。

> **AMD 说明：** `make setup` 会安装 ROCm 版 PyTorch（用于 VoxCPM/TTS），并下载 **whisper.cpp + Vulkan**（用于转写）。ROCm wheel 包**不在** PyPI 上，并且必须事先安装 AMD Adrenalin 驱动（≥ 26.2.2）；如果看起来缺少驱动，安装程序会发出警告。参见下文的“GPU 后端”。

### 模块化安装（extras）

`make setup` 会询问你需要哪些模块。如果你更想以非交互方式安装，细粒度的目标依然可用（注意：这些目标不会安装 GPU 版 PyTorch，之后请运行 `make configure`，或者直接使用 `make setup`）：

| 命令                                           | 安装内容                                                               |
| ---------------------------------------------- | ---------------------------------------------------------------------- |
| `make setup_env_minimal`                       | 仅**核心**：语音 → 转写 → 剪贴板。                                     |
| `make setup_env_claude`                        | 核心 + `claude-agent-sdk`（启用 `Ctrl+Alt+A` Claude 流程）。           |
| `make setup_env_tts`                           | 核心 + `voxcpm` + `soundfile`（TTS，体积大：约 5 GB 模型权重）。       |
| `make setup_env` *（旧版，默认 NVIDIA）*       | 核心 + Claude + TTS + CUDA 版 PyTorch（`--extras all`）。              |
| `make setup_env_custom EXTRAS="claude tts"`    | 自由组合 extras。                                                      |

Extras（传给 `poetry install --extras`）：`claude`、`tts`、`whisper-gpu`（通过 `openai-whisper` 在 AMD GPU 上转写）、`all`。

如果缺少某个 extra，应用仍会启动，只是禁用对应的流程并给出说明性的警告（`extra 'claude' not installed`），绝不会直接崩溃。

### 语言

Claude 默认用 PT-BR 回复。如需更改：

```bash
# 将助手切换为英语
make run ARGS="--output-lang en"
```

在内部，标准提示词（用英语编写）带有一个 `{output_lang}` 占位符，在运行时填充；不保留提示词的翻译副本。

**应用自身的消息**（日志、CLI 帮助文本）同样通过 `gettext` + Babel 进行本地化。默认语言为 PT-BR；可通过环境变量切换：

```bash
# 应用日志使用英语（或 `es`、`ru`、`zh-CN`）
VOICEMATE_LANG=en make run
```

可用的翻译目录：`pt_BR`、`en`、`es`、`ru` 和 `zh_CN`。每个面向用户的字符串都必须存在于所有这些翻译目录中；除 `en` 以外的每个翻译目录都提供翻译，`en` 的 `msgstr` 保持为空（英语 msgid 本身就是文本）。

编辑 / 重新生成翻译目录：

```bash
make i18n-extract     # 将 _() 字符串提取到 voicemate.pot
make i18n-update      # 将新的键同步到现有的 .po 文件
make i18n-compile     # 将 .po 编译为 .mo（gettext 在运行时加载 .mo）
```

翻译目录文件位于 `app/i18n/locales/{pt_BR,en,es,ru,zh_CN}/LC_MESSAGES/voicemate.po`。

### 代码约定

- **标识符、配置键、docstring、新的注释**：英语（PEP 8）。
- **LLM 提示词**：标准英语版本，带 `{output_lang}` 占位符。不保留提示词的翻译副本。
- **面向用户的字符串**（日志、消息、帮助）：以英语作为 `msgid`，翻译放在 `app/i18n/locales/<lang>/LC_MESSAGES/voicemate.po` 中。默认语言为 PT-BR。添加新翻译的方法：在代码中用 `_()` 标记，然后运行 `make i18n-extract && make i18n-compile`。

### 配置 Claude Code（可选，仅 AI 流程需要）

如果你只需要剪贴板流程（`Ctrl+Alt+V`），可以跳过本节，并使用 `--no-claude-chat` 运行。

对于 AI 流程（`Ctrl+Alt+A`），VoiceMate 通过 `claude-agent-sdk` 与 Claude 通信，它会**复用本地的 `claude` CLI 及其凭据**，无需额外的 API 密钥。

1. **安装 Node.js 18+**（已安装可跳过）。从 [nodejs.org](https://nodejs.org/) 下载，或使用 `nvm-windows` / `fnm` 等版本管理器。

2. **全局安装 Claude Code：**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

3. **进行认证。** 运行一次 CLI 并按照交互式登录流程操作（它会打开浏览器）：
   ```bash
   claude
   ```
   选择你使用的认证方式（Anthropic 账户或 Claude Pro/Max）。登录后输入 `/exit` 退出对话，凭据此时已保存在本地。

4. **验证是否可用：**
   ```bash
   claude --version
   claude -p "ping"
   ```
   如果 `ping` 返回了 Claude 的回复，就说明配置完成了。

Claude 完成认证后，VoiceMate 的 AI 流程会在 `poetry run voice-mate` 时自动使用它。如果缺少 `claude` 或尚未登录，AI 流程会被静默跳过，剪贴板流程照常工作。

### 配置 TTS（VoxCPM2）

默认情况下，Claude 的回复由 [VoxCPM2](https://github.com/OpenBMB/VoxCPM) 朗读：这是一个拥有 20 亿参数的多语言模型（支持 PT-BR），它根据文字描述生成声音，而不需要参考音频。

- 当你的环境运行在 Python 3.12 上时，`voxcpm` 软件包会自动安装。首次运行时，模型权重会从 Hugging Face 下载（几 GB，需要一点时间）。
- 默认声音是“一位年轻的巴西女性，自然而温暖，语速平稳”，可以用 `--tts-voice "..."` 自定义。
- 要禁用 TTS，请使用 `--no-tts` 运行（回复仍会进入剪贴板，三连提示音也会恢复）。
- 如果 VoxCPM2 启动失败（没有 CUDA、磁盘空间不足等），应用会静默回退到无 TTS 状态，无需任何操作。

#### GPU 后端（NVIDIA / AMD / CPU）

`torch`/`torchaudio` **没有**在 `pyproject.toml` 中固定版本，因为合适的版本取决于你的显卡和操作系统。`make setup`（通过 `app.setup.gpu_bootstrap`）会检测平台和 GPU，并安装正确的版本：

| 平台 × GPU           | PyTorch 版本               | 转写（按可用性从优到次）                                  | TTS（OmniVoice/VoxCPM） |
| -------------------- | -------------------------- | --------------------------------------------------------- | ---------------------- |
| Windows + NVIDIA     | CUDA `cu128`               | `faster-whisper`（CUDA）                                  | GPU（CUDA）            |
| Windows + AMD        | ROCm（`repo.radeon.com`）  | **whisper.cpp + Vulkan**                                  | GPU（ROCm）            |
| Linux/WSL2 + AMD     | ROCm（pytorch.org）        | **通过 CTranslate2-ROCm 运行 faster-whisper** → whisper.cpp + Vulkan → openai-whisper | GPU（ROCm） |
| Linux + NVIDIA       | CUDA `cu128`               | `faster-whisper`（CUDA）                                  | GPU（CUDA）            |
| 任意平台，无 GPU     | CPU                        | `faster-whisper`（int8）                                  | CPU（较慢）            |

**Linux/WSL2 上的 AMD（AMD 推荐方案）：** 安装程序会提供构建 [CTranslate2-ROCm 复刻版（fork）](https://github.com/arlo-phoenix/CTranslate2-rocm) 的选项。有了它，转写使用的就是与 NVIDIA 完全相同的 `faster-whisper` 引擎（质量完全一致）。如果你跳过它（或构建失败），这条链会自动回退到用 Vulkan 构建的 **whisper.cpp**（服务器模式让模型保持常驻，启动很快）并配合 silero-VAD，再回退到 `openai-whisper`。该选择会被记住（配置中的 `ct2_rocm_ok`）；`make configure` 会重试，`make stt-eval` 会客观衡量质量（WER + 单词拆分错误检测器）。

**Windows 上的 AMD：** 转写使用 **whisper.cpp + Vulkan**，即一个小型原生程序加上一个 GGUF 模型（large-v3-turbo fp16），下载到 `~/.cache/voicemate/whispercpp/`（经过 SHA-256 校验）。ROCm 版 PyTorch 仍然会被安装，但只用于 TTS。

确认 GPU 加速已生效：

```bash
poetry run python -c "import torch; print('GPU:', torch.cuda.is_available())"
```

这里必须输出 `GPU: True`（在 ROCm 上，AMD 的 HIP 会报告为 `cuda`，因此对 AMD 来说 `True` 也是正确的）。如果输出 `False`：

- **NVIDIA：** 更新驱动（`nvidia-smi`）；较新的驱动（≥ 545）支持 CUDA 12.8。
- **AMD：** 安装/更新 Adrenalin 驱动（≥ 26.2.2），然后运行 `make configure`。

如果你没有 GPU 并且只需要剪贴板流程，请使用 `--no-tts` 运行。VoxCPMSpeaker 在启动时如果检测到 PyTorch 没有加速，也会打印一条针对显卡厂商的警告。

每次运行时都可以覆盖检测结果：`--gpu-backend {auto,nvidia,amd,cpu}`、`--whisper-backend {faster-whisper,whispercpp,openai-whisper}` 和 `--stt-strategy {auto,faster-whisper-rocm,whispercpp,openai-whisper}`。

### 平台与触发方式

平台层（`app/platform/`）会检测你所处的环境，并选择合适的快捷键机制和剪贴板集成方式；可以用 `--platform` / `--trigger` 覆盖：

| 平台            | 快捷键触发方式（默认）                    | 剪贴板               | 说明 |
| --------------- | ----------------------------------------- | -------------------- | ----- |
| `windows`       | `keyboard-hooks`（keyboard/mouse 库）     | pyperclip            | 行为与以往相同（包括监听器保活） |
| `linux-x11`     | `pynput`（GlobalHotKeys）                 | pyperclip（xclip）   | `poetry install --extras linux` |
| `linux-wayland` | `evdev`（/dev/input，需要加入 `input` 组）| pyperclip（wl-copy） | `sudo usermod -aG input $USER` |
| `wsl2`          | `socket`：本地 HTTP 守护进程 + Windows 端的小型快捷键脚本 | WSLg 同步（备用 `clip.exe`） | 参见 [docs/wsl2.md](docs/wsl2.md) |

默认快捷键在所有平台上都相同：`Ctrl+Alt+V`（剪贴板）和 `Ctrl+Alt+A`（Claude）。在 WSL2 上，它们由 `scripts/windows/voicemate-hotkeys.ahk`（或 `.ps1`）注册，脚本会向守护进程发送 POST 请求，语义同样是“停止时按下的快捷键决定处理程序”。运行 `make doctor` 可以检查麦克风/音频/触发方式/GPU，并给出可操作的修复建议。在 Windows 上，[配套应用](#配套应用托盘)取代了这些脚本。

## 使用方法

```bash
make run
```

默认快捷键：

- **`Ctrl+Alt+V`**：剪贴板流程（转写 → 剪贴板）
- **`Ctrl+Alt+A`**：Claude 流程（转写 → Claude → AI 回复进入剪贴板 + TTS）

### 剪贴板流程

1. 按 `Ctrl+Alt+V` 开始录音（开始提示音）
2. 自然地说话
3. 再按一次 `Ctrl+Alt+V` 停止
4. 转写结果被复制到剪贴板（双提示音）
5. 在任意位置用 `Ctrl+V` 粘贴

### Claude 流程（语音多轮对话）

1. 按 `Ctrl+Alt+A` 开始录音
2. 说出你的提示
3. 再按一次 `Ctrl+Alt+A` 停止：VoiceMate 进行转写，把转写结果复制到剪贴板，并发送给 Claude
4. AI 的回复会替换剪贴板内容，VoxCPM2 开始朗读（默认使用 PT-BR）
5. 再按 `Ctrl+Alt+A` 可以继续追问，对话会在同一会话中继续

**由停止键决定去向：** 你可以用 `Ctrl+Alt+V` 开始、用 `Ctrl+Alt+A` 停止（反之亦然）。*停止*时按下的快捷键决定由哪个处理程序接手。

**在 Claude 思考或朗读时取消：** 在 AI 回复期间（或 TTS 朗读期间）按下任意快捷键，会立即取消并开始新的录音。对话上下文会被保留。

**Win+V 历史记录：** 由于转写结果和 AI 回复都会经过剪贴板，Windows 剪贴板历史记录（`Win+V`）会同时显示两者，方便你对比自己说的话和 Claude 的回答。

### 选项

```bash
# 选择其他 Whisper 模型
poetry run voice-mate --model medium

# 自定义快捷键
poetry run voice-mate --hotkey "ctrl+shift+r" --claude-chat-hotkey "ctrl+shift+c"

# 禁用 Claude 流程（仅剪贴板）
poetry run voice-mate --no-claude-chat

# 为 Claude 设置系统提示词
poetry run voice-mate --claude-system-prompt "你是一个简洁的效率助手。"

# 限制多轮会话的轮数
poetry run voice-mate --claude-max-turns 20

# 禁用 TTS（回复只进入剪贴板 + 提示音）
poetry run voice-mate --no-tts

# 自定义 TTS 声音配置
poetry run voice-mate --tts-voice "一位巴西男性，声音低沉，语速从容。"

# TTS 强制使用 CPU（较慢，但无需 GPU）
poetry run voice-mate --tts-device cpu

# 将生成的 TTS 音频保存到指定文件夹
poetry run voice-mate --tts-save-dir ./tts_logs

# Whisper 转写强制使用 CPU（没有可用的 GPU 时）
poetry run voice-mate --cpu

# 本次运行覆盖 GPU 检测 / 转写后端
poetry run voice-mate --gpu-backend amd                       # 强制使用 AMD（ROCm）
poetry run voice-mate --gpu-backend nvidia --whisper-backend faster-whisper

# 改用鼠标侧键（仅剪贴板流程）
poetry run voice-mate --input-method mouse --mouse-button x

# 调整看门狗和监听器保活
poetry run voice-mate --listener-refresh-seconds 30 --watchdog-timeout 60
```

### 模型

| 模型               | 显存（GPU） | 速度      | 质量       |
| ------------------ | ----------- | --------- | ---------- |
| `tiny`             | 约 75 MB    | 非常快    | 基础       |
| `base`             | 约 140 MB   | 快        | 良好       |
| `small`            | 约 460 MB   | 中等      | 很好       |
| `medium`           | 约 1.0 GB   | 中等      | 优秀       |
| `large-v3-turbo`   | 约 1.5 GB   | 快        | 极佳       |
| `large-v3`         | 约 3.0 GB   | 慢        | 最高       |

默认是 `large-v3-turbo`：速度与质量的最佳平衡，尤其适合混合语言的音频。

## 配套应用（托盘）

配套应用是一个驻留在系统托盘中的小型桌面应用（PySide6）。它负责启动并监管引擎、注册快捷键、播放提示音、把每条转写结果写入剪贴板并确认写入成功，还为你提供一个统一的地方来查看状态和退出所有组件。在 Windows 上，它取代了 PowerShell/AutoHotkey 脚本，并驱动 WSL2 中的引擎；在 Linux 上，它是一个可选的界面。引擎本身没有变化：不使用配套应用时，`make run` 依然可用。

### 在 Windows 上安装

**使用安装程序（推荐）。** 运行 `VoiceMate-Setup-<version>.exe`。它只为你的用户安装（不会弹出管理员权限提示），安装到 `%LOCALAPPDATA%\Programs\VoiceMate`，并将 VoiceMate 添加到“开始”菜单；桌面快捷方式和登录时启动是可选的（首次安装时会提供后者，之后由设置中的 **登录时启动 VoiceMate** 控制）。安装程序支持英语、葡萄牙语、西班牙语、俄语和简体中文。如需自行构建（需要 Python 3.12+ 和 Inno Setup 6.3+，`winget install JRSoftware.InnoSetup`）：

```powershell
make companion-venv        # 只需一次：.venv-companion，包含固定版本的 PySide6 和 PyInstaller
make companion-installer   # dist\VoiceMate（PyInstaller），然后 dist\installer\VoiceMate-Setup-<version>.exe
```

**从源码运行。** 在 Windows 上使用 Python 3.12+：

```powershell
make companion-venv   # 只需一次
make run-tray
```

无论哪种方式，引擎仍然运行在 WSL2 中（按 [docs/wsl2.md](docs/wsl2.md) 安装）：配套应用会替你启动它，或者连接到已经在运行的引擎（例如 systemd 服务）。如果旧的快捷键脚本（PowerShell 或 AutoHotkey）仍在运行，请关闭它，并从 `shell:startup` 中删除它的快捷方式：现在由配套应用负责 `Ctrl+Alt+V` 和 `Ctrl+Alt+A`。

### 固定到任务栏

Windows 不允许安装程序固定应用。打开“开始”菜单，搜索 VoiceMate，右键单击它并选择 **固定到任务栏**。VoiceMate 运行时，单击固定的图标会打开其状态窗口；右键单击它会提供 **设置**、**重启引擎**、**重启 WSL...** 和 **退出 VoiceMate**。固定使用的是“开始”菜单中的快捷方式，因此适用于已安装的应用。首次运行时，会有一条 **将 VoiceMate 固定到任务栏** 通知提醒你这些步骤。

托盘图标（VoiceMate 的人形图标，角落的小徽标显示录音、转写和就绪状态，类似 Windows 的麦克风使用中指示器）是另一回事：Windows 11 会把新的托盘图标隐藏在时钟旁边的箭头后面，因此 VoiceMate 默认会让自己的图标保持显示在任务栏上。它只在你还没有做出决定时这样做：如果你在 Windows 任务栏设置中隐藏了该图标，VoiceMate 会尊重这一选择，不会再让它显示出来。若要永久隐藏，请在设置 > **常规** 中关闭 **始终在任务栏上显示 VoiceMate 图标**。若要让你在 Windows 中隐藏的图标重新显示，请关闭该选项，单击 **应用**，然后再次开启并单击 **确定**：无论 Windows 设置如何，图标都会重新显示。

### 退出

托盘图标菜单 > **退出 VoiceMate**（状态窗口中也有这个按钮，固定图标的右键菜单中也有这一项）。退出时，由配套应用启动的引擎会被停止；配套应用只是连接上的引擎（例如 systemd 服务）则会继续运行。对于已安装的应用，可在终端（PowerShell）中执行：

```powershell
& "$env:LOCALAPPDATA\Programs\VoiceMate\VoiceMate.exe" --command quit
```

从源码运行时：`make run-tray ARGS="--command quit"`。`--command` 永远不会启动 VoiceMate：如果它没有在运行，则什么也不会发生。

### 设置

托盘图标菜单 > **设置...**，分为三个选项卡：**快捷键**；**声音**（内置声音或你自己的 WAV 文件，以及音量）；**常规**：语言、通知、**登录时启动 VoiceMate**、**始终在任务栏上显示 VoiceMate 图标**（仅限 Windows）以及引擎（模式、WSL 发行版、引擎文件夹，以及 **音频故障时重启 WSL**：**自动**、**先询问** 或 **从不**）。设置保存在 `%APPDATA%\VoiceMate\companion.toml`（Linux：`~/.config/voicemate/companion.toml`）中，卸载后仍会保留。日志位于 `%LOCALAPPDATA%\VoiceMate\logs`（Linux：`~/.local/state/voicemate/logs`）；托盘图标菜单 > **引擎** > **打开日志** 可以打开它们。未能进入剪贴板的转写内容会保存在 `%LOCALAPPDATA%\VoiceMate\pending.json`（Linux：`~/.local/state/voicemate/pending.json`）中，直到你复制它们或清空列表（托盘菜单中的 **未复制** > **清空列表**，或状态窗口中的 **清空**），因此即使重启或崩溃之后，它们仍然位于 **未复制** 下。退出时仍在等待写入剪贴板的结果也会保存在那里。该文件包含转写文本：它保存在你的用户配置文件中，列表清空后即被删除，卸载程序会把它和日志一起删除。如果该文件无法读取，VoiceMate 会把它另存为 `pending.json.broken-<date>`，以空列表启动并通知你。

更换语言需要重启后才能生效，因此 VoiceMate 会询问 **重启 VoiceMate？**：**立即重启** 会重启它（引擎也会重启，大约需要 10 秒）；**稍后** 会保留你的选择，在此之前 **常规** 选项卡会显示“重启 VoiceMate 后生效。”

### Linux（可选）

CLI 照常工作。如需托盘图标和状态窗口，在仓库中执行：

```bash
poetry install --extras ui
make run-tray
```

在 Linux 上，引擎使用自己的快捷键（**快捷键** 选项卡以只读方式显示它们）。没有系统托盘时（例如未安装 AppIndicator 扩展的 GNOME），状态窗口就是主窗口。要将 VoiceMate 添加到应用程序菜单：

```bash
mkdir -p ~/.local/share/applications &&
  sed "s|@VOICEMATE_DIR@|$PWD|g" packaging/linux/voicemate-companion.desktop \
  > ~/.local/share/applications/voicemate-companion.desktop
```

### 故障排除

| 现象 | 解决方法 |
| ------- | --- |
| 长时间显示“正在启动引擎...” | 首次加载模型需要 10 到 60 秒。请查看 `engine.log`（**引擎** > **打开日志**），并检查设置中的 WSL 发行版和引擎文件夹 |
| 首次运行时某个快捷键显示“已被其他应用占用” | 旧的快捷键脚本（PowerShell 或 AutoHotkey）仍在运行：关闭它，并将其从 `shell:startup` 中删除 |
| “WSL 音频已停止”或“没有麦克风” | 连接麦克风。WSL 会按照 **音频故障时重启 WSL** 的设置自动重启；也可以手动操作：**引擎** > **重启 WSL...** |
| “引擎版本比此应用旧。请重启或更新引擎。” | 在 WSL 中更新代码（`git pull`），然后使用 **重启引擎** |
| 某条转写内容未被复制 | 它会保留在 **未复制** 下，既在托盘菜单中（单击即可复制），也在状态窗口中（**复制**），即使 VoiceMate 重启后也是如此。**清空列表**（托盘）或 **清空**（状态窗口）会在询问后将其清空 |
| SmartScreen 对安装程序发出警告 | 安装程序没有代码签名：**更多信息** > **仍要运行** |

## Makefile

| 命令               | 说明                                          |
| ------------------ | --------------------------------------------- |
| `make setup`       | 检测平台 + GPU，安装对应的 PyTorch + 模块，并记住选择 |
| `make configure`   | 重新运行 GPU/模块选择程序（例如更换 GPU 之后） |
| `make doctor`      | 环境诊断（麦克风/音频、触发方式、whisper.cpp、GPU）并给出修复建议 |
| `make stt-eval`    | STT 质量关卡：基于本地样本的 WER + 单词拆分错误检测器 |
| `make setup_env`   | 旧版安装方式（默认 NVIDIA + 全部 extras）     |
| `make lock`        | 重新生成 `poetry.lock`（修改 pyproject 之后） |
| `make run`         | 使用默认模型（`large-v3-turbo`）运行          |
| `make run-large`   | 使用 `large-v3` 运行                          |
| `make run-turbo`   | 使用 `large-v3-turbo` 运行                    |
| `make format`      | 用 Ruff 格式化代码                            |
| `make lint`        | 用 Ruff 检查代码 + 用 Mypy 检查类型           |
| `make test`        | 运行 pytest 测试套件                          |
| `make run-tray`    | 运行配套应用（托盘）；`ARGS="..."` 用于传递参数 |
| `make companion-venv` | 创建 `.venv-companion`（Windows 上配套应用的开发环境，版本固定） |
| `make companion-test` / `make companion-lint` | 测试 / 检查配套应用 |
| `make companion-build` | 用 PyInstaller 打包配套应用（`dist\VoiceMate`） |
| `make companion-installer` | 用 Inno Setup 构建 Windows 安装程序（`dist\installer`） |
| `make clean`       | 清除缓存                                      |

## 架构

```
app/
├── main.py                          # 入口 + CLI 解析 + 流程装配
├── core/
│   └── config.py                    # 配置 dataclass + FlowConfig + TTSConfig
└── services/
    ├── recorder.py                  # 麦克风采集（sounddevice）
    ├── transcriber.py               # Whisper 推理（faster-whisper）
    ├── audio_feedback.py            # 跨平台提示音
    ├── audio_player.py              # 基于队列的音频播放器，用于 TTS 流式输出
    ├── recording_session.py         # 状态机：idle → recording → processing
    ├── transcription_handler.py     # Protocol + ClipboardHandler
    ├── claude_chat_handler.py       # Claude 流程：发送 + 双剪贴板 + TTS + 取消
    ├── claude_runtime.py            # 用于 claude-agent-sdk 的同步 ↔ asyncio 桥接
    ├── tts.py                       # TextToSpeech Protocol + NullSpeaker
    ├── voxcpm_speaker.py            # VoxCPM2 朗读器（流式 + 取消）
    ├── input_listener.py            # 键盘 / 鼠标触发抽象
    ├── multi_hotkey_listener.py     # 多个全局快捷键，各自对应不同回调
    ├── listener_keepalive.py        # 定期重新安装钩子（Windows 修复）
    └── watchdog.py                  # 进程级健康监控
```

### 为什么需要监听器保活？

在 Windows 上，全局快捷键库使用的低级钩子（`WH_KEYBOARD_LL` / `WH_MOUSE_LL`）如果回调耗时超过 `LowLevelHooksTimeout`（Windows 10+ 上最长 1000 ms），就会被操作系统**静默移除**。在 CPU 高负载时，这种情况会在没有任何通知的情况下发生（[Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)）。VoiceMate 默认每 60 秒重新注册一次快捷键，因此即使操作系统移除了钩子，下一次定时也会将它重新安装。

### 为什么采用可插拔的 TTS？

架构将**编排层**（`tts.py` 中的 `TextToSpeech` Protocol）与**具体实现**（`VoxCPMSpeaker`）分离开来。这样以后就能轻松试用其他 TTS 库（edge-tts、ElevenLabs、Piper 等）：只需创建一个新的 Protocol 实现，并通过配置接入。如果某个库不合适，只删除它的文件即可。

## 技术栈

- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)**：用 CTranslate2 优化的 Whisper
- **[sounddevice](https://python-sounddevice.readthedocs.io/)**：麦克风采集
- **[keyboard](https://github.com/boppreh/keyboard)** / **[mouse](https://github.com/boppreh/mouse)**：全局输入钩子
- **[pyperclip](https://github.com/asweigart/pyperclip)**：剪贴板访问
- **[claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python)**：Claude 流程，基于本地的 `claude` CLI
- **[voxcpm](https://github.com/OpenBMB/VoxCPM)**：多语言 TTS，可根据文字描述设计声音
- **[soundfile](https://github.com/bastibe/python-soundfile)**：WAV 读写（可选，仅在保存 TTS 音频时使用）

## 参与贡献

欢迎提交 issue 和 PR。提交 PR 之前请运行 `make all`（格式化 + 检查 + 测试）。

## 许可证

[MIT](LICENSE) © Álli Terhorst

[NanoBR](https://github.com/nano-br) 的一部分：面向日常效率的开源工具集。
