"""The Voice tab: choosing a custom TTS provider, loading its voices, testing, applying."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QLineEdit  # noqa: E402

from app.companion.contract import AssistantSettings, CompanionSettings, TtsProbe  # noqa: E402
from app.companion.ui.app import CompanionUi  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402
from app.companion.ui.settings_window import AssistantPage, SettingsDialog, VoicePage  # noqa: E402
from app.protocol.tts_provider import Voice  # noqa: E402


def _open(ui: CompanionUi) -> SettingsDialog:
    ui.show_settings()
    assert ui.settings_dialog is not None
    return ui.settings_dialog


def _choose_provider(page: VoicePage) -> None:
    page.tts_engine.setCurrentIndex(page.tts_engine.findData("external"))


def _items(page: VoicePage) -> list[str]:
    return [page.tts_voice.itemText(index) for index in range(page.tts_voice.count())]


def test_the_voice_tab_starts_on_the_built_in_voice_with_the_provider_fields_off(ui: CompanionUi) -> None:
    dialog = _open(ui)
    page = dialog.voice_page
    assert dialog.tabs.tabText(dialog.tabs.indexOf(page)) == "Voice"
    assert page.tts_engine.currentText() == "Built-in (engine setup)"
    assert not any(widget.isEnabled() for widget in (page.tts_url, page.tts_voice, page.tts_token, page.test_button))
    assert not dialog.apply_button.isEnabled()  # nothing edited


def test_choosing_a_provider_enables_its_fields_and_the_token_is_masked(ui: CompanionUi) -> None:
    dialog = _open(ui)
    page = dialog.voice_page
    _choose_provider(page)
    assert all(
        widget.isEnabled()
        for widget in (page.tts_url, page.tts_voice, page.tts_token, page.test_button, page.load_voices_button)
    )
    assert page.tts_token.echoMode() == QLineEdit.EchoMode.Password
    assert page.tts_url.text() == "http://127.0.0.1:8765"
    assert dialog.apply_button.isEnabled()


def test_apply_sends_the_provider_settings(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    page = dialog.voice_page
    _choose_provider(page)
    page.tts_url.setText("http://127.0.0.1:9100")
    page.tts_voice.setEditText("narrator")
    page.tts_token.setText("s3cret")
    dialog.apply_button.click()
    assert process_events(lambda: bool(fake.called("apply_settings")))
    sent = cast(CompanionSettings, fake.called("apply_settings")[-1][0])
    assert (sent.tts_engine, sent.tts_url, sent.tts_voice, sent.tts_token) == (
        "external",
        "http://127.0.0.1:9100",
        "narrator",
        "s3cret",
    )
    assert "s3cret" not in repr(sent)


def test_saved_provider_settings_load_into_the_tab(ui: CompanionUi, fake: FakeController) -> None:
    fake.set_settings(
        replace(fake.settings(), tts_engine="external", tts_url="http://127.0.0.1:7000", tts_voice="x", tts_token="t")
    )
    page = _open(ui).voice_page
    assert page.tts_engine.currentData() == "external"
    assert (page.tts_url.text(), page.voice_id(), page.tts_token.text()) == ("http://127.0.0.1:7000", "x", "t")
    assert "x" in _items(page)  # a saved voice the provider did not list stays selectable


def test_load_voices_fills_the_picker_from_the_provider(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    page = _open(ui).voice_page
    _choose_provider(page)
    page.tts_url.setText("http://127.0.0.1:9100")
    page.load_voices_button.click()
    assert process_events(lambda: bool(fake.called("tts_voices")))
    assert fake.called("tts_voices")[-1] == ("http://127.0.0.1:9100",)  # the address as typed, not yet applied
    assert process_events(lambda: "Anna (a)" in _items(page))
    assert _items(page) == ["Provider default", "Anna (a)", "Ben (b)"]
    assert "Connected to Demo provider" in page.status.text()
    assert page.load_voices_button.isEnabled()
    page.tts_voice.setCurrentIndex(2)
    assert page.voice_id() == "b"


def test_a_voice_typed_before_loading_is_selected_by_name_after_it(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    page = _open(ui).voice_page
    _choose_provider(page)
    page.tts_voice.setEditText("b")
    page.load_voices_button.click()
    assert process_events(lambda: "Ben (b)" in _items(page))
    assert page.voice_id() == "b" and page.tts_voice.currentText() == "Ben (b)"  # now shown by name


def test_test_voice_asks_the_controller_to_speak_with_the_chosen_voice(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.tts_probe = TtsProbe(True, "Playing a test sentence from Demo provider.", "Demo provider")
    page = _open(ui).voice_page
    _choose_provider(page)
    page.tts_voice.setEditText("a")
    page.test_button.click()
    assert not page.test_button.isEnabled()  # busy while the provider answers
    assert process_events(lambda: bool(fake.called("test_tts")))
    assert fake.called("test_tts")[-1] == ("a",)
    assert process_events(lambda: "Playing a test sentence" in page.status.text())
    assert page.test_button.isEnabled()


def test_a_provider_problem_is_shown_and_nothing_is_changed(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.tts_probe = TtsProbe(False, "Could not reach the provider at http://127.0.0.1:8765. Is it running?")
    page = _open(ui).voice_page
    _choose_provider(page)
    page.test_button.click()
    assert process_events(lambda: "Could not reach the provider" in page.status.text())
    assert page.test_button.isEnabled() and _items(page) == ["Provider default"]


def test_the_external_engine_mode_disables_the_tab(ui: CompanionUi, fake: FakeController) -> None:
    dialog = _open(ui)
    general = dialog.general_page
    allowed = [general.engine_mode.itemData(i) for i in range(general.engine_mode.count())]
    assert "external" in allowed
    general.engine_mode.setCurrentIndex(general.engine_mode.findData("external"))
    assert not dialog.voice_page._box.isEnabled()  # that engine is not started by this app
    general.engine_mode.setCurrentIndex(0)
    assert dialog.voice_page._box.isEnabled()


# --- hundreds of voices ------------------------------------------------------------------------------------


def _many_voices() -> tuple[Voice, ...]:
    names = [f"Voice {index:03d}" for index in range(300, 0, -1)]  # listed out of order
    return (*(Voice(f"v{name[-3:]}", name) for name in names), Voice("zz", "Zed", default=True))


def test_the_picker_is_sorted_by_name_with_the_default_first(ui: CompanionUi) -> None:
    page = _open(ui).voice_page
    page.set_voices(_many_voices(), "")
    items = _items(page)
    assert items[:3] == ["Provider default", "Zed (zz)", "Voice 001 (v001)"]
    assert items[-1] == "Voice 300 (v300)" and len(items) == 302
    assert page.voice_id() == ""


def test_typing_part_of_a_name_filters_the_picker(ui: CompanionUi, process_events: Callable[..., bool]) -> None:
    page = _open(ui).voice_page
    _choose_provider(page)
    page.set_voices(_many_voices(), "")
    completer = page.tts_voice.completer()
    assert completer is not None
    completer.setCompletionPrefix("E 12")  # any part of the name, any case
    found = [completer.completionModel().index(row, 0).data() for row in range(completer.completionCount())]
    assert "Voice 125 (v125)" in found and "Voice 012 (v012)" not in found
    assert len(found) == 10 and all("e 12" in str(text) for text in found)
    page.tts_voice.setEditText("Voice 125 (v125)")  # what picking it from the popup leaves in the field
    assert page.voice_id() == "v125"


# --- the assistant's voice ---------------------------------------------------------------------------------


def _assistant(ui: CompanionUi) -> tuple[SettingsDialog, AssistantPage]:
    dialog = _open(ui)
    page = dialog.assistant_page
    page.mode.setCurrentIndex(page.mode.findData("own_session"))
    return dialog, page


def test_the_assistant_voice_loads_from_the_engine_and_is_applied(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog, page = _assistant(ui)
    assert page.speak_replies.isChecked() and page.voice.isEnabled()
    page.load_voices_button.click()
    assert process_events(lambda: bool(fake.called("assistant_voices")))
    assert process_events(lambda: page.voice.count() == 4)
    names = [page.voice.itemText(index) for index in range(page.voice.count())]
    assert names == ["Engine default", "Dora (pf_dora)", "Alex (pm_alex)", "Santa (pm_santa)"]
    assert "Voices found: 3" in page.voice_status.text()
    page.voice.setCurrentIndex(2)
    dialog.apply(close_after=False)
    assert process_events(lambda: fake.settings().assistant.voice == "pm_alex")
    assert fake.settings().assistant.speak_replies is True


def test_the_test_button_speaks_with_the_chosen_voice(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    _dialog, page = _assistant(ui)
    page.voice.setEditText("pm_santa")
    page.test_voice_button.click()
    assert process_events(lambda: bool(fake.called("test_assistant_voice")))
    assert fake.called("test_assistant_voice")[-1] == ("pm_santa",)
    assert process_events(lambda: page.test_voice_button.isEnabled())


def test_an_engine_without_a_voice_says_why_and_speak_replies_off_disables_the_picker(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.assistant_voice_probe = TtsProbe(False, "No voice available: the engine is not running")
    _dialog, page = _assistant(ui)
    page.load_voices_button.click()
    assert process_events(lambda: "not running" in page.voice_status.text())
    assert page.voice.count() == 1  # only the engine default
    page.speak_replies.setChecked(False)
    assert not page.voice.isEnabled() and not page.test_voice_button.isEnabled()
    assert page.collect().speak_replies is False


def test_a_saved_assistant_voice_loads_into_the_tab(ui: CompanionUi, fake: FakeController) -> None:
    saved = AssistantSettings(mode="own_session", speak_replies=False, voice="pm_alex")
    fake.set_settings(replace(fake.settings(), assistant=saved))
    page = _open(ui).assistant_page
    assert not page.speak_replies.isChecked()
    assert page.voice.currentText() == "pm_alex"
    assert page.collect().voice == "pm_alex"
