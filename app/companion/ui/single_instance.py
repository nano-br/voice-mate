"""Single instance: the lock, and the channel that forwards `--command` to the running app.

- Lock: on Windows the named mutex (`app.companion.win.single_instance`), since
  `QLocalServer.listen()` succeeds twice there; elsewhere a `QLockFile` in
  `$XDG_RUNTIME_DIR` (a lock left by a dead process is detected as stale).
- Channel: a per-user `QLocalServer`. A second process connects, writes one line
  (`show`, `quit`, `restart-engine`, `restart-wsl` or `settings`), and waits for
  "ok" before exiting. Only whitelisted commands are accepted.
"""

from __future__ import annotations

import getpass
import hashlib
import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import Final, Protocol, get_args

from PySide6.QtCore import QByteArray, QEventLoop, QLockFile, QObject, QStandardPaths, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from app.companion.contract import LOCAL_SERVER_PREFIX, SINGLE_INSTANCE_MUTEX, CompanionCommand

log = logging.getLogger(__name__)

COMMANDS: Final[tuple[CompanionCommand, ...]] = get_args(CompanionCommand)
REPLY_OK: Final = b"ok"
REPLY_ERROR: Final = b"error"
MAX_LINE: Final = 64


class InstanceLock(Protocol):
    def acquire(self) -> bool: ...

    def release(self) -> None: ...


class FileInstanceLock:
    """`QLockFile`: Linux (and any non-Windows) lock; stale when its owner process is gone."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = QLockFile(str(path))
        self._lock.setStaleLockTime(0)  # never stale by age; only when the owner PID is dead

    def acquire(self) -> bool:
        return bool(self._lock.tryLock(0))

    def release(self) -> None:
        self._lock.unlock()


def _user_tag() -> str:
    """The user name, safe for a pipe/socket name (pipes are machine-global on Windows)."""
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", user)
    if not safe or safe != user:
        # Keep distinct users distinct even when their names sanitize the same way.
        safe = f"{safe}-{hashlib.sha1(user.encode('utf-8')).hexdigest()[:8]}"
    return safe


def server_name(suffix: str = "") -> str:
    return f"{LOCAL_SERVER_PREFIX}{_user_tag()}{suffix}"


def create_instance_lock(suffix: str = "") -> InstanceLock:
    if sys.platform == "win32":
        from app.companion.win.single_instance import NamedMutex

        return NamedMutex(SINGLE_INSTANCE_MUTEX + suffix)
    runtime_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
    directory = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir())
    return FileInstanceLock(directory / f"{LOCAL_SERVER_PREFIX}{_user_tag()}{suffix}.lock")


class CommandServer(QObject):
    """Listens for commands from later launches (jump list tasks, the pinned shortcut...)."""

    command_received = Signal(str)  # a CompanionCommand

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_new_connection)
        self._buffers: dict[QLocalSocket, bytes] = {}

    def listen(self) -> bool:
        """Start listening. Call only while holding the instance lock: a leftover socket
        (Unix, after a crash) can then only be stale, so it is removed first."""
        if sys.platform != "win32":
            QLocalServer.removeServer(self.name)
        if not self._server.listen(self.name):
            log.warning("command server could not listen on %s: %s", self.name, self._server.errorString())
            return False
        return True

    def close(self) -> None:
        self._server.close()

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            self._buffers[socket] = b""
            socket.readyRead.connect(lambda current=socket: self._on_ready_read(current))
            socket.disconnected.connect(lambda current=socket: self._forget(current))
            if socket.bytesAvailable() > 0:
                # The line can arrive with the connection (seen with Windows pipes): readyRead
                # was already emitted before we connected to it.
                self._on_ready_read(socket)

    def _on_ready_read(self, socket: QLocalSocket) -> None:
        data = self._buffers.get(socket, b"") + bytes(socket.readAll().data())
        if b"\n" not in data and len(data) <= MAX_LINE:
            self._buffers[socket] = data
            return
        line = data.split(b"\n", 1)[0].strip().decode("utf-8", errors="replace")
        self._buffers[socket] = b""
        accepted = line in COMMANDS
        socket.write(QByteArray((REPLY_OK if accepted else REPLY_ERROR) + b"\n"))
        socket.flush()
        socket.disconnectFromServer()
        if accepted:
            self.command_received.emit(line)
        else:
            log.warning("ignored unknown command from a local client: %r", line[:MAX_LINE])

    def _forget(self, socket: QLocalSocket) -> None:
        self._buffers.pop(socket, None)
        socket.deleteLater()


def send_command(name: str, command: CompanionCommand, timeout_ms: int = 3000) -> bool:
    """Send `command` and wait (at most `timeout_ms`) for the "ok"; True once acknowledged.

    Runs a local QEventLoop instead of the blocking waitFor* calls: those are unreliable
    with Windows pipes, and the event loop also lets a server living in the same thread
    answer (tests). Needs a QCoreApplication.
    """
    socket = QLocalSocket()
    loop = QEventLoop()
    reply = bytearray()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)

    def on_connected() -> None:
        socket.write(QByteArray(command.encode("utf-8") + b"\n"))
        socket.flush()

    def on_ready_read() -> None:
        reply.extend(bytes(socket.readAll().data()))
        if b"\n" in reply:
            loop.quit()

    socket.connected.connect(on_connected)
    socket.readyRead.connect(on_ready_read)
    socket.disconnected.connect(loop.quit)
    socket.errorOccurred.connect(lambda _error: loop.quit())
    socket.connectToServer(name)
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        timer.start(timeout_ms)
        loop.exec()
    timer.stop()
    reply.extend(bytes(socket.readAll().data()))  # the server closes right after replying
    if not reply:
        log.warning("no running instance answered %r on %s: %s", command, name, socket.errorString())
    socket.abort()
    return bytes(reply).strip() == REPLY_OK
