from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from app.companion import main as companion_main  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def keep_the_test_process_aumid(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        from app.companion.win import aumid

        monkeypatch.setattr(aumid, "set_app_user_model_id", lambda app_id="": True)


class _TakenLock:
    def acquire(self) -> bool:
        return False

    def release(self) -> None:
        raise AssertionError("never acquired")


class _FreeLock:
    def __init__(self) -> None:
        self.released = False

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        self.released = True


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
    sent: list[tuple[str, str]] = []

    def send(name: str, command: str) -> bool:
        sent.append((name, command))
        return True

    monkeypatch.setattr(companion_main, "create_instance_lock", lambda suffix: _TakenLock())
    monkeypatch.setattr(companion_main, "send_command", send)
    monkeypatch.setattr(companion_main, "QCoreApplication", lambda args: None)
    assert companion_main.main(argv) == 0
    assert [command for _name, command in sent] == [expected]
    assert sent[0][0].startswith("voicemate-companion-")


def test_a_second_autostart_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(companion_main, "create_instance_lock", lambda suffix: _TakenLock())
    monkeypatch.setattr(companion_main, "send_command", lambda name, command: pytest.fail("must not send"))
    assert companion_main.main(["--autostart"]) == 0


def test_a_second_launch_reports_an_unanswered_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(companion_main, "create_instance_lock", lambda suffix: _TakenLock())
    monkeypatch.setattr(companion_main, "send_command", lambda name, command: False)
    monkeypatch.setattr(companion_main, "QCoreApplication", lambda args: None)
    assert companion_main.main(["--command", "quit"]) == 1


@pytest.mark.parametrize("command", ["quit", "show", "settings", "restart-engine", "restart-wsl"])
def test_a_command_never_starts_voicemate(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    """The uninstaller sends quit; a jump list task may outlive the app: exit 0, start nothing."""
    lock = _FreeLock()
    monkeypatch.setattr(companion_main, "create_instance_lock", lambda suffix: lock)
    monkeypatch.setattr(companion_main, "_run", lambda args, suffix: pytest.fail("must not start"))
    assert companion_main.main(["--command", command]) == 0
    assert lock.released


def test_demo_uses_its_own_lock_and_the_fake_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    suffixes: list[str] = []

    def lock_for(suffix: str) -> _TakenLock:
        suffixes.append(suffix)
        return _TakenLock()

    monkeypatch.setattr(companion_main, "create_instance_lock", lock_for)
    monkeypatch.setattr(companion_main, "send_command", lambda name, command: name.endswith("-demo"))
    monkeypatch.setattr(companion_main, "QCoreApplication", lambda args: None)
    assert companion_main.main(["--demo"]) == 0
    assert suffixes == ["-demo"]
    assert isinstance(companion_main._create_controller(demo=True), FakeController)


def test_the_core_controller_is_imported_lazily() -> None:
    """The UI branch must run (--demo) without app.companion.controller."""
    probe = "import sys, app.companion.main\nprint('app.companion.controller' in sys.modules)\n"
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=_ROOT)
    assert result.stdout.strip() == "False"
