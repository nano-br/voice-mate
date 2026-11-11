[English](troubleshooting.md) | [Português](troubleshooting.pt-BR.md) | [Español](troubleshooting.es.md) | [Русский](troubleshooting.ru.md) | **简体中文**

# 故障排查

目的：如何诊断 VoiceMate 的问题，列出应用显示的确切消息，以及每一条该怎么处理。返回 [README](../README.zh-CN.md)。

## 第一步

1. **查看日志。** 托盘菜单 > **引擎** > **打开日志** 会打开包含 `companion.log` 和 `engine.log` 的文件夹（Windows 为 `%LOCALAPPDATA%\VoiceMate\logs`，Linux 为 `~/.local/state/voicemate/logs`）。`engine.log` 记录了引擎打印的一切。
2. **在引擎文件夹中运行 `make doctor`**（配套应用的话，要在 WSL 内运行）。它绝不会在第一个问题上停下：每项检查都会打印 ✓ 或 ✗，每项失败的检查都会打印一行 `fix:`，给出要运行的命令。
3. **重启引擎：** **引擎** > **重启引擎**。出现“VoiceMate 已停止重试”之后，它也会重置监管器。

### `make doctor` 检查什么

按这个顺序：检测到的平台；仅在 WSL2 上：WSLg 的 PulseAudio 套接字、`PULSE_SERVER`、从 ALSA 到 PulseAudio 的插件、原生剪贴板工具（`wl-copy`、`xclip`）以及供 `clip.exe` 使用的 WSL 互操作；音频库、输入设备和输出设备；快捷键触发器（`evdev` 对 `/dev/input` 的访问、`pynput` 的 `DISPLAY`、WSL2 上的 HTTP 守护进程，或 Windows 钩子）；whisper.cpp 二进制文件、模型和 VAD；Vulkan 设备（Linux 和 WSL2）；Claude CLI（`node` 和 `claude`，已保存的流程为 `clipboard` 时跳过）；安装了 Kokoro 时的 `espeak-ng`；带 GPU 的 PyTorch。

## 配套应用消息

| 消息（通知标题） | 含义 | 怎么办 |
|---|---|---|
| “VoiceMate 正在启动” | 引擎还在加载模型时按下了快捷键（10 到 60 秒，第一次启动要下载模型，会长得多） | 等一会儿再按 |
| “未找到引擎文件夹” | 配套应用查找的文件夹里没有引擎的检出副本 | 把引擎克隆到其中一个文件夹（[installation.md](installation.md#2-install-the-engine)），或在设置 > **常规** 中设置“引擎文件夹：” |
| “无法启动引擎” | `wsl.exe` 或 `make run-engine` 失败了 | 查看 `engine.log`；检查 `make` 和 `poetry` 能否在登录 shell 中工作（`wsl -e bash -lc "make --version && poetry --version"`） |
| “VoiceMate 已停止重试” | 引擎失败得太频繁（15 分钟内重启超过 5 次，连续 3 次启动超时，或 1 小时内 WSL 重启超过 3 次） | 查看 `engine.log`，排除原因，然后使用 **重启引擎**。第一次启动时，缓慢的模型下载可能连续三次用完引擎启动的 240 秒：在引擎文件夹中（WSL 内）运行一次 `make run`，加载完成后用 `Ctrl+C` 停止，再使用 **重启引擎** |
| “引擎已过期” / “引擎比这个应用旧。请重启或更新它。” | 引擎的检出副本比配套应用旧 | 更新它（`git pull`，然后 `make setup`），再 **重启引擎** |
| “引擎由 systemd 运行” | `voicemate` 这个 systemd 服务在运行引擎，所以配套应用连接到它 | 保持原样，或在 WSL 中运行 `systemctl --user disable --now voicemate`，让 VoiceMate 来管理引擎 |
| “引擎拒绝访问” | 引擎拒绝了 API 令牌 | VoiceMate 每 30 秒会重新读取一次 `~/.config/voicemate/api-token`；如果问题持续，请重启引擎 |
| “无法连接引擎” / “引擎忙碌” | 引擎没有（及时）响应一次快捷键 | VoiceMate 会不断重连；如果持续很久，请查看 `engine.log` |
| “快捷键不可用” / “已被其他应用使用”（“快捷键”选项卡） | 另一个程序占用了这个组合键。常常是旧的 VoiceMate 快捷键脚本 | 关闭这个脚本并把它从 `shell:startup` 中移除，或者选择其他快捷键 |
| “没有麦克风” | Windows 没有麦克风 | 连接一个 |
| “麦克风不可用” | 引擎无法打开麦克风 | 在 Windows 上：在 Windows 隐私设置中允许桌面应用使用麦克风，并关闭任何独占它的应用。如果 WSL 音频卡住了，请重启 WSL |
| “WSL 音频已停止” | WSLg 的 PulseAudio 没有响应 | 见 [WSL 音频与麦克风](#wsl-音频与麦克风) |
| “没有转写出内容” / “没有录到内容” | 没有检测到语音，或者录音没有捕获到音频 | 检查麦克风和输入音量 |
| “转写缓慢” | 转写比音频慢得多，通常是因为没有 GPU | `make doctor`（带 GPU 的 PyTorch、Vulkan 设备）；见 [wsl2.md](wsl2.md#speech-to-text-performance-on-wsl2) |
| “转写失败” | 语音转文字后端抛出了错误 | 查看 `engine.log` |
| “Claude 没有回答” | 发给 Claude 的请求失败了 | 查看 `engine.log`；检查 `claude` 能否在 WSL 中工作（`claude -p "ping"`） |
| “未复制到剪贴板” | 另一个应用一直锁定着剪贴板 | 文本在 **未复制** 里：在那里点击它即可复制 |
| “复制失败” | 从“最近”或“未复制”复制失败了 | 再试一次 |
| “设置已重置” | `companion.toml` 无法读取；VoiceMate 以默认值启动，并把旧文件保留为 `companion.toml.broken-<timestamp>` | 对比并修复旧文件，或者保留默认值 |
| ‘“未复制”列表已重置’ | `pending.json` 无法读取；旧文件（连同其中的文本）被保留为 `pending.json.broken-<timestamp>` | 如果需要，从旧文件中找回文本 |

## 转写模式

| 消息 | 含义 | 怎么办 |
|---|---|---|
| “要安装 CPU 模式吗？” / “要安装 GPU 模式吗？” | 你选的模式没有安装在引擎的环境中 | **安装** 会下载它（大小写在询问里）并显示输出。取消则保留当前模式 |
| “安装失败。详情见下方，引擎保持当前模式。” | `make stt-install` 带着错误退出了 | 查看窗口里的输出，排除原因（网络、磁盘空间、ROCm），再试一次，或者在引擎文件夹中运行 `make stt-install MODE=gpu` |
| “这台电脑上没有 GPU 模式” | 检查没有发现 VoiceMate 可以使用的显卡 | 使用 CPU 模式 |
| “GPU 模式无法工作。请从“转写”菜单安装它。”（状态窗口） | GPU 模式回退到了 CPU | 托盘菜单 > “转写” > “GPU（最快）”会提供安装；`make doctor` 显示 GPU 检查 |
| “正在更改转写模式” | 一项检查或一次安装仍在运行 | 等它完成，然后再试 |
| “无法更改转写模式” | 引擎无法用新模式重启 | 查看 `engine.log` |

## 语音助手

| 现象 | 原因 | 怎么办 |
|---|---|---|
| “未找到 Claude Code”，并且语音提示回答“助手无法启动” | 登录时启动的 VoiceMate 获得的是保存在 Windows 注册表中的 `PATH`。原生的 Claude Code 安装程序把 `claude.exe` 放在 `%USERPROFILE%\.local\bin`，这个文件夹往往只由 shell 配置文件加入 `PATH`，所以终端能找到 `claude`，VoiceMate 却找不到 | 在当前版本上无需处理：VoiceMate 还会在 `~/.local/bin` 和 `%APPDATA%\npm` 中查找。要检查，在终端里运行 `where claude`：如果什么都没打印，请安装 Claude Code。如果 `claude` 在其他位置，请把 `VOICEMATE_CLAUDE_CLI` 设为它的完整路径（[configuration.zh-CN.md](configuration.zh-CN.md#环境变量)），然后在助手窗口中点 **重新启动** |
| “Claude Code 尚未登录” | 这台电脑上没有 Claude 登录 | 在终端里运行 `claude auth login`，然后点 **重新启动**。Claude Desktop 的登录不算数 |
| “助手无法启动”或“助手已停止” | 进程失败或结束了 | 打开 `companion.log`（托盘菜单 > **引擎** > **打开日志**），排除原因，然后点 **重新启动** |
| “无法准备助手” | VoiceMate 无法安装技能，或无法写入 `~/.voicemate/assistant.json` | 检查你的用户文件夹和 `~/.claude` 是否可写；日志里有详情 |
| 没有“在 claude.ai 上打开”链接，手机上也什么都没有 | 无法开启 Remote Control。助手在这台电脑上继续工作 | 重启对话：设置 > **助手**，把“助手模式：”设为关闭，应用，再重新打开。`companion.log` 会说明那个请求得到的回答。这个请求没有文档说明，可能在不同的 Claude Code 版本之间发生变化（见[研究](research/claude-code-client.md#61-a-voicemate-owned-session-in-the-desktop-tested-works)） |
| 助手用错了语言回答，或者没有称呼 | 技能无法读取 `~/.voicemate/assistant.json`，并说“未找到 VoiceMate 的助手设置” | 重启对话（设置 > **助手**，关闭，应用，再重新打开）：对话开始时 VoiceMate 会重新写入这个文件。检查这个文件是否在你的用户文件夹（`~/.voicemate/`）中 |
| 托盘菜单里没有“助手”项 | 助手处于关闭状态 | 设置 > **助手** > “助手模式：” > “VoiceMate 自己的对话” |
| “有不止一个对话匹配 ...”或“没有对话匹配 ...” | 输入的指令无法确定是哪一个标题 | 多输入一些标题内容，或者关闭“在输入的文本中也识别语音指令” |
| 个性文件被拒绝 | 它不是一个已存在的 `.md` 文件 | 用“浏览...”选择一个 Markdown 文件 |
| “未选择 Claude Desktop 对话” | 模式是“Claude Desktop 对话”，而“Desktop 对话：”是空的 | 在设置 > **助手** 中输入 Claude Desktop 侧边栏里的确切标题 |
| Desktop 模式下出现“无法准备助手”，日志里写着“no Claude Desktop conversation titled ...” | 没有带记录的 Desktop 对话拥有这个确切标题 | 在 Claude Desktop 侧边栏里逐个字母核对标题；从未有过消息的对话还没有记录 |
| Desktop 模式下出现“助手无法启动”，日志里写着 `window_missing`、`conversation_missing` 或 `ambiguous` | Claude Desktop 没有打开，对话不在它的侧边栏里，或者有两个对话用了这个标题 | 打开 Claude Desktop，给两个相同标题中的一个改名，然后点 **重新启动** |
| 日志里写着 `locked` 或 `focus_lost`，并且有一条消息没有到达 Desktop | 屏幕被锁定了，或者 VoiceMate 输入时另一个窗口抢走了焦点。什么都没有输入 | 解锁屏幕，在一条消息所需的 2 秒内不要碰键盘，然后重新发送 |
| 你正在 Desktop 对话里输入的草稿消失了 | 助手发来的消息会清除它所写入的那个对话的草稿 | 不要在助手使用的对话（Desktop 模式）里打字，也不要在它即将写入的对话里打字 |
| Claude Desktop 更新之后，Desktop 模式找不到它的控件了 | 更新改变了窗口，所以配置文件不再匹配 | 设置 > **助手** > **立即检查** 是否有更新的配置文件；如果没有，见 [desktop-profile.md](desktop-profile.md#writing-a-profile-for-a-new-desktop-version) |
| Claude Desktop 配置文件下方出现“问题：...” | 导入或下载的配置文件被拒绝了（原因会指出字段） | 修复文件后再次 **导入...**，或者点 **使用默认值** |
| 助手无法浏览网页、打开 pull request 或使用其他仅限 Desktop 的工具 | 在“VoiceMate 自己的对话”中它只有 `voicemate` 工具 | 请它把任务交给一个 Desktop 对话，或者切换到 Desktop 模式 |
| 助手没有声音，窗口显示“没有可用的声音” | 没有任何东西能朗读：引擎已停止或没有声音（它的语音引擎不是 Kokoro 或自定义提供方，或者提供方掉线了），并且 Windows 没有这种语言的声音，或者“语音来源：”是“引擎的声音” | 在设置 > **助手** 中，把“语音来源：”设为“Windows 的声音”（或“自动”）并点 **测试**；在 Windows 设置 > 时间和语言 > 语音 下添加一个声音。或者检查设置 > **声音** 和引擎的语音引擎（[协议](tts-provider-protocol.md#the-engine-speaks-the-same-protocol)）；“朗读回复”必须开启。回复仍然可以在窗口里阅读 |
| 我一录音，助手的声音就停了，之后才回来 | 这是有意的设计：声音绝不会盖过麦克风。录音期间和录音被转写期间它都会等待，然后把被打断的那句话和排队的内容再说一遍 | 无需处理。对助手的新提示、“别说了”或助手的快捷键会让它彻底静音 |
| 助手窗口里的 **说话** 按钮被禁用 | 助手尚未就绪（关闭、正在启动、正在回答或出了故障），或者引擎仍在转写上一段录音 | 等窗口里出现“就绪”。出错之后，点 **重新启动** |
| 其他对话的回答一直没有到达 | 只有助手写入过、启动过或被要求关注的对话才会被跟进。等待在 1 小时内没有新的一轮时结束，你要求的关注在 12 小时后结束，任何一种结束时助手都会收到一条备注 | 问助手有什么待处理的。选择“只保留”或“只播放提示音”时，回答在 **待听的回答** 里等待，而不在对话里 |
| 助手说它无法发送到终端或 IDE 会话（中央对话收到 `not sent (in use)` 或 `not sent (busy)`） | `in use`：这个会话正在某个程序里打开，或者它的记录在最近 2 分钟内有变化。`busy`：VoiceMate 发给它的上一条消息仍在被回答，或者刚刚回答完 | 在那里关掉这个会话，或者等一会儿，然后再问一次。更多见 [assistant.zh-CN.md](assistant.zh-CN.md#-故障排查) |
| 托盘图标上有蓝色角标 | 有其他对话的回答在等待 | 使用“收听已就绪的内容”（托盘菜单，`Ctrl+Alt+R`），或打开窗口 |
| 助手的某个快捷键显示“已被其他应用使用” | `Ctrl+Alt+S`、`Ctrl+Alt+W` 或 `Ctrl+Alt+R` 被占用了 | 设置 > **快捷键**：更改它或清除它 |
| 助手拒绝通过语音更改某项设置 | 它不在目录里（快捷键、文件、文件夹和引擎都被排除在外），或者这个值不是允许的值 | 在设置里更改它。清单见 [usage.zh-CN.md](usage.zh-CN.md#用语音修改-voicemate) |

## WSL 音频与麦克风

- 打开麦克风绝不会阻塞快捷键：它在后台运行。如果 6 秒内没有打开，引擎会播放错误提示音，回到空闲状态，并报告“麦克风不可用”。
- 在 WSL2 上，引擎每 20 秒探测一次 WSLg PulseAudio（`pactl info`），并把音频报告为正常、不正常或未知（状态窗口里的“音频：”）。
- 当 Windows 失去它的麦克风时，WSLg PulseAudio 常常会永久卡住，即使麦克风回来了也一样（`pactl info` 给出 `Connection failure: Timeout`）。只有 `wsl --shutdown` 才能把它救回来。

配套应用通过“音频出故障时重启 WSL：”这项设置来处理：

| 设置 | 行为 |
|---|---|
| “自动”（默认） | 自己重启 WSL，除非还有其他 WSL 发行版在运行：`wsl --shutdown` 会把它们全部停止，包括 Docker，所以那时它会先询问 |
| “先询问” | 显示“WSL 需要重启”和菜单项“立即重启 WSL...” |
| “从不” | 什么都不做；用 **引擎** > **重启 WSL...** 手动重启 |

当音频连续两次探测都不正常，或者在 Windows 有捕获设备而音频不正常时麦克风失败，就需要重启 WSL。在 Windows 没有麦克风时绝不会发生：配套应用会等待麦克风（每 3 秒检查一次）。重启之前，它最多等 30 秒让引擎空闲，然后停止引擎，运行 `wsl --shutdown`，再重新启动引擎。

不使用配套应用时：在 PowerShell 中运行 `wsl --shutdown`，然后再次 `make run`。

## 快捷键

- **使用配套应用的 Windows：** 配套应用会注册 `Ctrl+Alt+V` 和 `Ctrl+Alt+A`。如果某个组合键显示“已被其他应用使用”，说明另一个程序占用了它。
- **Windows 原生引擎（`make run`）：** 系统负载很重时，Windows 可能会悄悄移除底层键盘钩子。引擎每 60 秒重新安装一次（`--listener-refresh-seconds`），看门狗会重启卡死的引擎（`--watchdog-timeout`）。
- **Wayland：** `evdev` 触发器需要你的用户在 `input` 组中（`sudo usermod -aG input $USER`，然后重新登录）。
- **X11：** `pynput` 触发器需要 `DISPLAY`。

## 引擎问题

本指南中引用的引擎消息是英文的。引擎会用 `--output-lang` 指定的语言打印它们，其默认值是葡萄牙语（`pt-BR`），或者用 `VOICEMATE_LANG` 指定的语言，后者优先。因此，用普通的 `make run` 启动的引擎会以葡萄牙语记录日志：加上 `ARGS="--output-lang en"`（或者在运行它的 shell 中设置 `VOICEMATE_LANG=en`）就能读到英文文本。配套应用会根据设置中的“听写语言：”自己传入这个参数。

| 现象 | 原因 | 修复 |
|---|---|---|
| 引擎日志中出现“Could not open the HTTP API on 127.0.0.1:{port}” | 端口被占用，常常是被另一个引擎占用 | 停止另一个引擎，或选择另一个端口（`--daemon-port`，以及 `companion.toml` 中的 `daemon_port`） |
| “Claude flow disabled: extra 'claude' not installed.” | 缺少 `claude` 附加组件 | `poetry install --extras claude`，或 `make configure` |
| “Failed to start Claude (run `claude login`)” | Claude CLI 缺失或已退出登录 | 安装它并登录（[installation.md](installation.md#claude-code-cli-for-the-claude-flow)） |
| “TTS disabled: engine '{engine}' packages not installed.” | 缺少 TTS 引擎的附加组件 | 安装下一行日志中指出的附加组件，或使用 `--no-tts` 运行 |
| 英语语音被转写错了（好像它是葡萄牙语） | 手动启动的引擎默认把 Whisper 固定为葡萄牙语 | `make run ARGS="--output-lang en"` 或 `--transcription-language en`。使用配套应用时，在设置 > **常规** 中设置“听写语言：” |
| 转写比预期慢 10 到 50 倍 | 后端回退到了 CPU | `make doctor`（带 GPU 的 PyTorch）；在 AMD 上，`rocminfo` 必须列出 GPU |
| CTranslate2-ROCm 构建失败 | ROCm 开发包不完整，或者（ROCm 7）构建选用了 gcc：“language hip not recognized” | `make doctor`；安装 ROCm HIP SDK；`git pull` 和 `make configure` 会再试一次，这次使用 ROCm 的 clang。见 [wsl2.md](wsl2.md#ctranslate2-rocm-faster-whisper-on-the-gpu-optional-best-quality) |
| 引擎日志中出现“Conv1D on GPU currently requires the cuDNN library”，并且在 AMD GPU 上的转写失败 | 在 ROCm 7 上，一个没有 MIOpen（AMD 对应 cuDNN 的组件）的 CTranslate2-ROCm 构建 | `git pull`，然后 `make configure` 并再次接受 CTranslate2-ROCm 构建：现在的构建包含 MIOpen。在此之前，引擎会回退到 openai-whisper（`ct2_rocm_ok = false`） |
| 界面语言不是你选的那个 | 语言要在重启之后才会更改 | 在“重启 VoiceMate？”中回答“立即重启” |

## 旧的快捷键脚本（不使用配套应用）

当端口上没有任何响应时，PowerShell 脚本 `scripts/windows/voicemate-hotkeys.ps1` 会打印“daemon offline”：在 WSL 中用 `make run` 启动引擎，然后再次按下快捷键。这些脚本只能配合不带令牌启动的引擎（`make run`）使用，绝不能配合 `make run-engine`。
