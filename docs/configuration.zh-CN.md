[English](configuration.md) | [Português](configuration.pt-BR.md) | [Español](configuration.es.md) | [Русский](configuration.ru.md) | **简体中文**

# 配置参考

目的：VoiceMate 的每一个引擎参数、设置键、环境变量和文件位置。返回 [README](../README.zh-CN.md)。

## 设置来自哪里

| 部分 | 来源 | 优先级 |
|---|---|---|
| 引擎 | `voice-mate` 的命令行参数（`make run ARGS="..."`）和 `~/.config/voicemate/config.toml`（由 `make setup` 和 `make configure` 写入） | 先看参数，再看 `config.toml`，然后是检测结果或内置默认值 |
| 配套应用 | `companion.toml`，通过“设置”编辑 | 以文件为准；无效的值会回退到默认值 |

参数通过 Makefile 的 `ARGS` 传给引擎，例如 `make run ARGS="--model medium --no-tts"`。`make` 不会转发写在目标名后面的内容，所以一定要用 `ARGS`。

## 引擎参数

### 语音转文字与输入

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--model {tiny,base,small,medium,large-v2,large-v3,large-v3-turbo}` | `large-v3-turbo` | Whisper 模型 |
| `--hotkey` | `ctrl+alt+v` | 剪贴板流程的快捷键 |
| `--cpu` | 关 | 强制使用 CPU（int8）。优先于所有 GPU 设置 |
| `--stt-mode {auto,cpu,gpu}` | 已保存的选择，否则为 `auto` | 在哪里转写。`cpu`：处理器上的 faster-whisper int8，带 VAD 的贪婪解码，GPU 留给其他工作。`gpu`：使用 GPU 后端的贪婪解码，在它无法工作时回退到 CPU（并给出安装提示）。`auto`：GPU 后端可用时用 GPU，否则用 CPU。见[语音识别模式](#语音识别模式) |
| `--cpu-threads` | `0`（一半的核心，最多 8 个） | faster-whisper 在 CPU 上使用的线程数 |
| `--gpu-backend {auto,nvidia,amd,cpu}` | 已保存的选择，否则为检测结果 | GPU 厂商；`auto` 会检测显卡 |
| `--whisper-backend {faster-whisper,whispercpp,openai-whisper}` | 已保存的选择，否则按厂商而定 | 语音转文字引擎 |
| `--stt-strategy {auto,faster-whisper-rocm,whispercpp,openai-whisper}` | 已保存的选择，否则为 `auto` | 仅限 AMD：回退链上的第一个后端 |
| `--whispercpp-mode {server,cli}` | `server` | `server` 让 whisper.cpp 模型一直保持加载；`cli` 每次录音都重新加载（较慢） |
| `--transcription-language {auto,pt,en,es,fr,de,it,ja,ru,zh}` | 由 `--output-lang` 派生 | 转写时固定的语言。`auto` 会对每段录音检测语言（对较短的录音不太稳定） |
| `--input-method {keyboard,mouse}` | `keyboard` | `mouse` 仅适用于 Windows（`keyboard-hooks` 触发器），且只用于剪贴板流程；它会停用 Claude 流程 |
| `--mouse-button` | `x` | 用于 `--input-method mouse` 的鼠标按键（`x` 是侧键） |
| `--max-recording-seconds` | `600` | 录音时长上限。到 80 % 时播放警告；到达上限时录音停止并被转写 |
| `--output-lang` | `pt-BR` | BCP-47 代码。Claude 回答所用的语言、默认的转写语言（`pt-BR` 得到 `pt`，`en` 得到 `en`，`zh-CN` 得到 `zh`）以及引擎消息的默认语言。默认值会把 Whisper 固定为葡萄牙语：用英语听写时请传入 `--output-lang en`（或 `--transcription-language en`）。配套应用在启动引擎时会根据“听写语言：”设置来传入它 |
| `--stt-engine {builtin,external}` | 已保存的选择，否则为 `builtin` | `builtin` 是上面选定的语音转文字引擎。`external` 是你自己运行的服务（[协议](stt-provider-protocol.zh-CN.md)） |
| `--stt-url` | `http://127.0.0.1:8766` | 仅限 `external`：提供方的基础 URL。无效的 URL 会被记入日志，并改用内置引擎 |
| `--stt-options` | 无 | 仅限 `external`：一个 JSON 对象，原样传给提供方的选项 |
| `--no-stt-fallback` | 关 | 仅限 `external`：提供方无法转写的录音会失败，而不是交给内置引擎 |

STT 提供方的令牌绝不是命令行参数：它从 `VOICEMATE_STT_TOKEN` 读取，否则从引擎文件中的 `stt_external_token` 读取，并且绝不会被记入日志。它的启动命令只来自 `VOICEMATE_STT_START_COMMAND`。引擎读取之后会把这两项从它的环境中移除。参见 [STT 提供方协议](stt-provider-protocol.zh-CN.md)。

### 平台、触发方式与 API

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--platform {windows,linux-x11,linux-wayland,wsl2}` | 已保存的选择，否则为检测结果 | 运行环境 |
| `--trigger {keyboard-hooks,pynput,evdev,socket}` | 按平台而定：`windows` 用 `keyboard-hooks`，`linux-x11` 用 `pynput`，`linux-wayland` 用 `evdev`，`wsl2` 用 `socket` | 快捷键如何到达引擎。`socket` 表示由 HTTP API 作为触发器 |
| `--daemon-port` | 已保存的选择，否则为 `47821` | 本地 HTTP API 的端口 |
| `--api` | 关（使用 `--trigger socket` 时始终开启） | 在原生快捷键监听器之外同时提供 HTTP API |
| `--api-token` | 关 | 除 `/health` 之外的每个路由都要求 `Authorization: Bearer <token>`（令牌文件见下文） |
| `--supervised` | 关 | stdin 到达文件末尾时正常退出（配套应用会一直保持 stdin 打开） |
| `--no-watchdog` | 看门狗开启 | 停用会重启卡死引擎的看门狗 |
| `--watchdog-timeout` | `120` | 看门狗的超时时间，单位为秒 |
| `--no-listener-refresh` | 刷新开启 | 仅限 Windows：停用对键盘钩子的定期重新安装 |
| `--listener-refresh-seconds` | `60` | 仅限 Windows：该重新安装的间隔 |

`make run-engine` 运行 `voice-mate --supervised --api --api-token`；配套应用启动的就是它。

### Claude 流程

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--claude-chat-hotkey` | `ctrl+alt+a` | Claude 流程的快捷键 |
| `--no-claude-chat` | 关 | 停用 Claude 流程（只保留剪贴板） |
| `--claude-system-prompt` | 内置提示词 | 替换系统提示词 |
| `--claude-no-system-prompt` | 关 | 不发送系统提示词。优先于 `--claude-system-prompt` |
| `--claude-max-turns` | `50` | 每个对话的轮数 |
| `--claude-model` | `claude-haiku-5-5` | Claude 模型 |
| `--claude-effort {low,medium,high,xhigh,max}` | `low` | 投入度级别。Haiku 4.5 及更早的版本会忽略它 |
| `--claude-enable-thinking` | 关 | 扩展思考。Sonnet 5.5、Opus 5.5 和 Fable 总是会思考，Haiku 5.5 和 Opus 5 在投入度为 `xhigh` 或 `max` 时也会 |
| `--claude-timeout-seconds` | `120.0` | 每一轮的超时时间 |

### 文字转语音（Claude 流程）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--no-tts` | 关 | 不朗读回答；回答仍然进入剪贴板 |
| `--tts-engine {<default>,kokoro,voxcpm,external,none}` | 已保存的选择，否则为默认引擎 | TTS 引擎。`external` 是你自己运行的服务（[协议](tts-provider-protocol.md)）。`none` 停用 TTS |
| `--tts-url` | `http://127.0.0.1:8765` | 仅限 `external`：提供方的基础 URL |
| `--tts-external-voice` | 提供方的默认值 | 仅限 `external`：声音 id（配套应用的声音选择器会填入它） |
| `--tts-options` | 无 | 仅限 `external`：作为 `options`（语速、风格）传给提供方的一个 JSON 对象。其他内容会被忽略并给出警告 |
| `--tts-kokoro-voice` | `pf_dora` | Kokoro 声音。葡萄牙语声音：`pf_dora`（女声）、`pm_alex` 和 `pm_santa`（男声） |
| `--tts-voice` | 一段葡萄牙语的描述，描绘一位巴西老年女性的声音 | 仅限 VoxCPM2：声音的文字描述，在没有声音种子时使用 |
| `--tts-cfg-value` | `2.0` | VoxCPM2 的 `cfg_value`（1.0 到 3.0） |
| `--tts-inference-timesteps` | `10` | VoxCPM2 的推理步数（4 到 30） |
| `--tts-device {auto,cuda,cpu,mps}` | `auto` | TTS 设备 |
| `--tts-save-dir` | 无 | 把每个生成的音频文件保存到这个文件夹 |
| `--tts-no-streaming` | 关 | 一次性生成，而不是流式输出（用于调试） |
| `--tts-voice-seed-mode {auto,fixed,off}` | `off` | 默认引擎和 VoxCPM2 如何选择声音（见 [usage.zh-CN.md](usage.zh-CN.md#声音种子模式)） |
| `--tts-voice-seed-path`、`--tts-voice-seed-text` | 无 | 参考 WAV 和它的确切文本，`fixed` 必需 |
| `--tts-voice-seed-cache-dir` | `~/.cache/voicemate` | `auto` 保存它的参考音频的位置 |
| `--tts-reset-seed` | 关 | 启动前删除 VoxCPM2 的自动种子（`voice_seed.wav`） |
| `--tts-show-progress` | 关 | 显示 VoxCPM2 的进度条 |
| `--tts-drain-timeout-seconds` | `60.0` | 音频播放器排空的超时时间 |
| `--tts-debug-vram` | 关 | 在每一句话前后记录显存用量 |

提供方的令牌绝不是命令行参数：它从 `VOICEMATE_TTS_TOKEN` 读取，否则从引擎文件中的 `tts_external_token` 读取，并且绝不会被记入日志。配套应用通过环境传入它，引擎读取之后会把它从自己的环境中移除。它以明文保存在配套应用的 `companion.toml` 中。

提供方的启动命令也不是命令行参数：引擎从 `VOICEMATE_TTS_START_COMMAND` 读取它，读取之后把它从自己的环境中移除，并且绝不记入日志。参见[随 VoiceMate 启动提供方](tts-provider-protocol.md#starting-the-provider-with-voicemate)。

配套应用以 `--tts-url=...` 和 `--tts-external-voice=...` 的形式传入地址和声音，带一个 `=`，所以以 `-` 开头的值不会被当作参数。

只有在某个聊天流程开启时 TTS 才会启动，它的预热也是如此：否则内置的 Kokoro 声音不会预热。如果所选引擎的软件包缺失，或者引擎启动失败，引擎会记录一条带有修复方法的警告，并在没有 TTS 的情况下运行。

## 引擎文件：`~/.config/voicemate/config.toml`

一个扁平的 TOML 文件，由 `make setup` 和 `make configure` 写入。无效的值会被忽略；文件缺失或损坏就等于“什么都没保存”。每个键都是可选的。

| 键 | 取值 | 含义 |
|---|---|---|
| `gpu_vendor` | `nvidia`、`amd`、`cpu` | GPU 厂商 |
| `whisper_backend` | `faster-whisper`、`whispercpp`、`openai-whisper` | 语音转文字引擎 |
| `tts_enabled` | `true`、`false` | TTS 开或关 |
| `tts_engine` | 默认引擎、`kokoro`、`voxcpm`、`external`、`none` | TTS 引擎 |
| `tts_external_url`、`tts_external_voice`、`tts_external_options` | 文本 | `external` 引擎的设置（地址、声音 id、JSON 选项） |
| `tts_external_token` | 文本 | `external` 引擎的 Bearer 令牌。在这个文件里是明文；`VOICEMATE_TTS_TOKEN` 优先 |
| `stt_engine` | `builtin`、`external` | 转写引擎 |
| `stt_external_url`、`stt_external_options` | 文本 | `external` 转写引擎的设置（地址、JSON 选项） |
| `stt_external_token` | 文本 | `external` 转写引擎的 Bearer 令牌。在这个文件里是明文；`VOICEMATE_STT_TOKEN` 优先 |
| `stt_fallback` | `true`、`false` | `false` 会关闭 `external` 转写引擎的内置回退 |
| `default_flow` | `clipboard`、`claude_chat` | `clipboard` 会关闭 Claude 流程 |
| `platform` | `windows`、`linux-x11`、`linux-wayland`、`wsl2` | 运行环境 |
| `trigger` | `keyboard-hooks`、`pynput`、`evdev`、`socket` | 快捷键机制 |
| `stt_strategy` | `auto`、`faster-whisper-rocm`、`whispercpp`、`openai-whisper` | AMD 回退链 |
| `ct2_rocm_ok` | `true`、`false` | 安装程序已验证 CTranslate2-ROCm。引擎在运行时失败会写入 `false`；`make configure` 会再试一次 |
| `daemon_port` | 整数 | HTTP API 端口 |
| `stt_mode` | `auto`、`cpu`、`gpu` | 默认的转写模式（由 `make setup` 和 `make configure` 询问） |

## 配套应用文件：`companion.toml`

位置：Windows 为 `%APPDATA%\VoiceMate\companion.toml`；Linux 为 `${XDG_CONFIG_HOME:-~/.config}/voicemate/companion.toml`。卸载程序会保留它。无法读取的文件会被改名为 `companion.toml.broken-<timestamp>` 放到一边，并由“设置已重置”通知说明。由更新版本写入的文件会以只读方式显示。

| 键 | 默认值 | 允许的值 | 在界面中 |
|---|---|---|---|
| `version` | `1` | 整数 | |
| `client_key` | 首次运行时生成 | 字符串 | |
| `language` | `auto` | `auto`、`pt-BR`、`en`、`es`、`ru`、`zh-CN` | 常规 > “语言：” |
| `dictation_language` | `interface` | `interface`（跟随 `language`）、`auto`（Whisper 检测每段录音）或一种语言：`pt`、`en`、`es`、`ru`、`zh`、`fr`、`de`、`it`、`ja` | 常规 > “听写语言：”（“与界面相同”“自动检测”，然后是各种语言） |
| `engine_mode` | Windows 上为 `wsl2`，Linux 上为 `local` | Windows：`wsl2`、`external`；Linux：`local`、`external` | 常规 > “模式：”（“在 WSL 中启动”“在这台电脑上启动”“仅连接”） |
| `wsl_distro` | `""`（默认的 WSL 发行版） | 发行版名称 | 常规 > “WSL 发行版：” |
| `engine_dir` | `""`（自动检测） | 相对于 `$HOME` 的路径，或绝对路径；`"`、`$`、反引号、反斜杠和换行会被拒绝 | 常规 > “引擎文件夹：” |
| `daemon_port` | `47821` | 1024 到 65535 | 仅在文件中 |
| `cues_enabled` | `true` | `true`、`false` | 声音 > “播放提示音”；托盘菜单 > “静音提示音” |
| `master_volume` | `0.8` | 0.0 到 1.0 | 声音 > “音量：” |
| `wsl_restart_policy` | `auto` | `auto`、`ask`、`never` | 常规 > “音频出故障时重启 WSL：”（“自动”“先询问”“从不”） |
| `notify_level` | `warnings` | `all`、`warnings`、`errors`、`none` | 常规 > “通知：”（“全部”“警告和错误”“仅错误”“无”） |
| `start_at_login` | `false` | `true`、`false` | 常规 > “登录时启动 VoiceMate” |
| `tray_icon_visible` | `true` | `true`、`false`（仅限 Windows 11） | 常规 > “始终在任务栏上显示 VoiceMate 图标” |
| `stt_mode` | `auto` | `auto`（引擎自己的设置）、`cpu`、`gpu` | 常规 > 转写 > “运行于：”；托盘菜单 > “转写” |
| `stt_cpu_model` | `large-v3-turbo` | `large-v3-turbo`、`large-v3`、`medium`、`small` | 常规 > 转写 > “CPU 模型：” |
| `stt_gpu_model` | `large-v3-turbo` | 与 `stt_cpu_model` 相同 | 常规 > 转写 > “GPU 模型：” |
| `tts_engine` | `default` | `default`（引擎自己的设置）、`external` | 声音 > “语音引擎：”（“内置（引擎设置）”“自定义提供方（HTTP）”） |
| `tts_url` | `http://127.0.0.1:8765` | 不含空格、引号或 `$` 的 `http://` 或 `https://` 地址 | 声音 > “提供方地址：” |
| `tts_voice` | `""`（提供方的默认值） | 不含空格或 shell 字符的声音 id | 声音 > “声音：” |
| `tts_token` | `""` | 不含换行的文本。在这个文件里是明文 | 声音 > “令牌：” |
| `tts_start_command` | `""`（VoiceMate 不启动提供方） | 单独一行，按输入的原样通过 shell 运行。在这个文件里是明文 | 声音 > “启动命令：” |
| `[assistant]` 表 | 见[助手设置](#助手设置) | | “助手”选项卡 |
| 带有 `flow` 和 `chord` 的 `[[hotkeys]]` | `clipboard` = `ctrl+alt+v`，`claude_chat` = `ctrl+alt+a` | Ctrl、Alt、Shift 或 Win 加一个键，或单独一个功能键；F12 被 Windows 保留 | “快捷键”选项卡 |
| `start`、`transcribing`、`ready`、`ai_ready`、`warning`、`error`、`answer`（有另一个对话的回答在等待）对应的 `[cues.<name>]` | `enabled = true`，`source = "preset"`，`preset = "classic"`，`file = ""`，`volume = 1.0` | `source`：`preset`、`file`；`preset`：`classic`、`soft`、`click`；`file`：PCM WAV（8 或 16 位） | “声音”选项卡 |

更改语言时会询问“重启 VoiceMate？”（“立即重启”或“稍后”）；在重启之前，“常规”选项卡会显示“VoiceMate 重启后生效。”。

### 听写语言

配套应用会把 `dictation_language` 转成传给它所启动引擎的两个参数：`--transcription-language <code>` 和 `--output-lang <BCP-47>`（`pt` 得到 `pt-BR`，`zh` 得到 `zh-CN`，其他的用它们自己的代码）。`interface` 使用界面语言；`auto` 传入 `--transcription-language auto`，并让 `--output-lang` 保持为界面语言，所以 Claude 仍然用它回答。更改这个键会重启引擎。在“仅连接”模式下这个键会被忽略，引擎保留它自己的参数，配套应用所连接的引擎（systemd 单元，或手动启动的引擎）也是如此。更多见 [usage.zh-CN.md](usage.zh-CN.md#听写语言)。

## 语音识别模式

引擎可以在处理器上转写，也可以在显卡上转写。CPU 模式的存在，是为了让 VoiceMate 在 GPU 忙于其他工作（视频剪辑、渲染、游戏）时依然可用：
它绝不会在 GPU 上加载任何东西。

| 模式 | 后端 | 解码 | 何时使用 |
|---|---|---|---|
| `cpu` | faster-whisper，int8 | beam 1，VAD 过滤，`--cpu-threads` | GPU 正忙，或没有 GPU |
| `gpu` | faster-whisper CUDA（NVIDIA）、openai-whisper ROCm（WSL2 中的 AMD）、whisper.cpp Vulkan（Windows 上的 AMD） | beam 1 | 最快 |
| `auto` | 引擎设置中的选择 | 后端的默认值（beam 5） | 与有这个选项之前一样 |

配套应用为每种模式保留一个模型（`stt_cpu_model`、`stt_gpu_model`），并把
`--stt-mode` 和 `--model` 传给它所启动的引擎。切换会重启引擎，这正是
释放（或占用）GPU 的原因。尚未安装的模式会在询问之后由配套应用安装，
并告知下载大小；同样的工具也可以手动运行：

```bash
make stt-check
make stt-install MODE=gpu
make stt-install MODE=cpu ARGS="--model medium"
```

`stt-install` 只改动引擎自己的虚拟环境（以及用于模型的 `~/.cache`）：
AMD 的 ROCm PyTorch 和 openai-whisper，NVIDIA 的 CUDA 库，
Windows 上的 whisper.cpp 构建，CPU 的 faster-whisper 模型。

### 基准测试

在 2026-10-27（CPU）和 2026-10-28（GPU）测得，使用来自 Common
Voice 的葡萄牙语听写（短录音约 6 秒，长录音约 65 秒），通过引擎自己的
后端，在同一台机器上运行（Intel i5-13400F，WSL2 中使用 ROCm 的 AMD Radeon RX 9070 XT）。WER 是
相对于参考文本的词错误率；延迟是每段录音的转写时间。

| 模式 | 模型 | Beam | 短录音：延迟，WER | 长录音：延迟，WER |
|---|---|---|---|---|
| CPU（8 线程，int8） | large-v3-turbo | 1 | 4.7 秒，5.0 % | 21 秒，3.3 % |
| CPU（8 线程，int8） | medium | 1 | 3.2 秒，5.3 % | 15 秒，5.1 % |
| GPU（Radeon，ROCm） | large-v3-turbo | 5 | 0.45 秒，4.8 % | 4.6 秒，2.9 % |
| GPU（Radeon，ROCm） | large-v3-turbo | 1 | 0.18 秒，4.5 % | 1.9 秒，3.6 % |
| GPU（Radeon，ROCm） | medium | 5 | 0.83 秒，6.1 % | 10.1 秒，6.9 % |
| GPU（Radeon，ROCm） | medium | 1 | 0.42 秒，5.8 % | 4.9 秒，7.3 % |

在 CPU 上，同一次运行中 beam 5 并没有带来更低的 WER，只是更慢（不在
表中）。在 GPU 上，贪婪解码比 beam 5 快约 2.4 倍，而 WER 只相差不到
一个百分点，所以 `gpu` 模式采用贪婪解码。`medium` 在两者上都不如
`large-v3-turbo` 准确，在 GPU 上还更慢，所以 turbo 模型是
两种模式的默认值；`medium` 和 `small` 是给较慢的处理器准备的。

## 助手设置

语音助手（[usage.zh-CN.md](usage.zh-CN.md#语音助手)）把它的选择保存在 `companion.toml` 的 `[assistant]` 表中：

| 键 | 默认值 | 允许的值 | 在界面中 |
|---|---|---|---|
| `mode` | `off` | `off`、`own_session`、`desktop` | 助手 > “助手模式：”（“关闭”“VoiceMate 自己的对话”“Claude Desktop 对话”） |
| `desktop_conversation` | `""` | Claude Desktop 侧边栏中某个对话的确切标题 | 助手 > “Desktop 对话：”（没有它，`desktop` 模式不会启动） |
| `model` | `haiku` | `haiku`、`sonnet`、`opus`（Claude Code 的别名） | 助手 > “模型：” |
| `effort` | `low` | `low`、`medium`、`high` | 助手 > “投入度：” |
| `new_conversation_model` | `sonnet` | 与 `model` 相同 | 助手 > “新对话使用：” |
| `new_conversation_effort` | `medium` | 与 `effort` 相同 | 助手 > “投入度：”（新对话） |
| `permission_mode` | `acceptEdits` | `default`、`acceptEdits`、`bypassPermissions` | 助手 > “权限：”（“每次更改前都询问”“接受文件编辑，其余询问”“完全访问，从不询问（有风险）”） |
| `resume_permission_mode` | `""` | 空（不传递任何模式）、`default`、`acceptEdits`、`plan`；`bypassPermissions` 会被拒绝并记入一行日志 | 无：在文件中设置。助手为转交一条消息而恢复已关闭的终端、IDE 或 Remote Control 会话时，该会话得到的权限模式。不能通过语音更改 |
| `answer_delivery` | `announce` | `speak`、`announce`、`sound`、`silent` | 助手 > “其他对话的回答：” |
| `speak_replies` | `true` | `true`、`false` | 助手 > “朗读回复” |
| `voice_source` | `auto` | `auto`、`engine`、`windows` | 助手 > “语音来源：”（“自动”“引擎的声音”“Windows 的声音”）。使用 `engine` 时，遇到引擎仍在加载声音的句子会等待它（最多 90 秒，每次中断只算一次），简短的通知则留在屏幕上，而不是落到 Windows 语音 |
| `voice` | `""`（来源的默认声音） | 引擎语音引擎的一个声音 id，或 Windows 某个声音的 `windows/...` id | 助手 > “助手声音：”（**加载声音**、**测试**） |
| `voice_commands` | `true` | `true`、`false` | 助手 > “在输入的文本中也识别语音指令” |
| `expressions` | `off` | `off`、`on` | 助手 > “表情标签：” |
| `user_name` | `""` | 文本 | 助手 > “你的名字：” |
| `personality_source` | `none` | `none`、`file`、`custom` | 助手 > 个性 > “来源：”（“无”“一个 Markdown 文件（例如你的 Obsidian 笔记库里的一篇笔记）”“自己编写”） |
| `personality_file` | `""` | 空，或一个已存在的 `.md` 文件（来源为 `file` 时必需） | 助手 > “个性文件：” |
| `obsidian_vault` | `""` | 一个文件夹 | 助手 > “笔记库文件夹：” |
| `skill_language` | `auto` | `auto`（界面是葡萄牙语时用葡萄牙语技能，否则用英语）、`en`、`pt-BR` | 助手 > “技能语言：” |
| `custom_skill_dir` | `""` | 空（内置的技能），或一个包含 `SKILL.md` 的文件夹 | 助手 > “自定义技能：” |
| `desktop_profile_auto_update` | `true` | `true`、`false` | 助手 > “助手启动时检查是否有更新的配置文件” |
| `desktop_profile_override` | `""` | 用 **导入...** 导入的配置文件 | 助手 > **导入...**、**使用默认值**（[desktop-profile.md](desktop-profile.md)） |
| `talk_hotkey` | `ctrl+alt+s` | 一个组合键，或用 `""` 表示没有 | 快捷键 > “与助手对话” |
| `window_hotkey` | `ctrl+alt+w` | 同上 | 快捷键 > “打开助手窗口” |
| `digest_hotkey` | `ctrl+alt+r` | 同上 | 快捷键 > “收听已就绪的内容” |
| `mode_hotkey` | `""` | 同上 | 快捷键 > “切换助手模式” |
| `back_hotkey` | `""` | 同上 | 快捷键 > “回到助手” |

无效的值会回退到默认值，并记入一行日志。带有 `enabled = true`（或 `mode = "on"`）的旧文件会被读作 `own_session`，没有 `personality_source` 的 `personality_file` 会被读作来源 `file`。助手的快捷键会和其他快捷键一起检查：同一个组合键不能给两个操作。

助手运行期间，一项更改会产生什么效果：

| 更改 | 效果 |
|---|---|
| `mode`、`skill_language`、`custom_skill_dir`，以及在自有模式下的 `effort` 和 `permission_mode` | 重启中央对话 |
| `desktop_conversation`（Desktop 模式） | 重启中央对话 |
| `model`（自有模式） | 从下一条回答开始生效，不重启 |
| `model` 或 `effort`（Desktop 模式） | VoiceMate 无法更改 Desktop 对话；它会给中央对话发一条备注，请它把这些应用到自己身上 |
| `permission_mode` | 应用到助手此后启动的对话，并立刻重启中央对话（自有模式），所以它绝不会保留旧的权限 |
| 名字、个性、笔记库、标签、`answer_delivery` | 写入 `assistant.json`，技能会重新读取它 |

你在“自己编写”里写的文本不在 `companion.toml` 中：VoiceMate 把它保存在状态文件夹里的 `assistant-personality.md` 中（最多 20,000 个字符），并把这个文件作为 `personality_file` 交给技能。

> [!WARNING]
> `permission_mode = "bypassPermissions"` 会让助手启动的每个对话都能在这台电脑上不经询问地修改任何文件、运行任何命令。只有“设置”窗口能选择它：通过语音，助手只能在 `default` 和 `acceptEdits` 之间切换。它绝不会应用到助手所恢复的已关闭会话：那次恢复不传递任何权限模式，除非你设置了 `resume_permission_mode`，而它会拒绝 `bypassPermissions`。

助手的回复由引擎朗读，使用引擎中配置的声音（“声音”选项卡，或引擎的 `--tts-engine`）：配套应用把每条回复的 `<voice>` 块一句一句地发送到引擎的 `/tts` 端点（[tts-provider-protocol.md](tts-provider-protocol.md#the-engine-speaks-the-same-protocol)），并在这台电脑上播放音频。

内置的 Kokoro 声音和自定义提供方都可以替助手朗读，Windows 已有的声音（在 设置，时间和语言，语音 下安装的每一种语言）也可以，且无需下载任何东西。“语音来源：”决定用哪个：`auto` 使用引擎的声音，在引擎无法朗读（或没有声音）时使用 Windows 的声音，`engine` 只用引擎，`windows` 只用 Windows。**加载声音** 和 **测试** 在保存之前就跟随所选的来源；一个声音 id 只属于一个来源，每个来源都会忽略另一个来源的 id。当没有任何东西能朗读时，回复只保留文字，助手窗口会显示一条带原因的“没有可用的声音”通知。“别说了”、新的提示和模式更改会立刻让它静音。它绝不会盖过录音朗读：麦克风录音期间，或录音正在被转写期间到来的内容都会等待；被录音打断的那句话会从头再说一遍，接着按顺序是已在它后面排队的句子。

VoiceMate 还会在你的用户文件夹中写入 `~/.voicemate/assistant.json`，这是 `voicemate-assistant` 技能读取的文件（`version`、`language`、`user_name`、`personality_file`、`notes_vault`、`expressions`、`mode`、`permission_mode`、`resume_permission_mode`、`answer_delivery`、`instructions`、`inbox` 以及 `settings` 目录）。使用葡萄牙语技能时，`language` 是 `"pt-BR"`，无论界面是什么语言，声音都说 pt-BR。使用英语技能（或自定义技能）时，语言跟随界面。

`pending` 列出助手正在等待或关注的请求（会话 id、标题、原因、起始时间、`watch`、`until`），`instructions` 是你给它的长期指令（[assistant.zh-CN.md](assistant.zh-CN.md#-长期指令)）。它的格式见 [usage.zh-CN.md](usage.zh-CN.md#技能)。技能本身安装在 `<Claude home>/skills/voicemate-assistant`，其中 Claude home 是 `~/.claude` 或 `CLAUDE_CONFIG_DIR` 中的文件夹。

### 助手可以用语音更改的设置

`assistant.json` 中的 `settings` 列表是助手可以更改的唯一列表。

助手的键：`assistant.mode`（`own_session`、`desktop`）、`assistant.model`、`assistant.effort`、`assistant.permission_mode`、`assistant.answer_delivery`、`assistant.new_conversation_model`、`assistant.new_conversation_effort`、`assistant.desktop_conversation`。

声音、文本和提示音的键：`assistant.speak_replies`、`assistant.voice`、`assistant.voice_commands`、`assistant.expressions`、`assistant.user_name`、`assistant.personality_source`、`assistant.desktop_profile_auto_update`、`assistant.skill_language`、`cues.enabled`、`cues.volume`（0 到 100）、`cues.answer.enabled`、`cues.answer.volume`（0 到 100）、`general.dictation_language`、`general.stt_mode` 和 `general.notify_level`。

有意排除的项目：快捷键；所有文件或文件夹（个性文件和文本、笔记库、自定义技能文件夹、导入的 Desktop 配置文件）；语音引擎、提供方的地址和令牌；引擎本身（文件夹、WSL 发行版、端口、模式）；模式 `off`；以及除回答提示音之外的其他提示音。值必须是列出的值之一（文本是最多 200 个字符的单独一行），`desktop` 需要已选择的 Desktop 对话，`personality_source` 的 `file` 需要已选择的文件；否则 VoiceMate 会拒绝并说明原因，同时告诉助手。

## 环境变量

| 变量 | 由谁读取 | 效果 |
|---|---|---|
| `VOICEMATE_LANG` | 引擎 | 只改变引擎消息的语言（`pt-BR`、`en`、`es`、`ru`、`zh-CN`）；没有它时，消息跟随 `--output-lang`，其默认值是葡萄牙语。请在运行 `make run` 的 shell 中设置它（WSL 内或 Linux 上的 bash，Windows 上 PowerShell 的 `$env:VOICEMATE_LANG`）。优先于 `--output-lang`。它不会改变 Whisper 听的语言，也不会改变 Claude 回答的语言。配套应用会忽略它 |
| `VOICEMATE_TTS_TOKEN` | 引擎 | 外部 TTS 提供方的 Bearer 令牌。优先于已保存的令牌。配套应用在启动引擎时设置它 |
| `VOICEMATE_TTS_START_COMMAND` | 引擎 | 当提供方的地址上没有任何服务在监听时，用来启动外部 TTS 提供方的命令行。配套应用在启动引擎时设置它，并把它列入 `WSLENV`，所以它能到达 WSL 内的引擎。绝不记入日志 |
| `VOICEMATE_STT_TOKEN` | 引擎 | 外部 STT 提供方的 Bearer 令牌。优先于已保存的令牌 |
| `VOICEMATE_STT_START_COMMAND` | 引擎 | 当提供方的地址上没有任何服务在监听时，用来启动外部 STT 提供方的命令行。绝不记入日志 |
| `VOICEMATE_API_TOKEN` | 引擎、配套应用 | 取代令牌文件。仅用于测试 |
| `VOICEMATE_CLAUDE_CLI` | 配套应用（助手） | 助手所启动的 Claude Code CLI 的路径或命令名。没有它时，VoiceMate 会在 `PATH` 中查找 `claude`，然后是 `~/.local/bin`（`claude.exe`，原生安装程序）和 `%APPDATA%\npm`（`claude.cmd`，npm）。如果 `claude` 在其他位置，请设置为完整路径 |
| `CLAUDE_CONFIG_DIR` | 配套应用（助手） | Claude home，技能安装的位置，也是读取对话的位置；默认 `~/.claude` |
| `PULSE_LATENCY_MSEC` | 引擎（Linux、WSL2） | PulseAudio 的音频缓冲；缺省时设为 `200` |
| `CT2_CUDA_ALLOCATOR` | 引擎（CTranslate2-ROCm） | 缺省时设为 `cub_caching`（针对 gfx1201 上内存错误的变通办法） |
| `MIOPEN_FIND_MODE`、`MIOPEN_USER_DB_PATH`、`PYTORCH_TUNABLEOP_*` | 引擎（仅限 AMD） | 缺省时设置；缓存放在 `~/.cache/voicemate` |
| `PULSE_SERVER` | `make doctor` | 在 WSL2 上检查（预期为 `unix:/mnt/wslg/PulseServer`） |
| `APPDATA`、`LOCALAPPDATA`（Windows）；`XDG_CONFIG_HOME`、`XDG_STATE_HOME`、`XDG_CACHE_HOME`（Linux） | 配套应用 | 配套应用文件的基础文件夹 |
| `LC_ALL`、`LC_MESSAGES`、`LANG`、`LANGUAGE` | Linux 上的配套应用 | “语言：”为自动时的界面语言。在 Windows 上，由 Windows 的显示语言决定 |

## API 令牌和端口

- HTTP API 只监听 `127.0.0.1`，默认端口 `47821`。WSL2 会把它转发到 Windows 的回环地址。
- 带有 `Origin` 头（浏览器）或 `Host` 不是回环地址的请求会得到 `403`。`/trigger` 只接受 POST。
- 使用 `--api-token` 时，如果 `~/.config/voicemate/api-token` 不存在，引擎会创建它（权限 0600，32 个随机字节），并且除 `/health` 之外的每个路由都需要 `Authorization: Bearer <token>`。在 Windows 上，配套应用通过 `wsl.exe` 读取这个文件，遭到拒绝后会重新读取，最多每 30 秒一次。
- `make run` 和 systemd 服务不带令牌运行；`make run-engine`（配套应用）带令牌运行。旧的快捷键脚本只能配合没有令牌的引擎使用。

## 文件和日志

| 文件 | Windows | Linux |
|---|---|---|
| `companion.toml` | `%APPDATA%\VoiceMate\` | `${XDG_CONFIG_HOME:-~/.config}/voicemate/` |
| `companion.log`、`engine.log`（轮转，5 MB，3 个备份） | `%LOCALAPPDATA%\VoiceMate\logs\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/logs/` |
| `pending.json`（**未复制** 列表；保存转写文本） | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-session.json` 和文件夹 `assistant\`（会话 id、助手的工作文件夹） | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-inbox.json`（为“收听已就绪的内容”保留的回答；保存它们的正文）、`assistant-conversations\`（助手启动的对话）、`assistant-personality.md`（你写的个性） | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-waiting.json`（“等待中”列表，含时限以及每份记录读到的位置）、`assistant-instructions.json`（你的长期指令；无法读取的文件会保留为 `.bad`） | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `desktop-profile\`（下载和导入的 Claude Desktop 配置文件） | `%LOCALAPPDATA%\VoiceMate\` | 不使用 |
| `assistant.json`（由技能读取）、技能 `voicemate-assistant` | `%USERPROFILE%\.voicemate\`、`%USERPROFILE%\.claude\skills\` | `~/.voicemate/`、`~/.claude/skills/` |
| 渲染后的提示音 | `%LOCALAPPDATA%\VoiceMate\cues\` | `${XDG_CACHE_HOME:-~/.cache}/voicemate/cues/` |
| 引擎的选择和 API 令牌 | WSL 内：`~/.config/voicemate/config.toml` 和 `api-token`（原生 Windows 引擎：`%USERPROFILE%\.config\voicemate\`） | `~/.config/voicemate/` |
| 引擎缓存（whisper.cpp 二进制文件和模型、声音种子、AMD 缓存） | WSL 内：`~/.cache/voicemate/`（原生 Windows 引擎：`%USERPROFILE%\.cache\voicemate\`） | `~/.cache/voicemate/` |
| systemd 服务日志 | `journalctl --user -u voicemate -f`，在 WSL 内 | 同样的命令 |

托盘菜单 > **引擎** > **打开日志** 会打开日志文件夹。
