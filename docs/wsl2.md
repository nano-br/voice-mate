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
                       git cmake build-essential libvulkan-dev glslc vulkan-tools \
                       spirv-headers spirv-tools glslang-tools
   ```
   > Os três últimos são exigidos pela compilação dos shaders Vulkan do
   > whisper.cpp (`SPIRV-Headers`/`glslangValidator`). `libglslang-dev` não
   > existe com esse nome no Ubuntu 24.04 — os pacotes acima bastam.

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

## Performance de STT no WSL2 (importante)

**No WSL2 o whisper.cpp via Vulkan NÃO acelera na GPU.** O Mesa, dentro do WSL2,
só expõe o `llvmpipe` — uma implementação de Vulkan **por software, rodando na
CPU**. A RX 9070 XT só é alcançável via **ROCm/HIP** (`/dev/dxg`), não via
Vulkan. Rodar o `large-v3-turbo` no llvmpipe leva **minutos** por fala.

Por isso, no WSL2 a cadeia de transcrição prioriza o que de fato usa a GPU:

```
faster-whisper-rocm (se CT2-ROCm validado)  →  openai-whisper (torch ROCm)  →  whisper.cpp (último recurso)  →  CPU
```

O **openai-whisper** roda sobre o mesmo torch ROCm que já acelera o TTS
(OmniVoice) — `make configure` o instala automaticamente na AMD/Linux. Uma fala
de 5 s deve transcrever em ~1–3 s.

Como confirmar o device Vulkan escolhido pelo whisper.cpp (quando ele é usado):
o servidor agora grava o log em `~/.cache/voicemate/whispercpp/server.log` — a
linha `ggml_vulkan: found device:` mostra `llvmpipe` no WSL2. O `make doctor`
também sinaliza isso ("Vulkan SEM GPU real").

### CT2-ROCm (faster-whisper na GPU) — opt-in de qualidade máxima

Recupera a qualidade idêntica à da `main` (faster-whisper CUDA) na GPU AMD. É um
build pesado (`make configure` → aceitar o CTranslate2-ROCm). Risco conhecido em
gfx1201: relatos de *memory access fault* (OpenNMT/CTranslate2#2021); o app já
aplica o workaround `CT2_CUDA_ALLOCATOR=cub_caching`. Se o build/validação
falhar, a cadeia cai sozinha para o openai-whisper (decisão persistida em
`ct2_rocm_ok`). O `hipcc` já vem com o usecase `rocm` do ROCm 7.2.

> O whisper.cpp **HIP** (em vez de Vulkan) resolveria isso nativamente, mas o PR
> de suporte a gfx120X (ggml-org/whisper.cpp#3757) ainda não foi mergeado.

## PyTorch ROCm — fonte dos wheels

No Linux/WSL2 + AMD o `make setup` instala os **wheels manylinux do
`repo.radeon.com`** (torch + torchvision + torchaudio + triton, com
`numpy==1.26.4`) — é a combinação que a AMD publica e testa para WSL.

Se o venv **já tem** um torch `+rocm` acelerando (instalação validada
manualmente), o setup **não reinstala por cima** — ele detecta e mantém. Para
forçar a reinstalação: `pip uninstall torch` dentro do venv e `make configure`.

> A trilha de ROCm do WSL é a **7.2** (pacote `7.2.70200`, instalado com
> `amdgpu-install --usecase=wsl,rocm --no-dkms`). Não use a `7.2.4` — é a trilha
> de Linux nativo e não tem o usecase `wsl`.

## Variáveis de ambiente recomendadas (`~/.bashrc`)

```bash
export PULSE_SERVER=unix:/mnt/wslg/PulseServer       # mic/áudio do WSLg
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True   # fragmentação de VRAM
export FLASH_ATTENTION_TRITON_AMD_ENABLE="TRUE"      # flash-attn via Triton (RDNA4)
```

O app já cuida sozinho de `PYTORCH_TUNABLEOP_*`/`MIOPEN_*` (cache em
`~/.cache/voicemate`) e de `CT2_CUDA_ALLOCATOR=cub_caching` (workaround do
faster-whisper-ROCm em gfx1201) — exportar é opcional, vale para outros apps.

> **Não** sete `HSA_OVERRIDE_GFX_VERSION` nem `HSA_ENABLE_DXG_DETECTION`: a
> RX 9070 XT é reconhecida nativamente como `gfx1201` pela trilha WSL do ROCm.

## Limitações conhecidas do WSL2 (não são erros)

- `rocm-smi` / `amd-smi` **não funcionam** no WSL2 — a detecção do app usa
  `rocminfo` (que funciona). VRAM: `cat /sys/class/drm/card0/device/mem_info_vram_used`.
- VRAM visível < 16 GB (~13–14 GB úteis — overhead da camada DXCore/librocdxg).
- Overhead geral de ~10–20% vs Linux nativo.

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
