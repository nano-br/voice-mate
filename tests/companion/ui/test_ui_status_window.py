from __future__ import annotations

import time
from collections.abc import Callable

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.companion.contract import Notification  # noqa: E402
from app.companion.ui.app import CompanionUi  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402


def test_shows_status_and_engine_information(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.publish(tray_state="warning", audio="down", mic_count=0, restarts=3, detail="WSL restarted 3 times")
    process_events()
    window = ui.status_window
    assert window.status_label.text() == "No microphone"
    assert window.detail_label.text() == "WSL restarted 3 times"
    assert window.engine_value.text() == "Running"
    assert window.audio_value.text() == "Not working"
    assert window.mics_value.text() == "None found"
    assert window.restarts_value.text() == "3"


def test_banners_follow_the_snapshot(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = ui.status_window
    window.show()
    process_events()
    assert window.outdated_banner.isHidden() and window.wsl_banner.isHidden()
    fake.publish(engine_outdated=True, pending_wsl_restart=True)
    process_events()
    assert window.outdated_banner.isVisible() and window.wsl_banner.isVisible()
    window.wsl_restart_button.click()
    window.wsl_later_button.click()
    window.outdated_restart.click()
    assert fake.called("answer_wsl_restart") == [(True,), (False,)]
    assert fake.called("restart_engine") == [()]


def test_flow_buttons_toggle_and_become_stop_buttons(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = ui.status_window
    assert [button.text() for button in window.flow_buttons] == ["Dictate", "Ask Claude"]
    assert window.flow_buttons[0].toolTip() == "Ctrl+Alt+V"
    window.flow_buttons[1].click()
    assert fake.called("toggle") == [("claude_chat",)]
    process_events()
    assert [button.text() for button in window.flow_buttons] == ["Stop and copy", "Stop and ask Claude"]
    assert window.cancel_button.isEnabled()
    window.cancel_button.click()
    assert fake.called("cancel") == [()]


def test_recording_line_ticks_every_second(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.publish(tray_state="recording", recording_since=time.time() - 1.2, flow="clipboard")
    process_events()
    assert ui.status_window.status_label.text() == "Recording 00:01"
    assert process_events(lambda: ui.status_window.status_label.text() == "Recording 00:02", timeout=2.5)


def test_pending_list_copies_and_hides_when_empty(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = ui.status_window
    window.show()
    item = fake.add_pending("lost text")
    process_events()
    assert window.pending_group.isVisible()
    assert window.pending_list.count() == 1
    assert window.pending_copy.isEnabled()  # the first item is preselected
    window.pending_copy.click()
    assert fake.called("copy_result") == [(item.instance, item.record["result_seq"])]
    process_events()
    assert not window.pending_group.isVisible()


def test_recent_list_copies_on_activation(ui: CompanionUi, fake: FakeController) -> None:
    window = ui.status_window
    recent = fake.recent_results()
    assert window.recent_list.count() == len(recent)
    window.recent_list.itemActivated.emit(window.recent_list.item(2))
    assert fake.called("copy_result") == [(recent[2].instance, recent[2].record["result_seq"])]


def test_buttons_open_settings_logs_and_restart(ui: CompanionUi, fake: FakeController) -> None:
    window = ui.status_window
    window.logs_button.click()
    window.restart_button.click()
    window.settings_button.click()
    assert fake.called("open_logs") == [()]
    assert fake.called("restart_engine") == [()]
    assert ui.settings_dialog is not None and ui.settings_dialog.isVisible()


def test_quit_button_quits_through_the_controller(
    ui: CompanionUi, fake: FakeController, exits: list[int], process_events: Callable[..., bool]
) -> None:
    window = ui.status_window
    window.show()
    window.quit_button.click()
    assert fake.called("quit") == [()]
    assert window.status_label.text() == "Quitting..."
    assert not window.quit_button.isEnabled()
    assert process_events(lambda: exits == [0])
    assert not window.isVisible()


def test_closing_hides_with_a_tray_and_quits_without_one(
    qapp: QApplication, ui: CompanionUi, fake: FakeController, exits: list[int], process_events: Callable[..., bool]
) -> None:
    ui.status_window.show()
    ui.status_window.close()
    assert fake.called("quit") == []

    no_tray_fake = FakeController()
    no_tray_exits: list[int] = []
    companion = CompanionUi(no_tray_fake, tray_available=False, exit_app=lambda: no_tray_exits.append(0))
    companion.start(show_window=False)
    assert companion.tray is None
    assert companion.status_window.isVisible()  # without a tray the window is the main UI
    companion.status_window.close()
    assert no_tray_fake.called("quit") == [()]
    assert process_events(lambda: no_tray_exits == [0])


def test_without_a_tray_notifications_show_in_the_window(
    qapp: QApplication, process_events: Callable[..., bool]
) -> None:
    fake = FakeController()
    companion = CompanionUi(fake, tray_available=False, exit_app=lambda: None)
    companion.start(show_window=True)
    fake.notify(Notification("warning", "WSL audio is down", "Restart WSL.", "show_status"))
    process_events()
    banner = companion.status_window.message_banner
    assert banner.isVisible()
    assert "WSL audio is down" in banner.label.text()
    companion.status_window.message_close.click()
    assert not banner.isVisible()
    companion.bridge.detach()
    companion.status_window.hide()


def test_wsl_restart_question_from_the_menu(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.publish(pending_wsl_restart=True)
    process_events()
    ui.confirm_wsl_restart()
    box = ui._wsl_box
    assert box is not None and box.isVisible()
    assert box.windowTitle() == "Restart WSL now?"
    default = box.defaultButton()
    assert default is not None and default.text() == "Not now"
    restart = next(b for b in box.buttons() if b.text() == "Restart WSL")
    restart.click()
    assert process_events(lambda: fake.called("answer_wsl_restart") == [(True,)])


def test_wsl_restart_question_closes_when_the_question_goes_away(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.publish(pending_wsl_restart=True)
    process_events()
    ui.confirm_wsl_restart()
    box = ui._wsl_box
    assert isinstance(box, QMessageBox)
    fake.publish(pending_wsl_restart=False)
    process_events()
    assert ui._wsl_box is None
    assert fake.called("answer_wsl_restart") == []


def test_escape_hides_the_window_when_a_tray_exists(ui: CompanionUi, process_events: Callable[..., bool]) -> None:
    from PySide6.QtTest import QTest

    ui.status_window.show()
    process_events()
    QTest.keyClick(ui.status_window, Qt.Key.Key_Escape)
    assert not ui.status_window.isVisible()


def test_restart_engine_stays_available_while_the_engine_starts(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.publish(tray_state="starting", supervisor="starting", engine_ready=False)
    process_events()
    assert ui.status_window.restart_button.isEnabled()  # the doc: restart works during the load
    ui.status_window.restart_button.click()
    assert fake.called("restart_engine") == [()]


def test_space_on_dictate_starts_and_stops_with_the_same_button(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    from PySide6.QtTest import QTest

    window = ui.status_window
    window.show()
    process_events()
    button = window.flow_buttons[0]
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    process_events()
    assert window.flow_buttons[0] is button  # relabeled, not rebuilt: the focus stays
    assert button.text() == "Stop and copy"
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert fake.called("toggle") == [("clipboard",), ("clipboard",)]


def test_flow_buttons_come_before_cancel_in_the_tab_order(ui: CompanionUi) -> None:
    window = ui.status_window
    first, second = window.flow_buttons
    assert first.nextInFocusChain() is second
    assert second.nextInFocusChain() is window.cancel_button


def test_header_icon_is_repainted_when_the_scale_changes(ui: CompanionUi) -> None:
    """Moved to a monitor with another scale: the header icon is painted again for it,
    not left as a blurry upscale of the old pixmap."""
    window = ui.status_window
    before = window.state_icon.pixmap().cacheKey()
    QApplication.sendEvent(window, QEvent(QEvent.Type.DevicePixelRatioChange))
    assert window.state_icon.pixmap().cacheKey() != before
