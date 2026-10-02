"""Trigger via the local HTTP daemon: the WSL2 path (and the automation path).

The app runs as a daemon inside WSL and listens on 127.0.0.1:<port>. The trigger
comes from outside: the companion app, or the Windows-side script
(scripts/windows/voicemate-hotkeys.ahk or .ps1), registers the global hotkeys and
does `POST /trigger {"flow": ...}`. WSL2's `localhostForwarding` exposes the port
to Windows automatically.

The HTTP server itself is `app.daemon.server.ApiServer` (routes, auth, readiness,
API v2): it is bound and serving before the model loads, so this listener only
adapts it to the `InputListener` protocol. `listen()` blocks until the server
stops (`POST /shutdown`, stdin EOF with `--supervised`, Ctrl+C).

Keeps the "stop decides the destination" design: each request is equivalent to
pressing that flow's hotkey (`session.toggle(flow)`, which returns the operation).
"""

from __future__ import annotations

from app.daemon.server import DEFAULT_DAEMON_PORT, ApiServer

__all__ = ["DEFAULT_DAEMON_PORT", "SocketTriggerListener"]


class SocketTriggerListener:
    """InputListener whose trigger is the daemon's HTTP API."""

    def __init__(self, server: ApiServer) -> None:
        self._server = server

    @property
    def port(self) -> int:
        return self._server.port

    def listen(self) -> None:
        self._server.start()  # no-op when main already started it (before the model load)
        self._server.wait_stopped()

    def reinstall(self) -> None:
        """No-op: there's no OS hook to reinstall."""
        return None

    def stop(self) -> None:
        self._server.stop()
