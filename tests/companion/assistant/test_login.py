"""Recognizing "the Claude sign-in expired" in the CLI's errors, and the guide link."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.companion.assistant.login import guide_url, is_login_failure


@pytest.mark.parametrize(
    "text",
    [
        "Failed to authenticate. API Error: 401 OAuth access token has expired. Re-authenticate to continue.",
        '{"type": "control_response", "response": {"subtype": "error", "error": "/login"}}',
        "Not logged in. Please run /login",
        "Invalid API key. Please run /login",
        "invalid x-api-key",
        "API Error: 401 Unauthorized",
        "HTTP 401: the OAuth token is no longer valid",
        "Your OAuth token expired yesterday",
        "authentication_error: something",
        "  FAILED   TO\nAUTHENTICATE  ",
    ],
)
def test_sign_in_failures_are_recognized(text: str) -> None:
    assert is_login_failure(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "API Error: 529 Overloaded",
        "API Error: 500 Internal server error",
        "Request timed out after 401 seconds",
        "See https://example.com/login for details",
        "The tool call failed: file not found",
        "Authentication of the tests passed",
    ],
)
def test_other_errors_are_not_sign_in_failures(text: str) -> None:
    assert not is_login_failure(text)


@pytest.mark.parametrize(
    ("catalog", "doc"),
    [
        ("en", "sign-in-again.md"),
        ("pt_BR", "sign-in-again.pt-BR.md"),
        ("es", "sign-in-again.es.md"),
        ("ru", "sign-in-again.ru.md"),
        ("zh_CN", "sign-in-again.zh-CN.md"),
        ("xx", "sign-in-again.md"),
    ],
)
def test_the_guide_link_follows_the_language(catalog: str, doc: str) -> None:
    assert guide_url(catalog) == f"https://github.com/nano-br/voice-mate/blob/main/docs/{doc}"
    # The page the link opens is in the repository, in that language.
    assert (Path(__file__).resolve().parents[3] / "docs" / doc).is_file()
