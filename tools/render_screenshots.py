"""Render the README screenshots and brand images from the companion's own UI code.

    python -m tools.render_screenshots [--lang en|pt-BR|es|ru|zh-CN|all] [--out docs/assets/screenshots]
    make docs-screenshots

Windows only: the screenshots must look like the real app, so they use the Windows
platform plugin (DirectWrite fonts, the windows11 style). Nothing ever appears on the
screen: every top-level widget (windows, menus, message boxes) gets WA_DontShowOnScreen
before it is first shown, so Qt lays it out and paints it without mapping a native window.
`--offscreen` uses Qt's offscreen platform instead (a fallback; its fonts and style differ).

What runs is the real UI (`CompanionUi`, the tray menu, the settings dialog, the question
boxes) driven by the demo `FakeController`: no engine, no hotkeys, no settings file, and a
tray icon object that is built (it reads the taskbar theme from the registry, read-only) but
never shown. The sample dictations are generic and written per language below; the engine
settings shown are fixed (the default WSL distro and a `voice-mate` folder), so nothing
from this computer ends up in the images.

Deterministic: light color scheme, the windows11 style, Segoe UI 9 pt, 2x device pixel
ratio (a DPI-unaware process, so the monitor's own scale never adds to it), every window
at the size the app opens it with, and fixed sample data: two runs write the same bytes.
Each language renders in its own process, like the app picks its language once at startup.

Writes (`--lang all`, the default; `--lang <one>` writes only that language's folder):
- `<out>/<lang>/<name>.png` for every name in `SCREENSHOT_NAMES`: the status window, one
  image per settings tab, the tray menu, the two question boxes and `hero`, the status
  window and the tray menu side by side.
- `<out>/tray-states.png`: the tray glyph of every state on a dark and a light taskbar strip.
- `<brand-out>/banner-light.png` and `banner-dark.png`: the app icon and the wordmark, for
  light and dark page backgrounds.

Every window grab gets the same framing (rounded corners, a hairline edge, a soft drop
shadow and a transparent margin); the window contents are never altered. Text that does
not fit its widget is reported as a warning.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QImageWriter,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QGraphicsDropShadowEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyleFactory,
    QTabWidget,
    QWidget,
)

from app.companion.contract import CompanionSettings, RecentItem, TrayState
from app.companion.ui.demo_controller import DEMO_INSTANCE, FakeController
from app.companion.ui.icons import GlyphTone, brand_image, glyph_color, glyph_image
from app.protocol.models import ResultKind, ResultRecord

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
LOCALES_DIR: Final = REPO_ROOT / "app" / "i18n" / "locales"
DEFAULT_OUT: Final = Path("docs/assets/screenshots")
DEFAULT_BRAND_OUT: Final = Path("docs/assets/brand")
# README language -> gettext catalog (app/i18n/locales/<catalog>).
LANGUAGES: Final[dict[str, str]] = {"en": "en", "pt-BR": "pt_BR", "es": "es", "ru": "ru", "zh-CN": "zh_CN"}
SCALE: Final = 2  # device pixels per logical pixel: crisp when a README shows them at half size
STYLE: Final = "windows11"

# The images written per language, in the order they are rendered (the READMEs link them).
SCREENSHOT_NAMES: Final = (
    "status",
    "settings-hotkeys",
    "settings-sounds",
    "settings-general",
    "tray-menu",
    "dialog-restart-wsl",
    "dialog-language",
    "hero",
)

# Framing, in logical pixels.
FRAME_MARGIN: Final = 24
FRAME_RADIUS: Final = 8
SHADOW_BLUR: Final = 28
SHADOW_OFFSET: Final = 6
SHADOW_ALPHA: Final = 70
EDGE_ALPHA: Final = 40
HERO_GAP: Final = 24  # between the status window and the tray menu

# Tray states, in the order of the tray-states image (left to right).
TRAY_STATES: Final[tuple[TrayState, ...]] = (
    "idle",
    "recording",
    "transcribing",
    "thinking",
    "speaking",
    "ready",
    "starting",
    "restarting",
    "warning",
    "error",
    "stopped",
)
TRAY_GLYPH_SIZE: Final = 32  # logical; the tray paints this size itself at 200 %
TRAY_GLYPH_GAP: Final = 20
TRAY_STRIP_PADDING: Final = 16
TRAY_STRIPS: Final[tuple[tuple[str, GlyphTone], ...]] = (
    ("#202020", "light"),
    ("#F3F3F3", "dark"),
)  # taskbar color, glyph tone

BANNER_SIZE: Final = (1280, 320)  # logical
BANNER_ICON: Final = 152
BANNER_GAP: Final = 36
BANNER_TEXT_PX: Final = 104
BANNER_WORDMARK: Final = "VoiceMate"  # product name: never translated
BANNER_INK: Final = {"light": "#1F2328", "dark": "#F0F6FC"}  # GitHub's text colors per page theme

PNG_DOTS_PER_METER: Final = round(96 * SCALE / 0.0254)


@dataclass(frozen=True)
class Samples:
    """What a person dictated, in one language: `recent` newest first."""

    recent: tuple[tuple[ResultKind, str], ...]
    pending: tuple[str, ...]


# Simulated user content, written per language (not UI text, so not in the catalogs).
SAMPLES: Final[dict[str, Samples]] = {
    "en": Samples(
        recent=(
            ("transcript", "Remind me to review the pull request before lunch."),
            ("ai_response", "Try 25-minute focus blocks with short breaks."),
            ("transcript", "Let's meet at three to go over the sign-up flow."),
            ("transcript", "The build is green again after the cache fix."),
            ("transcript", "Book a table for four on Saturday evening."),
        ),
        pending=("Add milk, eggs and coffee to the shopping list.",),
    ),
    "pt-BR": Samples(
        recent=(
            ("transcript", "Me lembra de revisar o pull request antes do almoço."),
            ("ai_response", "Experimente blocos de 25 minutos de foco com pausas curtas."),
            ("transcript", "Vamos nos reunir às três para revisar o fluxo de cadastro."),
            ("transcript", "O build voltou a passar depois da correção do cache."),
            ("transcript", "Reservar uma mesa para quatro no sábado à noite."),
        ),
        pending=("Adicionar leite, ovos e café à lista de compras.",),
    ),
    "es": Samples(
        recent=(
            ("transcript", "Recuérdame revisar el pull request antes del almuerzo."),
            ("ai_response", "Prueba bloques de 25 minutos de concentración con pausas cortas."),
            ("transcript", "Reunámonos a las tres para repasar el flujo de registro."),
            ("transcript", "La build vuelve a pasar después del arreglo de la caché."),
            ("transcript", "Reservar una mesa para cuatro el sábado por la noche."),
        ),
        pending=("Añadir leche, huevos y café a la lista de la compra.",),
    ),
    "ru": Samples(
        recent=(
            ("transcript", "Напомни мне посмотреть пул-реквест до обеда."),
            ("ai_response", "Попробуйте блоки по 25 минут с короткими перерывами."),
            ("transcript", "Давайте встретимся в три и обсудим процесс регистрации."),
            ("transcript", "После исправления кэша сборка снова проходит."),
            ("transcript", "Забронировать столик на четверых на субботний вечер."),
        ),
        pending=("Добавить молоко, яйца и кофе в список покупок.",),
    ),
    "zh-CN": Samples(
        recent=(
            ("transcript", "提醒我午饭前看一下这个合并请求。"),
            ("ai_response", "可以试试专注 25 分钟，再休息 5 分钟。"),
            ("transcript", "我们三点开会，过一遍注册流程。"),
            ("transcript", "修复缓存之后，构建又通过了。"),
            ("transcript", "订一张周六晚上的四人桌。"),
        ),
        pending=("把牛奶、鸡蛋和咖啡加到购物清单里。",),
    ),
}


# ---------------------------------------------------------------------- entry point


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tools.render_screenshots",
        description="Render the README screenshots and brand banners from the companion UI (Windows only).",
    )
    parser.add_argument("--lang", choices=[*LANGUAGES, "all"], default="all", help="README language (default: all).")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Screenshots folder, relative to the repository root, not the current folder (default: {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--brand-out",
        type=Path,
        default=DEFAULT_BRAND_OUT,
        help=f"Banner folder, relative to the repository root; only used with --lang all (default: {DEFAULT_BRAND_OUT}).",
    )
    parser.add_argument(
        "--offscreen", action="store_true", help="Use Qt's offscreen platform instead of the hidden Windows one."
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if sys.platform != "win32":
        print("render_screenshots runs on Windows only: the screenshots show the Windows app.", file=sys.stderr)
        return 2
    args = parse_args(argv)
    out: Path = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    brand_out: Path = args.brand_out if args.brand_out.is_absolute() else REPO_ROOT / args.brand_out
    if args.lang != "all":
        return _render_language(args.lang, out, args.offscreen)

    qapp = _application(args.offscreen)
    render_tray_states(out / "tray-states.png")
    for theme in ("light", "dark"):
        render_banner(theme, brand_out / f"banner-{theme}.png")
    del qapp
    failed = []
    for language in LANGUAGES:
        # A process per language: the app also picks its language (and fonts) once at startup.
        command = [sys.executable, "-m", "tools.render_screenshots", "--lang", language, "--out", str(out)]
        if args.offscreen:
            command.append("--offscreen")
        if subprocess.run(command, cwd=REPO_ROOT, check=False).returncode != 0:
            failed.append(language)
    if failed:
        print(f"failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------- Qt setup


class _NeverOnScreen(QObject):
    """Every top-level widget (windows, menus, message boxes) acts shown but is never mapped."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Polish and isinstance(watched, QWidget) and watched.isWindow():
            watched.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        return False


_FILTERS: list[QObject] = []  # keeps the event filter alive


def _application(offscreen: bool) -> QApplication:
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"))
    else:
        # DPI-unaware: Windows reports 96 dpi whatever the monitor's scale, so the device
        # pixel ratio is exactly QT_SCALE_FACTOR.
        os.environ["QT_QPA_PLATFORM"] = "windows:dpiawareness=0"
    os.environ["QT_SCALE_FACTOR"] = str(SCALE)
    os.environ.pop("QT_SCREEN_SCALE_FACTORS", None)
    qapp = QApplication.instance() or QApplication([sys.argv[0]])
    assert isinstance(qapp, QApplication)
    qapp.setQuitOnLastWindowClosed(False)
    hider = _NeverOnScreen(qapp)
    qapp.installEventFilter(hider)
    _FILTERS.append(hider)
    QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Light)
    if STYLE not in (key.lower() for key in QStyleFactory.keys()):
        raise SystemExit(f"the {STYLE} style is not available in this Qt; the screenshots would not match")
    qapp.setStyle(STYLE)
    qapp.setFont(QFont("Segoe UI", 9))
    ratio = qapp.primaryScreen().devicePixelRatio()
    if ratio != SCALE:
        raise SystemExit(f"expected a device pixel ratio of {SCALE}, got {ratio}")
    return qapp


def _compile_catalogs(locales: Path) -> None:
    """The .mo files are not in git: compile every .po into `locales`, a temporary folder
    the caller creates and removes, so the checkout's own locales folder is never written."""
    from babel.messages.mofile import write_mo
    from babel.messages.pofile import read_po

    for po in LOCALES_DIR.glob("*/LC_MESSAGES/voicemate.po"):
        catalog = po.parents[1].name
        with po.open("rb") as source:
            messages = read_po(source, locale=catalog)
        mo = locales / catalog / "LC_MESSAGES" / "voicemate.mo"
        mo.parent.mkdir(parents=True)
        with mo.open("wb") as target:
            write_mo(target, messages)


def _pump(qapp: QApplication, seconds: float = 0.2) -> None:
    """Run the Qt loop for a while: queued signals, layouts, deferred deletions."""
    _pump_until(qapp, lambda: False, seconds)


def _pump_until(qapp: QApplication, condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------- one language


class _SampleController(FakeController):
    """The demo controller with this language's sample dictations."""

    def __init__(self, settings: CompanionSettings, samples: Samples) -> None:
        super().__init__(settings=settings)
        self._recent = [
            RecentItem(DEMO_INSTANCE, _record(seq, text, kind))
            for seq, (kind, text) in zip(range(len(samples.recent), 0, -1), samples.recent, strict=True)
        ]
        self._next_seq = len(samples.recent) + 1


def _record(seq: int, text: str, kind: ResultKind) -> ResultRecord:
    return ResultRecord(
        result_seq=seq,
        op_seq=seq,
        kind=kind,
        flow="claude_chat" if kind == "ai_response" else "clipboard",
        text=text,
        final=True,
        created_ts=0.0,
        age_s=0.0,
        delivery="delivered",
    )


def _render_language(language: str, out: Path, offscreen: bool) -> int:
    qapp = _application(offscreen)
    locales = Path(tempfile.mkdtemp(prefix="voicemate-shots-"))
    try:
        _compile_catalogs(locales)
        # The catalogs are read on demand by the UI: the folder must outlive the whole render.
        return _render_with_catalogs(qapp, language, out, locales)
    finally:
        shutil.rmtree(locales, ignore_errors=True)


def _render_with_catalogs(qapp: QApplication, language: str, out: Path, locales: Path) -> int:
    import app.i18n as i18n

    i18n._LOCALES_DIR = locales
    i18n.set_language(language)  # type: ignore[arg-type]
    catalog = LANGUAGES[language]
    if i18n.active_language() != catalog:
        raise SystemExit(f"catalog {catalog} did not load (got {i18n.active_language()})")

    from app.companion import main as companion_main
    from app.companion.ui.app import CompanionUi
    from app.i18n import _

    translator = companion_main.install_qt_translations(qapp)
    companion_main.install_script_fonts(qapp)

    target = out / language
    target.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    settings = CompanionSettings(
        client_key="screenshots",
        language=language,  # type: ignore[arg-type]
        wsl_distro="",
        engine_dir="voice-mate",
    )
    fake = _SampleController(settings, SAMPLES[language])
    ui = CompanionUi(fake, tray_available=True, exit_app=lambda: None, relaunch=lambda: None, running_catalog=catalog)
    ui.bridge.attach()
    fake.start()
    for text in SAMPLES[language].pending:
        fake.add_pending(text)
    _pump(qapp)

    # Status window: ready, with the recent dictations and the Not copied list.
    status = ui.status_window
    status.present()
    _pump(qapp)
    status_image = _grab(status, "status", warnings)
    written: set[str] = set()
    _save(target, "status", frame(status_image), written)
    status.hide()

    # Settings, one image per tab, in the dialog's own tab order.
    ui.show_settings()
    dialog = ui.settings_dialog
    assert dialog is not None
    tabs = dialog.tabs
    pages: list[tuple[QWidget, str]] = [
        (dialog.hotkeys_page, "settings-hotkeys"),
        (dialog.sounds_page, "settings-sounds"),
        (dialog.general_page, "settings-general"),
    ]
    if len(pages) != tabs.count():
        raise SystemExit(f"the settings dialog has {tabs.count()} tabs; update the screenshot list")
    # The hotkey checks answer from a worker thread: wait for "Available".
    checking = _("Checking...")
    if not _pump_until(qapp, lambda: all(row.status.text() != checking for row in dialog.hotkeys_page.rows)):
        raise SystemExit("the hotkey checks did not finish")
    for page, name in sorted(pages, key=lambda pair: tabs.indexOf(pair[0])):
        tabs.setCurrentWidget(page)
        _pump(qapp)
        _save(target, name, frame(_grab(dialog, name, warnings)), written)
    dialog.hide()

    # The tray menu, as a right click on the tray icon opens it.
    tray = ui.tray
    assert tray is not None
    menu_image = _grab_menu(qapp, tray.menu, warnings)
    _save(target, "tray-menu", frame(menu_image), written)

    # Question boxes.
    for name, opener, attribute in (
        ("dialog-restart-wsl", ui.confirm_restart_wsl, "_restart_wsl_box"),
        ("dialog-language", ui.offer_language_restart, "_language_box"),
    ):
        image = _grab_question(qapp, opener, partial(getattr, ui, attribute), name, warnings)
        _save(target, name, frame(image), written)

    _save(target, "hero", hero(status_image, menu_image), written)
    if written != set(SCREENSHOT_NAMES):
        raise SystemExit(f"rendered {sorted(written)}, expected {sorted(SCREENSHOT_NAMES)}")

    for warning in warnings:
        print(f"warning: {language}: {warning}")
    print(f"{language}: wrote {target.relative_to(REPO_ROOT) if target.is_relative_to(REPO_ROOT) else target}")
    ui.bridge.detach()
    del translator
    return 0


def _save(target: Path, name: str, image: QImage, written: set[str]) -> None:
    if name not in SCREENSHOT_NAMES:
        raise SystemExit(f"{name} is not in SCREENSHOT_NAMES")
    save_png(image, target / f"{name}.png")
    written.add(name)


def _grab(widget: QWidget, name: str, warnings: list[str]) -> QImage:
    image = widget.grab().toImage()
    warnings.extend(f"{name}: {problem}" for problem in clipped_texts(widget))
    if image.devicePixelRatio() != SCALE:
        raise SystemExit(f"{name}: grabbed at {image.devicePixelRatio()}x instead of {SCALE}x")
    return image


def _grab_menu(qapp: QApplication, menu: QMenu, warnings: list[str]) -> QImage:
    menu.aboutToShow.emit()
    menu.popup(QPoint(0, 0))
    _pump(qapp)
    image = _grab(menu, "tray-menu", warnings)
    for action in menu.actions():
        text = action.text().replace("&&", "&").split("\t")[0]
        if action.isVisible() and text and menu.width() < menu.fontMetrics().horizontalAdvance(text) + 40:
            warnings.append(f"tray-menu: menu narrower than {text!r}")
    menu.hide()
    return image


def _grab_question(
    qapp: QApplication,
    opener: Callable[[], None],
    current: Callable[[], QMessageBox | None],
    name: str,
    warnings: list[str],
) -> QImage:
    """Open one of the UI's question boxes, grab it and close it. It is never on screen, so
    nothing should answer it; should anything close it before the grab, open it again."""
    for _attempt in range(3):
        opener()
        box = current()
        if not isinstance(box, QMessageBox):
            raise SystemExit(f"{name}: the dialog did not open")
        _pump(qapp)
        still_open = current() is box
        if still_open:
            image = _grab(box, name, warnings)
            box.close()
            _pump(qapp)
            return image
        print(f"note: {name} closed before the grab; opening it again")
    raise SystemExit(f"{name}: the dialog kept closing before the grab")


def clipped_texts(root: QWidget) -> list[str]:
    """Texts that do not fit their widget: unwrapped labels, buttons, check boxes, combo
    boxes and tabs."""
    problems: list[str] = []
    for widget in [root, *root.findChildren(QWidget)]:
        if widget is not root and not widget.isVisibleTo(root):
            continue
        metrics = widget.fontMetrics()
        if isinstance(widget, QLabel) and not widget.wordWrap() and widget.text() and "<" not in widget.text():
            needed = max(metrics.horizontalAdvance(line) for line in widget.text().split("\n"))
            if widget.width() + 1 < needed:
                problems.append(f"label clipped ({widget.width()} < {needed}): {widget.text()!r}")
        elif isinstance(widget, (QPushButton, QCheckBox)) and widget.text():
            if widget.width() + 1 < widget.sizeHint().width():
                problems.append(f"{type(widget).__name__} clipped: {widget.text()!r}")
        elif isinstance(widget, QComboBox):
            needed = metrics.horizontalAdvance(widget.currentText())
            if widget.width() - 28 < needed:  # the arrow and the margins
                problems.append(f"combo box too narrow ({widget.width()} for {needed}): {widget.currentText()!r}")
        elif isinstance(widget, QTabWidget):
            bar = widget.tabBar()
            for index in range(bar.count()):
                if bar.tabRect(index).width() + 1 < bar.tabSizeHint(index).width():
                    problems.append(f"tab clipped: {bar.tabText(index)!r}")
        elif isinstance(widget, QAbstractButton) and widget.text() and widget.width() + 1 < widget.sizeHint().width():
            problems.append(f"button clipped: {widget.text()!r}")
    return problems


# ---------------------------------------------------------------------- framing


def frame(grab: QImage) -> QImage:
    """The window grab with rounded corners, a hairline edge and a soft drop shadow on a
    transparent margin. The contents are not touched."""
    width, height = grab.width(), grab.height()
    margin, radius = FRAME_MARGIN * SCALE, FRAME_RADIUS * SCALE
    window = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    window.fill(Qt.GlobalColor.transparent)
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
    painter = QPainter(window)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setClipPath(shape)
    painter.drawImage(0, 0, _at_device_pixels(grab))
    painter.setClipping(False)
    painter.setPen(QPen(QColor(0, 0, 0, EDGE_ALPHA), SCALE * 0.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = SCALE * 0.25
    painter.drawRoundedRect(QRectF(inset, inset, width - 2 * inset, height - 2 * inset), radius, radius)
    painter.end()
    return _with_shadow(window, margin)


def _with_shadow(image: QImage, margin: int) -> QImage:
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(image))
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(SHADOW_BLUR * SCALE)
    shadow.setOffset(0, SHADOW_OFFSET * SCALE)
    shadow.setColor(QColor(0, 0, 0, SHADOW_ALPHA))
    item.setGraphicsEffect(shadow)
    scene.addItem(item)
    out = QImage(image.width() + 2 * margin, image.height() + 2 * margin, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    source = QRectF(-margin, -margin, out.width(), out.height())
    scene.render(painter, QRectF(0, 0, out.width(), out.height()), source)
    painter.end()
    return out


def _at_device_pixels(image: QImage) -> QImage:
    copy = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    copy.setDevicePixelRatio(1.0)
    return copy


def hero(status: QImage, menu: QImage) -> QImage:
    """The status window and the tray menu side by side, vertically centred, framed together."""
    status, menu = frame(status), frame(menu)
    overlap = (2 * FRAME_MARGIN - HERO_GAP) * SCALE  # their shared margin shrinks to the gap
    width = status.width() + menu.width() - overlap
    height = max(status.height(), menu.height())
    out = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawImage(0, (height - status.height()) // 2, status)
    painter.drawImage(status.width() - overlap, (height - menu.height()) // 2, menu)
    painter.end()
    return out


# ---------------------------------------------------------------------- shared images


def render_tray_states(path: Path) -> None:
    """Every state's tray glyph (the code the tray uses, at the 200 % tray size) on a
    dark and a light taskbar strip. No text: the READMEs caption it."""
    glyph = TRAY_GLYPH_SIZE * SCALE
    gap, padding = TRAY_GLYPH_GAP * SCALE, TRAY_STRIP_PADDING * SCALE
    strip_width = len(TRAY_STATES) * glyph + (len(TRAY_STATES) - 1) * gap + 2 * padding
    strip_height = glyph + 2 * padding
    spacing = 12 * SCALE
    out = QImage(strip_width, 2 * strip_height + spacing, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for row, (taskbar, tone) in enumerate(TRAY_STRIPS):
        top = row * (strip_height + spacing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(taskbar))
        painter.drawRoundedRect(QRectF(0, top, strip_width, strip_height), 8 * SCALE, 8 * SCALE)
        for index, state in enumerate(TRAY_STATES):
            image = glyph_image(state, glyph_color(tone, state), glyph)
            painter.drawImage(QPointF(padding + index * (glyph + gap), top + padding), image)
    painter.end()
    save_png(out, path)


def render_banner(theme: str, path: Path) -> None:
    """The app icon (drawn by the code that makes the packaged icon) and the wordmark,
    centred on a transparent background. `light` is for light pages (dark text)."""
    width, height = BANNER_SIZE[0] * SCALE, BANNER_SIZE[1] * SCALE
    icon = brand_image(BANNER_ICON * SCALE)
    font = QFont()
    font.setFamilies(["Segoe UI Variable Display", "Segoe UI"])
    font.setWeight(QFont.Weight.DemiBold)
    font.setPixelSize(BANNER_TEXT_PX * SCALE)
    metrics = QFontMetricsF(font)
    text = metrics.tightBoundingRect(BANNER_WORDMARK)
    gap = BANNER_GAP * SCALE
    left = (width - (icon.width() + gap + text.width())) / 2
    out = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.drawImage(QPointF(left, (height - icon.height()) / 2), icon)
    # Optical centre: the cap height sits on the icon's middle.
    baseline = height / 2 + metrics.capHeight() / 2
    path_text = QPainterPath()
    path_text.addText(QPointF(left + icon.width() + gap - text.left(), baseline), font, BANNER_WORDMARK)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(BANNER_INK[theme]))
    painter.drawPath(path_text)
    painter.end()
    save_png(out, path)


def save_png(image: QImage, path: Path) -> None:
    """Maximum zlib compression; the physical size says 2x (192 dpi)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = _at_device_pixels(image).convertToFormat(QImage.Format.Format_ARGB32)
    image.setDotsPerMeterX(PNG_DOTS_PER_METER)
    image.setDotsPerMeterY(PNG_DOTS_PER_METER)
    writer = QImageWriter(str(path), b"png")
    writer.setQuality(0)  # PNG: 0 = smallest file (the image itself is lossless)
    if not writer.write(image):
        raise RuntimeError(f"could not write {path}: {writer.errorString()}")


if __name__ == "__main__":
    raise SystemExit(main())
