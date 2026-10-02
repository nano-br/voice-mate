from __future__ import annotations

from collections.abc import Callable

import pytest

pytest.importorskip("PySide6")

from app.companion.contract import Notification, NotificationLevel  # noqa: E402
from app.companion.ui.app import CompanionUi  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402


@pytest.fixture
def balloons(ui: CompanionUi, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, NotificationLevel]]:
    """Pretend the tray shows balloons (offscreen Qt has no system tray) and record them."""
    shown: list[tuple[str, str, NotificationLevel]] = []
    assert ui.tray is not None
    monkeypatch.setattr(ui.tray, "supports_messages", lambda: True)
    monkeypatch.setattr(ui.tray, "show_message", lambda title, message, level: shown.append((title, message, level)))
    return shown


def test_controller_notifications_become_tray_balloons(
    ui: CompanionUi,
    fake: FakeController,
    balloons: list[tuple[str, str, NotificationLevel]],
    process_events: Callable[..., bool],
) -> None:
    fake.notify(
        Notification(
            "error", "Not copied to the clipboard", "A transcription did not reach the clipboard.", "show_status"
        )
    )
    fake.notify(Notification("info", "Engine ready", "Press Ctrl+Alt+V."))
    process_events()
    assert balloons == [
        ("Not copied to the clipboard", "A transcription did not reach the clipboard.", "error"),
        ("Engine ready", "Press Ctrl+Alt+V.", "info"),
    ]
    assert not ui.status_window.message_banner.isVisible()


def test_notifications_from_another_thread_reach_the_gui_thread(
    ui: CompanionUi,
    fake: FakeController,
    balloons: list[tuple[str, str, NotificationLevel]],
    process_events: Callable[..., bool],
) -> None:
    import threading

    thread = threading.Thread(target=lambda: fake.notify(Notification("warning", "Slow", "The backend is slow.")))
    thread.start()
    thread.join()
    assert process_events(lambda: balloons == [("Slow", "The backend is slow.", "warning")])


@pytest.mark.parametrize(
    ("action", "check"),
    [
        ("show_status", lambda ui, fake: ui.status_window.isVisible()),
        ("open_settings", lambda ui, fake: ui.settings_dialog is not None and ui.settings_dialog.isVisible()),
        ("open_logs", lambda ui, fake: fake.called("open_logs") == [()]),
    ],
)
def test_clicking_the_last_balloon_runs_its_action(
    ui: CompanionUi,
    fake: FakeController,
    balloons: list[tuple[str, str, NotificationLevel]],
    process_events: Callable[..., bool],
    action: str,
    check: Callable[[CompanionUi, FakeController], bool],
) -> None:
    fake.notify(Notification("warning", "Title", "Message", "none"))
    fake.notify(Notification("warning", "Title", "Message", action))  # type: ignore[arg-type]
    process_events()
    assert ui.tray is not None
    ui.tray.message_clicked.emit()
    assert check(ui, fake)


def test_click_on_a_balloon_without_action_does_nothing(
    ui: CompanionUi,
    fake: FakeController,
    balloons: list[tuple[str, str, NotificationLevel]],
    process_events: Callable[..., bool],
) -> None:
    fake.notify(Notification("info", "Title", "Message"))
    process_events()
    assert ui.tray is not None
    ui.tray.message_clicked.emit()
    assert not ui.status_window.isVisible()
    assert fake.called("open_logs") == []


def test_without_balloon_support_the_status_window_shows_it(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.notify(Notification("error", "Delivery failed", "Copy it from Recent.", "show_status"))
    process_events()
    banner = ui.status_window.message_banner
    assert banner.isVisible()
    assert "Delivery failed" in banner.label.text() and "Copy it from Recent." in banner.label.text()
