"""Changing the UI language: the restart question, the hint, and the restart itself.

The `ui` fixture runs in English ("en"), reads "auto" as English, and records restarts
in `relaunches` instead of starting a process.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.companion.contract import UiLanguage  # noqa: E402
from app.companion.ui.app import CompanionUi  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402
from app.companion.ui.settings_window import SettingsDialog  # noqa: E402
from app.i18n import catalog_for  # noqa: E402

HINT = "Takes effect after VoiceMate restarts."


def _open(ui: CompanionUi) -> SettingsDialog:
    ui.show_settings()
    assert ui.settings_dialog is not None
    return ui.settings_dialog


def _choose(dialog: SettingsDialog, language: UiLanguage) -> None:
    combo = dialog.general_page.language
    index = combo.findData(language)
    assert index >= 0, language
    combo.setCurrentIndex(index)


def _hint_shown(dialog: SettingsDialog) -> bool:
    hint = dialog.general_page.language_hint
    return hint.text() == HINT and not hint.isHidden()


def _button(box: QMessageBox, text: str) -> None:
    (button,) = [button for button in box.buttons() if button.text() == text]
    button.click()


def _applied(fake: FakeController, process_events: Callable[..., bool], count: int) -> bool:
    return process_events(lambda: len(fake.called("apply_settings")) == count)


def test_the_question_has_the_right_texts(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    _choose(dialog, "es")
    dialog.ok_button.click()
    assert process_events(lambda: ui._language_box is not None)
    box = ui._language_box
    assert box is not None and box.isVisible()
    assert box.windowTitle() == "Restart VoiceMate?"
    assert box.text() == "VoiceMate needs to restart to change the language. Restart now?"
    assert sorted(button.text() for button in box.buttons()) == ["Later", "Restart now"]
    assert box.defaultButton() is not None and box.defaultButton().text() == "Later"
    assert box.isModal()


@pytest.mark.parametrize("close_after", [True, False], ids=["ok", "apply"])
def test_changing_to_another_catalog_asks_to_restart(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool], close_after: bool
) -> None:
    dialog = _open(ui)
    _choose(dialog, "pt-BR")
    (dialog.ok_button if close_after else dialog.apply_button).click()
    assert process_events(lambda: ui._language_box is not None)
    assert fake.settings().language == "pt-BR"  # saved before asking


def test_auto_and_the_explicit_choice_of_the_same_catalog_need_no_restart(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    assert fake.settings().language == "auto"  # read as English, like the running UI
    dialog = _open(ui)
    _choose(dialog, "en")
    assert not _hint_shown(dialog)
    dialog.apply_button.click()
    assert _applied(fake, process_events, 1)
    assert process_events(lambda: dialog.ok_button.isEnabled())  # the UI side of the save ran
    assert fake.settings().language == "en"
    assert ui._language_box is None

    _choose(dialog, "auto")
    dialog.ok_button.click()
    assert _applied(fake, process_events, 2)
    assert process_events(lambda: not dialog.isVisible())  # OK closed it: the save finished on the UI side
    assert fake.settings().language == "auto"
    assert ui._language_box is None


def test_other_edits_do_not_ask_even_with_a_pending_language(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.set_settings(replace(fake.settings(), language="es"))  # saved earlier, "Later"
    dialog = _open(ui)
    assert _hint_shown(dialog)
    dialog.general_page.engine_dir.setText("elsewhere")
    dialog.ok_button.click()
    assert _applied(fake, process_events, 1)
    assert process_events(lambda: not dialog.isVisible())  # OK closed it: the save finished on the UI side
    assert ui._language_box is None


def test_the_hint_follows_the_selected_language(ui: CompanionUi) -> None:
    dialog = _open(ui)
    assert not _hint_shown(dialog)
    _choose(dialog, "es")
    assert _hint_shown(dialog)  # before applying, too
    _choose(dialog, "auto")
    assert not _hint_shown(dialog)


def test_later_keeps_the_setting_and_shows_the_hint(
    ui: CompanionUi,
    fake: FakeController,
    relaunches: list[int],
    exits: list[int],
    process_events: Callable[..., bool],
) -> None:
    dialog = _open(ui)
    _choose(dialog, "es")
    dialog.apply_button.click()
    assert process_events(lambda: ui._language_box is not None)
    box = ui._language_box
    assert box is not None
    _button(box, "Later")
    process_events()
    assert ui._language_box is None
    assert fake.settings().language == "es"
    assert _hint_shown(dialog)
    assert relaunches == [] and not ui.quitting and fake.called("quit") == []

    # Still there after closing and reopening the window.
    dialog.ok_button.click()
    assert process_events(lambda: not dialog.isVisible())
    dialog = _open(ui)
    assert _hint_shown(dialog)
    assert exits == []


def test_restart_now_starts_a_new_instance_then_quits_gracefully(
    ui: CompanionUi,
    fake: FakeController,
    relaunches: list[int],
    exits: list[int],
    process_events: Callable[..., bool],
) -> None:
    dialog = _open(ui)
    _choose(dialog, "es")
    dialog.ok_button.click()
    assert process_events(lambda: ui._language_box is not None)
    box = ui._language_box
    assert box is not None
    _button(box, "Restart now")
    assert relaunches == [0]
    assert ui.quitting
    assert fake.called("quit") == [()]  # the same path as "Quit VoiceMate": the engine stops
    assert process_events(lambda: exits == [0], timeout=3.0)


def test_a_failed_restart_is_reported_and_the_app_keeps_running(
    qapp: QApplication, fake: FakeController, exits: list[int], process_events: Callable[..., bool]
) -> None:
    def broken() -> None:
        raise OSError("no such file")

    ui = CompanionUi(
        fake,
        tray_available=False,
        exit_app=lambda: exits.append(0),
        relaunch=broken,
        language_catalog=lambda language: "es" if language == "es" else "en",
        running_catalog="en",
    )
    ui.bridge.attach()
    try:
        ui.offer_language_restart()
        box = ui._language_box
        assert box is not None
        _button(box, "Restart now")
        process_events()
        assert not ui.quitting
        assert fake.called("quit") == []
        assert [n.title for n in ui.notifications.shown] == ["Could not restart VoiceMate"]
        assert ui.notifications.shown[0].level == "error"
        assert exits == []
    finally:
        ui.bridge.detach()
        ui.status_window.hide()
        ui.deleteLater()
        process_events()


def test_without_a_relauncher_restart_is_reported(
    qapp: QApplication, fake: FakeController, exits: list[int], process_events: Callable[..., bool]
) -> None:
    ui = CompanionUi(fake, tray_available=False, exit_app=lambda: exits.append(0))
    ui.bridge.attach()
    try:
        ui.restart_app()
        assert not ui.quitting
        assert [n.title for n in ui.notifications.shown] == ["Could not restart VoiceMate"]
    finally:
        ui.bridge.detach()
        ui.status_window.hide()
        ui.deleteLater()
        process_events()


def test_quit_closes_an_unanswered_question(
    ui: CompanionUi, relaunches: list[int], exits: list[int], process_events: Callable[..., bool]
) -> None:
    ui.offer_language_restart()
    box = ui._language_box
    assert box is not None
    ui.quit_app()
    assert ui._language_box is None
    assert relaunches == []
    assert process_events(lambda: exits == [0], timeout=3.0)


def test_the_running_catalog_defaults_to_the_active_language(qapp: QApplication, fake: FakeController) -> None:
    ui = CompanionUi(fake, tray_available=False, language_catalog=lambda language: language.replace("-", "_"))
    try:
        # The english_texts fixture makes "en" the active catalog.
        assert not ui.language_needs_restart("en")
        assert ui.language_needs_restart("pt-BR")
    finally:
        ui.deleteLater()


def test_the_catalog_resolution_is_the_one_set_language_uses(qapp: QApplication, fake: FakeController) -> None:
    ui = CompanionUi(fake, tray_available=False)
    try:
        assert ui._language_catalog is catalog_for  # tested with set_language in tests/test_i18n.py
    finally:
        ui.deleteLater()
