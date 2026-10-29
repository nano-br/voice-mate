"""The Claude Desktop driver's selectors and timings as data: a versioned JSON profile.

`app/companion/win/claude_desktop.py` finds the window, the sidebar rows, the open conversation and
the prompt editor through UI Automation, and types with a short key sequence. Everything it needs to
know about the Desktop's UI lives in a *profile*, so a Desktop update that moves things around is
fixed by publishing a new JSON file, not a new release. The full format is in `docs/desktop-profile.md`.

Format (schema 1), one JSON object, unknown keys refused:

- `schema` (1), `profile_version` (integer, grows on every publish), `min_voicemate` (optional "x.y.z"),
  `app` (`name`, `tested_versions`, `notes`: documentation only).
- `window`: a *matcher* for the top-level window.
- `sidebar`: `container` (locator), `row` (step), `label` (matcher, a direct child of a row: rows
  without it are navigation items), `label_text` (step, first text is the title), `status`
  (matcher), `status_text` (step, texts joined by a space).
- `conversation`: `title_format` (contains `{title}` once), `document` (locator), `visible_text`
  (step), `max_visible_lines`.
- `prompt`: `editor` (locator), `text` (step), `focus` (matcher applied to the focused control).
- `keys`: `clear_draft`, `paste`, `submit` (lists of key names, sent one at a time with the
  guards checked before each), `interval_s` (between the events of one key), `wait_s` (after each key).
- `paste`: `verify_max_chars`, `verify_head_chars`, `unverified_wait_s`.
- `timeouts`: `lock_wait_s`, `open_wait_s`, `foreground_wait_s`, `focus_wait_s`,
  `focus_prompt_wait_s`, `paste_wait_s`, `submit_wait_s`, `poll_s`.

A *matcher* is an object of conditions that must all hold: `control_type`, `class_equals`,
`class_prefix`, `class_contains`, `automation_id`, `name_equals`, `name_prefix`, `name_suffix`.
There are no regular expressions: Python's `re` has no timeout, and plain text comparisons cover every
real case. A *step* is `{"match": matcher, "max_depth": n}`: the first control in
the subtree (depth first, the root included) that matches. A *locator* is a step or
`{"path": [step, ...]}`, each step searched inside the previous result.

A profile is data and widens nothing. These guards live in code and no profile can change them: the
window must belong to the Claude Desktop process (checked before any matcher sees it), a key is
sent only when the Claude window owns the foreground, and only the keys in `ALLOWED_KEYS` can ever
be sent (each role has its own subset). There is no field that runs a command or opens a path or a
URL.

`ProfileManager` resolves which profile is active: a user override file, else the cached remote
profile when it is newer than the packaged one, else the packaged one. The update check is a plain
HTTPS GET with ETag (standard library only) and never raises; run it on a worker thread, not the UI's.
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, NoReturn

log = logging.getLogger(__name__)

# A UI Automation control (a `uiautomation` object; a fake in the tests).
Control = Any

SUPPORTED_SCHEMA: Final = 1
# The project URL comes from pyproject.toml (`repository`); the profile is published from this repo.
DEFAULT_PROFILE_URL: Final = "https://raw.githubusercontent.com/nano-br/voice-mate/main/profiles/claude-desktop.json"
PACKAGED_PROFILE_PATH: Final = Path(__file__).resolve().parent / "profiles" / "claude-desktop.json"
MAX_PROFILE_BYTES: Final = 64 * 1024
MAX_TEXT_CHARS: Final = 300

# The only keys the driver can ever send, as profile names -> `uiautomation` SendKeys text.
KEY_SENDKEYS: Final[Mapping[str, str]] = {
    "ctrl+a": "{Ctrl}a",
    "delete": "{Delete}",
    "ctrl+v": "{Ctrl}v",
    "enter": "{Enter}",
    "escape": "{Esc}",
}
ALLOWED_KEYS: Final = frozenset(KEY_SENDKEYS)
# What each step of the flash may use: a profile cannot, say, make the paste step press Delete.
ROLE_KEYS: Final[Mapping[str, frozenset[str]]] = {
    "clear_draft": frozenset({"ctrl+a", "delete", "escape"}),
    "paste": frozenset({"ctrl+v"}),
    "submit": frozenset({"enter"}),
}
# What the backend accepts in one press: exactly one allowed key.
ALLOWED_SENDKEYS: Final = frozenset(KEY_SENDKEYS.values())

CONTROL_TYPES: Final = frozenset(
    {
        "AppBarControl",
        "ButtonControl",
        "CalendarControl",
        "CheckBoxControl",
        "ComboBoxControl",
        "CustomControl",
        "DataGridControl",
        "DataItemControl",
        "DocumentControl",
        "EditControl",
        "GroupControl",
        "HeaderControl",
        "HeaderItemControl",
        "HyperlinkControl",
        "ImageControl",
        "ListControl",
        "ListItemControl",
        "MenuBarControl",
        "MenuControl",
        "MenuItemControl",
        "PaneControl",
        "ProgressBarControl",
        "RadioButtonControl",
        "ScrollBarControl",
        "SemanticZoomControl",
        "SeparatorControl",
        "SliderControl",
        "SpinnerControl",
        "SplitButtonControl",
        "StatusBarControl",
        "TabControl",
        "TabItemControl",
        "TableControl",
        "TextControl",
        "ThumbControl",
        "TitleBarControl",
        "ToolBarControl",
        "ToolTipControl",
        "TreeControl",
        "TreeItemControl",
        "WindowControl",
    }
)


class ProfileError(ValueError):
    """A profile that cannot be used; the message says which field and why (English, for the log and the UI)."""


def _fail(path: str, message: str) -> NoReturn:
    raise ProfileError(f"{path}: {message}")


# --- validation helpers ----------------------------------------------------------------------


def _obj(value: object, path: str, required: Iterable[str], optional: Iterable[str] = ()) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "expected an object")
    required, optional = tuple(required), tuple(optional)
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        _fail(path, f"unknown key(s) {', '.join(unknown)}")
    missing = [key for key in required if key not in value]
    if missing:
        _fail(path, f"missing key(s) {', '.join(missing)}")
    return value


def _int(value: object, path: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "expected an integer")
    if not low <= value <= high:
        _fail(path, f"must be between {low} and {high}")
    return value


def _num(value: object, path: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _fail(path, "expected a number")
    if not low <= value <= high:
        _fail(path, f"must be between {low} and {high}")
    return float(value)


def _str(value: object, path: str, max_chars: int = MAX_TEXT_CHARS, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(path, "expected a string")
    if len(value) > max_chars:
        _fail(path, f"longer than {max_chars} characters")
    if not value and not allow_empty:
        _fail(path, "must not be empty")
    if any(ord(ch) < 32 for ch in value):
        _fail(path, "control characters are not allowed")
    return value


def _version_tuple(text: str) -> tuple[int, int, int]:
    a, b, c, *_ = [*(int(part) for part in re.findall(r"\d+", text)[:3]), 0, 0, 0]
    return a, b, c


# --- the model -------------------------------------------------------------------------------

_MATCHER_STRINGS: Final = (
    "class_equals",
    "class_prefix",
    "class_contains",
    "automation_id",
    "name_equals",
    "name_prefix",
    "name_suffix",
)


@dataclass(frozen=True)
class Matcher:
    """Conditions on one control, all of which must hold. Absent fields are not checked."""

    control_type: str | None = None
    class_equals: str | None = None
    class_prefix: str | None = None
    class_contains: str | None = None
    automation_id: str | None = None
    name_equals: str | None = None
    name_prefix: str | None = None
    name_suffix: str | None = None

    def matches(self, control: Control) -> bool:
        """Properties are read lazily and in the order type, class, id, name: each read is a UIA call."""
        if self.control_type is not None and control.ControlTypeName != self.control_type:
            return False
        if self.class_equals is not None or self.class_prefix is not None or self.class_contains is not None:
            cls = str(control.ClassName)
            if self.class_equals is not None and cls != self.class_equals:
                return False
            if self.class_prefix is not None and not cls.startswith(self.class_prefix):
                return False
            if self.class_contains is not None and self.class_contains not in cls:
                return False
        if self.automation_id is not None and str(control.AutomationId) != self.automation_id:
            return False
        if self.name_equals is not None or self.name_prefix is not None or self.name_suffix is not None:
            name = str(control.Name)
            if self.name_equals is not None and name != self.name_equals:
                return False
            if self.name_prefix is not None and not name.startswith(self.name_prefix):
                return False
            if self.name_suffix is not None and not name.endswith(self.name_suffix):
                return False
        return True

    @property
    def has_identity(self) -> bool:
        """At least one condition besides the control type (the window and focus matchers need one)."""
        return any(getattr(self, key) is not None for key in _MATCHER_STRINGS)

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}

    @classmethod
    def parse(cls, value: object, path: str, *, needs_identity: bool = False) -> Matcher:
        data = _obj(value, path, (), ("control_type", *_MATCHER_STRINGS))
        fields: dict[str, str] = {}
        if "control_type" in data:
            control_type = _str(data["control_type"], f"{path}.control_type", 40)
            if control_type not in CONTROL_TYPES:
                _fail(f"{path}.control_type", f"unknown control type {control_type!r}")
            fields["control_type"] = control_type
        for key in _MATCHER_STRINGS:
            if key in data:
                fields[key] = _str(data[key], f"{path}.{key}")
        matcher = cls(**fields)
        if not fields:
            _fail(path, "needs at least one condition")
        if needs_identity and not matcher.has_identity:
            _fail(path, "needs a class, id or name condition, not only a control type")
        return matcher


@dataclass(frozen=True)
class Step:
    """The first control matching `match` within `max_depth` levels of the root (the root is depth 0)."""

    match: Matcher
    max_depth: int

    def to_dict(self) -> dict[str, Any]:
        return {"match": self.match.to_dict(), "max_depth": self.max_depth}

    @classmethod
    def parse(cls, value: object, path: str) -> Step:
        data = _obj(value, path, ("match", "max_depth"))
        return cls(Matcher.parse(data["match"], f"{path}.match"), _int(data["max_depth"], f"{path}.max_depth", 1, 90))


@dataclass(frozen=True)
class Locator:
    """One step, or a path of up to four steps, each searched inside the previous result."""

    steps: tuple[Step, ...]

    def to_dict(self) -> dict[str, Any]:
        if len(self.steps) == 1:
            return self.steps[0].to_dict()
        return {"path": [step.to_dict() for step in self.steps]}

    @classmethod
    def parse(cls, value: object, path: str) -> Locator:
        if isinstance(value, dict) and "path" in value:
            data = _obj(value, path, ("path",))
            steps = data["path"]
            if not isinstance(steps, list) or not 1 <= len(steps) <= 4:
                _fail(f"{path}.path", "expected a list of 1 to 4 steps")
            return cls(tuple(Step.parse(step, f"{path}.path[{i}]") for i, step in enumerate(steps)))
        return cls((Step.parse(value, path),))


@dataclass(frozen=True)
class AppInfo:
    """Documentation only: which Desktop versions the profile was checked against."""

    name: str
    tested_versions: tuple[str, ...]
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tested_versions": list(self.tested_versions), "notes": self.notes}

    @classmethod
    def parse(cls, value: object, path: str) -> AppInfo:
        data = _obj(value, path, (), ("name", "tested_versions", "notes"))
        versions = data.get("tested_versions", [])
        if not isinstance(versions, list) or len(versions) > 20:
            _fail(f"{path}.tested_versions", "expected a list of at most 20 strings")
        return cls(
            _str(data.get("name", "Claude Desktop"), f"{path}.name", 80),
            tuple(_str(v, f"{path}.tested_versions[{i}]", 40) for i, v in enumerate(versions)),
            _str(data.get("notes", ""), f"{path}.notes", 1000, allow_empty=True),
        )


@dataclass(frozen=True)
class Sidebar:
    container: Locator
    row: Step
    label: Matcher
    label_text: Step
    status: Matcher
    status_text: Step

    def to_dict(self) -> dict[str, Any]:
        return {
            "container": self.container.to_dict(),
            "row": self.row.to_dict(),
            "label": self.label.to_dict(),
            "label_text": self.label_text.to_dict(),
            "status": self.status.to_dict(),
            "status_text": self.status_text.to_dict(),
        }

    @classmethod
    def parse(cls, value: object, path: str) -> Sidebar:
        data = _obj(value, path, ("container", "row", "label", "label_text", "status", "status_text"))
        return cls(
            Locator.parse(data["container"], f"{path}.container"),
            Step.parse(data["row"], f"{path}.row"),
            Matcher.parse(data["label"], f"{path}.label"),
            Step.parse(data["label_text"], f"{path}.label_text"),
            Matcher.parse(data["status"], f"{path}.status"),
            Step.parse(data["status_text"], f"{path}.status_text"),
        )


@dataclass(frozen=True)
class Conversation:
    title_format: str
    document: Locator
    visible_text: Step
    max_visible_lines: int

    @property
    def prefix(self) -> str:
        return self.title_format.split("{title}", 1)[0]

    @property
    def suffix(self) -> str:
        return self.title_format.split("{title}", 1)[1]

    def document_name(self, title: str) -> str:
        """The name the open conversation's document has when `title` is open."""
        return self.title_format.replace("{title}", title)

    def title_of(self, document_name: str) -> str:
        """The title inside a document name (the name itself when it does not follow the format)."""
        prefix, suffix = self.prefix, self.suffix
        if len(document_name) >= len(prefix) + len(suffix) and document_name.startswith(prefix):
            if document_name.endswith(suffix):
                return document_name[len(prefix) : len(document_name) - len(suffix)]
        return document_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "title_format": self.title_format,
            "document": self.document.to_dict(),
            "visible_text": self.visible_text.to_dict(),
            "max_visible_lines": self.max_visible_lines,
        }

    @classmethod
    def parse(cls, value: object, path: str) -> Conversation:
        data = _obj(value, path, ("title_format", "document", "visible_text", "max_visible_lines"))
        title_format = _str(data["title_format"], f"{path}.title_format", 80)
        if (
            title_format.count("{title}") != 1
            or "{" in title_format.replace("{title}", "")
            or "}" in title_format.replace("{title}", "")
        ):
            _fail(f"{path}.title_format", "must contain {title} exactly once and no other braces")
        return cls(
            title_format,
            Locator.parse(data["document"], f"{path}.document"),
            Step.parse(data["visible_text"], f"{path}.visible_text"),
            _int(data["max_visible_lines"], f"{path}.max_visible_lines", 1, 5000),
        )


@dataclass(frozen=True)
class Prompt:
    editor: Locator
    text: Step
    focus: Matcher

    def to_dict(self) -> dict[str, Any]:
        return {"editor": self.editor.to_dict(), "text": self.text.to_dict(), "focus": self.focus.to_dict()}

    @classmethod
    def parse(cls, value: object, path: str) -> Prompt:
        data = _obj(value, path, ("editor", "text", "focus"))
        return cls(
            Locator.parse(data["editor"], f"{path}.editor"),
            Step.parse(data["text"], f"{path}.text"),
            Matcher.parse(data["focus"], f"{path}.focus", needs_identity=True),
        )


@dataclass(frozen=True)
class Keys:
    clear_draft: tuple[str, ...]
    paste: tuple[str, ...]
    submit: tuple[str, ...]
    interval_s: float
    wait_s: float

    def sendkeys(self, role: Literal["clear_draft", "paste", "submit"]) -> tuple[str, ...]:
        """The `SendKeys` text of each key of one step, in order (one press each, so the guards run
        before every key); only names from `KEY_SENDKEYS` can get here."""
        return tuple(KEY_SENDKEYS[name] for name in getattr(self, role))

    def to_dict(self) -> dict[str, Any]:
        return {
            "clear_draft": list(self.clear_draft),
            "paste": list(self.paste),
            "submit": list(self.submit),
            "interval_s": self.interval_s,
            "wait_s": self.wait_s,
        }

    @classmethod
    def parse(cls, value: object, path: str) -> Keys:
        data = _obj(value, path, ("clear_draft", "paste", "submit", "interval_s", "wait_s"))
        roles: dict[str, tuple[str, ...]] = {}
        for role, allowed in ROLE_KEYS.items():
            names = data[role]
            if not isinstance(names, list) or not 1 <= len(names) <= 4:
                _fail(f"{path}.{role}", "expected a list of 1 to 4 key names")
            for i, name in enumerate(names):
                if not isinstance(name, str) or name not in allowed:
                    _fail(f"{path}.{role}[{i}]", f"key not allowed here (allowed: {', '.join(sorted(allowed))})")
            roles[role] = tuple(names)
        return cls(
            roles["clear_draft"],
            roles["paste"],
            roles["submit"],
            _num(data["interval_s"], f"{path}.interval_s", 0.0, 0.2),
            _num(data["wait_s"], f"{path}.wait_s", 0.0, 1.0),
        )


@dataclass(frozen=True)
class Paste:
    verify_max_chars: int  # a longer paste may become an attachment chip, not prompt text: not verified
    verify_head_chars: int
    unverified_wait_s: float

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def parse(cls, value: object, path: str) -> Paste:
        data = _obj(value, path, ("verify_max_chars", "verify_head_chars", "unverified_wait_s"))
        return cls(
            _int(data["verify_max_chars"], f"{path}.verify_max_chars", 1, 100_000),
            _int(data["verify_head_chars"], f"{path}.verify_head_chars", 1, 200),
            _num(data["unverified_wait_s"], f"{path}.unverified_wait_s", 0.0, 5.0),
        )


_TIMEOUT_RANGES: Final[Mapping[str, tuple[float, float]]] = {
    "lock_wait_s": (0.0, 120.0),
    "open_wait_s": (0.0, 30.0),
    "foreground_wait_s": (0.0, 10.0),
    "focus_wait_s": (0.0, 10.0),
    "focus_prompt_wait_s": (0.0, 15.0),
    "paste_wait_s": (0.0, 10.0),
    "submit_wait_s": (0.0, 10.0),
    "poll_s": (0.01, 1.0),
}


@dataclass(frozen=True)
class Timeouts:
    lock_wait_s: float
    open_wait_s: float
    foreground_wait_s: float
    focus_wait_s: float
    focus_prompt_wait_s: float
    paste_wait_s: float
    submit_wait_s: float
    poll_s: float

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def parse(cls, value: object, path: str) -> Timeouts:
        data = _obj(value, path, _TIMEOUT_RANGES)
        return cls(**{key: _num(data[key], f"{path}.{key}", *bounds) for key, bounds in _TIMEOUT_RANGES.items()})


@dataclass(frozen=True)
class DesktopProfile:
    """A validated profile. Build it with `parse_profile`, never by hand from untrusted data."""

    schema: int
    profile_version: int
    min_voicemate: str | None
    app: AppInfo
    window: Matcher
    sidebar: Sidebar
    conversation: Conversation
    prompt: Prompt
    keys: Keys
    paste: Paste
    timeouts: Timeouts

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"schema": self.schema, "profile_version": self.profile_version}
        if self.min_voicemate is not None:
            out["min_voicemate"] = self.min_voicemate
        out["app"] = self.app.to_dict()
        out["window"] = self.window.to_dict()
        out["sidebar"] = self.sidebar.to_dict()
        out["conversation"] = self.conversation.to_dict()
        out["prompt"] = self.prompt.to_dict()
        out["keys"] = self.keys.to_dict()
        out["paste"] = self.paste.to_dict()
        out["timeouts"] = self.timeouts.to_dict()
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"


def profile_from_dict(data: object) -> DesktopProfile:
    """Validate strictly and build the profile; raises `ProfileError` with the field and the reason."""
    top = _obj(
        data,
        "profile",
        ("schema", "profile_version", "window", "sidebar", "conversation", "prompt", "keys", "paste", "timeouts"),
        ("min_voicemate", "app"),
    )
    schema = _int(top["schema"], "schema", 0, 1_000_000)
    if schema != SUPPORTED_SCHEMA:
        _fail("schema", f"schema {schema} is not supported (this VoiceMate reads schema {SUPPORTED_SCHEMA})")
    min_voicemate = None
    if "min_voicemate" in top:
        min_voicemate = _str(top["min_voicemate"], "min_voicemate", 20)
        if not re.fullmatch(r"\d+(?:\.\d+){0,2}", min_voicemate):
            _fail("min_voicemate", "expected a version such as 1.2.0")
    return DesktopProfile(
        schema=schema,
        profile_version=_int(top["profile_version"], "profile_version", 1, 1_000_000_000),
        min_voicemate=min_voicemate,
        app=AppInfo.parse(top.get("app", {}), "app"),
        window=Matcher.parse(top["window"], "window", needs_identity=True),
        sidebar=Sidebar.parse(top["sidebar"], "sidebar"),
        conversation=Conversation.parse(top["conversation"], "conversation"),
        prompt=Prompt.parse(top["prompt"], "prompt"),
        keys=Keys.parse(top["keys"], "keys"),
        paste=Paste.parse(top["paste"], "paste"),
        timeouts=Timeouts.parse(top["timeouts"], "timeouts"),
    )


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ProfileError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _bad_constant(name: str) -> NoReturn:
    raise ProfileError(f"{name} is not valid JSON")


def parse_profile(raw: bytes | str) -> DesktopProfile:
    """JSON text or bytes to a validated profile. Refuses big input, bad JSON, duplicate keys and NaN."""
    size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))
    if size > MAX_PROFILE_BYTES:
        raise ProfileError(f"the profile is larger than {MAX_PROFILE_BYTES // 1024} KiB")
    try:
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
        data = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_bad_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProfileError(f"not valid JSON ({exc})") from exc
    return profile_from_dict(data)


@functools.cache
def load_packaged() -> DesktopProfile:
    """The profile shipped with the app (the behavior of the Desktop port when this was released)."""
    return parse_profile(PACKAGED_PROFILE_PATH.read_bytes())


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


# --- sources and update ----------------------------------------------------------------------

ProfileSourceName = Literal["override", "remote", "packaged"]
UpdateStatus = Literal["updated", "unchanged", "not_newer", "disabled", "invalid", "incompatible", "error"]


@dataclass(frozen=True)
class UpdateResult:
    """What a check for a new profile found. Never an exception: `detail` is English text for the log."""

    status: UpdateStatus
    detail: str = ""
    version: int | None = None  # profile_version that is active after the check


@dataclass(frozen=True)
class FetchResponse:
    status: int
    etag: str | None
    body: bytes


Fetcher = Callable[[str, Mapping[str, str], float], FetchResponse]


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,  # noqa: ANN401
        code: int,
        msg: str,
        headers: Any,  # noqa: ANN401
        newurl: str,
    ) -> urllib.request.Request | None:
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError(f"refusing a redirect to a non-HTTPS URL: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urllib_fetch(url: str, headers: Mapping[str, str], timeout: float) -> FetchResponse:
    """A GET with the standard library; a 304 is a normal answer, any other failure raises."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "VoiceMate-profile-updater", "Accept": "application/json", **headers}
    )
    opener = urllib.request.build_opener(_HttpsOnlyRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return FetchResponse(
                int(response.status), response.headers.get("ETag"), response.read(MAX_PROFILE_BYTES + 1)
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResponse(304, exc.headers.get("ETag") if exc.headers else None, b"")
        raise


@dataclass
class _Resolved:
    profile: DesktopProfile
    source: ProfileSourceName = "packaged"
    error: str = field(default="")


class ProfileManager:
    """Which profile is active, and how a newer one arrives.

    Resolution: the override file (when it exists and is valid), else the cached remote profile (valid,
    supported, `profile_version` above the packaged one's), else the packaged profile. Thread-safe;
    `check_for_update` does network I/O, so call it from a worker thread.

    `changed(profile)` is called, on the thread that caused it, when the effective profile changes
    (an update, an import, a clear). The Desktop sender takes `manager.current` as its `profile`, so it
    picks the new profile up on its next operation.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        auto_update: bool = True,
        override_path: Path | None = None,
        url: str = DEFAULT_PROFILE_URL,
        voicemate_version: str = "",
        changed: Callable[[DesktopProfile], None] | None = None,
        fetch: Fetcher | None = None,
        packaged: DesktopProfile | None = None,
    ) -> None:
        if not url.lower().startswith("https://"):
            raise ValueError("the profile URL must be HTTPS")
        self._cache_dir = Path(cache_dir)
        self._override_path = Path(override_path) if override_path is not None else self._cache_dir / "override.json"
        self._url = url
        self._version = voicemate_version
        self._changed = changed
        self._fetch = fetch if fetch is not None else urllib_fetch
        self._packaged = packaged if packaged is not None else load_packaged()
        self.auto_update = auto_update
        self._lock = threading.RLock()
        self._check_lock = threading.Lock()
        self._resolved = self._resolve()

    # --- reading -------------------------------------------------------------------------------

    def current(self) -> DesktopProfile:
        with self._lock:
            return self._resolved.profile

    def source(self) -> ProfileSourceName:
        with self._lock:
            return self._resolved.source

    @property
    def last_error(self) -> str:
        """Why the last override or cached profile was ignored ("" when nothing was)."""
        with self._lock:
            return self._resolved.error

    @property
    def override_path(self) -> Path:
        return self._override_path

    # --- resolution ----------------------------------------------------------------------------

    @property
    def _cache_file(self) -> Path:
        return self._cache_dir / "remote-profile.json"

    @property
    def _meta_file(self) -> Path:
        return self._cache_dir / "remote-profile.meta.json"

    def _compatible(self, profile: DesktopProfile) -> str:
        """ "" when this VoiceMate can use `profile`, else the reason it cannot."""
        if (
            profile.min_voicemate
            and self._version
            and _version_tuple(profile.min_voicemate) > _version_tuple(self._version)
        ):
            return f"needs VoiceMate {profile.min_voicemate} or newer (this is {self._version})"
        return ""

    def _read_cached(self) -> tuple[DesktopProfile | None, str]:
        if not self._cache_file.exists():
            return None, ""
        try:
            profile = parse_profile(self._cache_file.read_bytes())
        except (OSError, ProfileError) as exc:
            return None, f"cached profile ignored: {exc}"
        reason = self._compatible(profile)
        return (None, f"cached profile ignored: {reason}") if reason else (profile, "")

    def _best_non_override(self) -> tuple[DesktopProfile, ProfileSourceName, str]:
        cached, error = self._read_cached()
        if cached is not None and cached.profile_version > self._packaged.profile_version:
            return cached, "remote", error
        return self._packaged, "packaged", error

    def _resolve(self) -> _Resolved:
        error = ""
        if self._override_path.exists():
            try:
                return _Resolved(parse_profile(self._override_path.read_bytes()), "override")
            except (OSError, ProfileError) as exc:
                error = f"override profile ignored: {exc}"
                log.warning("desktop profile: %s", error)
        profile, source, cache_error = self._best_non_override()
        if cache_error:
            log.warning("desktop profile: %s", cache_error)
        return _Resolved(profile, source, error or cache_error)

    def _refresh(self) -> None:
        with self._lock:
            before = self._resolved
            self._resolved = self._resolve()
            after = self._resolved
        if (after.profile, after.source) != (before.profile, before.source) and self._changed is not None:
            try:
                self._changed(after.profile)
            except Exception:
                log.exception("desktop profile: the changed callback failed")

    # --- the person's own profile --------------------------------------------------------------

    def import_profile(self, path: Path) -> DesktopProfile:
        """Validate `path` and make it the override. Raises `ProfileError` and changes nothing when invalid."""
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            raise ProfileError(f"could not read the file ({exc})") from exc
        profile = parse_profile(raw)  # validated before anything is written
        with self._lock:
            _atomic_write(self._override_path, profile.to_json())
        self._refresh()
        return profile

    def export_profile(self, path: Path) -> None:
        """Write the effective profile (override, remote or packaged) as JSON."""
        _atomic_write(Path(path), self.current().to_json())

    def clear_override(self) -> None:
        with self._lock:
            with contextlib.suppress(FileNotFoundError):
                self._override_path.unlink()
        self._refresh()

    # --- the remote profile --------------------------------------------------------------------

    def check_for_update(self, timeout: float = 5.0, *, manual: bool = False) -> UpdateResult:
        """Ask the published profile URL for a newer profile. Blocks up to `timeout`: never call it on the UI thread.

        With `auto_update` off an automatic check does nothing; pass `manual=True` (a "check now" button)
        to check anyway. Never raises: a network or format problem is logged and the current profile stays.
        """
        if not manual and not self.auto_update:
            return UpdateResult("disabled", "automatic updates are off", self.current().profile_version)
        with self._check_lock:
            try:
                return self._check(timeout)
            except Exception as exc:
                log.warning("desktop profile: update check failed: %s", exc)
                return UpdateResult("error", str(exc) or type(exc).__name__, self.current().profile_version)

    def _stored_etag(self) -> str | None:
        try:
            meta = json.loads(self._meta_file.read_text(encoding="utf-8"))
            etag = meta.get("etag")
            return etag if isinstance(etag, str) and self._cache_file.exists() else None
        except (OSError, ValueError, AttributeError):
            return None

    def _check(self, timeout: float) -> UpdateResult:
        headers: dict[str, str] = {}
        etag = self._stored_etag()
        if etag:
            headers["If-None-Match"] = etag
        response = self._fetch(self._url, headers, timeout)
        active = self.current().profile_version
        if response.status == 304:
            return UpdateResult("unchanged", "the published profile did not change", active)
        if response.status != 200:
            return UpdateResult("error", f"unexpected HTTP status {response.status}", active)
        try:
            profile = parse_profile(response.body)
        except ProfileError as exc:
            log.warning("desktop profile: the published profile was refused: %s", exc)
            return UpdateResult("invalid", str(exc), active)
        reason = self._compatible(profile)
        if reason:
            return UpdateResult("incompatible", reason, active)
        best, _, _ = self._best_non_override()
        if profile.profile_version <= best.profile_version:
            return UpdateResult("not_newer", f"published version {profile.profile_version} is not newer", active)
        with self._lock:
            _atomic_write(self._cache_file, profile.to_json())
            _atomic_write(self._meta_file, json.dumps({"etag": response.etag}) + "\n")
        self._refresh()
        log.info("desktop profile: adopted published profile version %d", profile.profile_version)
        return UpdateResult("updated", f"adopted version {profile.profile_version}", self.current().profile_version)
