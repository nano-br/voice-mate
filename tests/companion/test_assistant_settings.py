"""The [assistant] table of companion.toml and the wiring of the assistant service in the controller."""

from __future__ import annotations

import operator
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest
from companion_core_fakes import ControllerKit, FakeDaemon, FakeDesktopParts, use_english, wait_until

from app.companion.assistant.contract import AssistantListener, AssistantSnapshot, Unsubscribe
from app.companion.contract import AssistantSettings, CompanionSettings, HotkeyBinding
from app.companion.controller import AssistantHost
from app.companion.settings_store import dump_settings, normalized, parse_settings, validate_settings


@pytest.fixture(autouse=True)
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    use_english(monkeypatch)


def test_the_assistant_is_off_by_default() -> None:
    assistant = CompanionSettings().assistant
    assert assistant == AssistantSettings()
    assert assistant.mode == "off"
    assert (assistant.model, assistant.effort) == ("haiku", "low")
    assert assistant.voice_commands is True


def test_round_trip_through_the_toml(tmp_path: Path) -> None:
    vault = tmp_path / "persona.md"
    vault.write_text("# me", encoding="utf-8")
    assistant = AssistantSettings(
        mode="own_session",
        model="opus",
        effort="high",
        voice_commands=False,
        expressions="on",
        user_name='Alli "A" T',
        personality_file=str(vault),
    )
    text = dump_settings(CompanionSettings(client_key="k" * 16, assistant=assistant))
    assert "\n[assistant]\n" in text
    result = parse_settings(text)
    assert result.problems == ()
    assert result.settings.assistant == assistant


def test_a_file_without_the_table_reads_the_defaults() -> None:
    result = parse_settings('version = 1\nlanguage = "en"\n')
    assert result.settings.assistant == AssistantSettings()
    assert result.problems == ()


def test_invalid_values_fall_back_with_a_log_line() -> None:
    text = (
        'version = 1\n[assistant]\nmode = "desktop_ui"\nmodel = "gpt"\neffort = 3\n'
        'voice_commands = "yes"\nexpressions = "loud"\nuser_name = 7\nbogus = 1\n'
    )
    result = parse_settings(text)
    assert result.settings.assistant == AssistantSettings()
    joined = "\n".join(result.problems)
    for key in ("mode", "model", "effort", "voice_commands", "expressions", "user_name", "bogus"):
        assert key in joined


def test_assistant_must_be_a_table() -> None:
    result = parse_settings('version = 1\nassistant = "on"\n')
    assert result.settings.assistant == AssistantSettings()
    assert any("assistant" in problem for problem in result.problems)


def test_personality_file_must_be_a_markdown_file_that_exists_when_applying(tmp_path: Path) -> None:
    good = tmp_path / "p.md"
    good.write_text("x", encoding="utf-8")
    txt = tmp_path / "p.txt"
    txt.write_text("x", encoding="utf-8")
    missing = str(tmp_path / "missing.md")
    # A missing .md file survives the read (its folder may just be unavailable): only applying complains.
    kept = parse_settings(f'version = 1\n[assistant]\npersonality_file = "{Path(missing).as_posix()}"\n')
    assert kept.settings.assistant.personality_file == Path(missing).as_posix()
    assert not kept.problems
    for bad in (missing, str(txt), str(tmp_path)):
        if bad != missing:
            result = parse_settings(f'version = 1\n[assistant]\npersonality_file = "{Path(bad).as_posix()}"\n')
            assert result.settings.assistant.personality_file == ""
            assert any("personality_file" in problem for problem in result.problems)
        errors = validate_settings(
            CompanionSettings(assistant=AssistantSettings(personality_source="file", personality_file=bad))
        )
        assert errors == ["The personality file must be an existing Markdown (.md) file."]
    ok = parse_settings(f'version = 1\n[assistant]\npersonality_file = "{good.as_posix()}"\n')
    assert ok.settings.assistant.personality_file == good.as_posix()
    good_file = AssistantSettings(personality_source="file", personality_file=str(good))
    assert validate_settings(CompanionSettings(assistant=good_file)) == []
    assert validate_settings(CompanionSettings()) == []


def test_validate_rejects_a_value_outside_the_enums() -> None:
    bad = replace(AssistantSettings(), model="gpt")  # type: ignore[arg-type]
    errors = validate_settings(CompanionSettings(assistant=bad))
    assert errors == ["Invalid value for Assistant model."]


def test_normalized_trims_the_name_and_the_path() -> None:
    settings = CompanionSettings(assistant=AssistantSettings(user_name="  Alli ", personality_file=" x.md "))
    assert normalized(settings).assistant.user_name == "Alli"
    assert normalized(settings).assistant.personality_file == "x.md"


def test_the_assistant_voice_settings_round_trip_and_default_to_speaking() -> None:
    assert (AssistantSettings().speak_replies, AssistantSettings().voice) == (True, "")
    assistant = AssistantSettings(speak_replies=False, voice="narrator")
    result = parse_settings(dump_settings(CompanionSettings(client_key="k" * 16, assistant=assistant)))
    assert result.problems == ()
    assert (result.settings.assistant.speak_replies, result.settings.assistant.voice) == (False, "narrator")


def test_a_bad_assistant_voice_falls_back_and_is_refused_when_applying() -> None:
    result = parse_settings('version = 1\n[assistant]\nspeak_replies = "no"\nvoice = 3\n')
    assert (result.settings.assistant.speak_replies, result.settings.assistant.voice) == (True, "")
    joined = "\n".join(result.problems)
    assert "speak_replies" in joined and "voice" in joined
    errors = validate_settings(CompanionSettings(assistant=AssistantSettings(voice="two\nlines")))
    assert errors == ["The assistant voice cannot contain line breaks or other control characters."]
    assert normalized(CompanionSettings(assistant=AssistantSettings(voice=" a "))).assistant.voice == "a"


# --- the controller ------------------------------------------------------------------------------


class RecordingAssistant:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def start(self) -> None:
        self.calls.append(("start", None))

    def stop(self) -> None:
        self.calls.append(("stop", None))

    def snapshot(self) -> AssistantSnapshot:
        return AssistantSnapshot()

    def subscribe(self, listener: AssistantListener) -> Unsubscribe:
        return lambda: None

    def apply_settings(self, settings: AssistantSettings) -> None:
        self.calls.append(("apply_settings", settings))

    def send(self, text: str) -> None:
        self.calls.append(("send", text))

    def restart(self) -> None:
        self.calls.append(("restart", None))

    def follow_assistant(self) -> None:
        self.calls.append(("follow_assistant", None))

    def back_to_assistant(self) -> None:
        self.calls.append(("back_to_assistant", None))

    def show_window(self) -> None:
        self.calls.append(("show_window", None))

    def hear_ready(self) -> None:
        self.calls.append(("hear_ready", None))

    def clear_inbox(self) -> None:
        self.calls.append(("clear_inbox", None))

    def refresh_context(self) -> None:
        self.calls.append(("refresh_context", None))

    def check_profile(self) -> None:
        self.calls.append(("check_profile", None))

    def import_profile(self, path: str) -> None:
        self.calls.append(("import_profile", path))

    def export_profile(self, path: str) -> None:
        self.calls.append(("export_profile", path))

    def use_default_profile(self) -> None:
        self.calls.append(("use_default_profile", None))


@pytest.fixture
def kit(tmp_path: Path) -> Iterator[ControllerKit]:
    built = ControllerKit(tmp_path)
    yield built
    built.close()


@pytest.fixture
def daemon() -> Iterator[FakeDaemon]:
    fake = FakeDaemon().start()
    yield fake
    fake.stop()


def _build(
    kit: ControllerKit, daemon: FakeDaemon, settings: CompanionSettings | None = None
) -> tuple[object, RecordingAssistant, list[Callable[[], object]]]:
    assistant = RecordingAssistant()
    factories: list[Callable[[], object]] = []

    def factory(host: AssistantHost) -> RecordingAssistant:
        factories.append(host.settings)
        return assistant

    controller, _parts, _backend = kit(daemon, assistant_factory=factory, settings=settings)
    return controller, assistant, factories


def test_the_controller_starts_applies_and_stops_the_assistant(kit: ControllerKit, daemon: FakeDaemon) -> None:
    controller, assistant, factories = _build(kit, daemon)
    assert controller.assistant() is assistant  # type: ignore[attr-defined]
    assert factories[0]() == controller.settings()  # type: ignore[attr-defined]

    controller.start()  # type: ignore[attr-defined]
    assert wait_until(lambda: ("start", None) in assistant.calls)

    edited = replace(controller.settings(), assistant=AssistantSettings(mode="own_session", model="sonnet"))  # type: ignore[attr-defined]
    assert controller.apply_settings(edited) == []  # type: ignore[attr-defined]
    assert wait_until(lambda: ("apply_settings", edited.assistant) in assistant.calls)

    # A change outside [assistant] only refreshes assistant.json (its settings catalog).
    count = len(assistant.calls)
    other = replace(controller.settings(), master_volume=0.5)  # type: ignore[attr-defined]
    assert controller.apply_settings(other) == []  # type: ignore[attr-defined]
    assert wait_until(lambda: len(assistant.calls) > count)
    assert assistant.calls[count:] == [("refresh_context", None)]

    # A new interface language reaches the assistant too, so it rewrites assistant.json.
    count = len(assistant.calls)
    spanish = replace(controller.settings(), language="es")  # type: ignore[attr-defined]
    assert controller.apply_settings(spanish) == []  # type: ignore[attr-defined]
    assert wait_until(lambda: len(assistant.calls) > count)
    assert assistant.calls[-1] == ("apply_settings", spanish.assistant)

    import threading

    done = threading.Event()
    controller.quit(done.set)  # type: ignore[attr-defined]
    assert done.wait(10)
    assert ("stop", None) in assistant.calls


def _assistant_settings(**changes: object) -> CompanionSettings:
    assistant = replace(
        AssistantSettings(mode="own_session", mode_hotkey="ctrl+alt+shift+f20", back_hotkey="ctrl+alt+shift+f19"),
        **changes,  # type: ignore[arg-type]
    )
    return CompanionSettings(
        engine_mode="wsl2",
        client_key="test-client-key-0001",
        hotkeys=(
            HotkeyBinding("clipboard", "ctrl+alt+shift+f23"),
            HotkeyBinding("claude_chat", "ctrl+alt+shift+f22"),
        ),
        assistant=assistant,
    )


def test_the_assistant_hotkeys_are_held_only_while_the_assistant_is_on(kit: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts()
    assistant = RecordingAssistant()
    controller, _parts, _backend = kit(
        daemon, parts=parts, settings=_assistant_settings(), assistant_factory=lambda _host: assistant
    )
    controller.start()
    hotkeys = parts.hotkeys
    assert hotkeys is not None
    assert wait_until(lambda: "assistant_talk" in hotkeys.held)
    assert hotkeys.held["assistant_talk"] == "ctrl+alt+s"
    assert hotkeys.held["assistant_window"] == "ctrl+alt+w"
    assert hotkeys.held["assistant_digest"] == "ctrl+alt+r"
    assert hotkeys.held["assistant_mode"] == "ctrl+alt+shift+f20"

    # A chord of an assistant action counts as taken for the other rows.
    assert controller.check_hotkey("ctrl+alt+w", "clipboard") == "duplicate"
    assert controller.check_hotkey("ctrl+alt+w", "assistant_window") == "ok"

    off = replace(controller.settings(), assistant=replace(controller.settings().assistant, mode="off"))
    assert controller.apply_settings(off) == []
    assert wait_until(lambda: "assistant_talk" not in hotkeys.held)
    assert "clipboard" in hotkeys.held


def test_the_assistant_hotkeys_reach_the_service(kit: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts()
    assistant = RecordingAssistant()
    controller, _parts, _backend = kit(
        daemon,
        parts=parts,
        settings=_assistant_settings(desktop_conversation="Central"),
        assistant_factory=lambda _host: assistant,
    )
    controller.start()
    hotkeys = parts.hotkeys
    assert hotkeys is not None
    assert wait_until(lambda: controller._poller.session() is not None and "assistant_window" in hotkeys.held)
    for action, call in (
        ("assistant_window", "show_window"),
        ("assistant_digest", "hear_ready"),
        ("assistant_back", "back_to_assistant"),
    ):
        hotkeys.press(action)
        assert wait_until(partial(operator.contains, assistant.calls, (call, None)))

    hotkeys.press("assistant_mode")
    assert wait_until(lambda: controller.settings().assistant.mode == "desktop")
    hotkeys.press("assistant_mode")
    assert wait_until(lambda: controller.settings().assistant.mode == "own_session")


def test_a_dictation_stopped_by_the_talk_hotkey_goes_to_the_assistant(kit: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts()
    assistant = RecordingAssistant()
    controller, _parts, _backend = kit(
        daemon, parts=parts, settings=_assistant_settings(), assistant_factory=lambda _host: assistant
    )
    controller.start()
    hotkeys = parts.hotkeys
    assert hotkeys is not None
    assert wait_until(lambda: controller._poller.session() is not None and "assistant_talk" in hotkeys.held)
    assert wait_until(lambda: controller.snapshot().supervisor == "healthy")

    hotkeys.press("assistant_talk")
    assert wait_until(lambda: len(daemon.triggers) == 1)
    assert daemon.triggers[0]["flow"] == "clipboard"
    daemon.go_live()
    hotkeys.press("assistant_talk")  # the stop decides where the text goes
    assert wait_until(lambda: len(daemon.triggers) == 2)
    threading.Event().wait(0.2)
    seq = daemon.publish_result("what is on my calendar")["data"]["result_seq"]
    daemon.set_state("idle")
    assert wait_until(lambda: ("send", "what is on my calendar") in assistant.calls)
    assert wait_until(lambda: daemon.ack_status(seq) == "delivered")
    assert parts.clipboard.texts == []

    # Started by the talk hotkey, stopped by the dictation hotkey: the clipboard gets it.
    hotkeys.press("assistant_talk")
    assert wait_until(lambda: len(daemon.triggers) == 3)
    daemon.go_live()
    hotkeys.press("clipboard")
    assert wait_until(lambda: len(daemon.triggers) == 4)
    threading.Event().wait(0.2)
    daemon.publish_result("plain dictation")
    daemon.set_state("idle")
    assert wait_until(lambda: parts.clipboard.texts == ["plain dictation"])
    assert ("send", "plain dictation") not in assistant.calls


def test_the_microphone_button_of_the_assistant_window_is_the_talk_hotkey(
    kit: ControllerKit, daemon: FakeDaemon
) -> None:
    parts = FakeDesktopParts()
    assistant = RecordingAssistant()
    controller, _parts, _backend = kit(
        daemon, parts=parts, settings=_assistant_settings(), assistant_factory=lambda _host: assistant
    )
    controller.start()
    assert wait_until(lambda: controller.snapshot().supervisor == "healthy")
    controller.talk()
    assert wait_until(lambda: len(daemon.triggers) == 1)
    daemon.go_live()
    controller.talk()
    assert wait_until(lambda: len(daemon.triggers) == 2)
    threading.Event().wait(0.2)
    daemon.publish_result("turn on the lights")
    daemon.set_state("idle")
    assert wait_until(lambda: ("send", "turn on the lights") in assistant.calls)
    assert parts.clipboard.texts == []


# --- wave 3 fields ---------------------------------------------------------------------------------


def test_round_trip_keeps_the_wave_3_fields(tmp_path: Path) -> None:
    persona = tmp_path / "me.md"
    persona.write_text("# me", encoding="utf-8")
    assistant = AssistantSettings(
        mode="desktop",
        desktop_conversation="VoiceMate central",
        permission_mode="acceptEdits",
        skill_language="pt-BR",
        custom_skill_dir="C:/skills/mine",
        answer_delivery="sound",
        personality_source="file",
        personality_file=str(persona),
        obsidian_vault="C:/vault",
        new_conversation_model="opus",
        new_conversation_effort="high",
        desktop_profile_auto_update=False,
        desktop_profile_override="C:/profiles/mine.json",
        talk_hotkey="ctrl+alt+s",
        window_hotkey="ctrl+alt+w",
        mode_hotkey="ctrl+alt+m",
        back_hotkey="ctrl+alt+b",
        digest_hotkey="ctrl+alt+r",
    )
    result = parse_settings(dump_settings(CompanionSettings(client_key="k" * 16, assistant=assistant)))
    assert result.problems == ()
    assert result.settings.assistant == assistant


@pytest.mark.parametrize(
    ("table", "source"),
    [
        ('personality_file = "C:/me.md"\n', "file"),
        ("", "none"),
        ('personality_file = "C:/me.md"\npersonality_source = "custom"\n', "custom"),
    ],
)
def test_the_personality_source_of_older_files(table: str, source: str) -> None:
    result = parse_settings(f"version = 1\n[assistant]\n{table}")
    assert result.settings.assistant.personality_source == source


def test_the_personality_file_is_checked_only_for_the_file_source(tmp_path: Path) -> None:
    missing = str(tmp_path / "gone.md")
    custom = AssistantSettings(personality_source="custom", personality_file=missing)
    assert validate_settings(CompanionSettings(assistant=custom)) == []
    chosen = AssistantSettings(personality_source="file")
    assert validate_settings(CompanionSettings(assistant=chosen)) == [
        "Choose the personality file, or another personality option."
    ]


def test_normalized_trims_the_vault_and_the_profile_override() -> None:
    assistant = AssistantSettings(obsidian_vault=" C:/vault ", desktop_profile_override=" C:/p.json ")
    cleaned = normalized(CompanionSettings(assistant=assistant)).assistant
    assert (cleaned.obsidian_vault, cleaned.desktop_profile_override) == ("C:/vault", "C:/p.json")
