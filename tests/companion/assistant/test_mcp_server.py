"""The stdlib JSON-RPC MCP server."""

from __future__ import annotations

from typing import Any

import pytest

from app.companion.assistant.mcp_server import McpServer, Tool, ToolError, check_arguments

SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "last": {"type": "integer", "default": 10}},
    "required": ["id"],
}


def _boom(_args: dict[str, Any]) -> str:
    raise RuntimeError("bug")


def _refuse(_args: dict[str, Any]) -> str:
    raise ToolError("not possible")


def server() -> McpServer:
    return McpServer(
        "voicemate",
        [
            Tool("read", "Read.", SCHEMA, lambda a: f"{a['id']}:{a['last']}"),
            Tool("boom", "Fails.", {"type": "object"}, _boom),
            Tool("refuse", "Refuses.", {"type": "object"}, _refuse),
        ],
    )


def call(name: str, arguments: object) -> dict[str, Any]:
    reply = server().handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )
    assert reply is not None
    return reply


def test_initialize_echoes_the_protocol_version_and_offers_tools() -> None:
    reply = server().handle(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
    )
    assert reply == {
        "jsonrpc": "2.0",
        "id": 0,
        "result": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "voicemate", "version": "1.0.0"},
        },
    }


def test_notifications_get_no_reply_and_ping_an_empty_result() -> None:
    assert server().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server().handle({"jsonrpc": "2.0", "id": 4, "method": "ping"}) == {"jsonrpc": "2.0", "id": 4, "result": {}}


def test_tools_list() -> None:
    reply = server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert reply is not None
    tools = reply["result"]["tools"]
    assert [t["name"] for t in tools] == ["read", "boom", "refuse"]
    assert tools[0] == {"name": "read", "description": "Read.", "inputSchema": SCHEMA}


def test_call_fills_defaults_and_returns_text() -> None:
    assert call("read", {"id": "abc"})["result"] == {"content": [{"type": "text", "text": "abc:10"}]}
    assert call("read", {"id": "abc", "last": 3, "extra": 1})["result"]["content"][0]["text"] == "abc:3"


def test_bad_arguments_and_failures_are_tool_errors() -> None:
    for name, arguments, text in [
        ("read", {}, "missing argument: id"),
        ("read", {"id": 5}, "argument id must be of type string"),
        ("read", {"id": "a", "last": True}, "argument last must be of type integer"),
        ("read", "nope", "arguments must be an object"),
        ("refuse", {}, "not possible"),
        ("boom", {}, "internal error: bug"),
    ]:
        result = call(name, arguments)["result"]
        assert result["isError"] is True
        assert result["content"][0]["text"] == text


def test_unknown_tool_and_method_are_json_rpc_errors() -> None:
    assert call("nope", {})["error"]["code"] == -32602
    reply = server().handle({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
    assert reply is not None and reply["error"]["code"] == -32601


def test_check_arguments_accepts_missing_arguments() -> None:
    assert check_arguments({"type": "object", "properties": {"n": {"type": "integer", "default": 2}}}, None) == {"n": 2}
    with pytest.raises(ToolError):
        check_arguments(SCHEMA, None)
