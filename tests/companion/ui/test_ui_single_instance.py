from __future__ import annotations

import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.companion.ui import single_instance  # noqa: E402
from app.companion.ui.single_instance import (  # noqa: E402
    CommandServer,
    FileInstanceLock,
    send_command,
    server_name,
)


@pytest.fixture
def server(qapp: QApplication) -> Iterator[tuple[CommandServer, list[str]]]:
    command_server = CommandServer(server_name(f"-test-{uuid.uuid4().hex[:8]}"))
    received: list[str] = []
    command_server.set_handler(received.append)
    assert command_server.listen()
    yield command_server, received
    command_server.close()


def test_two_clients_forward_their_commands(
    server: tuple[CommandServer, list[str]], process_events: Callable[..., bool]
) -> None:
    command_server, received = server
    # Both clients and the server share this thread: send_command runs its own event loop.
    assert send_command(command_server.name, "settings") == "ok"
    assert send_command(command_server.name, "quit") == "ok"
    process_events()
    assert received == ["settings", "quit"]


def test_unknown_commands_are_refused(
    server: tuple[CommandServer, list[str]], process_events: Callable[..., bool]
) -> None:
    command_server, received = server
    assert send_command(command_server.name, "format-disk") == "error"  # type: ignore[arg-type]
    process_events()
    assert received == []


def test_commands_wait_for_the_ui(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    """The server listens before the UI exists: early commands are kept, in order."""
    command_server = CommandServer(server_name(f"-test-{uuid.uuid4().hex[:8]}"))
    assert command_server.listen()
    assert send_command(command_server.name, "show") == "ok"
    assert send_command(command_server.name, "settings") == "ok"
    received: list[str] = []
    command_server.set_handler(received.append)
    assert received == ["show", "settings"]
    assert send_command(command_server.name, "quit") == "ok"
    process_events()
    assert received == ["show", "settings", "quit"]
    command_server.close()


def test_a_quitting_instance_says_so_and_drops_the_command(
    server: tuple[CommandServer, list[str]], process_events: Callable[..., bool]
) -> None:
    command_server, received = server
    command_server.set_quitting()
    assert send_command(command_server.name, "show") == "quitting"
    process_events()
    assert received == []


def test_sending_without_a_server_answers_none(qapp: QApplication) -> None:
    assert send_command(server_name(f"-absent-{uuid.uuid4().hex[:8]}"), "show", timeout_ms=500) == "none"


def test_server_name_is_per_user_and_private(monkeypatch: pytest.MonkeyPatch, qapp: QApplication) -> None:
    monkeypatch.setattr(single_instance.getpass, "getuser", lambda: "Álli T\\x")
    name = Path(server_name()).name
    assert name.startswith("voicemate-companion-")
    assert all(char.isascii() and (char.isalnum() or char in "._-") for char in name)
    monkeypatch.setattr(single_instance.getpass, "getuser", lambda: "alli")
    if sys.platform == "win32":
        assert server_name("-demo") == "voicemate-companion-alli-demo"
    else:
        # An absolute socket path in the user's private (0700) runtime directory.
        path = Path(server_name("-demo"))
        assert path.is_absolute() and path.parent == single_instance.runtime_dir()
        assert path.name == "voicemate-companion-alli-demo.sock"


def test_file_lock_admits_one_holder(qapp: QApplication, tmp_path: Path) -> None:
    first = FileInstanceLock(tmp_path / "companion.lock")
    second = FileInstanceLock(tmp_path / "companion.lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_create_instance_lock_admits_one_instance(qapp: QApplication) -> None:
    suffix = f"-test-{uuid.uuid4().hex[:8]}"
    lock = single_instance.create_instance_lock(suffix)
    expected = "NamedMutex" if sys.platform == "win32" else "FileInstanceLock"
    assert type(lock).__name__ == expected
    assert lock.acquire()
    assert not single_instance.create_instance_lock(suffix).acquire()
    lock.release()
    again = single_instance.create_instance_lock(suffix)
    assert again.acquire()
    again.release()
