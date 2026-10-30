"""The assistant's text screen, its tray entry and its settings tab."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.companion.assistant import personality  # noqa: E402
from app.companion.assistant.contract import ChatMessage, InboxEntry, PendingEntry, ProfileStatus  # noqa: E402
from app.companion.contract import AssistantSettings  # noqa: E402
from app.companion.ui.app import CompanionUi  # noqa: E402
from app.companion.ui.assistant_window import AssistantWindow  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402
from app.companion.ui.settings_window import AssistantPage  # noqa: E402

ON = AssistantSettings(mode="own_session")


def _window(ui: CompanionUi) -> AssistantWindow:
    ui.show_assistant()
    assert ui.assistant_window is not None
    return ui.assistant_window


def _enable(fake: FakeController, process_events: Callable[..., bool]) -> None:
    fake.set_settings(replace(fake.settings(), assistant=ON))
    process_events()


def test_the_tray_entry_shows_only_when_the_mode_is_not_off(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    assert ui.tray is not None
    ui.tray._before_menu_shows()
    assert not ui.tray.assistant_action.isVisible()
    _enable(fake, process_events)
    ui.tray._before_menu_shows()
    assert ui.tray.assistant_action.isVisible()
    assert ui.tray.assistant_action.text() == "Assistant"
    ui.tray.assistant_action.trigger()
    assert ui.assistant_window is not None


def test_off_disables_the_input_with_the_reason(ui: CompanionUi, fake: FakeController) -> None:
    window = _window(ui)
    fake.assistant_service.publish(state="off")
    assert window.snapshot.state == "off"
    assert not window.input.isEnabled()
    assert not window.send_button.isEnabled()
    assert "Turn it on in Settings" in window.reason_label.text()


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"state": "starting"}, "starting"),
        ({"state": "busy"}, "answering"),
        ({"state": "error", "error": "cli_missing"}, "VOICEMATE_CLAUDE_CLI"),
        ({"state": "error", "error": "not_logged_in"}, "claude auth login"),
        ({"state": "error", "error": "exited"}, "Restart the assistant"),
    ],
)
def test_input_is_disabled_with_a_reason_in_each_blocked_state(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool], changes: dict[str, object], reason: str
) -> None:
    window = _window(ui)
    fake.assistant_service.publish(**changes)
    assert process_events(lambda: window.snapshot.state == changes["state"])
    assert not window.input.isEnabled()
    assert reason in window.reason_label.text()


def test_ready_shows_the_messages_and_the_link(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = _window(ui)
    fake.assistant_service.publish(
        state="ready",
        session_url="https://claude.ai/code/session_x",
        messages=(
            ChatMessage("user", "what is open?"),
            ChatMessage("assistant", "Two <things> are open."),
            ChatMessage("notice", 'No conversation matches "x".'),
        ),
    )
    assert process_events(lambda: window.input.isEnabled())
    text = window.messages.toPlainText()
    assert "You" in text
    assert "what is open?" in text
    assert "Two <things> are open." in text  # escaped, not interpreted as HTML
    assert "VoiceMate" in text
    assert "https://claude.ai/code/session_x" in window.status_label.text()
    assert window.reason_label.isHidden()


def test_enter_sends_and_shift_enter_adds_a_line(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = _window(ui)
    fake.assistant_service.publish(state="ready")
    assert process_events(lambda: window.input.isEnabled())
    window.show()
    window.input.setPlainText("first line")
    cursor = window.input.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    window.input.setTextCursor(cursor)
    QTest.keyClick(window.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(window.input, "second line")
    assert window.input.toPlainText() == "first line\nsecond line"
    assert fake.assistant_service.called("send") == []

    QTest.keyClick(window.input, Qt.Key.Key_Return)
    assert fake.assistant_service.called("send") == [("first line\nsecond line",)]
    assert window.input.toPlainText() == ""
    window.hide()


def test_send_button_and_empty_text(ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]) -> None:
    window = _window(ui)
    fake.assistant_service.publish(state="ready")
    assert process_events(lambda: window.send_button.isEnabled())
    window.send_button.click()
    assert fake.assistant_service.called("send") == []
    window.input.setPlainText("  hello  ")
    window.send_button.click()
    assert fake.assistant_service.called("send") == [("hello",)]


def test_restart_and_back_buttons(ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]) -> None:
    window = _window(ui)
    fake.assistant_service.publish(state="error", error="exited")
    assert process_events(lambda: not window.restart_button.isHidden())
    window.restart_button.click()
    assert fake.assistant_service.called("restart") == [()]
    assert process_events(lambda: window.snapshot.state == "ready")
    assert window.restart_button.isHidden()

    fake.assistant_service.publish(followed_title="Hotkey work")
    assert process_events(lambda: not window.back_button.isHidden())
    assert "Hotkey work" in window.status_label.text()
    window.back_button.click()
    assert fake.assistant_service.called("follow_assistant") == [()]
    assert process_events(lambda: window.back_button.isHidden())


def test_an_older_snapshot_never_replaces_a_newer_one(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = _window(ui)
    newest = fake.assistant_service.publish(state="ready")
    assert process_events(lambda: window.snapshot.rev == newest.rev)
    window._render_newer(replace(newest, rev=newest.rev - 1, state="busy"))
    assert window.snapshot.state == "ready"


def test_quitting_unsubscribes_and_hides(ui: CompanionUi, fake: FakeController) -> None:
    window = _window(ui)
    window.set_quitting()
    assert not window.isVisible()
    fake.assistant_service.publish(state="busy")
    assert window.snapshot.state != "busy"


# --- the settings tab -----------------------------------------------------------------------------


def test_the_assistant_tab_edits_every_field(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool], tmp_path: object
) -> None:
    ui.show_settings()
    dialog = ui.settings_dialog
    assert dialog is not None
    page = dialog.assistant_page
    tabs = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    assert "Assistant" in tabs
    assert page.mode.currentData() == "off"
    assert not page.model.isEnabled()  # everything else waits for the mode
    assert not dialog.apply_button.isEnabled()

    page.mode.setCurrentIndex(page.mode.findData("own_session"))
    assert page.model.isEnabled()
    page.model.setCurrentIndex(page.model.findData("opus"))
    page.effort.setCurrentIndex(page.effort.findData("high"))
    page.voice_commands.setChecked(False)
    page.expressions.setCurrentIndex(page.expressions.findData("on"))
    page.user_name.setText("Alli")
    assert dialog.apply_button.isEnabled()
    assert [page.model.itemData(i) for i in range(page.model.count())] == ["haiku", "sonnet", "opus"]
    assert [page.effort.itemData(i) for i in range(page.effort.count())] == ["low", "medium", "high"]

    dialog.apply(close_after=False)
    assert process_events(lambda: fake.settings().assistant.mode == "own_session")
    assert fake.settings().assistant == AssistantSettings(
        mode="own_session", model="opus", effort="high", voice_commands=False, expressions="on", user_name="Alli"
    )
    assert fake.assistant_service.called("apply_settings")


def test_the_personality_file_has_a_clear_button(ui: CompanionUi) -> None:
    ui.show_settings()
    assert ui.settings_dialog is not None
    page = ui.settings_dialog.assistant_page
    page.mode.setCurrentIndex(page.mode.findData("own_session"))
    page.personality_file.setText("C:/vault/persona.md")
    assert page.collect().personality_file == "C:/vault/persona.md"
    page.clear.click()
    assert page.collect().personality_file == ""


# --- wave 3: direct mode, the inbox, the window hotkey ---------------------------------------------


def _entries() -> tuple[InboxEntry, ...]:
    return (
        InboxEntry("c1", "Report", 1_700_000_000.0, "All done."),
        InboxEntry("c2", "", 1_700_000_100.0, ""),
    )


def test_direct_mode_shows_the_banner_and_the_back_button(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = _window(ui)
    assert window.direct_banner.isHidden()
    fake.assistant_service.publish(state="ready", direct_title="Release notes", followed_title="Release notes")
    assert process_events(lambda: not window.direct_banner.isHidden())
    assert '"Release notes"' in window.direct_banner.text()
    assert not window.back_button.isHidden()
    window.back_button.click()
    assert fake.assistant_service.called("follow_assistant") == [()]


def test_the_inbox_lists_held_answers_and_shows_one(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = _window(ui)
    assert window.inbox_box.isHidden()
    fake.assistant_service.publish(state="ready", inbox=_entries())
    assert process_events(lambda: window.inbox_list.count() == 2)
    assert not window.inbox_box.isHidden()
    assert window.inbox_list.item(0).text().startswith("Report, ")
    assert window.inbox_list.item(1).text().startswith("(untitled), ")
    assert window.inbox_text.isHidden()
    window.inbox_list.setCurrentRow(0)
    assert not window.inbox_text.isHidden()
    assert window.inbox_text.toPlainText() == "All done."
    window.inbox_list.setCurrentRow(1)
    assert window.inbox_text.toPlainText() == "(no text in the answer)"

    window.hear_button.click()
    assert fake.assistant_service.called("hear_ready") == [()]
    assert process_events(lambda: window.inbox_box.isHidden())
    fake.assistant_service.publish(inbox=_entries())
    assert process_events(lambda: window.inbox_list.count() == 2)
    window.clear_inbox_button.click()
    assert fake.assistant_service.called("clear_inbox") == [()]


def test_the_tray_counts_the_answers_waiting(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    _enable(fake, process_events)
    tray = ui.tray
    assert tray is not None
    assert tray.answers_waiting == 0
    fake.assistant_service.publish(inbox=_entries())
    assert process_events(lambda: tray.answers_waiting == 2)
    tray._before_menu_shows()
    assert tray.hear_action.isVisible()
    assert tray.hear_action.text() == "Hear what's ready (2)"
    assert "Answers waiting: 2" in tray.tray.toolTip()
    tray.hear_action.trigger()
    assert fake.assistant_service.called("hear_ready") == [()]
    assert process_events(lambda: tray.answers_waiting == 0)
    assert tray.hear_action.text() == "Hear what's ready"
    assert "Answers waiting" not in tray.tray.toolTip()


def test_a_window_request_presents_the_assistant_window(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    assert ui.assistant_window is None
    fake.assistant_service.show_window()
    assert process_events(lambda: ui.assistant_window is not None)


# --- wave 3: the Assistant tab ----------------------------------------------------------------------


def _page(ui: CompanionUi) -> AssistantPage:
    ui.show_settings()
    assert ui.settings_dialog is not None
    page = ui.settings_dialog.assistant_page
    page.mode.setCurrentIndex(page.mode.findData("own_session"))
    return page


def test_the_desktop_mode_shows_the_conversation_and_its_model_hint(ui: CompanionUi) -> None:
    page = _page(ui)
    assert page.desktop_conversation.isHidden()
    assert "next answer" in page.model_hint.text()
    page.mode.setCurrentIndex(page.mode.findData("desktop"))
    assert not page.desktop_conversation.isHidden()
    assert "cannot set the model of a Desktop conversation" in page.model_hint.text()
    page.desktop_conversation.setEditText(" VoiceMate central ")
    assert page.collect().desktop_conversation == "VoiceMate central"


def test_the_new_conversation_defaults_permission_and_delivery_are_collected(ui: CompanionUi) -> None:
    page = _page(ui)
    page.new_model.setCurrentIndex(page.new_model.findData("opus"))
    page.new_effort.setCurrentIndex(page.new_effort.findData("high"))
    page.answer_delivery.setCurrentIndex(page.answer_delivery.findData("silent"))
    assert page.permission_warning.isHidden()
    page.permission.setCurrentIndex(page.permission.findData("bypassPermissions"))
    assert not page.permission_warning.isHidden()
    page.profile_auto_update.setChecked(False)
    collected = page.collect()
    assert (collected.new_conversation_model, collected.new_conversation_effort) == ("opus", "high")
    assert collected.answer_delivery == "silent"
    assert collected.permission_mode == "bypassPermissions"
    assert collected.desktop_profile_auto_update is False


def test_the_personality_source_shows_its_own_fields(ui: CompanionUi, tmp_path: Path) -> None:
    page = _page(ui)
    assert page.personality_source.currentData() == "none"
    assert page.personality_file.isHidden() and page.custom_text.isHidden()
    page.personality_source.setCurrentIndex(page.personality_source.findData("file"))
    assert not page.personality_file.isHidden() and not page.vault.isHidden()
    assert page.custom_text.isHidden()

    page.vault.setEditText(str(tmp_path / "gone"))
    assert not page.vault_warning.isHidden()
    assert "does not exist" in page.vault_warning.text()
    page.vault.setEditText(str(tmp_path))
    assert "no .obsidian" in page.vault_warning.text()
    vault = tmp_path / "Notes"
    (vault / ".obsidian").mkdir(parents=True)
    page.vault.setEditText(str(vault))
    assert page.vault_warning.isHidden()
    assert page.collect().obsidian_vault == str(vault)

    page.personality_source.setCurrentIndex(page.personality_source.findData("custom"))
    assert page.personality_file.isHidden() and not page.custom_text.isHidden()
    assert page.collect().personality_source == "custom"


def test_the_written_personality_is_saved_and_refreshes_the_context(ui: CompanionUi, fake: FakeController) -> None:
    page = _page(ui)
    page.personality_source.setCurrentIndex(page.personality_source.findData("custom"))
    page.custom_text.setPlainText("Calm and short.")
    page.custom_save.click()
    assert page.custom_status.text() == "Saved."
    assert personality.read_custom() == "Calm and short."
    assert fake.assistant_service.called("refresh_context") == [()]
    page.custom_text.setPlainText("x" * (personality.CUSTOM_LIMIT + 1))
    page.custom_save.click()
    assert page.custom_status.text().startswith("Not saved: ")


def test_the_vault_suggestions_come_from_obsidian(
    ui: CompanionUi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "Notes"
    vault.mkdir()
    appdata = tmp_path / "appdata"
    (appdata / "obsidian").mkdir(parents=True)
    (appdata / "obsidian" / "obsidian.json").write_text(
        json.dumps({"vaults": {"a": {"path": str(vault), "ts": 1}}}), encoding="utf-8"
    )
    monkeypatch.setenv("APPDATA", str(appdata))
    page = _page(ui)
    assert page.vault.count() == 1
    assert page.vault.currentText() == str(vault)


def test_the_profile_section_shows_the_status_and_calls_the_service(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    page = _page(ui)
    assert page.profile_status.text() == "Loaded when the assistant starts."
    fake.assistant_service.publish(profile=ProfileStatus("override", 3, "bad remote file", checking=True))
    assert process_events(lambda: "version 3" in page.profile_status.text())
    assert "Checking for a newer one..." in page.profile_status.text()
    assert "Problem: bad remote file" in page.profile_status.text()
    page.profile_check.click()
    page.profile_default.click()
    assert fake.assistant_service.called("check_profile") == [()]
    assert fake.assistant_service.called("use_default_profile") == [()]


# --- round 4: waiting for, the message view, the written personality --------------------------------


def test_the_waiting_for_list_shows_each_follow_up(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = _window(ui)
    assert window.pending_box.isHidden()
    pending = (
        PendingEntry("c1", "Release notes", "the changelog draft", 1_700_000_000.0),
        PendingEntry("c2", "", "", 1_700_000_100.0),
    )
    fake.assistant_service.publish(state="ready", pending=pending)
    assert process_events(lambda: window.pending_list.count() == 2)
    assert not window.pending_box.isHidden()
    assert window.pending_list.item(0).text().startswith("Release notes: the changelog draft, since ")
    assert window.pending_list.item(1).text().startswith("(untitled), since ")
    fake.assistant_service.publish(pending=())
    assert process_events(lambda: window.pending_box.isHidden())


def test_the_messages_are_rebuilt_only_when_they_change_and_keep_the_scroll(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = _window(ui)
    window.resize(500, 300)
    many = tuple(ChatMessage("assistant", f"line {i} " + "word " * 30) for i in range(60))
    fake.assistant_service.publish(state="ready", messages=many)
    process_events()
    bar = window.messages.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()  # a new conversation starts at the end
    rebuilt: list[str] = []
    original = window.messages.setHtml

    def counting(html: str) -> None:
        rebuilt.append(html)
        original(html)

    window.messages.setHtml = counting  # type: ignore[method-assign]
    bar.setValue(0)  # the person scrolls up to read
    fake.assistant_service.publish(state="busy")  # same messages, another state
    process_events()
    assert rebuilt == []
    assert bar.value() == 0
    fake.assistant_service.publish(messages=(*many, ChatMessage("assistant", "one more")))
    process_events()
    assert len(rebuilt) == 1
    assert bar.value() == 0  # not pulled down while reading


def test_ok_saves_the_written_personality_too(ui: CompanionUi, fake: FakeController) -> None:
    page = _page(ui)
    dialog = ui.settings_dialog
    assert dialog is not None
    page.personality_source.setCurrentIndex(page.personality_source.findData("custom"))
    page.custom_text.setPlainText("Warm and brief.")
    assert dialog.is_dirty()
    dialog.apply(close_after=True)
    assert personality.read_custom() == "Warm and brief."
    assert fake.assistant_service.called("refresh_context")


def test_a_written_personality_that_cannot_be_saved_keeps_the_dialog_open(ui: CompanionUi) -> None:
    page = _page(ui)
    dialog = ui.settings_dialog
    assert dialog is not None
    page.personality_source.setCurrentIndex(page.personality_source.findData("custom"))
    page.custom_text.setPlainText("x" * (personality.CUSTOM_LIMIT + 1))
    dialog.apply(close_after=True)
    assert dialog.isVisible()
    assert dialog.tabs.currentWidget() is page
    assert page.custom_status.text().startswith("Not saved: ")


def test_the_microphone_button_talks_and_follows_the_recording(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    # The same as the talk hotkey: one click records, the next stops and sends; a recording
    # started with the hotkey shows here as well, with the elapsed time.
    window = _window(ui)
    fake.assistant_service.publish(state="ready")
    assert process_events(lambda: window.mic_button.isEnabled())
    assert window.mic_button.text() == "Talk"
    assert "Ctrl+Alt+S" in window.mic_button.toolTip()
    window.mic_button.click()
    assert fake.called("talk") == [()]
    assert process_events(lambda: window.mic_button.text() == "Stop and send")
    assert window.mic_status.text().startswith("Recording")
    window.mic_button.click()
    assert process_events(lambda: fake.snapshot().tray_state == "transcribing")
    assert process_events(lambda: window.mic_status.text() != "" and not window.mic_status.text().startswith("Rec"))
    assert not window.mic_button.isEnabled()  # nothing to start while the text is on its way


def test_the_microphone_waits_for_an_assistant_that_takes_prompts(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    window = _window(ui)
    fake.assistant_service.publish(state="busy")
    assert process_events(lambda: window.snapshot.state == "busy")
    assert not window.mic_button.isEnabled()
    fake.publish(tray_state="recording", recording_since=None)  # the hotkey recorded anyway
    assert process_events(lambda: window.mic_button.isEnabled())  # stopping is always offered
    assert window.mic_button.text() == "Stop and send"
