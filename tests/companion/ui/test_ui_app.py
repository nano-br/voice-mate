from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.companion.contract import CompanionSettings, Notification  # noqa: E402
from app.companion.ui import app as app_module  # noqa: E402
from app.companion.ui.app import CompanionUi  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402


def test_unknown_commands_are_ignored(ui: CompanionUi, fake: FakeController) -> None:
    ui.handle_command("format-disk")  # type: ignore[arg-type]
    ui.handle_command("restart-engine")
    assert fake.called("restart_engine") == [()]
    assert fake.calls[-1][0] == "restart_engine"


def test_restart_wsl_from_the_jump_list_asks_first(ui: CompanionUi, fake: FakeController) -> None:
    ui.handle_command("restart-wsl")
    assert ui._restart_wsl_box is not None and ui._restart_wsl_box.isVisible()
    assert fake.called("restart_wsl") == []


def test_commands_and_notifications_are_ignored_while_quitting(
    ui: CompanionUi, fake: FakeController, exits: list[int], process_events: Callable[..., bool]
) -> None:
    fake.quit_delay = 0.3
    ui.quit_app()
    ui.handle_command("settings")
    ui.handle_command("restart-engine")
    fake.notify(Notification("error", "late", "arrives while quitting"))
    process_events()
    assert ui.settings_dialog is None
    assert fake.called("restart_engine") == []
    assert ui.notifications.shown == []
    assert process_events(lambda: exits == [0], timeout=3.0)  # let the quit finish in this test


class _BrokenQuit(FakeController):
    def quit(self, on_done: Callable[[], None]) -> None:
        raise RuntimeError("boom")


class _SilentQuit(FakeController):
    def quit(self, on_done: Callable[[], None]) -> None:
        self._call("quit")  # never calls on_done


def test_quit_finishes_at_once_when_the_controller_fails(
    qapp: QApplication, process_events: Callable[..., bool]
) -> None:
    exits: list[int] = []
    finished: list[bool] = []
    companion = CompanionUi(_BrokenQuit(), tray_available=False, exit_app=lambda: exits.append(0))
    companion.quit_finished.connect(lambda: finished.append(True))
    companion.quit_app()
    assert finished == [True]  # finished at once, the exit itself runs from the event loop
    assert process_events(lambda: exits == [0])


def test_a_quit_finished_before_the_loop_starts_still_exits(qapp: QApplication) -> None:
    """A `quit` buffered during startup runs before app.exec(); QCoreApplication.quit()
    called then would be dropped, so the exit must wait for the loop."""
    companion = CompanionUi(_BrokenQuit(), tray_available=False)  # the real exit: QCoreApplication.quit
    companion.quit_app()  # controller.quit() raises: quitting finishes before exec()
    timed_out: list[bool] = []

    def give_up() -> None:
        timed_out.append(True)
        QCoreApplication.quit()

    guard = QTimer()  # without the deferred exit, exec() would run until this fires
    guard.setSingleShot(True)
    guard.timeout.connect(give_up)
    guard.start(3000)
    started = time.monotonic()
    qapp.exec()
    guard.stop()
    assert timed_out == [] and time.monotonic() - started < 2.5


def test_quit_has_a_safety_timeout(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, process_events: Callable[..., bool]
) -> None:
    monkeypatch.setattr(app_module, "QUIT_SAFETY_MS", 50)
    exits: list[int] = []
    fake = _SilentQuit()
    companion = CompanionUi(fake, tray_available=False, exit_app=lambda: exits.append(0))
    started: list[bool] = []
    companion.quit_started.connect(lambda: started.append(True))
    companion.quit_app()
    assert started == [True]
    assert fake.called("quit") == [()]
    assert process_events(lambda: exits == [0], timeout=2.0)


def test_autostart_waits_for_a_late_system_tray(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    available = [False]
    companion = CompanionUi(FakeController(), tray_probe=lambda: available[0], exit_app=lambda: None)
    assert companion.tray is None
    companion.start(show_window=False, wait_for_tray=True)
    process_events()
    assert not companion.status_window.isVisible()  # waiting quietly
    available[0] = True
    assert process_events(lambda: companion.tray is not None, timeout=3.0)
    assert companion.has_tray
    assert not companion.status_window.isVisible()
    companion.bridge.detach()


def test_autostart_falls_back_to_the_window_without_a_tray(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, process_events: Callable[..., bool]
) -> None:
    monkeypatch.setattr(app_module, "TRAY_WAIT_S", 0.2)
    companion = CompanionUi(FakeController(), tray_probe=lambda: False, exit_app=lambda: None)
    companion.start(show_window=False, wait_for_tray=True)
    assert process_events(lambda: companion.status_window.isVisible(), timeout=3.0)
    assert companion.tray is None
    companion.status_window.hide()
    companion.bridge.detach()


def test_the_jump_list_follows_the_engine_mode(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    modes: list[str] = []
    fake = FakeController(settings=CompanionSettings(engine_mode="wsl2"))
    companion = CompanionUi(
        fake, tray_available=False, exit_app=lambda: None, update_jump_list=lambda s: modes.append(s.engine_mode)
    )
    companion.start(show_window=False)
    assert modes == ["wsl2"]
    companion.settings_changed()  # nothing changed: no rewrite
    assert modes == ["wsl2"]
    assert fake.apply_settings(replace(fake.settings(), engine_mode="external")) == []
    companion.settings_changed()
    assert modes == ["wsl2", "external"]
    companion.status_window.hide()
    companion.bridge.detach()
