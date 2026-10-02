# Research: OS context menu + text-to-speech

Purpose: research notes (written early in the project) on reading selected text aloud from the operating system's context menu. Kept for reference; nothing here is implemented. The TTS that shipped speaks Claude's answers ([usage.md](../usage.md#tts-engines)). Back to the [README](../../README.md).

## Goal

Let the user select text in any application, right-click it, and get an option such as "VoiceMate: Read text" that speaks the selected text. A future evolution of the project into both directions: voice to text AND text to voice.

---

## Part 1: context menu integration

### Windows: registry shell extensions

**With the `context_menu` library:**
```python
from context_menu import menus

def read_text(filenames, params):
    # Receives the selected text or file
    import subprocess
    subprocess.run(["voice-mate", "--speak", filenames[0]])

cm = menus.ContextMenu("VoiceMate", type="FILES")
cm.add_items([
    menus.ContextCommand("Read text", command=read_text),
])
cm.compile()  # Registers in the Windows Registry
```

**By hand (winreg):**
```
HKEY_CLASSES_ROOT\*\shell\VoiceMate\
    (Default) = "VoiceMate: Read"
    Icon = "path\to\voicemate.ico"
    command\
        (Default) = "python path\to\voicemate.py --speak "%1""
```

**Limitations:**
- The Windows context menu works for **files** in Explorer.
- For selected text in any app the approach is different: it has to read from the clipboard.
- Alternative flow: "Copy text, then hotkey, then VoiceMate reads from the clipboard".

**Library:** `context_menu` (PyPI) or `WindowsContextMenu` (GitHub)

### macOS: Automator Quick Actions (Services)

**How it works:**
1. Create a Quick Action in Automator.
2. Set it to receive "text" from "any application".
3. Add a "Run Shell Script" action that calls VoiceMate.
4. Save it in `~/Library/Services/`.

**Result:** it shows in the context menu (right click) as "VoiceMate: Read" in any app with selected text.

```bash
# Script inside Automator:
echo "$1" | python3 -m voicemate --speak-stdin
```

**Pros:** native integration, works in any app, no extra dependencies.
**Cons:** the user has to set it up by hand (or with a setup script).

### Linux: Nautilus extensions + freedesktop

**Nautilus (GNOME):**
```python
# ~/.local/share/nautilus-python/extensions/voicemate_ext.py
from gi.repository import Nautilus, GObject

class VoiceMateExtension(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        item = Nautilus.MenuItem(
            name="VoiceMate::Read",
            label="VoiceMate: Read text",
        )
        item.connect("activate", self.on_read, files)
        return [item]
```

**Simple scripts:**
- Put a script in `~/.local/share/nautilus/scripts/VoiceMate-Read`.
- Works in the file manager, not in arbitrary apps.

**For text in any app:** as on Windows, a hotkey plus the clipboard works better.

### Universal approach: hotkey + clipboard

The most reliable cross-platform way to "read the selected text" in any application:

1. The user selects text.
2. Presses a dedicated hotkey (for example `ctrl+alt+r`).
3. VoiceMate copies the current selection (simulated `Ctrl+C`).
4. Reads the text from the clipboard.
5. Speaks it with TTS.

This works in **any application** on **any OS**, without context menu integration.

---

## Part 2: text-to-speech libraries

### pyttsx3: offline, cross-platform

```python
import pyttsx3

engine = pyttsx3.init()
engine.setProperty("rate", 150)     # Speed
engine.setProperty("volume", 0.9)   # Volume
# Available voices depend on the OS:
# Windows: SAPI5 (Microsoft voices)
# macOS: NSSpeechSynthesizer
# Linux: espeak
engine.say("Hello, world!")
engine.runAndWait()
```

**Pros:** offline, no API key, cross-platform, control of speed and voice.
**Cons:** robotic quality, limited voices; Brazilian Portuguese depends on the voices installed in the OS.
**Best for:** quick feedback, environments without internet.

### edge-tts: Microsoft neural voices (free)

```python
import edge_tts
import asyncio

async def speak(text):
    communicate = edge_tts.Communicate(text, "pt-BR-FranciscaNeural")
    await communicate.save("output.mp3")

asyncio.run(speak("Olá, mundo!"))
```

**Brazilian Portuguese voices available:**
- `pt-BR-FranciscaNeural` (female, natural)
- `pt-BR-AntonioNeural` (male, natural)

**Pros:** excellent neural quality, 200+ voices, 70+ languages, free.
**Cons:** needs internet, depends on a Microsoft service (which may change).
**Best for:** professional quality, natural Brazilian Portuguese.

### gTTS: Google Text-to-Speech

```python
from gtts import gTTS

tts = gTTS("Olá, mundo!", lang="pt-br")
tts.save("output.mp3")
```

**Pros:** simple, good quality, many languages.
**Cons:** needs internet, Google API, no native speed control.
**Best for:** simple use, prototyping.

### Comparison

| Library | Offline | Quality | Brazilian Portuguese | Latency | Dependencies |
|-----------|---------|-----------|-------|----------|-------------|
| **pyttsx3** | Yes | Low to medium | Depends on the OS | Low | Minimal |
| **edge-tts** | No | High (neural) | Excellent | Medium | `edge-tts` |
| **gTTS** | No | Medium to high | Good | Medium to high | `gtts` |

### Recommendation

**Primary:** `edge-tts`, for neural quality and natural Brazilian Portuguese with `pt-BR-FranciscaNeural`.
**Fallback:** `pyttsx3`, without internet or for minimal latency.

**Suggested architecture:**
```python
class TextToSpeech(Protocol):
    def speak(self, text: str) -> None: ...

class EdgeTTSSpeaker:
    """Neural TTS through Microsoft Edge (needs internet)."""

class OfflineSpeaker:
    """Offline TTS through pyttsx3."""

def create_speaker(prefer_offline: bool = False) -> TextToSpeech:
    if prefer_offline:
        return OfflineSpeaker()
    return EdgeTTSSpeaker()
```

---

## Part 3: proposed complete flow

```
[User selects text] -> [Hotkey: Ctrl+Alt+R]
    |
[VoiceMate captures the clipboard]
    |
[TTS processes the text] -> [Audio plays]
    |
[Visual feedback: the tray icon changes / an overlay appears]
```

**Gradual implementation:**
1. **Phase 1:** hotkey + clipboard + edge-tts (works in any app, on any OS).
2. **Phase 2:** add the Windows context menu (for people who prefer the right click).
3. **Phase 3:** Automator workflow for macOS.
4. **Phase 4:** Nautilus extension for Linux.

---

## References

- [context_menu PyPI](https://pypi.org/project/context-menu/)
- [edge-tts PyPI](https://pypi.org/project/edge-tts/)
- [pyttsx3 PyPI](https://pypi.org/project/pyttsx3/)
- [gTTS PyPI](https://pypi.org/project/gtts/)
- [Nautilus-python docs](https://linuxconfig.org/how-to-write-nautilus-extensions-with-nautilus-python)
- [macOS Automator Services](https://thesweetsetup.com/create-your-own-services-menu-items-for-files-on-macos-using-automator/)
- [WindowsContextMenu GitHub](https://github.com/offerrall/WindowsContextMenu)
