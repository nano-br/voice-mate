# VoiceMate no WSL2 (GPU AMD via ROCm)

O app roda **inteiro dentro do WSL** (Ubuntu): captura do microfone, transcrição
na GPU, clipboard e TTS. A única peça do lado Windows é um script mínimo que
registra as hotkeys globais e dispara um request HTTP local para o daemon —
hotkeys globais do Windows não chegam a processos em background do WSL.

```
[Windows]  Ctrl+Alt+V / Ctrl+Alt+A
   │  voicemate-hotkeys.ahk (ou .ps1)
   ▼  POST http://127.0.0.1:47821/trigger {"flow": ...}
[WSL2]  VoiceMate daemon (trigger=socket)
   ├── mic via PulseAudio do WSLg (RDPSource)
   ├── STT na GPU AMD (faster-whisper CT2-ROCm → whisper.cpp Vulkan)
   ├── clipboard (sincroniza com o Windows; fallback clip.exe)
   └── Claude + TTS (OmniVoice em PyTorch ROCm)
```

## Pré-requisitos

1. **Windows 11 + WSL2 atualizado** (`wsl --update`) com WSLg (vem por padrão).
2. **Driver AMD Adrenalin ≥ 26.2.2** no Windows.
3. **ROCm dentro do WSL** ([guia oficial AMD](https://rocm.docs.amd.com/projects/radeon/en/latest/docs/install/wsl/install-radeon.html)) — `rocminfo` deve listar a GPU (ex.: `gfx1201`).
4. Pacotes de áudio/build no Ubuntu:
   ```bash
   sudo apt install -y libportaudio2 libasound2-plugins pulseaudio-utils \
                       git cmake build-essential libvulkan-dev glslc vulkan-tools
   ```

## Instalação

Dentro do WSL:

```bash
git clone https://github.com/nano-br/voice-mate.git && cd voice-mate
make setup     # detecta WSL2 + AMD, instala torch ROCm, whisper.cpp (Vulkan),
               # oferece o build do CTranslate2-ROCm e salva as escolhas
make doctor    # diagnóstico: mic/áudio WSLg, binários, GPU — com correções
```

No Windows, registre as hotkeys (escolha UMA das opções):

- **AutoHotkey v2** (recomendado): dê dois cliques em
  `scripts\windows\voicemate-hotkeys.ahk`. Para iniciar com o Windows, coloque
  um atalho dele em `shell:startup`.
- **PowerShell puro**:
  `powershell -ExecutionPolicy Bypass -File scripts\windows\voicemate-hotkeys.ps1`

## Rodando

```bash
make run    # no WSL — imprime a porta do daemon e fica escutando
```

Aperte `Ctrl+Alt+V` em qualquer lugar do Windows → beep → fale → `Ctrl+Alt+V`
de novo → transcrição no clipboard do Windows. `Ctrl+Alt+A` faz o fluxo Claude
(resposta no clipboard + falada via TTS).

### Autostart (systemd)

O `make setup` oferece instalar o serviço; manualmente:

```bash
cp scripts/systemd/voicemate.service ~/.config/systemd/user/
# ajuste o WorkingDirectory se o checkout não está em ~/voice-mate
systemctl --user daemon-reload
systemctl --user enable --now voicemate
loginctl enable-linger $USER     # serviço vivo mesmo sem terminal aberto
journalctl --user -u voicemate -f  # logs
```

## Microfone no WSLg

O WSLg expõe o microfone do Windows como source PulseAudio (`RDPSource`):

```bash
export PULSE_SERVER=unix:/mnt/wslg/PulseServer   # coloque no ~/.bashrc
pactl list sources short    # deve listar RDPSource
pactl list sinks short      # deve listar RDPSink (saída de áudio)
```

Se o PortAudio (sounddevice) não enxergar os devices, instale o shim ALSA→Pulse
(`libasound2-plugins`, ver pré-requisitos) — o `make doctor` confere tudo isso.

> Privacidade: confira em Configurações do Windows → Privacidade → Microfone
> que apps desktop podem usar o microfone.

## Solução de problemas

| Sintoma | Causa provável | Correção |
|---|---|---|
| Hotkey não faz nada | daemon parado / script Windows não rodando | `make run` no WSL; rode o .ahk/.ps1 |
| "Daemon offline" no Windows | porta diferente / firewall | confira `--daemon-port` e o print do `make run` |
| Sem device de entrada | mic WSLg desabilitado | `wsl --update`; `make doctor` |
| Transcrição lenta (10–50x) | caiu p/ CPU silenciosamente | `make doctor` (torch GPU); `rocminfo` |
| CT2-ROCm falhou no build | ROCm dev incompleto | `make doctor`; instale rocm-hip-sdk; `make configure` re-tenta |

## Qualidade de transcrição

Gate objetivo (WER + palavras quebradas) contra amostras suas:

```bash
make stt-eval ARGS="--backends faster-whisper --save-baseline"  # uma vez (referência)
make stt-eval                                                   # compara todos os backends
```

Veja `samples/ptbr/README.md`.
