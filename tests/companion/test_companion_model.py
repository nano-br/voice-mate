from __future__ import annotations

from typing import Any, cast

import pytest
from companion_core_fakes import use_english

from app.companion.contract import CompanionSettings, CompanionSnapshot, HotkeyBinding, NotifyLevel
from app.companion.delivery import DeliveryJob
from app.companion.model import (
    READY_S,
    CoreState,
    DeliveryOutcome,
    Effects,
    EventSeen,
    HealthSeen,
    MicCountSeen,
    ModelInput,
    NotCopiedSeen,
    NoticeRequested,
    Now,
    ResultSeen,
    SettingsSeen,
    SnapshotSeen,
    Started,
    SupervisorUpdate,
    Tick,
    reduce,
    snippet,
    to_snapshot,
    tray_state,
)
from app.protocol.models import Event, HealthPayload


@pytest.fixture(autouse=True)
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    use_english(monkeypatch)


class Sim:
    """Feeds inputs through the reducer with a fake clock and records the effects."""

    def __init__(self, *, mode: str = "wsl2", notify_level: NotifyLevel = "all") -> None:
        self.mono = 100.0
        self.wall = 1_700_000_000.0
        self.state = CoreState()
        self.cues: list[str] = []
        self.notes: list[tuple[str, str, str]] = []
        settings = CompanionSettings(
            engine_mode=mode,  # type: ignore[arg-type]
            notify_level=notify_level,
            hotkeys=(HotkeyBinding("clipboard", "ctrl+alt+v"), HotkeyBinding("claude_chat", "ctrl+alt+a")),
        )
        self.feed(SettingsSeen(settings, mode == "local"))
        self.feed(Started())

    def now(self) -> Now:
        return Now(self.mono, self.wall)

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += seconds

    def feed(self, message: ModelInput) -> Effects:
        self.state, effects = reduce(self.state, message, self.now())
        if effects.cue is not None:
            self.cues.append(effects.cue)
        self.notes += [(n.level, n.title, n.message) for n in effects.notifications]
        return effects

    def snap(self) -> CompanionSnapshot:
        return to_snapshot(self.state, 1, self.now())

    @property
    def tray(self) -> str:
        return tray_state(self.state, self.mono)

    def healthy(self) -> Sim:
        self.feed(SupervisorUpdate("healthy", 0, None, None, 0, False))
        self.feed(HealthSeen(health()))
        self.feed(MicCountSeen(1))
        return self

    def event(self, kind: str, data: dict[str, Any]) -> Effects:
        return self.feed(EventSeen(cast(Event, {"seq": 1, "ts": 0.0, "type": kind, "data": data})))


def health(**overrides: object) -> HealthPayload:
    payload: dict[str, object] = {
        "status": "ok",
        "api_version": 2,
        "version": "1",
        "instance": "inst",
        "pid": 1,
        "uptime_s": 1,
        "ready": True,
        "audio": "ok",
        "lang": "en",
        "platform": "wsl2",
        "trigger": "socket",
        "flows": ["clipboard", "claude_chat"],
        "flow_info": [
            {"name": "clipboard", "kind": "clipboard", "hotkey": "ctrl+alt+v"},
            {"name": "claude_chat", "kind": "claude_chat", "hotkey": "ctrl+alt+a"},
        ],
        "tts": False,
        "auth": False,
    }
    payload.update(overrides)
    return cast(HealthPayload, payload)


def state(
    name: str, *, phase: str | None = None, mic_live: bool = False, needs_cue: bool = True, flow: str = "clipboard"
) -> dict[str, Any]:
    return {
        "state": name,
        "phase": phase,
        "op_seq": 1,
        "flow": flow if name != "idle" else None,
        "flow_kind": "clipboard",
        "client_id": "me",
        "mic_live": mic_live,
        "needs_cue": needs_cue,
    }


def job(
    *, final: bool = True, kind: str = "transcript", spoken: bool = False, needs_cue: bool = True, manual: bool = False
) -> DeliveryJob:
    return DeliveryJob("inst", 1, 1, kind, "clipboard", "hello   world", final, spoken, needs_cue, manual)  # type: ignore[arg-type]


def test_nothing_user_facing_before_start() -> None:
    state_before = reduce(CoreState(), SettingsSeen(CompanionSettings(), False), Now(0, 0))[0]
    snapshot = to_snapshot(state_before, 0, Now(0, 0))
    assert snapshot.tray_state == "stopped"
    assert snapshot.status_text == ""


def test_dictation_happy_path_cues_and_tray() -> None:
    sim = Sim().healthy()
    assert sim.tray == "idle"
    assert sim.snap().status_text == "Listening for Ctrl+Alt+V"
    sim.event("state", state("recording"))
    assert sim.tray == "recording" and sim.cues == []  # no cue before the mic is live
    sim.advance(1.0)
    sim.event("state", state("recording", mic_live=True))
    assert sim.cues == ["start"]
    sim.advance(11.0)
    assert sim.snap().status_text == "Recording 00:12"
    assert sim.snap().recording_since == sim.wall - 12.0
    sim.event("state", state("processing", phase="transcribing"))
    assert sim.tray == "transcribing" and sim.cues == ["start", "transcribing"]
    sim.event("state", state("idle"))
    sim.feed(DeliveryOutcome(job(), True))
    assert sim.tray == "ready" and sim.cues[-1] == "ready"
    assert sim.snap().status_text == "Copied: hello world"
    sim.advance(READY_S)
    sim.feed(Tick())
    assert sim.tray == "idle"
    assert len(sim.cues) == 3


def test_cues_follow_needs_cue_for_daemon_events() -> None:
    sim = Sim().healthy()
    sim.event("state", state("recording", needs_cue=False))
    sim.event("state", state("recording", mic_live=True, needs_cue=False))
    sim.event("state", state("processing", phase="transcribing", needs_cue=False))
    sim.feed(DeliveryOutcome(job(needs_cue=False), True))
    assert sim.cues == []
    assert sim.tray == "transcribing"  # still processing


def test_claude_flow_phases_and_ai_ready_unless_spoken() -> None:
    sim = Sim().healthy()
    sim.event("state", state("processing", phase="thinking", flow="claude_chat"))
    assert sim.tray == "thinking"
    assert sim.snap().status_text == "Claude is answering..."
    sim.event("state", state("processing", phase="speaking", flow="claude_chat"))
    assert sim.tray == "speaking"
    sim.feed(DeliveryOutcome(job(final=False), True))  # the transcript: intermediate
    assert sim.cues == []
    sim.feed(DeliveryOutcome(job(kind="ai_response"), True))
    assert sim.cues == ["ai_ready"]
    sim.feed(DeliveryOutcome(job(kind="ai_response", spoken=True), True))
    assert sim.cues == ["ai_ready"]


def test_an_operation_can_end_without_a_final_result() -> None:
    """A restart, cancel, empty answer or chat_failed ends an op with `state: idle` only."""
    sim = Sim().healthy()
    sim.event("state", state("processing", phase="transcribing", flow="claude_chat"))
    sim.event("state", state("processing", phase="thinking", flow="claude_chat"))
    sim.feed(DeliveryOutcome(job(final=False), True))  # the transcript, never the answer
    assert sim.tray == "thinking"
    sim.event("state", state("idle"))
    assert sim.tray == "idle"
    assert sim.snap().status_text == "Listening for Ctrl+Alt+V"
    assert sim.cues == ["transcribing"]


def test_a_standing_warning_is_not_cued_again_when_an_operation_ends() -> None:
    sim = Sim().healthy()
    sim.feed(HealthSeen(health(audio="down")))
    assert sim.tray == "warning" and sim.cues == ["warning"]
    sim.event("state", state("recording"))  # the user tries anyway (no mic_live: audio is down)
    sim.event("state", state("idle"))
    assert sim.tray == "warning"
    assert sim.cues == ["warning"]  # same warning shown again: no second beep
    sim.feed(HealthSeen(health(audio="ok")))
    sim.feed(HealthSeen(health(audio="down")))  # a new occurrence: it beeps
    assert sim.cues == ["warning", "warning"]


def test_a_warning_found_while_starting_cues_when_the_engine_is_up() -> None:
    sim = Sim()
    sim.feed(SupervisorUpdate("starting", 0, None, None, 0, False))
    sim.feed(MicCountSeen(0))  # no microphone, seen before the engine is ready
    assert sim.tray == "starting" and sim.cues == []
    sim.feed(SupervisorUpdate("healthy", 0, None, None, 0, False))
    assert sim.tray == "warning" and sim.cues == ["warning"]


def test_an_outdated_engine_found_while_starting_cues() -> None:
    sim = Sim()
    sim.feed(SupervisorUpdate("starting", 0, None, None, 0, False))
    v1: dict[str, object] = dict(health())
    del v1["api_version"]
    sim.feed(HealthSeen(cast(HealthPayload, v1)))
    sim.feed(SupervisorUpdate("healthy", 0, None, None, 0, False))
    assert sim.tray == "warning" and sim.cues == ["warning"]


def test_the_mic_error_after_idle_is_diagnosed_and_a_retry_clears_it() -> None:
    """The real daemon publishes `state idle` before `error mic_unavailable`."""
    sim = Sim().healthy()
    sim.event("state", state("recording"))
    sim.event("state", state("idle"))
    sim.event("error", {"code": "mic_unavailable", "detail": "", "message": "", "op_seq": 1, "needs_cue": True})
    assert sim.tray == "warning" and sim.cues == ["error"]
    sim.event("state", state("recording"))
    sim.event("state", state("recording", mic_live=True))  # the retry works
    sim.event("state", state("idle"))
    assert sim.tray == "idle" and sim.cues == ["error", "start"]


def test_systemd_notice_passes_the_default_filter_and_names_the_command() -> None:
    sim = Sim(notify_level="warnings").healthy()
    sim.feed(NoticeRequested("systemd_attached"))
    (level, _title, message) = sim.notes[-1]
    assert level == "warning"
    assert "`systemctl --user disable --now voicemate`" in message


def test_settings_reset_and_auth_notices() -> None:
    sim = Sim().healthy()
    sim.feed(NoticeRequested("settings_reset", ("C:\\cfg\\companion.toml.broken",)))
    assert sim.notes[-1][1] == "Settings reset"
    assert "C:\\cfg\\companion.toml.broken" in sim.notes[-1][2]
    sim.feed(NoticeRequested("auth_failed"))
    assert sim.notes[-1][:2] == ("error", "Engine access denied")


def test_a_cancelled_recording_returns_to_idle_quietly() -> None:
    sim = Sim().healthy()
    sim.event("state", state("recording", mic_live=True))
    sim.event("state", state("idle"))  # /cancel: no result, error or warning for this op
    assert sim.tray == "idle"
    assert sim.cues == ["start"]
    assert sim.notes == []
    assert sim.snap().recording_since is None


def test_result_written_by_the_engine_shows_ready() -> None:
    sim = Sim().healthy()
    data = {
        "result_seq": 3,
        "op_seq": 3,
        "kind": "transcript",
        "flow": "clipboard",
        "text": "from the daemon",
        "needs_delivery": False,
        "final": True,
        "spoken": False,
        "needs_cue": True,
    }
    sim.feed(ResultSeen(data))  # type: ignore[arg-type]
    assert sim.tray == "ready" and sim.cues == ["ready"]
    assert sim.snap().last_text_snippet == "from the daemon"


def test_time_limit_soon_warns_without_leaving_recording() -> None:
    sim = Sim().healthy()
    sim.event("state", state("recording", mic_live=True))
    sim.event("warning", {"code": "time_limit_soon", "detail": "", "message": "x", "op_seq": 1, "needs_cue": True})
    assert sim.tray == "recording"
    assert sim.cues == ["start", "warning"]


def test_no_speech_is_an_info_notification_without_cue() -> None:
    sim = Sim().healthy()
    sim.event("warning", {"code": "no_speech", "detail": "", "message": "nada", "op_seq": 1, "needs_cue": True})
    assert sim.cues == []
    assert sim.notes == [("info", "Nothing transcribed", "No speech was detected in the recording.")]


def test_daemon_message_is_never_relayed() -> None:
    sim = Sim().healthy()
    sim.event(
        "error",
        {
            "code": "transcription_failed",
            "detail": "x",
            "message": "Falha na transcricao",
            "op_seq": 1,
            "needs_cue": True,
        },
    )
    assert sim.cues == ["error"]
    assert sim.tray == "idle"
    assert all("Falha" not in message for _l, _t, message in sim.notes)
    assert sim.notes[0][:2] == ("error", "Transcription failed")


def test_slow_backend_is_notified_once_per_session() -> None:
    sim = Sim().healthy()
    for _ in range(3):
        sim.advance(1000)
        sim.event("warning", {"code": "slow_backend", "detail": "", "message": "", "op_seq": 1, "needs_cue": True})
    assert len(sim.notes) == 1


def test_mic_unavailable_replaces_the_warning_cue_and_diagnoses() -> None:
    sim = Sim().healthy()
    sim.feed(HealthSeen(health(audio="down")))
    cues_before = list(sim.cues)
    assert cues_before == ["warning"]  # audio down: companion-local, always plays
    sim.event("error", {"code": "mic_unavailable", "detail": "", "message": "", "op_seq": 1, "needs_cue": True})
    assert sim.cues == ["warning", "error"]
    assert sim.tray == "warning"
    assert sim.snap().status_text == "Microphone unavailable"
    assert any("audio bridge is stuck" in message for _l, _t, message in sim.notes)


def test_mic_unavailable_entering_warning_plays_only_the_error_cue() -> None:
    sim = Sim().healthy()
    sim.event("error", {"code": "mic_unavailable", "detail": "", "message": "", "op_seq": 1, "needs_cue": True})
    assert sim.cues == ["error"]  # not "warning" + "error"
    assert sim.tray == "warning"
    assert "privacy" in sim.notes[-1][2]  # audio ok: a Windows-side problem


def test_mic_unavailable_without_any_microphone() -> None:
    sim = Sim().healthy()
    sim.feed(MicCountSeen(0))
    assert sim.snap().status_text == "No microphone"
    sim.event("error", {"code": "mic_unavailable", "detail": "", "message": "", "op_seq": 1, "needs_cue": True})
    assert sim.notes[-1][2] == "No microphone is connected. Connect one and try again."


def test_warning_clears_on_mic_live_or_when_audio_and_mic_are_back() -> None:
    sim = Sim().healthy()
    sim.feed(HealthSeen(health(audio="down")))
    assert sim.tray == "warning"
    sim.feed(HealthSeen(health(audio="ok")))
    assert sim.tray == "idle"
    sim.event("error", {"code": "mic_unavailable", "detail": "", "message": "", "op_seq": 1, "needs_cue": True})
    assert sim.tray == "warning"
    sim.event("state", state("recording", mic_live=True))
    sim.event("state", state("idle"))
    assert sim.tray == "idle"
    sim.feed(MicCountSeen(0))
    assert sim.tray == "warning"
    sim.feed(MicCountSeen(2))
    assert sim.tray == "idle"


def test_supervisor_states_map_to_tray_states_with_local_cues() -> None:
    sim = Sim()
    sim.feed(SupervisorUpdate("starting", 0, None, None, 0, False))
    assert sim.tray == "starting" and sim.snap().status_text == "Starting engine..."
    sim.healthy()
    sim.feed(SupervisorUpdate("degraded", 0, None, None, 0, False))
    assert sim.tray == "warning" and sim.cues == ["warning"]
    sim.feed(SupervisorUpdate("backoff", 2, "crashed", None, 2, False))
    assert sim.tray == "restarting"
    assert sim.snap().status_text == "Restarting (attempt 2)"
    assert sim.snap().detail == "The engine stopped unexpectedly."
    sim.feed(SupervisorUpdate("failed", 2, None, "engine_breaker", 6, False))
    assert sim.tray == "error" and sim.cues == ["warning", "error"]
    assert sim.snap().restarts == 6
    sim.feed(SupervisorUpdate("stopped", 0, None, None, 0, False))
    assert sim.tray == "stopped"


def test_restarting_drops_the_engine_state() -> None:
    sim = Sim().healthy()
    sim.event("state", state("recording", mic_live=True))
    sim.feed(SupervisorUpdate("restarting", 1, "unresponsive", None, 1, False))
    sim.feed(SupervisorUpdate("healthy", 0, None, None, 1, False))
    assert sim.tray == "idle"
    assert sim.snap().recording_since is None


def test_outdated_engine() -> None:
    sim = Sim()
    sim.feed(SupervisorUpdate("healthy", 0, None, None, 0, False))
    health_v1: dict[str, object] = dict(health())
    del health_v1["api_version"]
    sim.feed(HealthSeen(cast(HealthPayload, health_v1)))
    snapshot = sim.snap()
    assert snapshot.engine_outdated and not snapshot.engine_ready
    assert snapshot.tray_state == "warning"
    assert sim.notes[-1][1] == "Engine is outdated"


def test_snapshot_rendering_never_cues() -> None:
    sim = Sim().healthy()
    data = {"state": state("processing", phase="transcribing"), "unacked": [], "audio": "down"}
    sim.feed(SnapshotSeen("inst-2", data))  # type: ignore[arg-type]
    assert sim.tray == "transcribing"
    assert sim.cues == []
    assert sim.state.instance == "inst-2"


def test_delivery_failure_cue_always_plays_and_not_copied_aggregates() -> None:
    sim = Sim().healthy()
    sim.feed(DeliveryOutcome(job(needs_cue=False), False))
    assert sim.cues == ["error"]
    assert sim.notes[-1][1] == "Not copied to the clipboard"
    assert sim.notes[-1][2] == "A transcription was not copied to the clipboard. Open VoiceMate to copy it."
    sim.advance(60)
    sim.feed(NotCopiedSeen(2))
    assert sim.notes[-1][2] == "3 transcriptions were not copied to the clipboard. Open VoiceMate to copy them."
    sim.advance(300)
    sim.feed(NotCopiedSeen(1))
    assert sim.notes[-1][2].startswith("A transcription")
    assert len(sim.notes) == 3  # never dropped


def test_manual_copy_failure_has_its_own_title() -> None:
    sim = Sim().healthy()
    sim.feed(DeliveryOutcome(job(manual=True), False))
    assert sim.cues == ["error"]
    assert sim.notes[-1][:2] == ("error", "Copy failed")
    assert sim.notes[-1][2] == "The text could not be copied to the clipboard. Try again."


def test_notifications_are_rate_limited_per_code() -> None:
    sim = Sim().healthy()
    for _ in range(3):
        sim.feed(NoticeRequested("engine_starting"))
    assert len(sim.notes) == 1
    sim.advance(301)
    sim.feed(NoticeRequested("engine_starting"))
    assert len(sim.notes) == 2


def test_notify_level_filters() -> None:
    sim = Sim(notify_level="errors").healthy()
    sim.feed(NoticeRequested("engine_starting"))  # warning
    sim.event("warning", {"code": "no_audio", "detail": "", "message": "", "op_seq": 1, "needs_cue": True})  # info
    sim.feed(NoticeRequested("trigger_error"))
    assert [level for level, _t, _m in sim.notes] == ["error"]
    sim = Sim(notify_level="none").healthy()
    sim.feed(NoticeRequested("trigger_error"))
    assert sim.notes == []


def test_manual_copy_shows_ready_without_a_cue() -> None:
    sim = Sim().healthy()
    sim.feed(DeliveryOutcome(job(manual=True), True))
    assert sim.tray == "ready" and sim.cues == []


def test_hotkey_in_use_lists_the_chords() -> None:
    sim = Sim().healthy()
    sim.feed(NoticeRequested("hotkey_in_use", ("ctrl+alt+v", "ctrl+alt+a")))
    assert "Ctrl+Alt+V, Ctrl+Alt+A" in sim.notes[-1][2]
    assert "shell:startup" in sim.notes[-1][2]


def test_local_mode_texts_and_engine_chords() -> None:
    sim = Sim(mode="local").healthy()
    assert sim.snap().hotkeys_owned_by_engine
    assert sim.snap().status_text == "Listening for Ctrl+Alt+V"
    sim.feed(HealthSeen(health(audio="down")))
    assert sim.snap().status_text == "Audio server is down"


def test_pending_wsl_restart_detail() -> None:
    sim = Sim().healthy()
    sim.feed(SupervisorUpdate("healthy", 0, None, None, 0, True))
    assert sim.snap().pending_wsl_restart
    assert "Restart WSL now" in sim.snap().detail


def test_snippet() -> None:
    assert snippet("  a\n b\t c ") == "a b c"
    long = "word " * 40
    short = snippet(long)
    assert len(short) <= 61 and short.endswith("…")
