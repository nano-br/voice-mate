"""When the assistant's Claude Code session lost its sign-in, and where to tell the person how to renew it.

Claude Code answers "not signed in" in many wordings (an expired OAuth token, a 401, "/login", an
invalid API key), and the same text reaches VoiceMate from two places: the `result` event of the
CLI's stream and the error line written in the transcript. `is_login_failure` is the one matcher
for both. Standard library only (no Qt).
"""

from __future__ import annotations

import re
from typing import Final

# The project URL comes from pyproject.toml (`repository`), as for the Desktop profile.
REPOSITORY_URL: Final = "https://github.com/nano-br/voice-mate"
GUIDE_ANCHOR: Final = "login-expired"
# What the person types in a terminal (the CLI has had `claude auth login` since its auth commands).
LOGIN_COMMAND: Final = "claude auth login"
# The doc of each catalog ("assistant.md" is English); `app.i18n.active_language()` names the catalog.
_GUIDE_SUFFIXES: Final = {"pt_BR": ".pt-BR", "es": ".es", "ru": ".ru", "zh_CN": ".zh-CN"}

_PHRASES: Final = (
    "failed to authenticate",
    "authentication_error",
    "authentication failed",
    "authentication required",
    "invalid authentication",
    "oauth token has expired",
    "oauth access token",
    "token has expired",
    "token expired",
    "expired token",
    "re-authenticate",
    "reauthenticate",
    "invalid api key",
    "invalid x-api-key",
    "not logged in",
    "not signed in",
)
# "/login" as a command ("Please run /login"), not as the end of a URL.
_LOGIN_COMMAND: Final = re.compile(r"(?<![\w/.:-])/login\b")
_STATUS_401: Final = re.compile(r"\b401\b")
_CREDENTIAL_WORDS: Final = ("authenticat", "unauthorized", "oauth", "token", "credential", "api key")


def is_login_failure(text: str) -> bool:
    """True when `text` (an error of Claude Code) means the sign-in is missing or expired.

    Only for what Claude Code itself reports: the error lines of the session's own transcript, the
    result of a failed turn and a refused Remote Control request. Never for the output of tools
    (an MCP server's result can say anything), which these callers do not pass."""
    lowered = " ".join(text.lower().split())
    if not lowered:
        return False
    if any(phrase in lowered for phrase in _PHRASES) or _LOGIN_COMMAND.search(lowered):
        return True
    return bool(_STATUS_401.search(lowered)) and any(word in lowered for word in _CREDENTIAL_WORDS)


def guide_url(catalog: str) -> str:
    """The "Signing in again" section of the assistant guide, in the language of `catalog`
    (English when that language has no translation), on the main branch."""
    return f"{REPOSITORY_URL}/blob/main/docs/assistant{_GUIDE_SUFFIXES.get(catalog, '')}.md#{GUIDE_ANCHOR}"
