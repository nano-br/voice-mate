"""Supervisor policy: a pure state machine (no I/O; the caller passes the time in).

Inputs are observations (probe results, process exits, mic errors, the Windows capture
device count, user commands); outputs are `Action`s the controller executes on its
supervisor thread, feeding the outcomes back in. Rules from docs/companion-app.md,
"Supervisor (WSL2)":

- startup grace (240 s): refused connections and `ready: false` both mean starting;
  still not ready when it ends -> restart the engine (counts toward the breaker);
- engine exited, or 3 missed probes after ready -> restart with backoff 2, 5, 15, 30,
  60, 120 s; the backoff resets after 10 min healthy;
- circuit breaker: more than 5 engine restarts in 15 min, more than 3 WSL restarts
  in 1 h, or 3 start timeouts in a row -> `failed`; a manual restart resets it;
- "address in use" means attach to the daemon holding the port, once: a second
  conflict before the engine is healthy goes through the backoff and the breaker;
- WSL restart when audio is `down` twice in a row, or on `mic_unavailable` while
  Windows has an active capture device and audio is not `ok`; never without a capture
  device (wait for one, then restart if audio is still not `ok`). `WslRestartPolicy`:
  never / ask / auto (auto asks when other distros run). Wait for an idle engine, at
  most 30 s. Our own `wsl.exe` exiting because of the restart is not a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.companion.contract import SupervisorState, WslRestartPolicy
from app.protocol.models import AudioHealth, ShutdownReason

ProbeKind = Literal["down", "starting", "ready"]  # down: refused/no answer; starting: ready=false
LaunchOutcome = Literal["spawned", "attached", "waiting"]  # waiting: nothing to spawn, wait for it
LaunchFailure = Literal["no_engine_dir", "spawn_error"]
RestartReason = Literal["crashed", "unresponsive", "start_timeout", "audio", "user", "wsl_user", "external"]
FailureReason = Literal["engine_breaker", "wsl_breaker", "no_engine_dir"]
PolicyNoticeCode = Literal[
    "engine_failed",
    "wsl_failed",
    "engine_dir_missing",
    "spawn_failed",
    "wsl_restarting",
    "wsl_restart_ask",
    "wsl_restart_ask_others",
]


@dataclass(frozen=True)
class Probe:
    kind: ProbeKind
    audio: AudioHealth = "unknown"


@dataclass(frozen=True)
class PolicyConfig:
    can_spawn: bool  # False in `external` mode: attach only
    can_restart_wsl: bool  # wsl2 mode only
    wsl_restart_policy: WslRestartPolicy = "auto"
    grace_s: float = 240.0
    missed_probes_limit: int = 3
    backoff_s: tuple[float, ...] = (2.0, 5.0, 15.0, 30.0, 60.0, 120.0)
    backoff_reset_s: float = 600.0
    engine_breaker: tuple[int, float] = (5, 15 * 60.0)
    wsl_breaker: tuple[int, float] = (3, 60 * 60.0)
    idle_wait_s: float = 30.0
    audio_down_limit: int = 2
    # Start timeouts are >= grace_s apart, so the time-window breaker never sees them:
    # this many in a row (without a healthy engine in between) -> failed.
    start_timeout_limit: int = 3


@dataclass(frozen=True)
class Launch:
    """Attach when /health answers; otherwise spawn (spawn modes) or wait (attach only)."""


@dataclass(frozen=True)
class StopEngine:
    """Graceful stop of the engine this app started (/shutdown, stdin EOF, kill).

    `include_attached`: also ask an attached daemon to shut down (a user's Restart engine)."""

    reason: ShutdownReason
    include_attached: bool = False


@dataclass(frozen=True)
class RestartWsl:
    """/shutdown, then `wsl --shutdown`; the caller reports back with `wsl_restart_done`."""


@dataclass(frozen=True)
class CheckOtherDistros:
    """List the running distros; answer with `other_distros` (ours excluded)."""


@dataclass(frozen=True)
class Notice:
    code: PolicyNoticeCode
    names: tuple[str, ...] = ()


Action = Launch | StopEngine | RestartWsl | CheckOtherDistros | Notice


class SupervisorPolicy:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config
        self.state: SupervisorState = "stopped"
        self.reason: RestartReason | None = None
        self.failure: FailureReason | None = None
        self.attempt = 0  # restart attempts since the engine was last healthy
        self.owned = False  # the running engine was spawned by us
        self.pending_wsl_restart = False
        self.engine_busy = False
        self.mic_count = -1
        self.last_audio: AudioHealth = "unknown"
        self._phase_since = 0.0
        self._healthy_since: float | None = None
        self._misses = 0
        self._backoff_index = 0
        self._backoff_until = 0.0
        self._engine_restarts: list[float] = []
        self._wsl_restarts: list[float] = []
        self._expect_exit = False
        self._relaunch_on_exit = False
        self._audio_down_count = 0
        self._wsl_wanted_at: float | None = None
        self._awaiting_distros = False
        self._declined = False
        self._waiting_for_mic = False
        self._start_timeouts = 0
        self._attached_after_conflict = False

    # --- views ---------------------------------------------------------------------------

    def restarts(self, now: float) -> int:
        """Engine + WSL restarts in the current circuit-breaker windows."""
        self._prune(now)
        return len(self._engine_restarts) + len(self._wsl_restarts)

    @property
    def backoff_until(self) -> float | None:
        return self._backoff_until if self.state == "backoff" else None

    @property
    def wsl_restart_waiting(self) -> bool:
        return self._wsl_wanted_at is not None

    def set_config(self, config: PolicyConfig) -> None:
        self.config = config

    # --- lifecycle -----------------------------------------------------------------------

    def start(self, now: float) -> list[Action]:
        if self.state != "stopped":
            return []
        self._reset_run_state()
        self.state = "starting"
        self._phase_since = now
        return [Launch()]

    def quit(self) -> list[Action]:
        self._reset_run_state()
        self.state = "stopped"
        return []

    def launched(self, now: float, outcome: LaunchOutcome) -> list[Action]:
        if self.state in ("stopped", "failed"):
            return []
        # A new process (or none): exits expected for an older one no longer apply.
        self.owned = outcome == "spawned"
        self._expect_exit = False
        self._relaunch_on_exit = False
        self._phase_since = now
        return []

    def launch_failed(self, now: float, failure: LaunchFailure) -> list[Action]:
        if self.state in ("stopped", "failed"):
            return []
        self.owned = False
        if failure == "no_engine_dir":
            self.state = "failed"
            self.failure = "no_engine_dir"
            return [Notice("engine_dir_missing")]
        return [Notice("spawn_failed"), *self._crash(now, "crashed")]

    def process_exited(self, now: float, address_in_use: bool) -> list[Action]:
        """The engine process we spawned exited (the caller filters out older processes)."""
        if not self.owned:
            return []
        self.owned = False
        if self._expect_exit:
            self._expect_exit = False
            if self._relaunch_on_exit and self.state == "restarting":
                self._relaunch_on_exit = False
                return [Launch()]
            return []
        if self.state in ("stopped", "failed", "backoff"):
            return []
        if address_in_use and not self._attached_after_conflict:
            # Another daemon holds the port: attach to it instead of backing off (once:
            # a second conflict before a healthy engine goes through the backoff/breaker).
            self._attached_after_conflict = True
            self.state = "restarting"
            self.reason = "external"
            self._phase_since = now
            return [Launch()]
        return self._crash(now, "crashed")

    def probe(self, now: float, probe: Probe) -> list[Action]:
        if self.state in ("stopped", "failed"):
            return []
        actions: list[Action] = []
        if probe.kind == "ready":
            actions += self._observe_audio(now, probe.audio)
        if self.state in ("starting", "restarting", "backoff"):
            if probe.kind == "ready":
                self._become_healthy(now)
            return actions
        # healthy / degraded
        if probe.kind == "ready":
            self.state = "healthy"
            self._misses = 0
            return actions
        if probe.kind == "starting":
            # Restarted under us (systemd, another client) and loading its model again.
            self.state = "restarting"
            self.reason = "external"
            self._phase_since = now
            self._misses = 0
            return actions
        self._misses += 1
        if self._misses >= self.config.missed_probes_limit and self.config.can_spawn:
            return actions + self._crash(now, "unresponsive")
        self.state = "degraded"
        return actions

    def tick(self, now: float) -> list[Action]:
        if self.state == "backoff" and now >= self._backoff_until:
            self.state = "restarting"
            self._phase_since = now
            return [Launch()]
        if (
            self.state in ("starting", "restarting")
            and self.config.can_spawn
            and now - self._phase_since >= self.config.grace_s
        ):
            self._start_timeouts += 1
            if self._start_timeouts >= self.config.start_timeout_limit:
                return self._fail(now, "engine_breaker", "engine_failed", stop=True)
            return self._crash(now, "start_timeout")
        if (
            self.state == "healthy"
            and self._healthy_since is not None
            and now - self._healthy_since >= self.config.backoff_reset_s
        ):
            self._backoff_index = 0
        if (
            self._wsl_wanted_at is not None
            and self.state in ("healthy", "degraded")
            and now - self._wsl_wanted_at >= self.config.idle_wait_s
        ):
            return self._do_wsl_restart(now, "audio", manual=False)
        return []

    def daemon_shutdown(self, now: float, reason: ShutdownReason) -> list[Action]:
        """A `shutdown` event we did not cause."""
        if self._expect_exit or self.state not in ("starting", "healthy", "degraded"):
            return []
        if reason == "user_quit":
            # Another client quit the engine: stay stopped, not a crash restart.
            self._reset_run_state()
            self.state = "stopped"
            return []
        self.state = "restarting"
        self.reason = "external"
        self._phase_since = now
        if self.owned:
            self._expect_exit = True
            self._relaunch_on_exit = True
        return []

    # --- user commands -------------------------------------------------------------------------

    def user_restart_engine(self, now: float) -> list[Action]:
        self._reset_breaker()
        self._clear_wsl_request()
        self.failure = None
        self.state = "restarting"
        self.reason = "user"
        self.attempt = 0
        self._misses = 0
        self._phase_since = now
        self._expect_exit = self.owned
        self._relaunch_on_exit = False
        # An attached daemon is asked to stop only when we can start one ourselves: in
        # `external` mode we could not bring it back.
        return [StopEngine("restart", include_attached=self.config.can_spawn), Launch()]

    def user_restart_wsl(self, now: float) -> list[Action]:
        if not self.config.can_restart_wsl:
            return []
        self._reset_breaker()
        self.failure = None
        return self._do_wsl_restart(now, "wsl_user", manual=True)

    def answer_wsl_restart(self, now: float, approved: bool) -> list[Action]:
        if not self.pending_wsl_restart:
            return []
        self.pending_wsl_restart = False
        if not approved:
            self._declined = True  # do not ask again until audio recovers or a new mic error
            return []
        return self._schedule_wsl_restart(now)

    def wsl_restart_done(self, now: float) -> list[Action]:
        if self.state != "restarting":
            return []
        self._phase_since = now
        return [Launch()]

    # --- audio and microphone ---------------------------------------------------------------

    def engine_activity(self, now: float, busy: bool) -> list[Action]:
        self.engine_busy = busy
        if not busy and self._wsl_wanted_at is not None and self.state in ("healthy", "degraded"):
            return self._do_wsl_restart(now, "audio", manual=False)
        return []

    def mic_error(self, now: float, mic_count: int, audio: AudioHealth) -> list[Action]:
        """`error mic_unavailable` from the engine."""
        if mic_count >= 0:
            self.mic_count = mic_count
        if audio != "unknown":
            self.last_audio = audio
        if mic_count == 0:
            self._waiting_for_mic = True  # no capture device: never restart, wait for one
            return []
        if audio == "ok":
            return []  # the engine reaches the audio server: a Windows-side problem (privacy, exclusive use)
        self._declined = False  # a fresh failure: ask again
        if mic_count > 0:
            return self._want_wsl_restart(now)
        return []

    def mic_count_changed(self, now: float, count: int) -> list[Action]:
        previous = self.mic_count
        self.mic_count = count
        if previous == 0 and count > 0 and self._waiting_for_mic:
            self._waiting_for_mic = False
            if self.last_audio != "ok":
                return self._want_wsl_restart(now)
        return []

    def other_distros(self, now: float, names: list[str]) -> list[Action]:
        if not self._awaiting_distros:
            return []
        self._awaiting_distros = False
        if self.state not in ("healthy", "degraded"):
            return []
        if names:
            # `wsl --shutdown` would stop them too (Docker included): ask instead.
            self.pending_wsl_restart = True
            return [Notice("wsl_restart_ask_others", tuple(names))]
        return self._schedule_wsl_restart(now)

    # --- internals ---------------------------------------------------------------------------

    def _observe_audio(self, now: float, audio: AudioHealth) -> list[Action]:
        self.last_audio = audio
        if audio == "ok":
            self._audio_down_count = 0
            self._declined = False
            return []
        if audio != "down":
            return []
        self._audio_down_count += 1
        if self.mic_count == 0:
            self._waiting_for_mic = True
            return []
        if self._audio_down_count >= self.config.audio_down_limit:
            return self._want_wsl_restart(now)
        return []

    def _want_wsl_restart(self, now: float) -> list[Action]:
        if not self.config.can_restart_wsl or self.state not in ("healthy", "degraded"):
            return []
        if self.pending_wsl_restart or self._awaiting_distros or self._wsl_wanted_at is not None or self._declined:
            return []
        policy = self.config.wsl_restart_policy
        if policy == "never":
            return []
        if policy == "ask":
            self.pending_wsl_restart = True
            return [Notice("wsl_restart_ask")]
        self._awaiting_distros = True
        return [CheckOtherDistros()]

    def _schedule_wsl_restart(self, now: float) -> list[Action]:
        if self.engine_busy:
            self._wsl_wanted_at = now  # wait for the engine to be idle, at most idle_wait_s
            return []
        return self._do_wsl_restart(now, "audio", manual=False)

    def _do_wsl_restart(self, now: float, reason: RestartReason, *, manual: bool) -> list[Action]:
        self._clear_wsl_request()
        actions: list[Action] = []
        if not manual:
            self._prune(now)
            self._wsl_restarts.append(now)
            limit, _window = self.config.wsl_breaker
            if len(self._wsl_restarts) > limit:
                return self._fail(now, "wsl_breaker", "wsl_failed", stop=False)
            actions.append(Notice("wsl_restarting"))
        self.state = "restarting"
        self.reason = reason
        self.attempt += 1
        self._misses = 0
        self._phase_since = now
        self._audio_down_count = 0
        self._expect_exit = self.owned  # our wsl.exe dies with the VM: not a crash
        self._relaunch_on_exit = False
        return [RestartWsl(), *actions]

    def _crash(self, now: float, reason: RestartReason) -> list[Action]:
        self._prune(now)
        self._engine_restarts.append(now)
        self._misses = 0
        self._expect_exit = self.owned
        self._relaunch_on_exit = False
        limit, _window = self.config.engine_breaker
        if len(self._engine_restarts) > limit:
            return self._fail(now, "engine_breaker", "engine_failed", stop=True)
        delays = self.config.backoff_s
        delay = delays[min(self._backoff_index, len(delays) - 1)]
        self._backoff_index += 1
        self.state = "backoff"
        self.reason = reason
        self.attempt += 1
        self._backoff_until = now + delay
        return [StopEngine("restart")]

    def _fail(self, now: float, failure: FailureReason, notice: PolicyNoticeCode, *, stop: bool) -> list[Action]:
        """A breaker tripped: stop retrying (and drop any WSL restart still waiting)."""
        self.state = "failed"
        self.failure = failure
        self._misses = 0
        self._clear_wsl_request()
        self._expect_exit = self.owned
        self._relaunch_on_exit = False
        return [StopEngine("supervisor"), Notice(notice)] if stop else [Notice(notice)]

    def _become_healthy(self, now: float) -> None:
        self.state = "healthy"
        self.reason = None
        self.attempt = 0
        self._misses = 0
        self._healthy_since = now
        self._start_timeouts = 0
        self._attached_after_conflict = False

    def _prune(self, now: float) -> None:
        _limit, engine_window = self.config.engine_breaker
        _limit, wsl_window = self.config.wsl_breaker
        self._engine_restarts = [t for t in self._engine_restarts if now - t < engine_window]
        self._wsl_restarts = [t for t in self._wsl_restarts if now - t < wsl_window]

    def _reset_breaker(self) -> None:
        self._engine_restarts.clear()
        self._wsl_restarts.clear()
        self._backoff_index = 0
        self._start_timeouts = 0
        self._attached_after_conflict = False

    def _clear_wsl_request(self) -> None:
        self.pending_wsl_restart = False
        self._wsl_wanted_at = None
        self._awaiting_distros = False
        self._declined = False

    def _reset_run_state(self) -> None:
        self.reason = None
        self.failure = None
        self.attempt = 0
        self._misses = 0
        self._expect_exit = False
        self._relaunch_on_exit = False
        self._healthy_since = None
        self._audio_down_count = 0
        self._start_timeouts = 0
        self._attached_after_conflict = False
        self._clear_wsl_request()
