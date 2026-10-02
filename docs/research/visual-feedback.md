# Research: visual feedback for VoiceMate

Purpose: research notes (written before the companion app existed) on visual indicators for VoiceMate. Kept for reference; the shipped design is the tray icon of the companion app ([brand.md](../brand.md), [companion-app.md](../companion-app.md)). Back to the [README](../../README.md).

## Goal

Replace or complement the sound cues with visual indicators that work on top of any open application (WhatsApp, IDE, browser, and so on).

---

## Option 1: transparent overlay with tkinter (recommended for an MVP)

**How it works:** a tiny window (15 to 20 px), without borders, semi-transparent, that always stays on top of every window. It changes color with the state (recording = pulsing red, idle = green or hidden).

**Implementation:**
```python
import tkinter as tk

root = tk.Tk()
root.overrideredirect(True)          # Remove borders and title
root.attributes("-topmost", True)     # Always on top
root.attributes("-alpha", 0.7)        # 70% opacity
root.geometry("20x20+1890+10")        # 20 px in the top right corner

canvas = tk.Canvas(root, width=20, height=20, bg="red", highlightthickness=0)
canvas.pack()

# Pulse animation
def pulse():
    current = root.attributes("-alpha")
    new_alpha = 0.3 if current > 0.5 else 0.7
    root.attributes("-alpha", new_alpha)
    root.after(500, pulse)
```

**Considerations:**
- tkinter has its own event loop (`mainloop()`): it must run in a separate thread, or use `root.after()` to integrate with the main loop.
- On Windows, `-transparentcolor` gives transparency by color (pixel-perfect).
- Cross-platform: works on Windows, macOS and Linux.
- No extra dependencies (tkinter ships with Python).

**Pros:** light, built in, simple.
**Cons:** basic look; threading with tkinter can be delicate (tkinter is not thread-safe: use `root.after()` to communicate between threads).

---

## Option 2: PyQt6/PySide6, a refined overlay

**How it works:** a frameless window with per-pixel transparency, smooth rendering and rounded corners.

```python
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # Does not show in the taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Click-through (mouse events go to the window below):
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(1890, 10, 40, 40)
```

**Pros:** professional rendering, per-pixel transparency, native click-through, smooth animations.
**Cons:** heavy dependency (about 60 MB), overkill for a simple indicator.

---

## Option 3: system tray with pystray (recommended as a complement)

**How it works:** an icon in the system tray (notification area) that changes icon or color to show the state, with a context menu for quick settings.

```python
import pystray
from PIL import Image

def create_icon(color):
    img = Image.new("RGB", (64, 64), color)
    return img

icon = pystray.Icon(
    "voicemate",
    create_icon("green"),
    "VoiceMate - Ready",
    menu=pystray.Menu(
        pystray.MenuItem("Recording", None, enabled=False),
        pystray.MenuItem("Quit", lambda: icon.stop()),
    ),
)
icon.run()
```

**State change:**
```python
# When recording starts:
icon.icon = create_icon("red")
icon.title = "VoiceMate - Recording..."

# When it stops:
icon.icon = create_icon("green")
icon.title = "VoiceMate - Ready"
```

**Dependencies:** `pystray` + `Pillow`
**Pros:** familiar to users, does not take over the screen, cross-platform, light.
**Cons:** can be too discreet; not always visible when the tray has many icons.

---

## Option 4: toast notifications

### Windows (win11toast)
```python
from win11toast import toast

toast("VoiceMate", "Transcription copied to the clipboard!", duration="short")
```

### Cross-platform (desktop-notifier)
```python
from desktop_notifier import DesktopNotifier

notifier = DesktopNotifier()
await notifier.send(title="VoiceMate", message="Recording started")
```

**Pros:** native to the OS, familiar, needs no window of its own.
**Cons:** not suited to a continuous state ("recording"), only to one-off events ("transcription done").

---

## Recommendation

| Approach | When to use it | Complexity |
|-----------|-------------|-------------|
| **pystray (tray icon)** | MVP: an indicator that is always visible, minimal effort | Low |
| **tkinter overlay** | Stretch goal: a pulsing red dot over the screen | Medium |
| **Toast notifications** | Complement: "transcription copied!" | Low |
| **PyQt6 overlay** | Future: if a rich UI with animations is wanted | High |

**Suggested gradual implementation:**
1. First: pystray for the tray icon (shows the state, menu to quit).
2. Then: a toast notification on "transcription copied".
3. Future: a tkinter overlay for a real-time visual indicator.

---

## References

- [pystray PyPI](https://pypi.org/project/pystray/)
- [desktop-notifier PyPI](https://pypi.org/project/desktop-notifier/)
- [win11toast PyPI](https://pypi.org/project/win11toast/)
- [Tkinter transparent windows - GeeksforGeeks](https://www.geeksforgeeks.org/python/transparent-window-in-tkinter/)
- [PyQt5 translucent windows](https://github.com/god233012yamil/PyQt5_Translucent_Windows)
