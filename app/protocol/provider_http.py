"""What the external provider protocols share: the provider's URL, its errors and plain HTTP calls.

VoiceMate talks to services you run yourself with two small HTTP protocols, `voicemate-tts`
(docs/tts-provider-protocol.md) and `voicemate-stt` (docs/stt-provider-protocol.md). Both use the
same transport: HTTP/1.1, an optional bearer token, and the same error envelope
`{"error": {"code": "...", "message": "..."}}`. This module holds that part once.

Standard library only: the companion imports it and ships without numpy.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal
from urllib.parse import urlsplit

CONNECT_TIMEOUT_S: Final = 5.0
READ_TIMEOUT_S: Final = 30.0

ErrorKind = Literal["unreachable", "unauthorized", "protocol", "busy", "not_ready", "rejected", "server"]

# Characters that are safe inside a command line value (the companion passes URL and voice
# to the engine as flags). Voice ids are opaque to VoiceMate, so this is deliberately narrow.
_SAFE_VALUE: Final = re.compile(r"[A-Za-z0-9._:@/+=%~-]*")


class ProviderError(Exception):
    """A provider that cannot be used. `kind` says why; `detail` is English, for logs."""

    def __init__(self, kind: ErrorKind, detail: str, *, code: str = "") -> None:
        super().__init__(detail)
        self.kind: ErrorKind = kind
        self.detail = detail
        self.code = code  # the provider's own error code (`unknown_voice`, `audio_too_long`...)


@dataclass(frozen=True)
class Endpoint:
    secure: bool
    host: str
    port: int
    prefix: str  # "" or "/something", no trailing slash


def url_is_valid(url: str) -> bool:
    try:
        parse_url(url)
    except ValueError:
        return False
    return True


def value_is_safe(value: str) -> bool:
    """True for a URL or voice id that can travel as a command line flag value."""
    return _SAFE_VALUE.fullmatch(value) is not None


def parse_url(url: str) -> Endpoint:
    """`http://host:port[/prefix]` (or https). Raises ValueError when it is anything else."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("the URL must look like http://127.0.0.1:8765")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("the URL cannot carry credentials, a query or a fragment")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("the URL has an invalid port") from exc
    secure = parts.scheme == "https"
    return Endpoint(secure, parts.hostname, port or (443 if secure else 80), parts.path.rstrip("/"))


class HttpProvider:
    """One provider at one URL. Every call opens its own short connection."""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        connect_timeout: float = CONNECT_TIMEOUT_S,
        read_timeout: float = READ_TIMEOUT_S,
    ) -> None:
        self.url = url
        self._endpoint = parse_url(url)
        self._token = token or None
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

    def health(self) -> str:
        """GET /v1/health -> "ok" or "loading"."""
        data = self._json("GET", "/v1/health")
        status = data.get("status")
        if status not in ("ok", "loading"):
            raise ProviderError("protocol", '/v1/health must answer {"status": "ok"} or "loading"')
        return str(status)

    def warmup(self) -> None:
        self._json("POST", "/v1/warmup", {})

    # --- plumbing ----------------------------------------------------------------------------------------

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        endpoint = self._endpoint
        if endpoint.secure:
            return http.client.HTTPSConnection(endpoint.host, endpoint.port, timeout=timeout)
        return http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=timeout)

    def _path(self, path: str) -> str:
        return f"{self._endpoint.prefix}{path}"

    def _headers(self, content_type: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "VoiceMate"}
        if content_type:
            headers["Content-Type"] = content_type
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(
        self, method: str, path: str, body: bytes | None, headers: Mapping[str, str], timeout: float
    ) -> tuple[int, bytes]:
        """One request on its own connection: the status and the whole body."""
        connection = self._connection(timeout)
        try:
            connection.request(method, self._path(path), body, dict(headers))
            response = connection.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise ProviderError("unreachable", describe_os_error(exc, self.url)) from exc
        finally:
            connection.close()
        return response.status, raw

    def _json(
        self, method: str, path: str, body: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = self._headers("application/json" if payload is not None else None)
        status, raw = self._request(method, path, payload, headers, timeout or self._connect_timeout)
        if status != 200:
            raise error_from(status, raw)
        return json_object(raw, path)


def json_object(raw: bytes, path: str) -> dict[str, Any]:
    """A 200 body as a JSON object, or a protocol error naming the endpoint."""
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except ValueError as exc:
        raise ProviderError("protocol", f"{path} did not answer JSON") from exc
    if not isinstance(data, dict):
        raise ProviderError("protocol", f"{path} must answer a JSON object")
    return data


def strings(value: object) -> tuple[str, ...]:
    """The strings of a JSON list (anything else in it, or a value that is not a list, is dropped)."""
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def error_from(status: int, raw: bytes) -> ProviderError:
    """A non-200 answer, read through the error envelope when there is one."""
    code, message = "", ""
    try:
        body = json.loads(raw.decode("utf-8"))
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code", ""))
            message = str(error.get("message", ""))
    except ValueError:
        pass
    detail = f"HTTP {status}" + (f" {code}" if code else "") + (f": {message}" if message else "")
    if status == 401:
        return ProviderError("unauthorized", detail, code=code)
    if status == 409:
        return ProviderError("busy", detail, code=code)
    if status == 503:
        return ProviderError("not_ready", detail, code=code)
    if 400 <= status < 500:
        return ProviderError("rejected", detail, code=code)
    return ProviderError("server", detail, code=code)


def describe_os_error(exc: BaseException, url: str) -> str:
    if isinstance(exc, ConnectionRefusedError):
        return f"nothing is listening at {url}"
    if isinstance(exc, TimeoutError):
        return f"{url} did not answer in time"
    if isinstance(exc, socket.gaierror):
        return f"the host in {url} was not found"
    return f"cannot talk to {url}: {exc}"
