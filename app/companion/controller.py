"""CompanionControllerImpl: the Qt-free core behind `app.companion.contract.CompanionController`.

Threads (docs/companion-app.md and the contract's "Threading"):
- the DISPATCHER owns all state (`CoreState`, the supervisor policy, the delivery queue)
  and publishes snapshots and notifications, in order, without holding locks;
- serial workers do the slow parts: `probe` (TCP probe + /health, Core Audio), `io`
  (ACKs, reconciliation, rendering cues, hotkey registration), `commands` (trigger,
  cancel), `supervisor` (spawn, stop, WSL restart) and `pending` (writes the Not
  copied list to disk, so a slow or failing disk never holds up a delivery);
- the `/events` long-poll thread, the engine log/exit threads, and on Windows the Win32
  hotkey/clipboard thread.
Workers never touch state: they post results back to the dispatcher.
"""

from __future__ import annotations

import itertools
import logging
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from app.companion import paths
from app.companion.chords import display_chord, normalize_chord
from app.companion.client import (
    COMPANION_NAME,
    COMPANION_VERSION,
    DEFAULT_HOST,
    EVENTS_WAIT_S,
    LEASE_S,
    DaemonClient,
    DaemonError,
    EventPoller,
)
from app.companion.contract import (
    CompanionController,
    CompanionSettings,
    CompanionSnapshot,
    CueName,
    CueSettings,
    EngineMode,
    HotkeyCheck,
    Notification,
    NotificationListener,
    RecentItem,
    SnapshotListener,
    Unsubscribe,
)
from app.companion.cues import CueBank, CuePlayer
from app.companion.delivery import (
    RECONCILE_INTERVAL_S,
    Delivered,
    DeliveryEffect,
    DeliveryFailed,
    DeliveryQueue,
    NotCopied,
    SendAck,
    StartDelivery,
)
from app.companion.desktop import Desktop, HotkeyHost, autostart_command, create_desktop
from app.companion.dictation import EngineLanguage, engine_language
from app.companion.model import (
    CoreState,
    DeliveryOutcome,
    EventSeen,
    HealthSeen,
    HotkeysSuspendedSeen,
    MicCountSeen,
    ModelInput,
    NotCopiedSeen,
    NoticeRequested,
    Now,
    PendingCount,
    ResultSeen,
    SettingsSeen,
    SnapshotSeen,
    Started,
    SupervisorUpdate,
    Tick,
    reduce,
    to_snapshot,
)
from app.companion.pending_store import PendingStore
from app.companion.runtime import Dispatcher, SerialExecutor, TimerHandle
from app.companion.settings_store import SettingsStore, normalized, validate_settings
from app.companion.supervisor.backend import EngineBackend, ExternalBackend, read_local_token
from app.companion.supervisor.policy import (
    Action,
    CheckOtherDistros,
    Launch,
    LaunchFailure,
    LaunchOutcome,
    Notice,
    PolicyConfig,
    Probe,
    RestartWsl,
    StopEngine,
    SupervisorPolicy,
)
from app.i18n import _
from app.protocol.models import (
    API_VERSION,
    Ack,
    AckRequest,
    Capability,
    EventsResponse,
    HealthPayload,
    RegisterRequest,
    RegisterResponse,
    ResultsResponse,
    TriggerRequest,
)

log = logging.getLogger(__name__)

ACK_RETRIES: Final = 5
ATTACHED_STOP_WAIT_S: Final = 8.0
QUIT_UNREGISTER_TIMEOUT_S: Final = 1.0
# Quit waits this long for the last write of the Not copied list.
PENDING_FLUSH_TIMEOUT_S: Final = 2.0
HOTKEY_RETRY_S: Final = 10.0
TRAY_PROMOTE_S: Final = (2.0, 10.0, 30.0)


@dataclass(frozen=True)
class Timings:
    """Intervals (seconds). The defaults are the documented ones; tests shorten them."""

    probe_interval_s: float = 5.0
    tick_s: float = 1.0
    reconcile_s: float = RECONCILE_INTERVAL_S
    mic_poll_s: float = 10.0
    mic_alert_poll_s: float = 3.0  # no microphone, or a warning showing
    events_wait_s: int = EVENTS_WAIT_S
    quit_cap_s: float = 15.0
    grace_s: float = 240.0
    backoff_s: tuple[float, ...] = (2.0, 5.0, 15.0, 30.0, 60.0, 120.0)
    # A chord another app holds (e.g. the old hotkeys script) is tried again this often.
    hotkey_retry_s: float = HOTKEY_RETRY_S
    # Windows 11 creates the tray icon's entry asynchronously after the icon first shows:
    # try to promote it at these offsets (seconds after start), stopping once it is found.
    tray_promote_s: tuple[float, ...] = TRAY_PROMOTE_S


BackendFactory = Callable[[CompanionSettings, Path], EngineBackend]
DesktopFactory = Callable[[], Desktop]


def is_outdated(health: HealthPayload) -> bool:
    """The daemon speaks an API older than ours (a v1 daemon has no `api_version` at all)."""
    api_version = health.get("api_version")
    return not isinstance(api_version, int) or isinstance(api_version, bool) or api_version < API_VERSION


def hotkeys_owned_by_engine(mode: EngineMode) -> bool:
    """Linux keeps the engine's native listener (and clipboard); Windows uses the companion's."""
    return mode == "local" or (mode == "external" and sys.platform != "win32")


def default_backend(settings: CompanionSettings, logs_dir: Path) -> EngineBackend:
    log_path = logs_dir / paths.ENGINE_LOG_NAME
    if settings.engine_mode == "wsl2":
        from app.companion.supervisor.wsl import WslBackend

        return WslBackend(settings.wsl_distro, settings.daemon_port, log_path, language=engine_language(settings))
    if settings.engine_mode == "local":
        from app.companion.supervisor.local import LocalBackend

        return LocalBackend(settings.daemon_port, log_path, language=engine_language(settings))
    if sys.platform == "win32":
        from app.companion.supervisor.wsl import read_wsl_token

        distro = settings.wsl_distro
        return ExternalBackend(lambda: read_wsl_token(distro))
    return ExternalBackend(read_local_token)


def _engine_settings_changed(old: CompanionSettings, new: CompanionSettings) -> bool:
    # The dictation language only counts as the flags it resolves to (a UI language change
    # alone restarts the engine only while dictation follows the interface), and not at all
    # in external mode, where the engine is not ours to start.
    return (old.engine_mode, old.wsl_distro, old.engine_dir, old.daemon_port, _spawned_language(old)) != (
        new.engine_mode,
        new.wsl_distro,
        new.engine_dir,
        new.daemon_port,
        _spawned_language(new),
    )


def _spawned_language(settings: CompanionSettings) -> EngineLanguage | None:
    return None if settings.engine_mode == "external" else engine_language(settings)


class CompanionControllerImpl:
    def __init__(
        self,
        store: SettingsStore,
        *,
        desktop_factory: DesktopFactory = create_desktop,
        backend_factory: BackendFactory = default_backend,
        cues_dir: Path | None = None,
        logs_dir: Path | None = None,
        timings: Timings | None = None,
        host: str = DEFAULT_HOST,
        pending_store: PendingStore | None = None,
    ) -> None:
        """`pending_store`: where the Not copied list survives a restart (None = memory
        only; `create_controller` passes the per-user file)."""
        self._store = store
        self._pending_store = pending_store
        self._backend_factory = backend_factory
        self._timings = timings or Timings()
        self._host = host
        self._logs_dir = logs_dir or paths.logs_dir()
        self._desktop = desktop_factory()
        self._cues = CuePlayer(CueBank(cues_dir or paths.cues_dir()), self._play_file)
        self._dispatcher = Dispatcher()
        self._io = SerialExecutor("companion-io")
        self._commands = SerialExecutor("companion-commands")
        self._supervisor = SerialExecutor("companion-supervisor")
        self._prober = SerialExecutor("companion-probe")
        self._pending_writer = SerialExecutor("companion-pending")
        self._spawn_lock = threading.Lock()
        self._hotkey_lock = threading.Lock()
        self._generations = itertools.count(1)
        self._quit_lock = threading.Lock()
        self._quit_callbacks: list[Callable[[], None]] = []
        self._quit_finished = False
        self._started = False
        self._quitting = False  # read from any thread, written once on the dispatcher
        self._reflect_autostart()

        # Dispatcher-owned state.
        settings = store.get()
        self._core, _effects = reduce(
            CoreState(), SettingsSeen(settings, hotkeys_owned_by_engine(settings.engine_mode)), self._now()
        )
        self._delivery = DeliveryQueue()
        if pending_store is not None:
            # Results the previous run could not copy: back in the Not copied list.
            self._delivery.restore_pending(pending_store.load())
        restored = tuple(self._delivery.pending())
        if restored:
            self._core, _effects = reduce(self._core, PendingCount(len(restored)), self._now())
        self._rev = 0
        self._snapshot: CompanionSnapshot = to_snapshot(self._core, 0, self._now())
        self._listeners: list[SnapshotListener] = []
        self._notification_listeners: list[NotificationListener] = []
        self._outbox: list[Notification] = []
        self._recent: tuple[RecentItem, ...] = ()
        self._pending: tuple[RecentItem, ...] = restored
        self._generation = 0
        self._hotkey_flows: tuple[str, ...] | None = None
        self._timers: dict[str, TimerHandle] = {}
        self._tray_promotion = 0  # generation: a newer request makes older results stale
        self._build_engine(settings)

    # --- wiring --------------------------------------------------------------------------------------

    def _policy_config(self, settings: CompanionSettings, backend: EngineBackend) -> PolicyConfig:
        return PolicyConfig(
            can_spawn=backend.can_spawn,
            can_restart_wsl=backend.can_restart_wsl,
            wsl_restart_policy=settings.wsl_restart_policy,
            grace_s=self._timings.grace_s,
            backoff_s=self._timings.backoff_s,
        )

    def _build_engine(self, settings: CompanionSettings) -> None:
        self._mode: EngineMode = settings.engine_mode
        self._backend = self._backend_factory(settings, self._logs_dir)
        self._client = DaemonClient(settings.daemon_port, host=self._host, token_provider=self._backend.read_token)
        self._policy = SupervisorPolicy(self._policy_config(settings, self._backend))
        capabilities: list[Capability] = ["cues"] if hotkeys_owned_by_engine(self._mode) else ["clipboard", "cues"]
        self._capabilities = capabilities
        poller_ref: list[EventPoller] = []

        def on_registered(response: RegisterResponse) -> None:
            self._post(lambda: self._on_registered(poller_ref[0], response))

        def on_events(response: EventsResponse, instance_changed: bool) -> None:
            self._post(lambda: self._on_events(poller_ref[0], response))

        def on_unauthorized() -> None:
            # The token was re-read (at most every 30 s) and is still refused.
            self._post(lambda: None if self._quitting else self._input(NoticeRequested("auth_failed")))

        poller = EventPoller(
            self._client,
            registration=self._registration,
            on_registered=on_registered,
            on_events=on_events,
            on_unauthorized=on_unauthorized,
            wait_s=self._timings.events_wait_s,
        )
        poller_ref.append(poller)
        self._poller = poller

    def _registration(self) -> RegisterRequest:
        return RegisterRequest(
            name=COMPANION_NAME,
            version=COMPANION_VERSION,
            os=sys.platform,
            client_key=self._store.get().client_key,
            capabilities=list(self._capabilities),
            lease_s=LEASE_S,
        )

    @staticmethod
    def _now() -> Now:
        return Now(time.monotonic(), time.time())

    # --- dispatcher plumbing ------------------------------------------------------------------------

    def _post(self, task: Callable[[], None]) -> None:
        self._dispatcher.post(lambda: self._run_task(task))

    def _run_task(self, task: Callable[[], None]) -> None:
        try:
            task()
        finally:
            self._publish()

    def _on_dispatcher(self, task: Callable[[], None]) -> None:
        if self._dispatcher.on_thread():
            self._run_task(task)
        else:
            self._post(task)

    def _schedule(self, name: str, delay_s: float, task: Callable[[], None]) -> None:
        previous = self._timers.pop(name, None)
        if previous is not None:
            previous.cancel()
        self._timers[name] = self._dispatcher.call_later(delay_s, lambda: self._run_task(task))

    def _later(self, delay_s: float, task: Callable[[], None]) -> None:
        self._dispatcher.call_later(delay_s, lambda: self._run_task(task))

    def _input(self, message: ModelInput) -> None:
        self._core, effects = reduce(self._core, message, self._now())
        if effects.cue is not None:
            self._cues.play(effects.cue)
        self._outbox.extend(effects.notifications)

    def _publish(self) -> None:
        snapshot = to_snapshot(self._core, self._snapshot.rev, self._now())
        if snapshot != self._snapshot:
            self._rev += 1
            snapshot = replace(snapshot, rev=self._rev)
            self._snapshot = snapshot
            for listener in list(self._listeners):
                try:
                    listener(snapshot)
                except Exception:  # noqa: BLE001 - a listener must not break the dispatcher
                    log.exception("snapshot listener failed")
        outbox, self._outbox = self._outbox, []
        for notification in outbox:
            log.info("notification [%s] %s: %s", notification.level, notification.title, notification.message)
            for notification_listener in list(self._notification_listeners):
                try:
                    notification_listener(notification)
                except Exception:  # noqa: BLE001
                    log.exception("notification listener failed")

    def _play_file(self, path: Path) -> None:
        self._desktop.sound.play(path)

    # --- CompanionController: lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._post(self._start_on_dispatcher)

    def _start_on_dispatcher(self) -> None:
        if self._quitting:
            return
        self._input(Started())
        backup = self._store.broken_backup
        if backup is not None:
            self._input(NoticeRequested("settings_reset", (str(backup),)))
        if self._store.first_run and self._desktop.taskbar_pin_tip:
            self._input(NoticeRequested("pin_taskbar"))
        if self._store.get().tray_icon_visible:
            # Startup: only an entry nobody decided on yet (Explorer creates it without
            # IsPromoted); hiding the icon in the Windows taskbar settings is respected.
            self._promote_tray_icon(True, self._timings.tray_promote_s, only_if_unset=True)
        pending_backup = self._pending_store.broken_backup if self._pending_store is not None else None
        if pending_backup is not None:
            self._input(NoticeRequested("pending_reset", (str(pending_backup),)))
        self._io.submit(self._start_desktop)
        self._poller.start()
        self._run(self._policy.start(time.monotonic()))
        self._sync_supervisor()
        self._schedule("probe", 0.0, self._probe_tick)
        self._schedule("tick", self._timings.tick_s, self._tick)
        self._schedule("mic", 0.0, self._mic_tick)
        self._schedule("reconcile", self._timings.reconcile_s, self._reconcile_tick)

    def _start_desktop(self) -> None:
        settings = self._store.get()
        try:
            self._desktop.start(self._on_hotkey)
        except Exception:  # noqa: BLE001 - the app still works from the menu without hotkeys
            log.exception("desktop services failed to start (no global hotkeys)")
        self._cues.configure(settings)
        self._register_hotkeys(None)

    def snapshot(self) -> CompanionSnapshot:
        return self._snapshot

    def subscribe(self, listener: SnapshotListener) -> Unsubscribe:
        def add() -> None:
            self._listeners.append(listener)
            try:
                listener(self._snapshot)
            except Exception:  # noqa: BLE001
                log.exception("snapshot listener failed")

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        self._on_dispatcher(add)
        return lambda: self._on_dispatcher(remove)

    def subscribe_notifications(self, listener: NotificationListener) -> Unsubscribe:
        def add() -> None:
            self._notification_listeners.append(listener)

        def remove() -> None:
            if listener in self._notification_listeners:
                self._notification_listeners.remove(listener)

        self._on_dispatcher(add)
        return lambda: self._on_dispatcher(remove)

    def quit(self, on_done: Callable[[], None]) -> None:
        with self._quit_lock:
            finished = self._quit_finished
            if not finished:
                self._quit_callbacks.append(on_done)
            first = len(self._quit_callbacks) == 1 and not finished
        if finished:
            # Already done: still from a controller thread, as the contract says.
            threading.Thread(target=on_done, name="companion-quit-done", daemon=True).start()
            return
        if not first:
            return  # already quitting: on_done runs with the first caller's
        cap = threading.Timer(self._timings.quit_cap_s, self._finish_quit)
        cap.daemon = True
        cap.start()
        self._post(self._begin_quit)

    def _finish_quit(self) -> None:
        with self._quit_lock:
            if self._quit_finished:
                return
            self._quit_finished = True
            callbacks = list(self._quit_callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - the caller's callback
                log.exception("quit callback failed")

    def _begin_quit(self) -> None:
        self._quitting = True
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        # What this run will not deliver goes to the Not copied list, saved before the
        # quit sequence flushes it: the engine may stop with us and take the results along.
        abandoned = self._delivery.abandon_queue()
        if abandoned:
            log.info("quitting: %s undelivered result(s) kept in the Not copied list", abandoned)
            self._apply_delivery([])
        self._policy.quit()
        self._sync_supervisor()
        self._poller.stop()
        threading.Thread(target=self._quit_sequence, name="companion-quit", daemon=True).start()

    def _quit_sequence(self) -> None:
        try:
            session = self._poller.take_session()
            if session is not None:
                try:
                    self._client.unregister(session.client_id, timeout=QUIT_UNREGISTER_TIMEOUT_S)
                except DaemonError as exc:
                    log.info("unregister failed: %s", exc)
            with self._spawn_lock:
                # An attached daemon keeps running; only what we started is stopped.
                self._backend.stop(self._client, "user_quit")
                self._backend.close()
            self._desktop.stop()
            self._flush_pending()
        except Exception:  # noqa: BLE001 - quitting must always finish
            log.exception("quit sequence failed")
        finally:
            for executor in (self._io, self._commands, self._supervisor, self._prober, self._pending_writer):
                executor.stop()
            self._finish_quit()
            self._post(self._dispatcher.stop)

    # --- CompanionController: commands ------------------------------------------------------------------

    def toggle(self, flow: str) -> None:
        self._post(lambda: self._toggle(flow))

    def _on_hotkey(self, flow: str) -> None:
        # Runs on the Win32 thread: hand it over at once.
        self._post(lambda: self._toggle(flow))

    def _toggle(self, flow: str) -> None:
        if self._quitting or not self._started:
            return
        state = self._policy.state
        if state in ("starting", "restarting", "backoff"):
            self._input(NoticeRequested("engine_starting"))
            return
        if state in ("stopped", "failed"):
            self._input(NoticeRequested("engine_stopped"))
            return
        if self._core.engine_outdated:
            self._input(NoticeRequested("engine_outdated"))
            return
        if not self._core.engine_ready:
            self._input(NoticeRequested("engine_starting"))
            return
        session = self._poller.session()
        request = TriggerRequest(flow=flow, expect="toggle")
        if session is not None:
            request["client_id"] = session.client_id
        client = self._client
        self._commands.submit(lambda: self._trigger_job(client, request))

    def _trigger_job(self, client: DaemonClient, request: TriggerRequest) -> None:
        try:
            response = client.trigger(request)
            log.info("trigger %s -> %s (op %s)", request.get("flow"), response.get("action"), response.get("op_seq"))
        except DaemonError as exc:
            log.warning("trigger %s failed: %s", request.get("flow"), exc)
            error = exc
            self._post(lambda: self._trigger_failed(error))

    def _trigger_failed(self, error: DaemonError) -> None:
        if error.kind == "offline":
            self._input(NoticeRequested("trigger_offline"))
        elif error.kind == "timeout":
            self._input(NoticeRequested("trigger_timeout"))
        elif error.status == 503:
            self._input(NoticeRequested("engine_starting"))
        elif error.status == 401:
            self._input(NoticeRequested("auth_failed"))
        elif error.status >= 500 or error.kind == "protocol":
            self._input(NoticeRequested("trigger_error"))
        else:
            self._input(NoticeRequested("trigger_rejected", status=error.status))

    def cancel(self) -> None:
        def run() -> None:
            if self._quitting or self._core.engine.state == "idle":
                return
            client = self._client
            op_seq = self._core.engine.op_seq

            def job() -> None:
                try:
                    client.cancel({"op_seq": op_seq})
                except DaemonError as exc:
                    log.warning("cancel failed: %s", exc)

            self._commands.submit(job)

        self._post(run)

    def restart_engine(self) -> None:
        def run() -> None:
            if self._quitting or not self._started:
                return
            self._run(self._policy.user_restart_engine(time.monotonic()))
            self._sync_supervisor()

        self._post(run)

    def restart_wsl(self) -> None:
        def run() -> None:
            if self._quitting or not self._started:
                return
            self._run(self._policy.user_restart_wsl(time.monotonic()))
            self._sync_supervisor()

        self._post(run)

    def answer_wsl_restart(self, approved: bool) -> None:
        def run() -> None:
            if self._quitting:
                return
            self._run(self._policy.answer_wsl_restart(time.monotonic(), approved))
            self._sync_supervisor()

        self._post(run)

    def settings(self) -> CompanionSettings:
        return self._store.get()

    def apply_settings(self, settings: CompanionSettings) -> list[str]:
        if self._store.read_only:
            return [_("The settings file was written by a newer version of VoiceMate, so it is read-only here.")]
        candidate = normalized(settings)
        errors = validate_settings(candidate, platform=self._store.platform)
        if errors:
            return errors
        # One registration at a time: a background re-registration must not undo this one.
        with self._hotkey_lock:
            old = self._store.get()
            if not candidate.client_key:
                candidate = replace(candidate, client_key=old.client_key)
            registered = self._apply_hotkeys(old, candidate)
            if isinstance(registered, list):
                return registered
            errors = self._apply_autostart(old, candidate)
            if not errors:
                try:
                    self._store.save(candidate)
                except OSError:
                    log.exception("saving settings failed")
                    errors = [_("Could not save the settings file.")]
            if errors:
                if registered:
                    self._apply_hotkeys(candidate, old)  # roll back to what is saved
                return errors
        applied = candidate
        self._post(lambda: self._settings_applied(old, applied))
        return []

    def _apply_hotkeys(self, old: CompanionSettings, new: CompanionSettings) -> bool | list[str]:
        """Register `new` hotkeys at once (rolled back on failure). True = re-registered,
        False = nothing to roll back, a list = localized errors. Call with `_hotkey_lock`."""
        host = self._desktop.hotkeys
        if host is None or hotkeys_owned_by_engine(new.engine_mode) or not self._started:
            return False
        bindings = self._hotkey_bindings(new)
        if new.hotkeys == old.hotkeys:
            # Unchanged, but a chord may still be held by another app since startup: try the
            # missing ones again (best effort: an unrelated setting must still apply).
            self._register_best_effort(host, bindings, notify=False)
            return False
        try:
            verdicts = host.register(bindings, atomic=True)
        except (OSError, TimeoutError):
            log.exception("hotkey registration failed")
            return True  # the thread is gone: nothing to roll back to
        taken = [bindings[flow] for flow, verdict in verdicts.items() if verdict != "ok"]
        if taken:
            return [_("{hotkey} is already used by another app.").format(hotkey=display_chord(c)) for c in taken]
        self._post(lambda: self._hotkeys_settled((), notify=False))
        return True

    def _hotkey_bindings(self, settings: CompanionSettings) -> dict[str, str]:
        """flow -> chord to hold: none when the engine owns the hotkeys; only flows the engine serves."""
        if hotkeys_owned_by_engine(settings.engine_mode):
            return {}
        flows = self._hotkey_flows
        return {b.flow: b.chord for b in settings.hotkeys if flows is None or b.flow in flows}

    def _register_best_effort(self, host: HotkeyHost, bindings: dict[str, str], *, notify: bool) -> None:
        """Hold what can be held; chords another app holds are retried later. Call with `_hotkey_lock`."""
        try:
            if notify:
                verdicts = host.register(bindings)
            else:
                held = host.registered()
                if held == bindings:
                    self._post(lambda: self._hotkeys_settled((), notify=False))
                    return
                if all(bindings.get(flow) == chord for flow, chord in held.items()):
                    # A retry: only the missing chords, so the held ones (Ctrl+Alt+V...) never
                    # drop for an instant while another chord is still taken.
                    verdicts = host.register_missing()
                else:
                    verdicts = host.register(bindings)
        except (OSError, TimeoutError):
            log.exception("hotkey registration failed")
            return
        taken = tuple(bindings[flow] for flow, verdict in verdicts.items() if verdict == "in_use" and flow in bindings)
        if taken:
            log.log(logging.INFO if notify else logging.DEBUG, "hotkeys held by another app: %s", taken)
        self._post(lambda: self._hotkeys_settled(taken, notify=notify))

    def _hotkeys_settled(self, taken: tuple[str, ...], *, notify: bool) -> None:
        """Dispatcher: notify once, then retry every `hotkey_retry_s` until every chord is ours
        (e.g. the user closed the old hotkeys script), the settings change, or Quit."""
        if self._quitting:
            return
        if not taken:
            timer = self._timers.pop("hotkey_retry", None)
            if timer is not None:
                timer.cancel()
            return
        if notify:
            self._input(NoticeRequested("hotkey_in_use", taken))
        self._schedule("hotkey_retry", self._timings.hotkey_retry_s, self._hotkey_retry_tick)

    def _hotkey_retry_tick(self) -> None:
        if self._quitting:
            return
        self._timers.pop("hotkey_retry", None)
        flows = self._hotkey_flows
        self._io.submit(lambda: self._register_hotkeys(set(flows) if flows else None, notify=False))

    def _apply_autostart(self, old: CompanionSettings, new: CompanionSettings) -> list[str]:
        # The OS (registry Run value / XDG file) is the source of truth for start at login.
        actual = self._autostart_state()
        if new.start_at_login == (old.start_at_login if actual is None else actual):
            return []
        try:
            self._desktop.set_autostart(new.start_at_login, autostart_command())
        except OSError:
            log.exception("start at login could not be changed")
            return [_("Could not change the start at login setting.")]
        return []

    def _autostart_state(self) -> bool | None:
        try:
            return self._desktop.autostart_enabled()
        except OSError:
            log.exception("reading the start at login state failed")
            return None

    def _reflect_autostart(self) -> None:
        """Start at login as the OS has it (the installer may have set it on its own)."""
        actual = self._autostart_state()
        settings = self._store.get()
        if actual is None or actual == settings.start_at_login:
            return
        updated = replace(settings, start_at_login=actual)
        if self._store.read_only:
            self._store.update_cached(updated)
            return
        try:
            self._store.save(updated)
        except OSError:
            self._store.update_cached(updated)

    def _settings_applied(self, old: CompanionSettings, new: CompanionSettings) -> None:
        self._input(SettingsSeen(new, hotkeys_owned_by_engine(new.engine_mode)))
        self._io.submit(lambda: self._cues.configure(new))
        if new.tray_icon_visible != old.tray_icon_visible and self._started and not self._quitting:
            # The user's own change in our settings: force the value. The icon is showing
            # already, so its entry most likely exists: try at once.
            self._promote_tray_icon(new.tray_icon_visible, (0.0, *self._timings.tray_promote_s), only_if_unset=False)
        if _engine_settings_changed(old, new) and not self._quitting:
            self._restart_supervision(new)
            return
        self._policy.set_config(self._policy_config(new, self._backend))

    def _restart_supervision(self, settings: CompanionSettings) -> None:
        """Engine settings changed: stop what we run (gracefully) and start over with the new ones."""
        old_backend, old_client, old_poller = self._backend, self._client, self._poller
        old_poller.stop()
        session = old_poller.take_session()

        def teardown() -> None:
            if session is not None:
                try:
                    old_client.unregister(session.client_id, timeout=QUIT_UNREGISTER_TIMEOUT_S)
                except DaemonError:
                    pass
            with self._spawn_lock:
                old_backend.stop(old_client, "restart")
                old_backend.close()

        self._supervisor.submit(teardown)
        was_engine_owned = hotkeys_owned_by_engine(self._mode)
        self._build_engine(settings)
        self._hotkey_flows = None
        if was_engine_owned != hotkeys_owned_by_engine(self._mode):
            self._io.submit(lambda: self._register_hotkeys(None))
        if self._started:
            self._poller.start()
            self._run(self._policy.start(time.monotonic()))
            self._sync_supervisor()
            self._schedule("probe", 0.5, self._probe_tick)

    def check_hotkey(self, chord: str, flow: str) -> HotkeyCheck:
        if hotkeys_owned_by_engine(self._store.get().engine_mode):
            return "engine_owned"
        normalized_chord = normalize_chord(chord)
        if normalized_chord is None:
            return "invalid"
        for binding in self._store.get().hotkeys:
            if binding.flow != flow and normalize_chord(binding.chord) == normalized_chord:
                return "duplicate"
        host = self._desktop.hotkeys
        if host is None:
            return "ok"
        try:
            return host.check(normalized_chord)
        except (OSError, TimeoutError):
            return "ok"  # the hotkey thread is not running yet: cannot tell

    def suspend_hotkeys(self, suspended: bool) -> None:
        host = self._desktop.hotkeys
        if host is not None:
            try:
                host.suspend(suspended)
            except (OSError, TimeoutError):
                log.exception("suspending hotkeys failed")
        self._post(lambda: self._input(HotkeysSuspendedSeen(suspended)))

    def preview_cue(self, cue: CueName, settings: CueSettings, master_volume: float) -> None:
        self._io.submit(lambda: self._cues.preview(cue, settings, master_volume))

    def recent_results(self) -> list[RecentItem]:
        return list(self._recent)

    def pending_results(self) -> list[RecentItem]:
        return list(self._pending)

    def copy_result(self, instance: str, result_seq: int) -> None:
        self._post(lambda: self._apply_delivery(self._delivery.copy(instance, result_seq, time.monotonic())))

    def clear_pending(self, keys: Sequence[tuple[str, int]]) -> None:
        chosen = tuple(keys)

        def run() -> None:
            cleared, effects = self._delivery.clear_pending(chosen)
            log.info("Not copied list: %s item(s) cleared", cleared)
            self._apply_delivery(effects)  # saves what is left (an empty list deletes pending.json)

        self._post(run)

    def open_logs(self) -> None:
        directory = self._logs_dir

        def job() -> None:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                self._desktop.open_path(directory)
            except OSError:
                log.exception("opening %s failed", directory)

        self._io.submit(job)

    # --- supervisor -------------------------------------------------------------------------------------

    def _sync_supervisor(self) -> None:
        policy = self._policy
        now = time.monotonic()
        self._input(
            SupervisorUpdate(
                policy.state,
                policy.attempt,
                policy.reason,
                policy.failure,
                policy.restarts(now),
                policy.pending_wsl_restart,
            )
        )
        if policy.state not in ("healthy", "degraded"):
            self._poller.set_enabled(False)

    def _run(self, actions: Sequence[Action]) -> None:
        backend, client = self._backend, self._client
        for action in actions:
            if isinstance(action, Launch):
                self._supervisor.submit(lambda: self._launch_job(backend, client))
            elif isinstance(action, StopEngine):
                stop = action
                self._supervisor.submit(lambda: self._stop_job(backend, client, stop))
            elif isinstance(action, RestartWsl):
                self._supervisor.submit(lambda: self._restart_wsl_job(backend, client))
            elif isinstance(action, CheckOtherDistros):
                self._supervisor.submit(lambda: self._distros_job(backend))
            elif isinstance(action, Notice):
                self._input(NoticeRequested(action.code, action.names))

    def _launch_job(self, backend: EngineBackend, client: DaemonClient) -> None:
        if self._quitting or backend is not self._backend:
            return
        if client.probe():
            try:
                client.health()
                answering = True
            except DaemonError:
                answering = False
            if answering:
                if backend.can_restart_wsl:
                    backend.ensure_keepalive()  # nothing else keeps the distro up for an attached daemon
                self._post(lambda: self._on_launched(backend, "attached", 0))
                return
        if not backend.can_spawn:
            self._post(lambda: self._on_launched(backend, "waiting", 0))
            return
        if backend.systemd_unit_enabled():
            backend.ensure_keepalive()
            self._post(lambda: self._input(NoticeRequested("systemd_attached")))
            self._post(lambda: self._on_launched(backend, "waiting", 0))
            return
        engine_dir = self._store.get().engine_dir
        if not engine_dir:
            engine_dir = backend.detect_engine_dir() or ""
            if engine_dir:
                self._remember_engine_dir(engine_dir)
        if not engine_dir:
            self._post(lambda: self._on_launch_failed(backend, "no_engine_dir"))
            return
        with self._spawn_lock:
            if self._quitting:
                return
            generation = next(self._generations)
            backend.stop_keepalive()

            def exited(gen: int, code: int, address_in_use: bool) -> None:
                # Through the supervisor queue, so it always lands after "launched".
                self._supervisor.submit(lambda: self._post(lambda: self._on_exit(backend, gen, code, address_in_use)))

            try:
                backend.spawn(engine_dir, generation, exited)
            except OSError:
                log.exception("spawning the engine failed")
                self._post(lambda: self._on_launch_failed(backend, "spawn_error"))
                return
        self._post(lambda: self._on_launched(backend, "spawned", generation))

    def _remember_engine_dir(self, engine_dir: str) -> None:
        settings = replace(self._store.get(), engine_dir=engine_dir)
        log.info("engine folder detected: %s", engine_dir)
        try:
            self._store.save(settings)
        except OSError:
            self._store.update_cached(settings)

    def _on_launched(self, backend: EngineBackend, outcome: LaunchOutcome, generation: int) -> None:
        if backend is not self._backend or self._quitting:
            return
        if outcome == "spawned":
            self._generation = generation
        self._run(self._policy.launched(time.monotonic(), outcome))
        self._sync_supervisor()
        self._schedule("probe", 0.5, self._probe_tick)

    def _on_launch_failed(self, backend: EngineBackend, failure: LaunchFailure) -> None:
        if backend is not self._backend or self._quitting:
            return
        self._run(self._policy.launch_failed(time.monotonic(), failure))
        self._sync_supervisor()

    def _on_exit(self, backend: EngineBackend, generation: int, code: int, address_in_use: bool) -> None:
        if backend is not self._backend or generation != self._generation or self._quitting:
            return
        log.info("engine exited with code %s (address in use: %s)", code, address_in_use)
        self._run(self._policy.process_exited(time.monotonic(), address_in_use))
        self._sync_supervisor()

    def _stop_job(self, backend: EngineBackend, client: DaemonClient, action: StopEngine) -> None:
        if backend.owned() is not None:
            backend.stop(client, action.reason)
            return
        if not action.include_attached or not client.probe():
            return
        try:
            client.shutdown(action.reason)
        except DaemonError:
            return
        deadline = time.monotonic() + ATTACHED_STOP_WAIT_S
        while time.monotonic() < deadline and client.probe():
            time.sleep(0.25)

    def _restart_wsl_job(self, backend: EngineBackend, client: DaemonClient) -> None:
        if self._quitting or backend is not self._backend:
            return
        if client.probe():
            try:
                client.shutdown("restart")
            except DaemonError:
                pass
        owned = backend.owned()
        if owned is not None:
            owned.wait(ATTACHED_STOP_WAIT_S)
        backend.shutdown_wsl()
        self._post(lambda: self._on_wsl_restart_done(backend))

    def _on_wsl_restart_done(self, backend: EngineBackend) -> None:
        if backend is not self._backend or self._quitting:
            return
        self._run(self._policy.wsl_restart_done(time.monotonic()))
        self._sync_supervisor()

    def _distros_job(self, backend: EngineBackend) -> None:
        names = backend.other_running_distros()

        def done() -> None:
            if backend is self._backend and not self._quitting:
                self._run(self._policy.other_distros(time.monotonic(), names))
                self._sync_supervisor()

        self._post(done)

    # --- probing and ticks ---------------------------------------------------------------------------------

    def _probe_tick(self) -> None:
        if self._quitting:
            return
        client = self._client
        self._prober.submit(lambda: self._probe_job(client))

    def _probe_job(self, client: DaemonClient) -> None:
        health: HealthPayload | None = None
        if client.probe():
            try:
                health = client.health()
            except DaemonError as exc:
                log.debug("health failed: %s", exc)
        if health is None:
            probe = Probe("down")
        elif is_outdated(health):
            # A v1 daemon (no `ready`, no `api_version`) is up and answering: for supervision
            # that is "ready" (never a start timeout); the model flags it as outdated and
            # triggers are refused. Its audio field never drives a WSL restart.
            probe = Probe("ready", "unknown")
        else:
            audio = health.get("audio", "unknown")
            probe = Probe(
                "ready" if health.get("ready") else "starting",
                audio if audio in ("ok", "down", "unknown") else "unknown",
            )
            if health.get("auth"):
                client.token()  # read it now (through wsl.exe), not on the first trigger
        self._post(lambda: self._on_probe(client, probe, health))

    def _on_probe(self, client: DaemonClient, probe: Probe, health: HealthPayload | None) -> None:
        if self._quitting or client is not self._client:
            return  # a stale client's result: its replacement schedules its own probes
        self._schedule("probe", self._timings.probe_interval_s, self._probe_tick)
        if health is not None:
            self._input(HealthSeen(health))
            self._sync_hotkey_flows()
        self._run(self._policy.probe(time.monotonic(), probe))
        self._sync_supervisor()
        connected = (
            self._policy.state in ("healthy", "degraded")
            and health is not None
            and not is_outdated(health)
            and bool(health.get("ready"))
        )
        self._poller.set_enabled(connected)

    def _tick(self) -> None:
        if self._quitting:
            return
        self._schedule("tick", self._timings.tick_s, self._tick)
        now = time.monotonic()
        self._input(Tick())
        self._run(self._policy.tick(now))
        self._apply_delivery(self._delivery.tick(now))
        self._sync_supervisor()

    def _mic_tick(self) -> None:
        if self._quitting:
            return
        desktop = self._desktop

        def job() -> None:
            count = desktop.mic_count()
            self._post(lambda: self._on_mic_count(count))

        self._prober.submit(job)

    def _on_mic_count(self, count: int) -> None:
        if self._quitting:
            return
        alert = count == 0 or self._core.warning is not None
        self._schedule("mic", self._timings.mic_alert_poll_s if alert else self._timings.mic_poll_s, self._mic_tick)
        if count != self._core.mic_count:
            self._input(MicCountSeen(count))
            self._run(self._policy.mic_count_changed(time.monotonic(), count))
            self._sync_supervisor()

    # --- tray icon visibility ----------------------------------------------------------------------------------

    def _promote_tray_icon(self, promoted: bool, offsets: tuple[float, ...], *, only_if_unset: bool) -> None:
        """Dispatcher: set the tray icon's taskbar visibility, trying at each offset (seconds
        from now) until the OS has an entry for it; a newer request cancels this one.
        `only_if_unset`: leave entries the user already decided on in the Windows settings."""
        promote = self._desktop.set_tray_icon_promoted
        if promote is None or not offsets:
            return
        self._tray_promotion += 1
        generation = self._tray_promotion
        waits = tuple(max(0.0, offset - previous) for previous, offset in zip((0.0, *offsets), offsets, strict=False))

        def attempt() -> bool:
            return promote(promoted, only_if_unset)

        self._schedule_tray_promote(attempt, waits, generation)

    def _schedule_tray_promote(self, attempt: Callable[[], bool], waits: tuple[float, ...], generation: int) -> None:
        wait, rest = waits[0], waits[1:]
        self._schedule("tray_promote", wait, lambda: self._tray_promote_try(attempt, rest, generation))

    def _tray_promote_try(self, attempt: Callable[[], bool], rest: tuple[float, ...], generation: int) -> None:
        if self._quitting or generation != self._tray_promotion:
            return
        self._timers.pop("tray_promote", None)

        def job() -> None:
            try:
                found = attempt()
            except Exception:  # noqa: BLE001 - cosmetic: never break the io worker
                log.exception("changing the tray icon visibility failed")
                found = False
            self._post(lambda: self._tray_promote_done(attempt, rest, generation, found))

        self._io.submit(job)

    def _tray_promote_done(
        self, attempt: Callable[[], bool], rest: tuple[float, ...], generation: int, found: bool
    ) -> None:
        if found or self._quitting or generation != self._tray_promotion:
            return
        if not rest:
            log.info("no taskbar entry for the tray icon (Windows 10, or Explorer has not created it)")
            return
        self._schedule_tray_promote(attempt, rest, generation)

    # --- hotkeys --------------------------------------------------------------------------------------------

    def _sync_hotkey_flows(self) -> None:
        names = tuple(flow.name for flow in self._core.flows)
        if names and names != self._hotkey_flows:
            self._hotkey_flows = names
            self._io.submit(lambda: self._register_hotkeys(set(names)))

    def _register_hotkeys(self, flows: set[str] | None, *, notify: bool = True) -> None:
        """Best effort (startup, flows changed, retries). Only flows the engine serves get a hotkey.

        `notify`: tell the user about chords another app holds (not again on retries)."""
        host = self._desktop.hotkeys
        if host is None or self._quitting:
            return
        with self._hotkey_lock:
            settings = self._store.get()
            if hotkeys_owned_by_engine(settings.engine_mode):
                bindings: dict[str, str] = {}
            else:
                bindings = {b.flow: b.chord for b in settings.hotkeys if flows is None or b.flow in flows}
            self._register_best_effort(host, bindings, notify=notify)

    # --- events ---------------------------------------------------------------------------------------------

    def _on_registered(self, poller: EventPoller, response: RegisterResponse) -> None:
        if poller is not self._poller or self._quitting:
            return
        instance = str(response["instance"])
        now = time.monotonic()
        log.info("registered as %s (instance %s, leases %s)", response["client_id"], instance, response.get("granted"))
        self._apply_delivery(self._delivery.set_instance(instance, now))
        snapshot = response["snapshot"]
        self._input(SnapshotSeen(instance, snapshot))
        self._run(self._policy.engine_activity(now, snapshot["state"]["state"] != "idle"))
        if "clipboard" in response.get("granted", []):
            self._apply_delivery(self._delivery.on_unacked(instance, list(snapshot.get("unacked", [])), now))
            self._schedule("reconcile", 0.2, self._reconcile_tick)  # reconcile on (re)connect
        self._sync_supervisor()

    def _on_events(self, poller: EventPoller, response: EventsResponse) -> None:
        if poller is not self._poller or self._quitting:
            return
        instance = str(response["instance"])
        if instance != self._delivery.instance:
            self._apply_delivery(self._delivery.set_instance(instance, time.monotonic()))
        for event in response.get("events", []):
            try:
                self._on_event(instance, event)
            except Exception:  # noqa: BLE001 - one malformed event must not drop the batch
                log.exception("event %r could not be handled", event)
        self._sync_supervisor()

    def _on_event(self, instance: str, event: object) -> None:
        if not isinstance(event, dict):
            return
        now = time.monotonic()
        if event["type"] == "result":
            data = event["data"]
            self._apply_delivery(self._delivery.on_result(instance, data, float(event.get("ts", 0.0)), now))
            if not data["needs_delivery"]:
                self._input(ResultSeen(data))
        elif event["type"] == "snapshot":
            self._input(EventSeen(event))  # type: ignore[arg-type]
            session = self._poller.session()
            if session is not None and "clipboard" in session.granted:
                self._apply_delivery(self._delivery.on_unacked(instance, list(event["data"].get("unacked", [])), now))
        elif event["type"] == "state":
            self._input(EventSeen(event))  # type: ignore[arg-type]
            self._run(self._policy.engine_activity(now, event["data"]["state"] != "idle"))
        elif event["type"] == "error" and event["data"].get("code") == "mic_unavailable":
            self._on_mic_error(event)
        elif event["type"] == "shutdown":
            self._run(self._policy.daemon_shutdown(now, event["data"].get("reason", "supervisor")))
        else:
            self._input(EventSeen(event))  # type: ignore[arg-type]

    def _on_mic_error(self, event: dict[str, object]) -> None:
        """Diagnose with a fresh capture device count (no microphone vs WSL audio stuck)."""
        desktop = self._desktop

        def job() -> None:
            count = desktop.mic_count()

            def handle() -> None:
                if self._quitting:
                    return
                if count != self._core.mic_count and count >= 0:
                    self._core = replace(self._core, mic_count=count)  # silently: the error speaks for it
                self._input(EventSeen(event))  # type: ignore[arg-type]
                self._run(self._policy.mic_error(time.monotonic(), self._core.mic_count, self._core.audio))
                self._sync_supervisor()
                self._schedule("mic", self._timings.mic_alert_poll_s, self._mic_tick)

            self._post(handle)

        self._prober.submit(job)

    # --- delivery ---------------------------------------------------------------------------------------------

    def _apply_delivery(self, effects: Sequence[DeliveryEffect]) -> None:
        for effect in effects:
            if isinstance(effect, StartDelivery):
                self._start_delivery(effect.job.key, effect.job.text)
            elif isinstance(effect, SendAck):
                self._send_ack(effect.instance, effect.acks, 0)
            elif isinstance(effect, Delivered):
                self._input(DeliveryOutcome(effect.job, True))
            elif isinstance(effect, DeliveryFailed):
                self._input(DeliveryOutcome(effect.job, False))
            elif isinstance(effect, NotCopied):
                self._input(NotCopiedSeen(effect.count))
        self._recent = tuple(self._delivery.recent())
        pending = tuple(self._delivery.pending())
        if pending != self._pending:
            self._pending = pending
            self._save_pending(pending)
        if len(self._pending) != self._core.pending_unacked:
            self._input(PendingCount(len(self._pending)))
        wakeup = self._delivery.next_wakeup()
        if wakeup is not None:
            self._schedule(
                "delivery",
                max(0.0, wakeup - time.monotonic()),
                lambda: self._apply_delivery(self._delivery.tick(time.monotonic())),
            )

    def _save_pending(self, items: tuple[RecentItem, ...]) -> None:
        """Write the Not copied list in the background, in order (the store never raises
        on I/O errors: it logs them and the list stays in memory)."""
        store = self._pending_store
        if store is None:
            return

        def write() -> None:
            store.save(items)

        self._pending_writer.submit(write)

    def _flush_pending(self) -> None:
        """Quit: let the last write of the Not copied list finish (bounded)."""
        if self._pending_store is None:
            return
        written = threading.Event()
        self._pending_writer.submit(written.set)
        if not written.wait(PENDING_FLUSH_TIMEOUT_S):
            log.warning("the Not copied list was still being written when quitting")

    def _start_delivery(self, key: tuple[str, int], text: str) -> None:
        def done(ok: bool) -> None:
            self._post(lambda: self._apply_delivery(self._delivery.on_delivery_done(key, ok, time.monotonic())))

        try:
            self._desktop.clipboard.deliver(text, done)
        except Exception:  # noqa: BLE001 - counts as a failed attempt
            log.exception("clipboard delivery could not start")
            done(False)

    def _send_ack(self, instance: str, acks: tuple[Ack, ...], attempt: int) -> None:
        poller, client = self._poller, self._client

        def retry() -> None:
            if attempt + 1 < ACK_RETRIES and not self._quitting:
                self._post(lambda: self._later(1.0 + attempt, lambda: self._send_ack(instance, acks, attempt + 1)))
            else:
                log.warning("ACK %s for instance %s dropped after %s attempts", list(acks), instance, attempt + 1)

        def job() -> None:
            session = poller.session()
            if session is None:
                retry()
                return
            try:
                client.ack(AckRequest(client_id=session.client_id, instance=instance, acks=list(acks)))
            except DaemonError as exc:
                if exc.kind == "http" and exc.status == 409:
                    log.info("ACK for an old instance %s ignored", instance)
                    return
                if exc.kind == "http" and exc.status == 410:
                    poller.invalidate(session.client_id)
                log.info("ACK failed (%s); retrying", exc)
                retry()

        self._io.submit(job)

    def _reconcile_tick(self) -> None:
        if self._quitting:
            return
        self._schedule("reconcile", self._timings.reconcile_s, self._reconcile_tick)
        session = self._poller.session()
        if session is None or "clipboard" not in session.granted:
            return
        client = self._client

        def job() -> None:
            try:
                response = client.results("unacked", limit=50)
            except DaemonError as exc:
                log.debug("reconciliation failed: %s", exc)
                return
            self._post(lambda: self._on_unacked(client, response))

        self._io.submit(job)

    def _on_unacked(self, client: DaemonClient, response: ResultsResponse) -> None:
        if client is not self._client or self._quitting:
            return
        instance = str(response["instance"])
        if instance != self._delivery.instance:
            return
        self._apply_delivery(self._delivery.on_unacked(instance, list(response["results"]), time.monotonic()))


_LOG_HANDLER_NAME: Final = "voicemate-companion-file"


def setup_file_logging(log_path: Path | None = None) -> None:
    """companion.log (rotating) for the `app` loggers; idempotent, never raises (without a
    data folder, `paths.DataDirError`, or a folder it cannot write, it logs and returns)."""
    app_logger = logging.getLogger("app")
    if any(handler.get_name() == _LOG_HANDLER_NAME for handler in app_logger.handlers):
        return
    try:
        target = log_path or paths.companion_log_path()
    except paths.DataDirError as exc:
        log.warning("companion log unavailable: %s", exc)
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(target, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    except OSError as exc:
        log.warning("companion log %s unavailable: %s", target, exc)
        return
    handler.set_name(_LOG_HANDLER_NAME)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    app_logger.addHandler(handler)
    if app_logger.level == logging.NOTSET or app_logger.level > logging.INFO:
        app_logger.setLevel(logging.INFO)


def create_controller(settings_path: Path | None = None) -> CompanionController:
    """The production controller: settings from `settings_path` (default: the per-user file),
    the Not copied list in `paths.pending_path()`.

    Also starts companion.log (the UI only logs to stderr). Raises `paths.DataDirError`
    when the per-user folders cannot be located (main.py reports it)."""
    setup_file_logging()
    controller: CompanionController = CompanionControllerImpl(
        SettingsStore(settings_path or paths.settings_path()),
        pending_store=PendingStore(paths.pending_path()),
    )
    return controller
