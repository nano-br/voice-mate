"""Switching the transcription mode from the tray and the settings window: check, ask, install, apply."""

from __future__ import annotations

import gc
from collections.abc import Callable
from dataclasses import replace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from app.companion.ui.app import CompanionUi  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402
from app.companion.ui.settings_window import SettingsDialog  # noqa: E402
from app.companion.ui.tray import TrayIcon  # noqa: E402


def _tray(ui: CompanionUi) -> TrayIcon:
    assert ui.tray is not None
    return ui.tray


def _answer(box: QMessageBox | None, text: str) -> None:
    assert box is not None and box.isVisible()
    button = next(candidate for candidate in box.buttons() if candidate.text() == text)
    button.click()


def _applied_modes(fake: FakeController) -> list[str]:
    return [args[0].stt_mode for args in fake.called("apply_settings")]  # type: ignore[attr-defined]


@pytest.fixture
def cpu_user(fake: FakeController) -> FakeController:
    """Someone on the CPU mode whose GPU mode is not installed yet."""
    fake.set_settings(replace(fake.settings(), stt_mode="cpu"))
    fake.stt_report["gpu"] = {"ready": False, "supported": True, "detail": "missing", "download_mb": 4300}
    return fake


def test_tray_lists_both_modes_and_checks_the_current_one(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    ui.settings_changed()
    tray = _tray(ui)
    assert tray.stt_menu.menuAction().isVisible()
    assert tray.stt_actions["cpu"].isChecked()
    assert not tray.stt_actions["gpu"].isChecked()


def test_switching_to_an_installed_mode_applies_at_once(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.set_settings(replace(fake.settings(), stt_mode="cpu"))
    _tray(ui).stt_actions["gpu"].trigger()
    assert process_events(lambda: _applied_modes(fake) == ["gpu"])
    assert ui._stt_box is None  # nothing to ask: it is installed
    assert fake.called("install_stt") == []


def test_missing_mode_asks_with_the_size_then_installs_and_switches(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    _tray(ui).stt_actions["gpu"].trigger()
    assert process_events(lambda: ui._stt_box is not None)
    box = ui._stt_box
    assert box is not None
    assert "4.3 GB" in box.text()
    default = box.defaultButton()
    assert default is not None and default.text() == "Cancel"  # a 4 GB download is an explicit click
    _answer(box, "Install")
    assert process_events(lambda: _applied_modes(cpu_user) == ["gpu"])
    assert cpu_user.called("install_stt") == [("gpu",)]
    dialog = ui._stt_dialog
    assert dialog is not None and dialog.isVisible()  # the log stays until the user closes it
    dialog.close_button.click()
    assert process_events(lambda: ui._stt_dialog is None)


def test_declining_the_install_keeps_the_current_mode(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    tray = _tray(ui)
    tray.stt_actions["gpu"].trigger()
    assert process_events(lambda: ui._stt_box is not None)
    _answer(ui._stt_box, "Cancel")
    assert process_events(lambda: ui._stt_box is None)
    process_events()
    assert cpu_user.called("install_stt") == []
    assert cpu_user.called("apply_settings") == []
    assert tray.stt_actions["cpu"].isChecked()  # the check goes back to the mode in use


def test_failed_install_does_not_switch(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    cpu_user.stt_install_ok = False
    _tray(ui).stt_actions["gpu"].trigger()
    assert process_events(lambda: ui._stt_box is not None)
    _answer(ui._stt_box, "Install")
    assert process_events(lambda: cpu_user.called("install_stt") == [("gpu",)])
    dialog = ui._stt_dialog
    assert dialog is not None
    assert process_events(lambda: dialog.close_button.isEnabled())
    gc.collect()
    process_events()
    assert ui._stt_dialog is dialog and dialog.isVisible()  # the failure details stay on screen
    assert "failed" in dialog.message.text()
    assert cpu_user.called("apply_settings") == []
    dialog.close_button.click()
    assert process_events(lambda: ui._stt_dialog is None)


def test_a_second_switch_while_one_is_under_way_is_refused_with_a_note(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    _tray(ui).stt_actions["gpu"].trigger()
    assert process_events(lambda: ui._stt_box is not None)
    answers: list[bool] = []
    ui.ensure_stt_ready(replace(cpu_user.settings(), stt_mode="gpu"), answers.append)
    assert answers == [False]
    assert cpu_user.called("check_stt") == [("gpu",)]  # no second check
    _answer(ui._stt_box, "Cancel")


def test_no_gpu_on_the_machine_says_so_without_asking(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    cpu_user.stt_report["gpu"] = {"ready": False, "supported": False, "detail": "no gpu", "download_mb": 0}
    _tray(ui).stt_actions["gpu"].trigger()
    assert process_events(lambda: cpu_user.called("check_stt") != [])
    process_events()
    assert ui._stt_box is None
    assert cpu_user.called("apply_settings") == []


def test_unknown_readiness_switches_anyway(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    cpu_user.stt_report = None  # type: ignore[assignment]  # WSL down: the engine falls back and says so
    _tray(ui).stt_actions["gpu"].trigger()
    assert process_events(lambda: _applied_modes(cpu_user) == ["gpu"])


def test_settings_window_installs_before_saving_a_new_mode(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    ui.show_settings()
    dialog = ui.settings_dialog
    assert isinstance(dialog, SettingsDialog)
    page = dialog.general_page
    page.stt_mode.setCurrentIndex(page.stt_mode.findData("gpu"))
    page.stt_gpu_model.setCurrentIndex(page.stt_gpu_model.findData("medium"))
    dialog.apply_button.click()
    assert process_events(lambda: ui._stt_box is not None)
    assert cpu_user.called("apply_settings") == []  # nothing saved before the answer
    _answer(ui._stt_box, "Install")
    assert process_events(lambda: _applied_modes(cpu_user) == ["gpu"])
    (sent,) = cpu_user.called("apply_settings")[0]
    assert sent.stt_gpu_model == "medium"  # type: ignore[attr-defined]
    dialog.hide()


def test_settings_window_declined_install_saves_nothing(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    ui.show_settings()
    dialog = ui.settings_dialog
    assert isinstance(dialog, SettingsDialog)
    page = dialog.general_page
    page.stt_mode.setCurrentIndex(page.stt_mode.findData("gpu"))
    dialog.apply_button.click()
    assert process_events(lambda: ui._stt_box is not None)
    _answer(ui._stt_box, "Cancel")
    assert process_events(lambda: "not ready" in dialog.message.text())
    assert cpu_user.called("apply_settings") == []
    dialog.hide()


def test_other_mode_model_change_needs_no_check(
    ui: CompanionUi, cpu_user: FakeController, process_events: Callable[..., bool]
) -> None:
    ui.show_settings()
    dialog = ui.settings_dialog
    assert isinstance(dialog, SettingsDialog)
    page = dialog.general_page
    page.stt_gpu_model.setCurrentIndex(page.stt_gpu_model.findData("medium"))  # on the CPU mode
    dialog.apply_button.click()
    assert process_events(lambda: cpu_user.called("apply_settings") != [])
    assert cpu_user.called("check_stt") == []
    dialog.hide()


def test_status_window_shows_the_effective_device_and_the_fallback(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.publish(stt={"mode": "gpu", "device": "cpu", "backend": "faster-whisper", "model": "large-v3-turbo"})
    process_events()
    text = ui.status_window.stt_value.text()
    assert text.startswith("CPU, large-v3-turbo")
    assert "GPU mode is not working" in text
