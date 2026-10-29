"""A tiny in-process MCP server (JSON-RPC 2.0), served to the CLI as an "sdk" server.

The host declares the server by name in the `initialize` control request
(`"sdkMcpServers": ["voicemate"]`); the CLI then sends every JSON-RPC message for it as a
`control_request` with subtype `mcp_message` and waits for a `control_response` carrying
`{"mcp_response": <JSON-RPC reply>}` (own_session.py does the transport). This module only
answers the messages: `initialize`, `ping`, `tools/list` and `tools/call`. Standard library only.

A tool failure is a normal result with `isError: true` (the model reads the message); only a
malformed request or an unknown method is a JSON-RPC error.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

log = logging.getLogger(__name__)

DEFAULT_PROTOCOL_VERSION: Final = "2025-06-18"
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602
INVALID_REQUEST: Final = -32600

JsonObject = dict[str, Any]
ToolHandler = Callable[[JsonObject], str]

_JSON_TYPES: Final[dict[str, tuple[type, ...]]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


class ToolError(Exception):
    """A failure the model should read (the message is the tool's answer)."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: Mapping[str, Any]  # a JSON schema of type "object"
    handler: ToolHandler

    def listing(self) -> JsonObject:
        return {"name": self.name, "description": self.description, "inputSchema": dict(self.input_schema)}


def check_arguments(schema: Mapping[str, Any], arguments: object) -> JsonObject:
    """`arguments` checked against the simple schemas used here (required keys and the types of
    the top-level properties), with the declared defaults filled in. Raises ToolError."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolError("arguments must be an object")
    properties = schema.get("properties", {})
    out: JsonObject = {}
    for key in schema.get("required", ()):
        if key not in arguments:
            raise ToolError(f"missing argument: {key}")
    for key, value in arguments.items():
        spec = properties.get(key)
        if spec is None:
            continue  # extra keys are ignored, like most MCP servers do
        wanted = _JSON_TYPES.get(str(spec.get("type", "")))
        is_bool_as_number = isinstance(value, bool) and spec.get("type") in ("integer", "number")
        if wanted is not None and (not isinstance(value, wanted) or is_bool_as_number):
            raise ToolError(f"argument {key} must be of type {spec.get('type')}")
        out[key] = value
    for key, spec in properties.items():
        if key not in out and isinstance(spec, dict) and "default" in spec:
            out[key] = spec["default"]
    return out


class McpServer:
    """Answers the JSON-RPC messages of one sdk MCP server. Thread-safe (no mutable state)."""

    def __init__(self, name: str, tools: Sequence[Tool], version: str = "1.0.0") -> None:
        self.name = name
        self.version = version
        self._tools = {tool.name: tool for tool in tools}

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def handle(self, message: Mapping[str, Any]) -> JsonObject | None:
        """The JSON-RPC reply to `message`, or None for a notification."""
        method = message.get("method")
        if "id" not in message:
            return None  # notifications/initialized, notifications/cancelled, ...
        msg_id = message.get("id")
        if not isinstance(method, str):
            return _error(msg_id, INVALID_REQUEST, "missing method")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "initialize":
            version = params.get("protocolVersion")
            return _result(
                msg_id,
                {
                    "protocolVersion": version if isinstance(version, str) and version else DEFAULT_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.name, "version": self.version},
                },
            )
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": [tool.listing() for tool in self._tools.values()]})
        if method == "tools/call":
            return self._call(msg_id, params)
        return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")

    def _call(self, msg_id: object, params: JsonObject) -> JsonObject:
        name = params.get("name")
        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            return _error(msg_id, INVALID_PARAMS, f"unknown tool: {name}")
        try:
            text = tool.handler(check_arguments(tool.input_schema, params.get("arguments")))
            failed = False
        except ToolError as exc:
            text, failed = str(exc), True
        except Exception as exc:  # noqa: BLE001 - a tool bug must not break the session
            log.exception("assistant: tool %s failed", tool.name)
            text, failed = f"internal error: {exc}", True
        log.info("assistant: tool %s %s", tool.name, "failed" if failed else "ok")
        result: JsonObject = {"content": [{"type": "text", "text": text}]}
        if failed:
            result["isError"] = True
        return _result(msg_id, result)


def _result(msg_id: object, result: JsonObject) -> JsonObject:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: object, code: int, message: str) -> JsonObject:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
