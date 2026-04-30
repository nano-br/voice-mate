# Pesquisa: Menu de Contexto do SO + Text-to-Speech

## Objetivo

Permitir que o usuário selecione texto em qualquer aplicativo, clique com botão direito, e tenha uma opção "VoiceMate: Ler texto" (ou similar) que pronuncie o texto selecionado. Evolução futura do projeto para bidirecional: voz→texto E texto→voz.

---

## Parte 1: Integração com Menu de Contexto

### Windows — Registry Shell Extensions

**Abordagem com biblioteca `context_menu`:**
```python
from context_menu import menus

def read_text(filenames, params):
    # Recebe texto selecionado ou arquivo
    import subprocess
    subprocess.run(["voice-mate", "--speak", filenames[0]])

cm = menus.ContextMenu("VoiceMate", type="FILES")
cm.add_items([
    menus.ContextCommand("Ler texto", command=read_text),
])
cm.compile()  # Registra no Windows Registry
```

**Abordagem manual (winreg):**
```
HKEY_CLASSES_ROOT\*\shell\VoiceMate\
    (Default) = "VoiceMate: Ler"
    Icon = "path\to\voicemate.ico"
    command\
        (Default) = "python path\to\voicemate.py --speak "%1""
```

**Limitações:**
- Menu de contexto do Windows funciona para **arquivos** no Explorer
- Para texto selecionado em qualquer app, a abordagem é diferente: precisa ler do clipboard
- Fluxo alternativo: "Copiar texto → Hotkey → VoiceMate lê do clipboard"

**Biblioteca:** `context_menu` (PyPI) ou `WindowsContextMenu` (GitHub)

### macOS — Automator Quick Actions (Services)

**Como funciona:**
1. Criar um Quick Action no Automator
2. Configurar para receber "texto" de "qualquer aplicação"
3. Adicionar ação "Run Shell Script" que chama VoiceMate
4. Salvar em `~/Library/Services/`

**Resultado:** Aparece no menu de contexto (botão direito) como "VoiceMate: Ler" em qualquer app que tenha texto selecionado.

```bash
# Script dentro do Automator:
echo "$1" | python3 -m voicemate --speak-stdin
```

**Prós:** Integração nativa, funciona em qualquer app, sem dependências extras
**Contras:** Requer configuração manual pelo usuário (ou script de setup)

### Linux — Nautilus Extensions + Freedesktop

**Nautilus (GNOME):**
```python
# ~/.local/share/nautilus-python/extensions/voicemate_ext.py
from gi.repository import Nautilus, GObject

class VoiceMateExtension(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        item = Nautilus.MenuItem(
            name="VoiceMate::Read",
            label="VoiceMate: Ler texto",
        )
        item.connect("activate", self.on_read, files)
        return [item]
```

**Scripts simples:**
- Colocar script em `~/.local/share/nautilus/scripts/VoiceMate-Ler`
- Funciona no file manager, não em apps arbitrários

**Para texto em qualquer app:** Similar ao Windows, melhor usar hotkey + clipboard.

### Abordagem Universal: Hotkey + Clipboard

A forma mais confiável e cross-platform de "ler texto selecionado" em qualquer aplicativo:

1. Usuário seleciona texto
2. Pressiona hotkey dedicado (ex: `ctrl+alt+r`)
3. VoiceMate copia a seleção atual (`Ctrl+C` simulado)
4. Lê o texto do clipboard
5. Pronuncia via TTS

Isso funciona em **qualquer aplicativo** em **qualquer SO**, sem precisar de integração com context menu.

---

## Parte 2: Bibliotecas de Text-to-Speech

### pyttsx3 — Offline, Cross-platform

```python
import pyttsx3

engine = pyttsx3.init()
engine.setProperty("rate", 150)     # Velocidade
engine.setProperty("volume", 0.9)   # Volume
# Vozes disponíveis dependem do SO:
# Windows: SAPI5 (Microsoft voices)
# macOS: NSSpeechSynthesizer
# Linux: espeak
engine.say("Olá, mundo!")
engine.runAndWait()
```

**Prós:** Offline, sem API key, cross-platform, controle de velocidade/voz
**Contras:** Qualidade robótica, vozes limitadas, PT-BR depende das vozes instaladas no SO
**Ideal para:** Feedback rápido, ambientes sem internet

### edge-tts — Neural voices da Microsoft (Gratuito)

```python
import edge_tts
import asyncio

async def speak(text):
    communicate = edge_tts.Communicate(text, "pt-BR-FranciscaNeural")
    await communicate.save("output.mp3")

asyncio.run(speak("Olá, mundo!"))
```

**Vozes PT-BR disponíveis:**
- `pt-BR-FranciscaNeural` (feminina, natural)
- `pt-BR-AntonioNeural` (masculina, natural)

**Prós:** Qualidade neural excelente, 200+ vozes, 70+ idiomas, gratuito
**Contras:** Requer internet, depende de serviço Microsoft (pode mudar)
**Ideal para:** Qualidade profissional, PT-BR natural

### gTTS — Google Text-to-Speech

```python
from gtts import gTTS

tts = gTTS("Olá, mundo!", lang="pt-br")
tts.save("output.mp3")
```

**Prós:** Simples, boa qualidade, muitos idiomas
**Contras:** Requer internet, Google API, sem controle de velocidade nativo
**Ideal para:** Uso simples, prototipagem

### Comparação

| Biblioteca | Offline | Qualidade | PT-BR | Latência | Dependências |
|-----------|---------|-----------|-------|----------|-------------|
| **pyttsx3** | Sim | Baixa-Média | Depende do SO | Baixa | Mínimas |
| **edge-tts** | Não | Alta (neural) | Excelente | Média | `edge-tts` |
| **gTTS** | Não | Média-Alta | Boa | Média-Alta | `gtts` |

### Recomendação

**Primário:** `edge-tts` — qualidade neural, PT-BR natural com `pt-BR-FranciscaNeural`
**Fallback:** `pyttsx3` — quando sem internet ou para latência mínima

**Arquitetura sugerida:**
```python
class TextToSpeech(Protocol):
    def speak(self, text: str) -> None: ...

class EdgeTTSSpeaker:
    """Neural TTS via Microsoft Edge (requer internet)."""

class OfflineSpeaker:
    """TTS offline via pyttsx3."""

def create_speaker(prefer_offline: bool = False) -> TextToSpeech:
    if prefer_offline:
        return OfflineSpeaker()
    return EdgeTTSSpeaker()
```

---

## Parte 3: Fluxo Completo Proposto

```
[Usuário seleciona texto] → [Hotkey: Ctrl+Alt+R]
    ↓
[VoiceMate captura clipboard]
    ↓
[TTS processa texto] → [Áudio é reproduzido]
    ↓
[Feedback visual: ícone no tray muda / overlay aparece]
```

**Implementação gradual:**
1. **Fase 1:** Hotkey + clipboard + edge-tts (funciona em qualquer app, qualquer SO)
2. **Fase 2:** Adicionar context menu do Windows (para quem preferir botão direito)
3. **Fase 3:** Automator workflow para macOS
4. **Fase 4:** Nautilus extension para Linux

---

## Referências

- [context_menu PyPI](https://pypi.org/project/context-menu/)
- [edge-tts PyPI](https://pypi.org/project/edge-tts/)
- [pyttsx3 PyPI](https://pypi.org/project/pyttsx3/)
- [gTTS PyPI](https://pypi.org/project/gtts/)
- [Nautilus-python docs](https://linuxconfig.org/how-to-write-nautilus-extensions-with-nautilus-python)
- [macOS Automator Services](https://thesweetsetup.com/create-your-own-services-menu-items-for-files-on-macos-using-automator/)
- [WindowsContextMenu GitHub](https://github.com/offerrall/WindowsContextMenu)
