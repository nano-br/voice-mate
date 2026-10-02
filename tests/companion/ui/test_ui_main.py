from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.companion import main as companion_main  # noqa: E402
from app.companion.contract import CompanionSettings  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402
from app.companion.ui.single_instance import Reply  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def keep_the_test_process_aumid(monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> None:
    if sys.platform == "win32":
        from app.companion.win import aumid

        monkeypatch.setattr(aumid, "set_app_user_model_id", lambda app_id="": True)
    monkeypatch.setattr(companion_main.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(companion_main, "_allow_foreground", lambda: None)


class _Lock:
    """Taken by another instance until `free_after` acquire attempts."""

    def __init__(self, free_after: int | None = None) -> None:
        self.attempts = 0
        self.free_after = free_after
        self.released = False

    def acquire(self) -> bool:
        self.attempts += 1
        return self.free_after is not None and self.attempts > self.free_after

    def release(self) -> None:
        self.released = True


class _Replies:
    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.sent: list[tuple[str, str]] = []

    def __call__(self, name: str, command: str, timeout_ms: int = 3000) -> Reply:
        self.sent.append((name, command))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def _patch(monkeypatch: pytest.MonkeyPatch, lock: _Lock, replies: _Replies) -> list[argparse.Namespace]:
    started: list[argparse.Namespace] = []
    monkeypatch.setattr(companion_main, "create_instance_lock", lambda suffix: lock)
    monkeypatch.setattr(companion_main, "send_command", replies)

    def run(app: QApplication, args: argparse.Namespace, suffix: str) -> int:
        started.append(args)
        return 7

    monkeypatch.setattr(companion_main, "_run", run)
    return started


def test_parse_args() -> None:
    args = companion_main.parse_args(["--command", "restart-wsl", "--autostart"])
    assert (args.command, args.autostart, args.demo) == ("restart-wsl", True, False)
    with pytest.raises(SystemExit):
        companion_main.parse_args(["--command", "format-disk"])


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], "show"),  # clicking the pinned shortcut again opens the status window
        (["--command", "settings"], "settings"),
        (["--command", "quit"], "quit"),
        (["--command", "restart-engine"], "restart-engine"),
    ],
)
def test_a_second_launch_forwards_its_command(monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: str) -> None:
    replies = _Replies("ok")
    started = _patch(monkeypatch, _Lock(), replies)
    assert companion_main.main(argv) == 0
    assert [command for _name, command in replies.sent] == [expected]
    assert Path(replies.sent[0][0]).name.startswith("voicemate-companion-")
    assert started == []


def test_a_second_launch_retries_while_the_first_one_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = _Replies("none", "none", "ok")  # not listening yet, then listening
    _patch(monkeypatch, _Lock(), replies)
    assert companion_main.main(["--command", "settings"]) == 0
    assert len(replies.sent) == 3


def test_a_second_launch_gives_up_after_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(companion_main, "FORWARD_TIMEOUT_S", 0.0)
    _patch(monkeypatch, _Lock(), _Replies("none"))
    assert companion_main.main(["--command", "quit"]) == 1


def test_a_refused_command_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _Lock(), _Replies("error"))
    assert companion_main.main(["--command", "show"]) == 1


def test_a_plain_launch_waits_for_a_quitting_instance_then_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    lock = _Lock(free_after=3)  # the old instance releases the lock a little later
    started = _patch(monkeypatch, lock, _Replies("quitting", "quitting", "none"))
    assert companion_main.main([]) == 7
    assert len(started) == 1
    assert lock.released


def test_a_command_to_a_quitting_instance_does_not_start_voicemate(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = _Replies("quitting")
    started = _patch(monkeypatch, _Lock(free_after=1), replies)
    assert companion_main.main(["--command", "settings"]) == 0
    assert replies.sent  # it did ask
    assert started == []


def test_a_quitting_instance_is_waited_for_a_fixed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every "quitting" reply must not push the deadline further: the wait is bounded."""
    monkeypatch.setattr(companion_main, "FORWARD_TIMEOUT_S", 0.0)
    monkeypatch.setattr(companion_main, "QUIT_WAIT_S", 0.05)
    started = _patch(monkeypatch, _Lock(), _Replies("quitting"))
    assert companion_main.main([]) == 1
    assert started == []


def test_a_command_releases_the_lock_it_got_while_forwarding(monkeypatch: pytest.MonkeyPatch) -> None:
    lock = _Lock(free_after=1)  # taken at first, free on the next try
    started = _patch(monkeypatch, lock, _Replies("none"))
    assert companion_main.main(["--command", "settings"]) == 0
    assert started == []
    assert lock.released


def test_forwarding_lets_the_running_instance_take_the_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    _patch(monkeypatch, _Lock(), _Replies("ok"))
    monkeypatch.setattr(companion_main, "_allow_foreground", lambda: calls.append(True))
    assert companion_main.main(["--command", "show"]) == 0
    assert calls == [True]


@pytest.mark.skipif(sys.platform != "win32", reason="AllowSetForegroundWindow is Windows-only")
def test_allow_any_foreground_calls_windows() -> None:
    from app.companion.win.single_instance import allow_any_foreground

    assert isinstance(allow_any_foreground(), bool)


def test_buffered_commands_run_after_everything_started(monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> None:
    order: list[str] = []

    class Server:
        def __init__(self, name: str) -> None:
            order.append("server")

        def listen(self) -> bool:
            order.append("listen")
            return True

        def set_quitting(self) -> None:
            pass

        def set_handler(self, handler: object) -> None:
            order.append("handler")

        def close(self) -> None:
            pass

    class Controller(FakeController):
        def start(self) -> None:
            order.append("controller.start")
            super().start()

    monkeypatch.setattr(companion_main, "CommandServer", Server)
    monkeypatch.setattr(companion_main, "_create_controller", lambda demo: Controller())
    monkeypatch.setattr(qapp, "exec", lambda: 0)
    assert companion_main._run(qapp, companion_main.parse_args(["--autostart", "--demo"]), "-test") == 0
    assert order == ["server", "listen", "controller.start", "handler"]


def test_a_second_autostart_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = _Replies("ok")
    _patch(monkeypatch, _Lock(), replies)
    assert companion_main.main(["--autostart"]) == 0
    assert replies.sent == []


@pytest.mark.parametrize("command", ["quit", "show", "settings", "restart-engine", "restart-wsl"])
def test_a_command_never_starts_voicemate(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    """The uninstaller sends quit; a jump list task may outlive the app: exit 0, start nothing."""
    lock = _Lock(free_after=0)
    started = _patch(monkeypatch, lock, _Replies("ok"))
    assert companion_main.main(["--command", command]) == 0
    assert started == []
    assert lock.released


def test_a_plain_launch_starts_when_nothing_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    lock = _Lock(free_after=0)
    started = _patch(monkeypatch, lock, _Replies("ok"))
    assert companion_main.main(["--autostart"]) == 7
    assert started[0].autostart
    assert lock.released


def test_demo_uses_its_own_lock_and_the_fake_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    suffixes: list[str] = []
    replies = _Replies("ok")
    _patch(monkeypatch, _Lock(), replies)

    def lock_for(suffix: str) -> _Lock:
        suffixes.append(suffix)
        return _Lock()

    monkeypatch.setattr(companion_main, "create_instance_lock", lock_for)
    assert companion_main.main(["--demo"]) == 0
    assert suffixes == ["-demo"]
    assert replies.sent[0][0].endswith(("-demo", "-demo.sock"))
    assert isinstance(companion_main._create_controller(demo=True), FakeController)


@pytest.fixture
def remove_translators(qapp: QApplication) -> Iterator[list[object]]:
    installed: list[object] = []
    yield installed
    for translator in installed:
        QCoreApplication.removeTranslator(translator)  # type: ignore[arg-type]


def test_qt_strings_follow_the_ui_language(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, remove_translators: list[object]
) -> None:
    monkeypatch.setattr(companion_main, "active_language", lambda: "en")
    assert companion_main.install_qt_translations(qapp) is None
    monkeypatch.setattr(companion_main, "active_language", lambda: "pt_BR")
    translator = companion_main.install_qt_translations(qapp)
    if translator is None:
        pytest.skip("this Qt build ships no qtbase_pt_BR.qm")
    remove_translators.append(translator)
    assert QCoreApplication.translate("QPlatformTheme", "Cancel") == "Cancelar"


@pytest.mark.skipif(sys.platform != "win32", reason="the jump list is Windows-only")
def test_jump_list_offers_restart_wsl_only_in_wsl_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.companion.win import jumplist

    written: list[list[str]] = []
    monkeypatch.setattr(jumplist, "set_jump_list_tasks", lambda tasks: written.append([t.command for t in tasks]))
    update = companion_main.jump_list_updater()
    assert update is not None
    update(CompanionSettings(engine_mode="wsl2"))
    update(CompanionSettings(engine_mode="external"))
    assert written == [
        ["settings", "restart-engine", "restart-wsl", "quit"],
        ["settings", "restart-engine", "quit"],
    ]


def test_the_core_controller_is_imported_lazily() -> None:
    """The UI branch must run (--demo) without app.companion.controller."""
    probe = "import sys, app.companion.main\nprint('app.companion.controller' in sys.modules)\n"
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=_ROOT)
    assert result.stdout.strip() == "False"


def test_after_restart_waits_for_the_lock_instead_of_forwarding(monkeypatch: pytest.MonkeyPatch) -> None:
    """The instance that restarted us is quitting: never send it "show", take over once it exits."""
    lock = _Lock(free_after=5)
    replies = _Replies("ok")
    started = _patch(monkeypatch, lock, replies)
    assert companion_main.main(["--after-restart"]) == 7
    assert replies.sent == []
    assert lock.attempts == 6
    assert started[0].after_restart and not started[0].autostart
    assert lock.released


def test_after_restart_gives_up_waiting_like_a_plain_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old instance never went away: show it, as any second launch does."""
    monkeypatch.setattr(companion_main, "RESTART_WAIT_S", 0.0)
    replies = _Replies("ok")
    started = _patch(monkeypatch, _Lock(), replies)
    assert companion_main.main(["--after-restart"]) == 0
    assert [command for _name, command in replies.sent] == ["show"]
    assert started == []


def test_after_restart_is_hidden_from_the_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        companion_main.parse_args(["--help"])
    assert "--after-restart" not in capsys.readouterr().out
    assert not companion_main.parse_args([]).after_restart


def test_run_gives_the_ui_a_relauncher_with_the_launch_arguments(
    monkeypatch: pytest.MonkeyPatch, qapp: QApplication
) -> None:
    from app.companion.ui.app import CompanionUi

    relaunched: list[list[str]] = []
    started: list[CompanionUi] = []

    class Server:
        def __init__(self, name: str) -> None:
            pass

        def listen(self) -> bool:
            return True

        def set_quitting(self) -> None:
            pass

        def set_handler(self, handler: object) -> None:
            pass

        def close(self) -> None:
            pass

    def start(self: CompanionUi, show_window: bool, wait_for_tray: bool = False) -> None:
        started.append(self)

    monkeypatch.setattr(companion_main, "CommandServer", Server)
    monkeypatch.setattr(companion_main, "_create_controller", lambda demo: FakeController())
    monkeypatch.setattr(companion_main, "relaunch_companion", lambda args: relaunched.append(list(args)))
    monkeypatch.setattr(companion_main.sys, "argv", ["VoiceMate.exe", "--autostart", "--demo"])
    monkeypatch.setattr(CompanionUi, "start", start)
    monkeypatch.setattr(qapp, "exec", lambda: 0)
    assert companion_main._run(qapp, companion_main.parse_args(["--autostart", "--demo"]), "-test") == 0
    (companion,) = started
    assert companion._relaunch is not None
    companion._relaunch()  # not restart_app(): that would quit the test's QApplication
    assert relaunched == [["--autostart", "--demo"]]  # relaunch_argv drops --autostart itself
    companion.bridge.detach()
    companion.deleteLater()
