from __future__ import annotations

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
    command_server.command_received.connect(received.append)
    assert command_server.listen()
    yield command_server, received
    command_server.close()


def test_two_clients_forward_their_commands(
    server: tuple[CommandServer, list[str]], process_events: Callable[..., bool]
) -> None:
    command_server, received = server
    # Both clients and the server share this thread: send_command runs its own event loop.
    assert send_command(command_server.name, "settings") is True
    assert send_command(command_server.name, "quit") is True
    process_events()
    assert received == ["settings", "quit"]


def test_unknown_commands_are_refused(
    server: tuple[CommandServer, list[str]], process_events: Callable[..., bool]
) -> None:
    command_server, received = server
    assert send_command(command_server.name, "format-disk") is False  # type: ignore[arg-type]
    process_events()
    assert received == []


def test_sending_without_a_server_fails_fast(qapp: QApplication) -> None:
    assert send_command(server_name(f"-absent-{uuid.uuid4().hex[:8]}"), "show", timeout_ms=500) is False


def test_server_name_is_per_user_and_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(single_instance.getpass, "getuser", lambda: "Álli T\\x")
    name = server_name()
    assert name.startswith("voicemate-companion-")
    assert all(char.isascii() and (char.isalnum() or char in "._-") for char in name)
    monkeypatch.setattr(single_instance.getpass, "getuser", lambda: "alli")
    assert server_name("-demo") == "voicemate-companion-alli-demo"


def test_file_lock_admits_one_holder(qapp: QApplication, tmp_path: Path) -> None:
    first = FileInstanceLock(tmp_path / "companion.lock")
    second = FileInstanceLock(tmp_path / "companion.lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_create_instance_lock_admits_one_instance(qapp: QApplication) -> None:
    import sys

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
