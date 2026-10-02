"""VoiceMate companion entry point: `python -m app.companion.main` (frozen: VoiceMate.exe).

Startup order (app/companion/contract.py, docs/companion-app.md):
1. AUMID, before any window exists (taskbar grouping with the pinned shortcut).
2. QApplication, then the single-instance lock. A second launch forwards its command
   (retrying while the first one starts up) and exits; if the first one is quitting,
   a plain launch waits for it to end and then starts. `--after-restart` (the new
   process of a restart, `app/companion/relaunch.py`) never forwards: it waits for the
   lock the quitting instance releases, bounded by `RESTART_WAIT_S`.
3. The command channel listens at once; commands wait until the UI exists.
4. The controller (lazy import, so a forwarded command never loads the core;
   `--demo` uses the fake controller) -> `settings()` -> `app.i18n.set_language`
   (plus Qt's own qtbase translations) -> build the UI -> `controller.start()`.
A `--command` never starts VoiceMate: with nothing running it exits 0.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from collections.abc import Callable, Sequence
from typing import Final, get_args

from PySide6.QtCore import QLibraryInfo, QLocale, QTimer, QTranslator
from PySide6.QtWidgets import QApplication

from app.companion.contract import CompanionCommand, CompanionController, CompanionSettings
from app.companion.relaunch import AFTER_RESTART_FLAG, relaunch_companion
from app.companion.ui.icons import app_icon
from app.companion.ui.single_instance import (
    CommandServer,
    InstanceLock,
    create_instance_lock,
    send_command,
    server_name,
)
from app.i18n import _, active_language, set_language

log = logging.getLogger("app.companion")

# --demo runs next to a real instance without touching it: its own lock and channel.
_DEMO_SUFFIX: Final = "-demo"
# The running instance may still be starting (not listening yet): keep trying this long.
FORWARD_TIMEOUT_S: Final = 10.0
# It answered "quitting": wait this long for it to end (its controller has a 15 s cap).
QUIT_WAIT_S: Final = 25.0
RETRY_INTERVAL_S: Final = 0.25
# A restart: the old instance quits within its UI's 20 s safety net (the controller's own
# cap is 15 s), then releases the lock. Past this, start like a plain launch would.
RESTART_WAIT_S: Final = 40.0
# Qt's own strings (context menus, file dialogs) come from these catalogs.
_QT_CATALOGS: Final = ("pt_BR", "es")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="voicemate-companion", description="VoiceMate tray companion.")
    parser.add_argument(
        "--command",
        choices=get_args(CompanionCommand),
        help="Send a command to the running instance (does nothing when VoiceMate is not running).",
    )
    parser.add_argument("--autostart", action="store_true", help="Started at sign-in: stay in the tray.")
    parser.add_argument("--demo", action="store_true", help="Fake engine that cycles through every state.")
    # Internal: added by a restart (app/companion/relaunch.py), never typed by users.
    parser.add_argument(AFTER_RESTART_FLAG, dest="after_restart", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _configure_logging() -> None:
    # A windowed (frozen) build has no stderr; the controller sets up companion.log.
    if sys.stderr is not None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _application() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication(sys.argv[:1])


def _create_controller(demo: bool) -> CompanionController:
    if demo:
        from app.companion.ui.demo_controller import FakeController

        return FakeController(autoplay=True)
    from app.companion.controller import create_controller

    controller: CompanionController = create_controller()
    return controller


def _wait_for_restart(lock: InstanceLock) -> bool:
    """`--after-restart`: the instance that started us is quitting; take the lock once it
    has exited. False when it is still held after `RESTART_WAIT_S`."""
    deadline = time.monotonic() + RESTART_WAIT_S
    while True:
        if lock.acquire():
            return True
        if time.monotonic() >= deadline:
            log.warning("the previous VoiceMate did not exit within %.0f s", RESTART_WAIT_S)
            return False
        time.sleep(RETRY_INTERVAL_S)


def _forward(lock: InstanceLock, args: argparse.Namespace, suffix: str) -> int | None:
    """Another instance holds the lock: hand it our command and return the exit code.

    None means the other instance went away meanwhile and this process now holds the
    lock (a plain launch while the old one was quitting): the caller starts normally.
    """
    if args.command is None and args.autostart:
        return 0  # signing in again while running: nothing to do
    command: CompanionCommand = args.command or "show"
    name = server_name(suffix)
    _allow_foreground()
    deadline = time.monotonic() + FORWARD_TIMEOUT_S
    quit_seen = False
    while True:
        reply = send_command(name, command, timeout_ms=1000)
        if reply == "ok":
            return 0
        if reply == "error":
            log.error("the running VoiceMate refused the command %r", command)
            return 1
        if reply == "quitting":
            if args.command is not None:
                return 0  # a command never starts VoiceMate; quit is already happening
            if not quit_seen:
                # One fixed wait for the old instance to end (its controller has a 15 s cap).
                quit_seen = True
                deadline = time.monotonic() + QUIT_WAIT_S
        if lock.acquire():
            if args.command is None:
                return None  # the caller starts VoiceMate and releases the lock at the end
            lock.release()
            return 0
        if time.monotonic() >= deadline:
            log.error("VoiceMate is running but did not answer the command %r", command)
            return 1
        time.sleep(RETRY_INTERVAL_S)


def _allow_foreground() -> None:
    """Windows: let the running instance bring its window to the front for our command
    (show, settings, the Restart WSL question). Without this, an instance started at
    login may not take the foreground, and its window only flashes in the taskbar."""
    if sys.platform == "win32":
        from app.companion.win.single_instance import allow_any_foreground

        allow_any_foreground()


def jump_list_updater() -> Callable[[CompanionSettings], None] | None:
    """Windows: rebuild the jump list tasks (Restart WSL only in WSL mode)."""
    if sys.platform != "win32":
        return None
    from app.companion.win.jumplist import JumpTask, set_jump_list_tasks

    def update(settings: CompanionSettings) -> None:
        tasks = [JumpTask(_("Settings"), "settings"), JumpTask(_("Restart engine"), "restart-engine")]
        if settings.engine_mode == "wsl2":
            tasks.append(JumpTask(_("Restart WSL..."), "restart-wsl"))
        tasks.append(JumpTask(_("Quit VoiceMate"), "quit"))
        set_jump_list_tasks(tasks)

    return update


def install_qt_translations(app: QApplication) -> QTranslator | None:
    """Qt's built-in strings (standard context menus, file dialogs) in the UI language.

    Only `qtbase_*` ships with the frozen build (pt_BR and es). Keep the returned object
    alive: an installed translator that gets garbage-collected stops translating.
    """
    catalog = active_language()
    if catalog not in _QT_CATALOGS:
        return None
    translator = QTranslator(app)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if not translator.load(QLocale(catalog), "qtbase", "_", directory):
        log.info("no qtbase translation for %s in %s", catalog, directory)
        return None
    app.installTranslator(translator)
    return translator


def _has_console() -> bool:
    return any(stream is not None and stream.isatty() for stream in (sys.stdin, sys.stderr))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging()
    if sys.platform == "win32":
        from app.companion.win.aumid import set_app_user_model_id

        set_app_user_model_id()

    app = _application()
    suffix = _DEMO_SUFFIX if args.demo else ""
    lock = create_instance_lock(suffix)
    if not lock.acquire():
        # A restart never forwards "show" to the instance that is quitting: it waits for it.
        # If that one is still there after the wait, behave like any second launch.
        if not (args.after_restart and args.command is None and _wait_for_restart(lock)):
            code = _forward(lock, args, suffix)
            if code is not None:
                return code
    try:
        if args.command is not None:
            # Nothing runs: a command never starts VoiceMate (the uninstaller sends quit,
            # a stale jump list task may send anything).
            log.info("no running instance for --command %s", args.command)
            return 0
        return _run(app, args, suffix)
    finally:
        lock.release()


def _run(app: QApplication, args: argparse.Namespace, suffix: str) -> int:
    from app.companion.ui.app import CompanionUi

    # Listen first: launches during startup queue their command instead of timing out.
    server = CommandServer(server_name(suffix))
    server.listen()

    app.setApplicationName("VoiceMate")
    app.setOrganizationName("VoiceMate")
    if sys.platform.startswith("linux"):
        app.setDesktopFileName("voicemate-companion")  # matches the .desktop file (packaging)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    controller = _create_controller(args.demo)
    set_language(controller.settings().language)
    qt_translator = install_qt_translations(app)
    launch_args = sys.argv[1:]
    ui = CompanionUi(
        controller,
        update_jump_list=None if args.demo else jump_list_updater(),
        relaunch=lambda: relaunch_companion(launch_args),
    )
    app.aboutToQuit.connect(ui.on_about_to_quit)
    ui.quit_started.connect(server.set_quitting)

    if _has_console():
        # Ctrl+C quits like the menu does; Python only sees signals between Qt events.
        signal.signal(signal.SIGINT, lambda _signum, _frame: ui.quit_app())
        signal_pump = QTimer(ui)
        signal_pump.timeout.connect(lambda: None)
        signal_pump.start(300)

    # --autostart (sign-in): quietly in the tray. On Linux the panel's tray may come up
    # after us: wait for it before falling back to the window.
    ui.start(show_window=not args.autostart, wait_for_tray=args.autostart and sys.platform != "win32")
    controller.start()
    # Last: commands that arrived during startup (a quit, say) run once everything runs.
    server.set_handler(ui.handle_command)
    code = int(app.exec())
    server.close()
    del qt_translator
    return code


if __name__ == "__main__":
    raise SystemExit(main())
