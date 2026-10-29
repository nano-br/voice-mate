"""The assistant service: a fake port, real transcripts in a temp Claude home."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from app.companion.assistant.contract import AssistantSnapshot, PortListener, PortStatus
from app.companion.assistant.inbox import Inbox
from app.companion.assistant.notifier import EVENT_MARKER, ReadyAnswer
from app.companion.assistant.service import AssistantServiceImpl, is_internal_user_text
from app.companion.assistant.session_reader import ConversationInfo
from app.companion.assistant.skill_install import AssistantContext, PackagedLanguage, SkillSource
from app.companion.contract import AssistantSettings, CompanionSettings
from app.companion.cues import CueName
from app.companion.win.desktop_profile import DesktopProfile, ProfileError, UpdateResult, load_packaged

ON = AssistantSettings(mode="own_session")


class FakePort:
    def __init__(self) -> None:
        self.listener: PortListener | None = None
        self.sent: list[str] = []
        self.started = 0
        self.stopped = 0
        self.stop_s = 0.0
        self.accepts = True
        self.models: list[str] = []
        self.live_model = True
        self._status = PortStatus()

    def start(self, on_change: PortListener) -> None:
        self.started += 1
        self.listener = on_change
        self.set(PortStatus(state="starting"))

    def set(self, status: PortStatus) -> None:
        self._status = status
        if self.listener is not None:
            self.listener(status)

    def send(self, text: str) -> bool:
        if self.accepts:
            self.sent.append(text)
        return self.accepts

    def status(self) -> PortStatus:
        return self._status

    def stop(self) -> None:
        time.sleep(self.stop_s)
        self.stopped += 1

    def set_model(self, model: str) -> bool:
        if self.live_model:
            self.models.append(model)
        return self.live_model


class FakeOwned:
    """The conversations the assistant opened: only what the service calls."""

    def __init__(self) -> None:
        self.owned: set[str] = set()
        self.sent: list[tuple[str, str]] = []
        self.modes: list[str] = []
        self.stop_s = 0.0

    def set_permission_mode(self, mode: str) -> None:
        self.modes.append(mode)

    def resume_all(self) -> list[str]:
        return []

    def is_owned(self, conversation_id: str) -> bool:
        return conversation_id in self.owned

    def send(self, conversation_id: str, text: str) -> None:
        self.sent.append((conversation_id, text))

    def stop_all(self) -> None:
        time.sleep(self.stop_s)


class FakeVoice:
    """The assistant's voice (`VoicePort`): records what it was asked to say."""

    def __init__(self) -> None:
        self.spoken: list[tuple[str, str, bool]] = []  # (reply key, voice text so far, final)
        self.languages: list[str] = []  # the language of each speak call
        self.notices: list[str] = []
        self.stops = 0
        self.listeners: list[Callable[[str], None]] = []

    def speak(self, key: str, text: str, *, final: bool, language: str = "") -> None:
        self.spoken.append((key, text, final))
        self.languages.append(language)

    def notice(self, text: str, language: str) -> None:
        self.notices.append(text)

    def stop(self) -> None:
        self.stops += 1

    def on_unavailable(self, listener: Callable[[str], None]) -> None:
        self.listeners.append(listener)


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.error: Exception | None = None

    def send(self, title: str, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((title, text))


class FakeProfiles:
    """The Desktop profile manager: the packaged profile, no network."""

    def __init__(self) -> None:
        self.auto_update = True
        self.last_error = ""
        self.profile = load_packaged()
        self.where = "packaged"
        self.checks: list[bool] = []
        self.result = UpdateResult("unchanged", version=self.profile.profile_version)
        self.imported: list[Path] = []
        self.import_error: Exception | None = None
        self.exported: list[Path] = []
        self.cleared = 0

    def current(self) -> DesktopProfile:
        return self.profile

    def source(self) -> str:
        return self.where

    def check_for_update(self, timeout: float = 5.0, *, manual: bool = False) -> UpdateResult:
        self.checks.append(manual)
        return self.result

    def import_profile(self, path: Path) -> DesktopProfile:
        if self.import_error is not None:
            raise self.import_error
        self.imported.append(path)
        self.where = "override"
        return self.profile

    def export_profile(self, path: Path) -> None:
        self.exported.append(path)

    def clear_override(self) -> None:
        self.cleared += 1
        self.where = "packaged"


class Rig:
    def __init__(
        self,
        claude_home: Path,
        settings: AssistantSettings = ON,
        language: str = "en",
        *,
        profiles: FakeProfiles | None = None,
        voice: FakeVoice | None = None,
    ) -> None:
        self.claude_home = claude_home
        self.settings = settings
        self.companion = CompanionSettings()
        self.language = language
        self.ports: list[FakePort] = []
        self.installs: list[tuple[AssistantSettings, str]] = []
        self.install_error: OSError | None = None
        self.titles: list[ConversationInfo] = []
        self.snapshots: list[AssistantSnapshot] = []
        self.contexts: list[AssistantContext] = []
        self.owned = FakeOwned()
        self.sender = FakeSender()
        self.profiles = profiles
        self.busy = False
        self.cues: list[CueName] = []
        self.spoken: list[str] = []
        self.apply_errors: list[str] = []
        self.inbox = Inbox(claude_home.parent / "inbox.json")
        self.skill_language: PackagedLanguage | None = None  # what the installer reports

        def make_port(_settings: AssistantSettings) -> FakePort:
            port = FakePort()
            self.ports.append(port)
            return port

        def install(settings: AssistantSettings, language: str) -> SkillSource | None:
            if self.install_error is not None:
                raise self.install_error
            self.installs.append((settings, language))
            if self.skill_language is None:
                return None
            return SkillSource(claude_home / "skill", self.skill_language)

        def apply(new: CompanionSettings) -> list[str]:
            # What the controller does: save, then hand the assistant part to the service.
            if self.apply_errors:
                return list(self.apply_errors)
            self.companion = new
            self.settings = new.assistant
            self.service.apply_settings(new.assistant)
            return []

        self.service = AssistantServiceImpl(
            lambda: self.settings,
            lambda: self.language,
            companion_settings=lambda: replace(self.companion, assistant=self.settings),
            apply_companion=apply,
            person_busy=lambda: self.busy,
            play_cue=self.cues.append,
            speak=lambda text, _language: self.spoken.append(text),
            voice=voice,
            port_factory=make_port,
            installer=install,
            context_writer=self.contexts.append,
            conversations=lambda _limit: self.titles,
            owned_factory=lambda _mode: cast(Any, self.owned),
            desktop_factory=lambda _profile: self.sender,
            profile_factory=(lambda *_args: cast(Any, profiles)) if profiles is not None else None,
            voicemate_version="9.9.9",
            personality_path=claude_home.parent / "personality.md",
            inbox=self.inbox,
            pending_path=claude_home.parent / "waiting.json",
            poll_s=0.02,
        )
        self.service.subscribe(self.snapshots.append)

    def ready(self, session_id: str = "s1") -> None:
        """The central is up: started, with its transcript on disk."""
        self.service.start()
        self.wait(lambda s: s.state == "starting")
        path = self.transcript(session_id)
        if not path.exists():
            _append(path, _user("hi", f"{session_id}-u0"))
        self.port.set(PortStatus(state="ready", session_id=session_id))
        self.wait(lambda s: s.state == "ready" and bool(s.messages))

    @property
    def port(self) -> FakePort:
        return self.ports[-1]

    def wait(self, predicate: Callable[[AssistantSnapshot], bool], timeout: float = 3.0) -> AssistantSnapshot:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = self.service.snapshot()
            if predicate(snapshot):
                return snapshot
            time.sleep(0.01)
        raise AssertionError(f"timeout; last snapshot: {self.service.snapshot()}")

    def transcript(self, session_id: str) -> Path:
        folder = self.claude_home / "projects" / "proj"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{session_id}.jsonl"


def _user(text: str, uuid: str) -> str:
    return json.dumps({"type": "user", "uuid": uuid, "message": {"role": "user", "content": text}})


def _assistant(text: str, uuid: str, mid: str = "m1") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "message": {"id": mid, "role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    )


def _append(path: Path, *lines: str) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as file:
        for line in lines:
            file.write(line + "\n")


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    built = Rig(home)
    yield built
    built.service.stop()


def test_off_by_default_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    rig = Rig(tmp_path, AssistantSettings())
    rig.service.start()
    rig.wait(lambda s: s.rev >= 0)
    time.sleep(0.05)
    assert rig.ports == []
    assert rig.installs == []
    assert rig.service.snapshot().state == "off"
    rig.service.stop()


def test_start_installs_with_the_interface_language_and_starts_the_port(rig: Rig) -> None:
    rig.language = "pt-BR"
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    assert rig.installs == [(ON, "pt-BR")]
    assert rig.port.started == 1


def test_install_failure_is_an_error_state(rig: Rig) -> None:
    rig.install_error = OSError("disk full")
    rig.service.start()
    snapshot = rig.wait(lambda s: s.state == "error")
    assert snapshot.error == "setup_failed"
    assert rig.ports == []


def test_port_state_and_session_url_reach_the_snapshot(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.port.set(PortStatus(state="ready", session_id="s1", session_url="https://claude.ai/code/x", remote="on"))
    snapshot = rig.wait(lambda s: s.state == "ready")
    assert snapshot.session_url == "https://claude.ai/code/x"
    assert snapshot.remote == "on"
    rig.port.set(PortStatus(state="error", error="cli_missing"))
    assert rig.wait(lambda s: s.state == "error").error == "cli_missing"


def test_messages_come_from_the_transcript_including_replies_typed_elsewhere(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    path = rig.transcript("s1")
    _append(
        path,
        _user("/voicemate-assistant", "u0"),
        _assistant("<voice>Oi!</voice><display>Ola, tudo bem?</display>", "a0", "m0"),
    )
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    snapshot = rig.wait(lambda s: len(s.messages) == 1)
    assert snapshot.messages[0].role == "assistant"
    assert snapshot.messages[0].text == "Ola, tudo bem?"  # the slash command is not shown

    # A prompt typed on the phone, and its reply, show up too.
    _append(path, _user("what is open?", "u1"), _assistant("<voice>Two things.</voice>", "a1", "m1"))
    snapshot = rig.wait(lambda s: len(s.messages) == 3)
    assert [m.role for m in snapshot.messages] == ["assistant", "user", "assistant"]
    assert snapshot.messages[1].text == "what is open?"
    assert snapshot.messages[2].text == "Two things."  # no display block: the voice block


def test_a_reply_without_the_template_is_shown_as_plain_text(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    _append(rig.transcript("s1"), _assistant("just text, no blocks", "a1"))
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    message = rig.wait(lambda s: len(s.messages) == 1).messages[0]
    assert message.degraded
    assert message.text == "just text, no blocks"


def test_only_the_last_30_messages_are_kept(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    _append(rig.transcript("s1"), *[_user(f"msg {i}", f"u{i}") for i in range(45)])
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    snapshot = rig.wait(lambda s: len(s.messages) == 30 and s.messages[-1].text == "msg 44")
    assert snapshot.messages[0].text == "msg 15"


def test_lines_of_one_reply_are_joined(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    path = rig.transcript("s1")
    _append(path, _assistant("<voice>One ", "a1", "m9"), _assistant("two</voice>", "a2", "m9"))
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    assert rig.wait(lambda s: len(s.messages) == 1).messages[0].text == "One two"


def test_plain_text_goes_to_the_assistant(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.service.send("  hello  ")
    deadline = time.monotonic() + 3
    while not rig.port.sent and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rig.port.sent == ["hello"]


def test_send_restarts_a_dead_conversation(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    first = rig.port
    first.accepts = False
    rig.service.send("hello")
    deadline = time.monotonic() + 3
    while len(rig.ports) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(rig.ports) == 2
    assert first.stopped == 1
    assert rig.port.sent == ["hello"]


def test_send_is_ignored_when_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    rig = Rig(tmp_path, AssistantSettings())
    rig.service.send("hello")
    time.sleep(0.1)
    assert rig.ports == []
    rig.service.stop()


def _info(title: str, session_id: str, path: Path) -> ConversationInfo:
    return ConversationInfo(session_id, path, title, 0.0, "proj")


def test_switch_conversation_follows_another_transcript_and_back(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    _append(rig.transcript("own"), _assistant("<voice>mine</voice>", "a1", "m1"))
    other = rig.transcript("other")
    _append(other, _user("question there", "u1"), _assistant("<voice>answer there</voice>", "a2", "m2"))
    rig.port.set(PortStatus(state="ready", session_id="own"))
    rig.wait(lambda s: [m.text for m in s.messages] == ["mine"])
    rig.titles = [_info("Hotkey work", "other", other), _info("VoiceMate", "own", rig.transcript("own"))]

    rig.service.send("go to the conversation hotkey work")
    snapshot = rig.wait(lambda s: s.followed_title == "Hotkey work" and len(s.messages) == 2)
    assert [m.text for m in snapshot.messages] == ["question there", "answer there"]
    assert rig.port.sent == []  # read-only: nothing was sent anywhere

    rig.service.follow_assistant()
    snapshot = rig.wait(lambda s: s.followed_title == "" and [m.text for m in s.messages] == ["mine"])
    assert snapshot.followed_title == ""


def test_typing_a_prompt_while_following_goes_back_to_the_assistant(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    other = rig.transcript("other")
    _append(other, _user("q", "u1"))
    rig.port.set(PortStatus(state="ready", session_id="own"))
    rig.titles = [_info("Hotkey work", "other", other)]
    rig.service.send("go to the conversation hotkey work")
    rig.wait(lambda s: s.followed_title == "Hotkey work")
    rig.service.send("how are you today")
    rig.wait(lambda s: s.followed_title == "")
    assert rig.port.sent == ["how are you today"]


def test_send_to_conversation_asks_the_assistant_to_deliver_it(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    other = rig.transcript("other")
    rig.titles = [_info("Hotkey work", "other", other)]
    rig.service.send("send to the hotkey work conversation: use the second option")
    deadline = time.monotonic() + 3
    while not rig.port.sent and time.monotonic() < deadline:
        time.sleep(0.01)
    prompt = rig.port.sent[0]
    assert '"Hotkey work"' in prompt
    assert "session id other" in prompt
    assert "use the second option" in prompt


def test_the_delegation_prompt_is_not_shown_as_the_persons_words(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    other = rig.transcript("other")
    rig.titles = [_info("Hotkey work", "other", other)]
    rig.service.send("send to the hotkey work conversation: use the second option")
    snapshot = rig.wait(lambda s: any(m.role == "notice" for m in s.messages))
    assert snapshot.messages[-1].text == 'Asking the assistant to send to "Hotkey work": use the second option'
    deadline = time.monotonic() + 3
    while not rig.port.sent and time.monotonic() < deadline:
        time.sleep(0.01)
    assert is_internal_user_text(rig.port.sent[0])
    own = rig.transcript("s1")
    _append(own, _user(rig.port.sent[0], "u1"), _assistant("<voice>ok</voice><display>Sent</display>", "a1"))
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    rig.wait(lambda s: any(m.role == "assistant" for m in s.messages))
    assert not any(m.role == "user" for m in rig.service.snapshot().messages)


def test_titles_are_only_read_when_the_text_may_be_a_command(rig: Rig) -> None:
    calls: list[int] = []

    def listing(limit: int) -> list[ConversationInfo]:
        calls.append(limit)
        return []

    rig.service._conversations = listing  # type: ignore[attr-defined]
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.service.send("how are you today")
    deadline = time.monotonic() + 3
    while not rig.port.sent and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rig.port.sent == ["how are you today"]
    assert calls == []
    rig.service.send("switch to the hotkey work conversation")
    rig.wait(lambda s: any(m.role == "notice" for m in s.messages))
    assert calls


def test_read_last_answer_shows_the_reply_in_a_notice(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.service.send("read the last answer")
    assert rig.wait(lambda s: bool(s.messages)).messages[-1].text == "There is no answer to show yet."
    _append(rig.transcript("s1"), _assistant("<voice>spoken</voice><display>Shown text</display>", "a1"))
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    rig.wait(lambda s: any(m.role == "assistant" for m in s.messages))
    rig.service.send("read the last answer")
    snapshot = rig.wait(lambda s: s.messages[-1].role == "notice" and "Shown text" in s.messages[-1].text)
    assert snapshot.messages[-1].text == "Last answer: Shown text"
    assert rig.port.sent == []


def test_a_notice_stays_where_it_happened_when_newer_messages_arrive(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    own = rig.transcript("s1")
    _append(own, _assistant("<voice>first</voice><display>First</display>", "a1"))
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    rig.wait(lambda s: any(m.role == "assistant" for m in s.messages))
    rig.service.send("read the last answer")
    rig.wait(lambda s: s.messages[-1].role == "notice")
    _append(own, _user("next question", "u2"), _assistant("<voice>second</voice><display>Second</display>", "a2"))
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    snapshot = rig.wait(lambda s: s.messages[-1].role == "assistant" and "Second" in s.messages[-1].text)
    assert [m.role for m in snapshot.messages] == ["assistant", "notice", "user", "assistant"]


def test_ambiguous_and_not_found_become_notices_and_send_nothing(rig: Rig, tmp_path: Path) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.titles = [_info("Hotkey bug", "a", tmp_path / "a.jsonl"), _info("Hotkey bugs", "b", tmp_path / "b.jsonl")]
    rig.service.send("go to the conversation hotkey bug")
    ambiguous = rig.wait(lambda s: bool(s.messages) and s.messages[-1].role == "notice").messages[-1]
    assert "More than one conversation" in ambiguous.text
    rig.service.send("go to the conversation zebra stripes")
    rig.wait(lambda s: s.messages[-1].text == 'No conversation matches "zebra stripes".')
    assert rig.port.sent == []


def test_commands_are_prompts_when_voice_commands_are_off(rig: Rig) -> None:
    rig.settings = replace(ON, voice_commands=False)
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.service.send("read the last answer")
    deadline = time.monotonic() + 3
    while not rig.port.sent and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rig.port.sent == ["read the last answer"]


def test_stop_speaking_is_a_no_op(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    before = rig.service.snapshot()
    rig.service.send("stop talking")
    time.sleep(0.1)
    assert rig.port.sent == []
    assert rig.service.snapshot().messages == before.messages


def test_effort_change_restarts_and_name_change_only_rewrites_the_settings(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    first = rig.port

    renamed = replace(ON, user_name="Alli")
    rig.settings = renamed
    rig.service.apply_settings(renamed)
    _until(lambda: len(rig.installs) >= 2)
    assert rig.installs[-1][0].user_name == "Alli"
    assert len(rig.ports) == 1

    harder = replace(renamed, effort="high")
    rig.settings = harder
    rig.service.apply_settings(harder)
    _until(lambda: len(rig.ports) == 2)
    assert first.stopped == 1


def test_model_change_in_the_own_session_is_live(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.settings = replace(ON, model="opus")
    rig.service.apply_settings(rig.settings)
    _until(lambda: rig.port.models == ["opus"])
    assert len(rig.ports) == 1
    assert rig.port.stopped == 0


def test_model_change_restarts_when_the_session_cannot_take_it_live(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.port.live_model = False
    rig.settings = replace(ON, model="opus")
    rig.service.apply_settings(rig.settings)
    _until(lambda: len(rig.ports) == 2)
    assert rig.ports[0].stopped == 1


def test_a_language_change_rewrites_the_settings_without_restarting(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.language = "pt-BR"
    rig.service.apply_settings(rig.settings)
    deadline = time.monotonic() + 3
    while rig.installs[-1][1] != "pt-BR" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rig.installs[-1] == (rig.settings, "pt-BR")
    assert len(rig.ports) == 1
    assert rig.port.stopped == 0


def test_turning_it_off_stops_the_conversation_and_clears_the_screen(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    _append(rig.transcript("s1"), _user("hi", "u1"))
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    rig.wait(lambda s: len(s.messages) == 1)
    rig.settings = AssistantSettings()
    rig.service.apply_settings(rig.settings)
    snapshot = rig.wait(lambda s: s.state == "off")
    assert snapshot.messages == ()
    assert rig.ports[0].stopped == 1


def test_restart_starts_a_new_port(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    rig.port.set(PortStatus(state="error", error="exited"))
    rig.wait(lambda s: s.state == "error")
    rig.service.restart()
    deadline = time.monotonic() + 3
    while len(rig.ports) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(rig.ports) == 2
    rig.wait(lambda s: s.state == "starting")


def test_snapshots_are_ordered_and_new_listeners_get_the_current_one(rig: Rig) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    revs = [s.rev for s in rig.snapshots]
    assert revs == sorted(revs)
    late: list[AssistantSnapshot] = []
    rig.service.subscribe(late.append)
    deadline = time.monotonic() + 3
    while not late and time.monotonic() < deadline:
        time.sleep(0.01)
    assert late[0] == rig.service.snapshot()


def test_actions_are_logged_not_dispatched(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    reply = (
        "<voice>ok</voice><actions>"
        '{"version": 1, "actions": [{"type": "focus", "session_id": "x"}, {"type": "notify", "text": "hi"}]}'
        "</actions>"
    )
    stop = json.dumps(
        {
            "type": "assistant",
            "uuid": "a1",
            "message": {
                "id": "m1",
                "role": "assistant",
                "content": [{"type": "text", "text": reply}],
                "stop_reason": "end_turn",
            },
        }
    )
    with caplog.at_level("INFO", logger="app.companion.assistant.service"):
        # The transcript read at the start is history: only a turn that ends afterwards is logged.
        _append(rig.transcript("s1"), _user("hi", "u0"))
        rig.port.set(PortStatus(state="ready", session_id="s1"))
        rig.wait(lambda s: len(s.messages) == 1)
        assert not any("assistant action" in r.message for r in caplog.records)
        _append(rig.transcript("s1"), stop)
        rig.wait(lambda s: len(s.messages) == 2)
        time.sleep(0.1)
    assert any("assistant action (not dispatched): notify" in r.message for r in caplog.records)


def test_internal_user_text_markers() -> None:
    assert is_internal_user_text("/voicemate-assistant")
    assert is_internal_user_text("<command-name>/voicemate-assistant</command-name>")
    assert not is_internal_user_text("what is open?")


# --- wave 3: modes, direct mode, settings by voice, the inbox, the Desktop profile --------------


def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timeout")


def _turn(actions: list[dict[str, object]], uuid: str, mid: str) -> str:
    """A finished reply of the central that carries `actions`."""
    reply = '<voice>ok</voice><actions>{"version": 1, "actions": ' + json.dumps(actions) + "}</actions>"
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "message": {
                "id": mid,
                "role": "assistant",
                "content": [{"type": "text", "text": reply}],
                "stop_reason": "end_turn",
            },
        }
    )


def _notices(snapshot: AssistantSnapshot) -> list[str]:
    return [m.text for m in snapshot.messages if m.role == "notice"]


def _ready_answer(title: str = "Report", cid: str = "c1") -> ReadyAnswer:
    return ReadyAnswer(conversation_id=cid, title=title, answer="All done.", note=f"{EVENT_MARKER} answer of {title}")


@pytest.fixture
def make_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Rig]]:
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    built: list[Rig] = []

    def make(
        settings: AssistantSettings = ON, *, profiles: FakeProfiles | None = None, voice: FakeVoice | None = None
    ) -> Rig:
        rig = Rig(home, settings, profiles=profiles, voice=voice)
        built.append(rig)
        return rig

    yield make
    for rig in built:
        rig.service.stop()


DESKTOP = AssistantSettings(mode="desktop", desktop_conversation="VoiceMate")


def test_desktop_mode_without_a_conversation_is_an_error(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(AssistantSettings(mode="desktop"))
    rig.service.start()
    snapshot = rig.wait(lambda s: s.state == "error")
    assert snapshot.error == "no_conversation"
    assert rig.ports == []


def test_desktop_model_change_is_a_note_for_the_central(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(DESKTOP)
    rig.ready()
    rig.settings = replace(DESKTOP, model="opus", effort="high")
    rig.service.apply_settings(rig.settings)
    _until(lambda: any(EVENT_MARKER in text for text in rig.port.sent))
    note = rig.port.sent[-1]
    assert "model to opus (effort high)" in note
    assert "set_session_model" in note
    assert len(rig.ports) == 1
    assert rig.port.models == []


def test_switching_the_mode_restarts_and_clears_the_conversation(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    rig.settings = DESKTOP
    rig.service.apply_settings(DESKTOP)
    _until(lambda: len(rig.ports) == 2)
    snapshot = rig.wait(lambda s: s.state == "starting")
    assert snapshot.messages == ()
    assert rig.ports[0].stopped == 1


def test_the_context_carries_the_mode_vault_delivery_and_inbox(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(replace(ON, obsidian_vault="C:/vault", answer_delivery="sound"))
    rig.inbox.add("c1", "Report", "All done.")
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    context = rig.contexts[-1]
    assert context.mode == "own_session"
    assert context.notes_vault == "C:/vault"
    assert context.answer_delivery == "sound"
    assert [entry["title"] for entry in context.inbox] == ["Report"]
    assert any(entry["key"] == "assistant.answer_delivery" for entry in context.settings)


def test_refresh_context_rewrites_the_settings_file(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.service.start()
    rig.wait(lambda s: s.state == "starting")
    before = len(rig.contexts)
    rig.service.refresh_context()
    _until(lambda: len(rig.contexts) > before)


def test_direct_mode_sends_verbatim_and_reports_back(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    other = rig.transcript("s2")
    _append(other, _user("earlier", "o1"), _assistant("The build is green.", "o2"))
    rig.titles = [_info("Release notes", "s2", other)]

    rig.service.send("talk directly to the conversation release notes")
    snapshot = rig.wait(lambda s: s.direct_title == "Release notes")
    assert snapshot.followed_title == "Release notes"

    rig.service.send("ship it today")
    _until(lambda: rig.sender.sent == [("Release notes", "ship it today")])
    rig.owned.owned.add("s2")
    rig.service.send("and tag it")
    _until(lambda: rig.owned.sent == [("s2", "and tag it")])
    assert len(rig.sender.sent) == 1

    sent_before = len(rig.port.sent)
    rig.service.send("back to the assistant")
    rig.wait(lambda s: not s.direct_title and not s.followed_title)
    _until(lambda: len(rig.port.sent) > sent_before)
    note = rig.port.sent[-1]
    assert note.startswith(EVENT_MARKER)
    assert '"Release notes" (id s2)' in note
    assert "- ship it today" in note and "- and tag it" in note
    assert "The build is green." in note


def test_a_failed_direct_send_is_a_notice(make_rig: Callable[..., Rig]) -> None:
    from app.companion.win.claude_desktop import ScreenLocked

    rig = make_rig()
    rig.ready()
    rig.titles = [_info("Release notes", "s2", rig.transcript("s2"))]
    rig.service.send("talk directly to the conversation release notes")
    rig.wait(lambda s: s.direct_title == "Release notes")
    rig.sender.error = ScreenLocked()
    rig.service.send("ship it")
    snapshot = rig.wait(lambda s: any("Not sent to" in text for text in _notices(s)))
    assert 'Not sent to "Release notes": the screen is locked' in _notices(snapshot)


def test_back_to_assistant_outside_direct_mode_is_a_no_op(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    sent = len(rig.port.sent)
    rig.service.back_to_assistant()
    time.sleep(0.1)
    assert len(rig.port.sent) == sent


def test_set_setting_action_changes_the_setting_and_tells_the_central(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    _append(
        rig.transcript("s1"),
        _turn([{"type": "set_setting", "key": "assistant.answer_delivery", "value": "silent"}], "a1", "m1"),
    )
    snapshot = rig.wait(lambda s: any(text.startswith("Setting changed") for text in _notices(s)))
    assert rig.settings.answer_delivery == "silent"
    assert "Setting changed: " in _notices(snapshot)[-1]
    _until(lambda: any("Setting change applied: assistant.answer_delivery" in text for text in rig.port.sent))
    assert len(rig.ports) == 1


def test_set_setting_refusals_are_notices_and_notes(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    _append(rig.transcript("s1"), _turn([{"type": "set_setting", "key": "nope.key", "value": 1}], "a1", "m1"))
    rig.wait(lambda s: any(text.startswith("Setting not changed") for text in _notices(s)))
    _until(lambda: any("Setting change refused: nope.key" in text for text in rig.port.sent))

    rig.apply_errors = ["The disk is full."]
    _append(
        rig.transcript("s1"),
        _turn([{"type": "set_setting", "key": "assistant.answer_delivery", "value": "silent"}], "a2", "m2"),
    )
    rig.wait(lambda s: any("The disk is full." in text for text in _notices(s)))
    assert rig.settings.answer_delivery == "announce"


def test_a_setting_that_restarts_reaches_the_next_central(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    first = rig.port
    _append(
        rig.transcript("s1"), _turn([{"type": "set_setting", "key": "assistant.effort", "value": "high"}], "a1", "m1")
    )
    _until(lambda: len(rig.ports) == 2)
    assert not any("Setting change applied" in text for text in first.sent)
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    _until(lambda: any("Setting change applied: assistant.effort" in text for text in rig.port.sent))


def test_speak_delivery_hands_the_answer_to_the_central_when_idle(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(replace(ON, answer_delivery="speak"))
    rig.ready()
    rig.busy = True
    assert rig.service._answer_ready(_ready_answer()) is False  # offered again later
    rig.busy = False
    assert rig.service._answer_ready(_ready_answer()) is True
    assert rig.port.sent[-1] == f"{EVENT_MARKER} answer of Report"
    assert rig.inbox.items() == ()


@pytest.mark.parametrize(
    ("delivery", "cues", "spoken"),
    [
        ("announce", ["answer"], ["The conversation Report has an answer."]),
        ("sound", ["answer"], []),
        ("silent", [], []),
    ],
)
def test_held_answers_go_to_the_inbox(
    make_rig: Callable[..., Rig], delivery: str, cues: list[str], spoken: list[str]
) -> None:
    rig = make_rig(replace(ON, answer_delivery=delivery))  # type: ignore[arg-type]
    rig.ready()
    sent = len(rig.port.sent)
    assert rig.service._answer_ready(_ready_answer()) is True
    snapshot = rig.wait(lambda s: len(s.inbox) == 1)
    assert snapshot.inbox[0].title == "Report"
    time.sleep(0.1)
    assert rig.cues == cues
    assert rig.spoken == spoken
    assert len(rig.port.sent) == sent
    assert [entry["title"] for entry in rig.contexts[-1].inbox] == ["Report"]


def test_alerts_wait_until_the_person_stops_dictating(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(replace(ON, answer_delivery="announce"))
    rig.ready()
    rig.busy = True
    rig.service._answer_ready(_ready_answer())
    rig.wait(lambda s: len(s.inbox) == 1)
    time.sleep(0.1)
    assert rig.cues == [] and rig.spoken == []
    rig.busy = False
    _until(lambda: rig.cues == ["answer"])
    assert rig.spoken == ["The conversation Report has an answer."]


def test_hear_ready_sends_one_digest_and_empties_the_inbox(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(replace(ON, answer_delivery="silent"))
    rig.ready()
    rig.inbox.add("c1", "Report", "All done.")
    rig.inbox.add("c2", "Deploy", "Deployed.")
    rig.service.hear_ready()
    snapshot = rig.wait(lambda s: s.inbox == () and any("going over 2" in text for text in _notices(s)))
    digest = rig.port.sent[-1]
    assert digest.startswith(f"{EVENT_MARKER} digest")
    assert "All done." in digest and "Deployed." in digest
    assert snapshot.inbox == ()
    assert rig.contexts[-1].inbox == []


def test_hear_ready_with_nothing_waiting_says_so(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    sent = len(rig.port.sent)
    rig.service.hear_ready()
    rig.wait(lambda s: "Nothing new yet." in _notices(s))
    assert rig.spoken == ["Nothing new yet."]
    assert len(rig.port.sent) == sent


def test_read_inbox_and_clear_inbox_actions(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    rig.inbox.add("c1", "Report", "All done.")
    _append(rig.transcript("s1"), _turn([{"type": "read_inbox"}], "a1", "m1"))
    _until(lambda: any(text.startswith(f"{EVENT_MARKER} digest") for text in rig.port.sent))
    assert rig.inbox.items() == ()

    rig.inbox.add("c2", "Deploy", "Deployed.")
    _append(rig.transcript("s1"), _turn([{"type": "clear_inbox"}], "a2", "m2"))
    _until(lambda: rig.inbox.items() == ())
    rig.wait(lambda s: s.inbox == ())


def test_show_window_counts_requests(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.service.start()
    rig.service.show_window()
    rig.service.show_window()
    rig.wait(lambda s: s.window_requests == 2)


def test_the_profile_is_checked_once_at_start_and_shown(make_rig: Callable[..., Rig]) -> None:
    profiles = FakeProfiles()
    rig = make_rig(profiles=profiles)
    rig.service.start()
    _until(lambda: profiles.checks == [False])
    snapshot = rig.wait(lambda s: s.profile is not None and not s.profile.checking)
    assert snapshot.profile is not None
    assert snapshot.profile.source == "packaged"
    assert snapshot.profile.version == profiles.profile.profile_version
    rig.service.restart()
    _until(lambda: len(rig.ports) == 2)
    time.sleep(0.1)
    assert profiles.checks == [False]


def test_check_now_is_manual_and_reports(make_rig: Callable[..., Rig]) -> None:
    profiles = FakeProfiles()
    rig = make_rig(profiles=profiles)
    rig.service.start()
    _until(lambda: profiles.checks == [False])
    rig.service.check_profile()
    _until(lambda: profiles.checks == [False, True])
    version = profiles.profile.profile_version
    rig.wait(lambda s: f"The Claude Desktop profile is up to date (version {version})." in _notices(s))
    profiles.result = UpdateResult("error", detail="offline")
    rig.service.check_profile()
    rig.wait(lambda s: any(text.startswith("Could not check") for text in _notices(s)))


def test_auto_update_follows_the_setting(make_rig: Callable[..., Rig]) -> None:
    profiles = FakeProfiles()
    rig = make_rig(profiles=profiles)
    rig.service.start()
    _until(lambda: profiles.checks == [False])
    rig.settings = replace(ON, desktop_profile_auto_update=False)
    rig.service.apply_settings(rig.settings)
    _until(lambda: profiles.auto_update is False)


def test_import_export_and_default_profile(make_rig: Callable[..., Rig], tmp_path: Path) -> None:
    profiles = FakeProfiles()
    rig = make_rig(profiles=profiles)
    rig.service.start()
    chosen = tmp_path / "mine.json"
    rig.service.import_profile(str(chosen))
    rig.wait(lambda s: any(text.startswith("Claude Desktop profile imported") for text in _notices(s)))
    assert profiles.imported == [chosen]
    assert rig.settings.desktop_profile_override == str(chosen)

    rig.service.export_profile(str(tmp_path / "out.json"))
    rig.wait(lambda s: any(text.startswith("Claude Desktop profile saved to") for text in _notices(s)))
    assert profiles.exported == [tmp_path / "out.json"]

    rig.service.use_default_profile()
    rig.wait(lambda s: "The default Claude Desktop profile is in use again." in _notices(s))
    assert profiles.cleared == 1
    assert rig.settings.desktop_profile_override == ""

    profiles.import_error = ProfileError("not a profile")
    rig.service.import_profile(str(tmp_path / "bad.json"))
    rig.wait(lambda s: any("was not imported: not a profile" in text for text in _notices(s)))
    assert rig.settings.desktop_profile_override == ""


def test_a_saved_override_is_imported_again_when_its_copy_is_gone(make_rig: Callable[..., Rig], tmp_path: Path) -> None:
    chosen = tmp_path / "mine.json"
    chosen.write_text("{}", encoding="utf-8")
    profiles = FakeProfiles()
    rig = make_rig(replace(ON, desktop_profile_override=str(chosen)), profiles=profiles)
    rig.service.start()
    _until(lambda: profiles.imported == [chosen])


# --- the voice ----------------------------------------------------------------------------------


def _reply(text: str, uuid: str, mid: str, *, end: bool = False) -> str:
    message: dict[str, object] = {"id": mid, "role": "assistant", "content": [{"type": "text", "text": text}]}
    if end:
        message["stop_reason"] = "end_turn"
    return json.dumps({"type": "assistant", "uuid": uuid, "message": message})


def test_a_new_reply_has_its_voice_block_spoken_once(make_rig: Callable[..., Rig]) -> None:
    voice = FakeVoice()
    rig = make_rig(voice=voice)
    rig.ready()
    _append(rig.transcript("s1"), _reply("<voice>Hello there. [laugh] Bye.</voice><display>Hi</display>", "a1", "m1"))
    _until(lambda: bool(voice.spoken))
    assert voice.spoken[0] == ("m1", "Hello there. [laugh] Bye.", True)  # closed block: final, tags kept
    _append(rig.transcript("s1"), _turn([], "a2", "m2"))
    _until(lambda: any(key == "m2" for key, _text, _final in voice.spoken))
    assert [key for key, _text, _final in voice.spoken].count("m1") == 1  # the turn end does not repeat it


def test_replies_already_in_the_transcript_are_not_spoken(make_rig: Callable[..., Rig]) -> None:
    voice = FakeVoice()
    rig = make_rig(voice=voice)
    _append(rig.transcript("s1"), _user("hi", "u0"), _reply("<voice>Old news.</voice>", "a0", "m0", end=True))
    rig.ready()
    time.sleep(0.1)
    assert voice.spoken == []


def test_speak_replies_off_keeps_the_replies_silent(make_rig: Callable[..., Rig]) -> None:
    voice = FakeVoice()
    rig = make_rig(replace(ON, speak_replies=False), voice=voice)
    rig.ready()
    _append(rig.transcript("s1"), _reply("<voice>Quiet.</voice>", "a1", "m1", end=True))
    rig.wait(lambda s: any(m.text == "Quiet." for m in s.messages))
    time.sleep(0.05)
    assert voice.spoken == []


def test_stop_talking_a_new_prompt_and_turning_the_voice_off_stop_it(make_rig: Callable[..., Rig]) -> None:
    voice = FakeVoice()
    rig = make_rig(voice=voice)
    rig.ready()
    rig.service.send("stop talking")
    _until(lambda: voice.stops >= 1)
    assert rig.port.sent == []
    rig.service.send("what is open?")
    _until(lambda: voice.stops >= 2 and rig.port.sent == ["what is open?"])
    before = voice.stops
    rig.service.apply_settings(replace(ON, speak_replies=False))
    _until(lambda: voice.stops > before)


def test_notices_go_through_the_voice_and_an_outage_is_reported_once(make_rig: Callable[..., Rig]) -> None:
    voice = FakeVoice()
    rig = make_rig(replace(ON, answer_delivery="announce"), voice=voice)
    rig.ready()
    rig.service._answer_ready(_ready_answer("Report"))
    _until(lambda: bool(voice.notices))
    assert voice.notices == ["The conversation Report has an answer."]
    assert rig.spoken == []  # not the Windows voice
    assert len(voice.listeners) == 1
    voice.listeners[0]("the engine is not running")
    snapshot = rig.wait(lambda s: bool(_notices(s)) and "No voice available" in _notices(s)[-1])
    assert _notices(snapshot)[-1] == "No voice available: the engine is not running"


def test_a_permission_change_restarts_the_own_central_at_once(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(replace(ON, permission_mode="bypassPermissions"))
    rig.ready()
    lowered = replace(rig.settings, permission_mode="acceptEdits")
    rig.settings = lowered
    rig.service.apply_settings(lowered)
    _until(lambda: len(rig.ports) == 2)
    assert rig.ports[0].stopped == 1
    assert rig.installs[-1][0].permission_mode == "acceptEdits"


def test_full_access_by_voice_is_refused(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    _append(
        rig.transcript("s1"),
        _turn([{"type": "set_setting", "key": "assistant.permission_mode", "value": "bypassPermissions"}], "a1", "m1"),
    )
    rig.wait(lambda s: any(text.startswith("Setting not changed") for text in _notices(s)))
    assert rig.settings.permission_mode == "acceptEdits"
    assert len(rig.ports) == 1


def test_speak_delivery_holds_the_answer_while_the_screen_follows_another_conversation(
    make_rig: Callable[..., Rig],
) -> None:
    rig = make_rig(replace(ON, answer_delivery="speak"))
    rig.ready()
    other = rig.transcript("s2")
    _append(other, _user("q", "o1"), _assistant("a", "o2"))
    rig.titles = [_info("Release notes", "s2", other)]
    rig.service.send("talk directly to the conversation release notes")
    rig.wait(lambda s: s.direct_title == "Release notes")
    sent = len(rig.port.sent)
    assert rig.service._answer_ready(_ready_answer()) is True
    rig.wait(lambda s: len(s.inbox) == 1)
    _until(lambda: rig.cues == ["answer"])
    assert rig.spoken == ["The conversation Report has an answer."]
    assert len(rig.port.sent) == sent  # nothing went to the central, whose answer would not be heard


def test_follow_up_actions_keep_the_waiting_for_list(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    rig.titles = [_info("Release notes", "s2", rig.transcript("s2"))]
    _append(
        rig.transcript("s1"),
        _turn(
            [
                {"type": "follow_up", "session_id": "s2", "reason": "asked for\nthe changelog"},
                {"type": "follow_up", "session_id": "s9", "title": "Spare", "reason": "a check"},
            ],
            "a1",
            "m1",
        ),
    )
    snapshot = rig.wait(lambda s: len(s.pending) == 2)
    assert [(p.conversation_id, p.title, p.reason) for p in snapshot.pending] == [
        ("s2", "Release notes", "asked for the changelog"),
        ("s9", "Spare", "a check"),
    ]
    assert [entry["session_id"] for entry in rig.contexts[-1].pending] == ["s2", "s9"]
    assert {"title", "reason", "since"} <= set(rig.contexts[-1].pending[0])
    _append(rig.transcript("s1"), _turn([{"type": "follow_up_done", "session_id": "s2"}], "a2", "m2"))
    snapshot = rig.wait(lambda s: len(s.pending) == 1)
    assert snapshot.pending[0].conversation_id == "s9"
    assert [entry["session_id"] for entry in rig.contexts[-1].pending] == ["s9"]


def test_the_waiting_for_list_survives_a_restart(make_rig: Callable[..., Rig]) -> None:
    first = make_rig()
    first.ready()
    _append(
        first.transcript("s1"),
        _turn(
            [{"type": "follow_up", "session_id": "s2", "title": "Release notes", "reason": "the changelog"}], "a1", "m1"
        ),
    )
    first.wait(lambda s: len(s.pending) == 1)
    first.service.stop()

    second = make_rig()
    second.service.start()
    snapshot = second.wait(lambda s: len(s.pending) == 1)
    assert [(p.conversation_id, p.title, p.reason) for p in snapshot.pending] == [
        ("s2", "Release notes", "the changelog")
    ]
    _until(lambda: bool(second.contexts))
    assert [entry["session_id"] for entry in second.contexts[-1].pending] == ["s2"]


def test_focus_action_makes_the_screen_follow_a_conversation_and_null_comes_back(
    make_rig: Callable[..., Rig],
) -> None:
    rig = make_rig()
    rig.ready()
    other = rig.transcript("s2")
    _append(other, _user("q", "o1"), _assistant("<voice>there</voice>", "o2", "mo"))
    rig.titles = [_info("Release notes", "s2", other)]
    _append(rig.transcript("s1"), _turn([{"type": "focus", "session_id": "s2", "title": "Release notes"}], "a1", "m1"))
    snapshot = rig.wait(lambda s: s.followed_title == "Release notes")
    assert any(m.text == "there" for m in snapshot.messages)
    rig.service._dispatcher.post(lambda: rig.service._focus(None, None))
    rig.wait(lambda s: not s.followed_title)


def test_the_pt_br_skill_answers_and_speaks_in_pt_br_whatever_the_interface(make_rig: Callable[..., Rig]) -> None:
    voice = FakeVoice()
    rig = make_rig(voice=voice)
    rig.skill_language = "pt-BR"
    rig.language = "en"
    rig.ready()
    assert rig.contexts[-1].language == "pt-BR"
    _append(rig.transcript("s1"), _assistant("<voice>Oi.</voice>", "a1", "m9"))
    _until(lambda: bool(voice.languages))
    assert voice.languages[-1] == "pt-BR"


def test_the_english_skill_follows_the_interface_language(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.skill_language = "en"
    rig.language = "es"
    rig.ready()
    assert rig.contexts[-1].language == "es"


def test_a_digest_whose_desktop_flash_failed_goes_back_to_the_inbox(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(replace(ON, answer_delivery="silent"))
    rig.ready()
    rig.inbox.add("c1", "Report", "All done.")
    rig.service.hear_ready()
    rig.wait(lambda s: any("going over 1" in text for text in _notices(s)))
    digest = rig.port.sent[-1]
    rig.service._dispatcher.post(lambda: rig.service._send_failed(digest))
    snapshot = rig.wait(lambda s: len(s.inbox) == 1)
    assert snapshot.inbox[0].answer == "All done."


def test_a_note_whose_desktop_flash_failed_waits_for_the_next_central(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    note = f"{EVENT_MARKER} something happened"
    rig.service._dispatcher.post(lambda: rig.service._send_failed(note))
    rig.service.restart()
    _until(lambda: len(rig.ports) == 2)
    rig.port.set(PortStatus(state="ready", session_id="s1"))
    _until(lambda: note in rig.port.sent)


def test_stop_stops_the_central_and_the_owned_conversations_at_the_same_time(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.ready()
    rig.owned.stop_s = 0.4
    rig.port.stop_s = 0.4
    began = time.monotonic()
    rig.service.stop()
    assert time.monotonic() - began < 0.75
