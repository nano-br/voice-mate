from __future__ import annotations

import pytest

from app.companion.contract import WslRestartPolicy
from app.companion.supervisor.policy import (
    Action,
    CheckOtherDistros,
    Launch,
    Notice,
    PolicyConfig,
    Probe,
    RestartWsl,
    StopEngine,
    SupervisorPolicy,
)

READY = Probe("ready", "ok")
DOWN = Probe("down")
LOADING = Probe("starting")


def policy(*, can_spawn: bool = True, can_restart_wsl: bool = True, wsl: WslRestartPolicy = "auto") -> SupervisorPolicy:
    return SupervisorPolicy(PolicyConfig(can_spawn=can_spawn, can_restart_wsl=can_restart_wsl, wsl_restart_policy=wsl))


def healthy(p: SupervisorPolicy, now: float = 0.0, outcome: str = "spawned") -> SupervisorPolicy:
    assert p.start(now) == [Launch()]
    p.launched(now, outcome)  # type: ignore[arg-type]
    p.probe(now + 1, READY)
    assert p.state == "healthy"
    return p


def kinds(actions: list[Action]) -> list[type]:
    return [type(action) for action in actions]


def notices(actions: list[Action]) -> list[str]:
    return [action.code for action in actions if isinstance(action, Notice)]


def test_start_launches_and_waits_through_loading() -> None:
    p = policy()
    assert p.start(0.0) == [Launch()]
    assert p.state == "starting"
    p.launched(0.5, "spawned")
    assert p.probe(5.0, DOWN) == []  # refused: still starting
    assert p.probe(10.0, LOADING) == []  # ready=false: still starting
    assert p.state == "starting"
    p.probe(60.0, READY)
    assert p.state == "healthy"
    assert p.start(61.0) == []  # already running


def test_grace_expiry_restarts_with_backoff() -> None:
    p = policy()
    p.start(0.0)
    p.launched(0.0, "spawned")
    assert p.tick(239.0) == []
    actions = p.tick(240.0)
    assert actions == [StopEngine("restart")]
    assert p.state == "backoff" and p.reason == "start_timeout"
    assert p.backoff_until == 242.0
    assert p.tick(241.0) == []
    assert p.tick(242.0) == [Launch()]
    assert p.state == "restarting"
    assert p.attempt == 1


def test_attach_only_mode_waits_forever_and_never_restarts() -> None:
    p = policy(can_spawn=False, can_restart_wsl=False)
    p.start(0.0)
    p.launched(0.0, "waiting")
    assert p.tick(10_000.0) == []
    assert p.state == "starting"
    p.probe(10_001.0, READY)
    for i in range(5):
        p.probe(10_002.0 + i, DOWN)
    assert p.state == "degraded"  # nothing to restart in external mode
    p.probe(10_010.0, READY)
    assert p.state == "healthy"


def test_three_missed_probes_restart_the_engine() -> None:
    p = healthy(policy())
    assert p.probe(10.0, DOWN) == []
    assert p.state == "degraded"
    assert p.probe(15.0, DOWN) == []
    actions = p.probe(20.0, DOWN)
    assert actions == [StopEngine("restart")]
    assert p.state == "backoff" and p.reason == "unresponsive"


def test_a_good_probe_clears_degraded() -> None:
    p = healthy(policy())
    p.probe(10.0, DOWN)
    p.probe(15.0, DOWN)
    p.probe(20.0, READY)
    assert p.state == "healthy"
    p.probe(25.0, DOWN)
    p.probe(30.0, DOWN)
    assert p.state == "degraded"  # the miss count started over


def test_backoff_sequence_and_reset_after_ten_healthy_minutes() -> None:
    p = healthy(policy())
    delays = []
    now = 100.0
    for _ in range(3):
        p.process_exited(now, False)
        assert p.backoff_until is not None
        delays.append(p.backoff_until - now)
        now = p.backoff_until
        p.tick(now)
        p.launched(now, "spawned")
        p.probe(now + 1, READY)
        now += 2
    assert delays == [2.0, 5.0, 15.0]
    p.tick(now + 601)  # ten minutes healthy
    p.process_exited(now + 602, False)
    assert p.backoff_until == now + 604  # back to 2 s


def test_engine_breaker_trips_after_five_restarts_in_fifteen_minutes() -> None:
    p = healthy(policy())
    now = 10.0
    for _ in range(5):
        p.process_exited(now, False)
        assert p.state == "backoff"
        now = p.backoff_until or now
        p.tick(now)
        p.launched(now, "spawned")
        now += 1
    actions = p.process_exited(now, False)
    assert p.state == "failed" and p.failure == "engine_breaker"
    assert notices(actions) == ["engine_failed"]
    assert p.tick(now + 1000) == []
    assert p.probe(now + 1001, READY) == []  # failed stays failed
    assert p.restarts(now) == 6
    # A manual restart resets it.
    assert kinds(p.user_restart_engine(now + 2000)) == [StopEngine, Launch]
    assert p.state == "restarting" and p.failure is None
    assert p.restarts(now + 2000) == 0


def test_restarts_outside_the_window_do_not_count() -> None:
    p = healthy(policy())
    now = 0.0
    for _ in range(5):
        p.process_exited(now, False)
        now = p.backoff_until or now
        p.tick(now)
        p.launched(now, "spawned")
        p.probe(now, READY)
        now += 901  # each restart in its own 15 min window
    p.process_exited(now, False)
    assert p.state == "backoff"


def test_our_own_stop_is_not_a_crash() -> None:
    p = healthy(policy())
    p.user_restart_engine(10.0)
    assert p.process_exited(11.0, False) == []
    assert p.state == "restarting"


def test_address_in_use_attaches_instead_of_backing_off() -> None:
    p = policy()
    p.start(0.0)
    p.launched(0.0, "spawned")
    actions = p.process_exited(3.0, True)
    assert actions == [Launch()]
    assert p.state == "restarting"
    assert p.restarts(3.0) == 0


def test_exit_of_a_not_owned_engine_is_ignored() -> None:
    p = healthy(policy(), outcome="attached")
    assert p.process_exited(5.0, False) == []
    assert p.state == "healthy"


def test_another_clients_user_quit_stops_without_restart() -> None:
    p = healthy(policy())
    assert p.daemon_shutdown(5.0, "user_quit") == []
    assert p.state == "stopped"
    assert p.process_exited(6.0, False) == []
    assert p.tick(1000.0) == []


def test_another_clients_restart_relaunches_without_backoff() -> None:
    p = healthy(policy())
    p.daemon_shutdown(5.0, "restart")
    assert p.state == "restarting"
    assert p.process_exited(6.0, False) == [Launch()]
    assert p.restarts(6.0) == 0


def test_launch_failures() -> None:
    p = policy()
    p.start(0.0)
    actions = p.launch_failed(0.1, "no_engine_dir")
    assert p.state == "failed" and p.failure == "no_engine_dir"
    assert notices(actions) == ["engine_dir_missing"]
    p = policy()
    p.start(0.0)
    actions = p.launch_failed(0.1, "spawn_error")
    assert notices(actions) == ["spawn_failed"]
    assert p.state == "backoff"


# --- WSL restarts ---------------------------------------------------------------------------------


def test_audio_down_twice_in_a_row_restarts_wsl_after_checking_distros() -> None:
    p = healthy(policy())
    p.mic_count_changed(1.0, 1)
    assert p.probe(5.0, Probe("ready", "down")) == []
    actions = p.probe(10.0, Probe("ready", "down"))
    assert actions == [CheckOtherDistros()]
    actions = p.other_distros(11.0, [])
    assert kinds(actions) == [RestartWsl, Notice]
    assert notices(actions) == ["wsl_restarting"]
    assert p.state == "restarting" and p.reason == "audio"
    assert p.process_exited(12.0, False) == []  # our wsl.exe died with the VM
    assert p.wsl_restart_done(20.0) == [Launch()]


def test_audio_down_once_then_ok_does_nothing() -> None:
    p = healthy(policy())
    p.mic_count_changed(1.0, 1)
    p.probe(5.0, Probe("ready", "down"))
    p.probe(10.0, Probe("ready", "ok"))
    assert p.probe(15.0, Probe("ready", "down")) == []


def test_other_running_distros_turn_auto_into_a_question() -> None:
    p = healthy(policy())
    p.mic_count_changed(1.0, 2)
    p.probe(5.0, Probe("ready", "down"))
    p.probe(10.0, Probe("ready", "down"))
    actions = p.other_distros(11.0, ["docker-desktop"])
    assert actions == [Notice("wsl_restart_ask_others", ("docker-desktop",))]
    assert p.pending_wsl_restart
    assert kinds(p.answer_wsl_restart(30.0, True)) == [RestartWsl, Notice]


def test_ask_policy_waits_for_the_answer_and_respects_a_no() -> None:
    p = healthy(policy(wsl="ask"))
    p.mic_count_changed(1.0, 1)
    p.probe(5.0, Probe("ready", "down"))
    assert notices(p.probe(10.0, Probe("ready", "down"))) == ["wsl_restart_ask"]
    assert p.pending_wsl_restart
    assert p.answer_wsl_restart(11.0, False) == []
    assert not p.pending_wsl_restart
    assert p.probe(15.0, Probe("ready", "down")) == []  # declined: no new question while still down
    p.probe(20.0, Probe("ready", "ok"))
    p.probe(25.0, Probe("ready", "down"))
    assert notices(p.probe(30.0, Probe("ready", "down"))) == ["wsl_restart_ask"]


def test_never_policy_never_restarts() -> None:
    p = healthy(policy(wsl="never"))
    p.mic_count_changed(1.0, 1)
    for t in range(5):
        assert p.probe(5.0 + t, Probe("ready", "down")) == []
    assert p.mic_error(20.0, 1, "down") == []


def test_mic_error_with_audio_ok_points_to_windows_not_wsl() -> None:
    p = healthy(policy())
    assert p.mic_error(5.0, 2, "ok") == []


def test_mic_error_with_a_mic_and_audio_not_ok_restarts_wsl() -> None:
    p = healthy(policy())
    assert p.mic_error(5.0, 1, "unknown") == [CheckOtherDistros()]


def test_no_capture_device_never_restarts_then_restarts_when_one_appears() -> None:
    p = healthy(policy())
    p.mic_count_changed(1.0, 0)
    assert p.mic_error(5.0, 0, "down") == []
    assert p.probe(6.0, Probe("ready", "down")) == []
    assert p.probe(7.0, Probe("ready", "down")) == []
    assert p.mic_count_changed(10.0, 1) == [CheckOtherDistros()]


def test_mic_appearing_with_audio_ok_does_nothing() -> None:
    p = healthy(policy())
    p.mic_count_changed(1.0, 0)
    p.mic_error(2.0, 0, "ok")
    p.probe(3.0, Probe("ready", "ok"))
    assert p.mic_count_changed(4.0, 1) == []


def test_wsl_restart_waits_for_an_idle_engine_at_most_thirty_seconds() -> None:
    p = healthy(policy())
    p.engine_activity(1.0, True)
    p.mic_error(2.0, 1, "down")
    assert p.other_distros(3.0, []) == []
    assert p.wsl_restart_waiting
    assert p.tick(20.0) == []
    actions = p.engine_activity(25.0, False)
    assert kinds(actions) == [RestartWsl, Notice]

    p = healthy(policy())
    p.engine_activity(1.0, True)
    p.mic_error(2.0, 1, "down")
    p.other_distros(3.0, [])
    assert kinds(p.tick(33.0)) == [RestartWsl, Notice]


def test_wsl_breaker_trips_after_three_restarts_in_an_hour() -> None:
    p = healthy(policy())
    now = 0.0
    for _ in range(3):
        p.mic_error(now, 1, "down")
        assert kinds(p.other_distros(now, [])) == [RestartWsl, Notice]
        p.wsl_restart_done(now + 5)
        p.launched(now + 5, "spawned")
        p.probe(now + 10, READY)
        now += 60
    p.mic_error(now, 1, "down")
    actions = p.other_distros(now, [])
    assert notices(actions) == ["wsl_failed"]
    assert p.state == "failed" and p.failure == "wsl_breaker"
    assert kinds(p.user_restart_wsl(now + 1)) == [RestartWsl]  # manual: allowed, no notice
    assert p.state == "restarting"


def test_no_wsl_restart_outside_wsl_mode() -> None:
    p = healthy(policy(can_restart_wsl=False))
    p.mic_count_changed(1.0, 1)
    p.probe(5.0, Probe("ready", "down"))
    assert p.probe(10.0, Probe("ready", "down")) == []
    assert p.user_restart_wsl(11.0) == []


def test_quit_stops_everything() -> None:
    p = healthy(policy())
    p.quit()
    assert p.state == "stopped"
    assert p.tick(10_000.0) == []
    assert p.probe(10_001.0, DOWN) == []


@pytest.mark.parametrize("state_probe", [DOWN, LOADING])
def test_restarted_under_us_while_healthy(state_probe: Probe) -> None:
    p = healthy(policy())
    p.probe(10.0, state_probe)
    assert p.state in ("degraded", "restarting")
