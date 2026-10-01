"""VoiceMate companion entry point: `python -m app.companion.main` (frozen: VoiceMate.exe).

Startup order (app/companion/contract.py, docs/companion-app.md):
1. AUMID, before any window exists (taskbar grouping with the pinned shortcut).
2. Single instance: the lock; a second launch forwards its command and exits.
3. QApplication, then the controller (lazy import: the core may not be installed
   in a UI-only checkout; `--demo` uses the fake controller instead).
4. `settings()` -> `app.i18n.set_language` -> build the UI -> `controller.start()`.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Sequence
from typing import get_args

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from app.companion.contract import CompanionCommand, CompanionController
from app.companion.ui.icons import app_icon
from app.companion.ui.single_instance import CommandServer, create_instance_lock, send_command, server_name
from app.i18n import _, set_language

log = logging.getLogger("app.companion")

# --demo runs next to a real instance without touching it: its own lock and channel.
_DEMO_SUFFIX = "-demo"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="voicemate-companion", description="VoiceMate tray companion.")
    parser.add_argument(
        "--command",
        choices=get_args(CompanionCommand),
        help="Send a command to the running instance (does nothing when VoiceMate is not running).",
    )
    parser.add_argument("--autostart", action="store_true", help="Started at sign-in: stay in the tray.")
    parser.add_argument("--demo", action="store_true", help="Fake engine that cycles through every state.")
    return parser.parse_args(argv)


def _configure_logging() -> None:
    # A windowed (frozen) build has no stderr; the controller sets up companion.log.
    if sys.stderr is not None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _create_controller(demo: bool) -> CompanionController:
    if demo:
        from app.companion.ui.demo_controller import FakeController

        return FakeController(autoplay=True)
    from app.companion.controller import create_controller

    controller: CompanionController = create_controller()
    return controller


def _forward(command: CompanionCommand | None, autostart: bool, suffix: str) -> int:
    """Another instance runs: hand it the command (plain launch = show it) and exit."""
    if command is None and autostart:
        return 0  # signing in again while running: nothing to do
    QCoreApplication(sys.argv[:1])
    if send_command(server_name(suffix), command or "show"):
        return 0
    log.error("VoiceMate is running but did not answer the command %r", command or "show")
    return 1


def _update_jump_list() -> None:
    if sys.platform != "win32":
        return
    from app.companion.win.jumplist import JumpTask, set_jump_list_tasks

    set_jump_list_tasks(
        [
            JumpTask(_("Settings"), "settings"),
            JumpTask(_("Restart engine"), "restart-engine"),
            JumpTask(_("Restart WSL"), "restart-wsl"),
            JumpTask(_("Quit VoiceMate"), "quit"),
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging()
    if sys.platform == "win32":
        from app.companion.win.aumid import set_app_user_model_id

        set_app_user_model_id()

    suffix = _DEMO_SUFFIX if args.demo else ""
    lock = create_instance_lock(suffix)
    if not lock.acquire():
        return _forward(args.command, args.autostart, suffix)
    try:
        if args.command is not None:
            # Nothing runs: a command never starts VoiceMate (the uninstaller sends quit,
            # a stale jump list task may send anything).
            log.info("no running instance for --command %s", args.command)
            return 0
        return _run(args, suffix)
    finally:
        lock.release()


def _run(args: argparse.Namespace, suffix: str) -> int:
    from app.companion.ui.app import CompanionUi

    app = QApplication(sys.argv[:1])
    app.setApplicationName("VoiceMate")
    app.setOrganizationName("VoiceMate")
    if sys.platform.startswith("linux"):
        app.setDesktopFileName("voicemate-companion")  # matches the .desktop file (packaging)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    controller = _create_controller(args.demo)
    set_language(controller.settings().language)
    ui = CompanionUi(controller)
    app.aboutToQuit.connect(ui.on_about_to_quit)

    server = CommandServer(server_name(suffix), ui)
    server.command_received.connect(ui.handle_command)
    server.listen()

    # Ctrl+C in a console quits like the menu does (Python only sees signals between Qt events).
    signal.signal(signal.SIGINT, lambda _signum, _frame: ui.quit_app())
    signal_pump = QTimer(ui)
    signal_pump.start(300)
    signal_pump.timeout.connect(lambda: None)

    # --autostart (sign-in): start quietly in the tray; a manual launch shows the window.
    ui.start(show_window=not args.autostart)
    controller.start()
    if not args.demo:
        QTimer.singleShot(0, _update_jump_list)
    code = int(app.exec())
    server.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
