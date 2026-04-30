# Pesquisa: Feedback Visual para VoiceMate

## Objetivo

Substituir ou complementar os feedbacks sonoros com indicadores visuais que funcionem por cima de qualquer aplicação aberta (WhatsApp, IDE, browser, etc.).

---

## Opção 1: Overlay transparente com tkinter (Recomendado para MVP)

**Como funciona:** Uma janela minúscula (15-20px), sem bordas, semi-transparente, que fica sempre por cima de todas as janelas. Muda de cor conforme o estado (gravando = vermelho pulsante, idle = verde/escondido).

**Implementação:**
```python
import tkinter as tk

root = tk.Tk()
root.overrideredirect(True)          # Remove bordas e título
root.attributes("-topmost", True)     # Sempre por cima
root.attributes("-alpha", 0.7)        # 70% opacidade
root.geometry("20x20+1890+10")        # 20px no canto superior direito

canvas = tk.Canvas(root, width=20, height=20, bg="red", highlightthickness=0)
canvas.pack()

# Animação de pulso
def pulse():
    current = root.attributes("-alpha")
    new_alpha = 0.3 if current > 0.5 else 0.7
    root.attributes("-alpha", new_alpha)
    root.after(500, pulse)
```

**Considerações:**
- tkinter tem seu próprio event loop (`mainloop()`), precisa rodar em thread separada ou usar `root.after()` para integrar com o loop principal
- No Windows, pode usar `-transparentcolor` para transparência por cor (pixel-perfect)
- Cross-platform: funciona em Windows, macOS e Linux
- Zero dependências extras (tkinter vem com Python)

**Prós:** Leve, built-in, simples
**Contras:** Visual básico, threading com tkinter pode ser delicado (tkinter não é thread-safe — usar `root.after()` para comunicação entre threads)

---

## Opção 2: PyQt6/PySide6 — Overlay sofisticado

**Como funciona:** Janela frameless com transparência por pixel, renderização suave, cantos arredondados.

```python
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # Não aparece na taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Click-through (eventos de mouse passam para a janela abaixo):
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(1890, 10, 40, 40)
```

**Prós:** Renderização profissional, per-pixel transparency, click-through nativo, animações fluidas
**Contras:** Dependência pesada (~60MB), overkill para um indicador simples

---

## Opção 3: System Tray com pystray (Recomendado como complemento)

**Como funciona:** Ícone no system tray (bandeja do sistema) que muda de ícone/cor para indicar estado. Menu de contexto para configurações rápidas.

```python
import pystray
from PIL import Image

def create_icon(color):
    img = Image.new("RGB", (64, 64), color)
    return img

icon = pystray.Icon(
    "voicemate",
    create_icon("green"),
    "VoiceMate - Pronto",
    menu=pystray.Menu(
        pystray.MenuItem("Gravando", None, enabled=False),
        pystray.MenuItem("Sair", lambda: icon.stop()),
    ),
)
icon.run()
```

**Mudança de estado:**
```python
# Quando começa a gravar:
icon.icon = create_icon("red")
icon.title = "VoiceMate - Gravando..."

# Quando para:
icon.icon = create_icon("green")
icon.title = "VoiceMate - Pronto"
```

**Dependências:** `pystray` + `Pillow`
**Prós:** Familiar ao usuário, não invade a tela, cross-platform, leve
**Contras:** Pode ser discreto demais, nem sempre visível se muitos ícones no tray

---

## Opção 4: Toast Notifications

### Windows (win11toast)
```python
from win11toast import toast

toast("VoiceMate", "Transcrição copiada para clipboard!", duration="short")
```

### Cross-platform (desktop-notifier)
```python
from desktop_notifier import DesktopNotifier

notifier = DesktopNotifier()
await notifier.send(title="VoiceMate", message="Gravação iniciada")
```

**Prós:** Nativo do SO, familiar, não requer janela própria
**Contras:** Não serve para estado contínuo ("gravando"), só para eventos pontuais ("transcrição concluída")

---

## Recomendação

| Abordagem | Quando usar | Complexidade |
|-----------|-------------|-------------|
| **pystray (tray icon)** | MVP — indicador sempre visível, mínimo esforço | Baixa |
| **tkinter overlay** | Stretch goal — dot vermelho pulsante sobre a tela | Média |
| **Toast notifications** | Complemento — "transcrição copiada!" | Baixa |
| **PyQt6 overlay** | Futuro — se quiser UI rica com animações | Alta |

**Sugestão de implementação gradual:**
1. Primeiro: pystray para tray icon (indica estado, menu para sair)
2. Depois: toast notification no "transcrição copiada"
3. Futuro: tkinter overlay para indicador visual em tempo real

---

## Referências

- [pystray PyPI](https://pypi.org/project/pystray/)
- [desktop-notifier PyPI](https://pypi.org/project/desktop-notifier/)
- [win11toast PyPI](https://pypi.org/project/win11toast/)
- [Tkinter transparent windows - GeeksforGeeks](https://www.geeksforgeeks.org/python/transparent-window-in-tkinter/)
- [PyQt5 translucent windows](https://github.com/god233012yamil/PyQt5_Translucent_Windows)
