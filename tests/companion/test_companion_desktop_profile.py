"""The Claude Desktop profile: format, strict validation, sources, update and the guards no profile can lift.

No network and no screen: the fetcher is a fake and the UI Automation tree is a fake.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.companion.win import claude_desktop as cd
from app.companion.win import desktop_profile as dp
from app.companion.win.desktop_profile import (
    DesktopProfile,
    FetchResponse,
    ProfileError,
    ProfileManager,
    load_packaged,
    parse_profile,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_JSON = dp.PACKAGED_PROFILE_PATH
URL = "https://example.test/claude-desktop.json"
Json = Any  # a JSON value or a loosely typed argument


def base() -> dict[str, Any]:
    return json.loads(PACKAGED_JSON.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def variant(version: int | None = None, **edits: Json) -> dict[str, Any]:
    """The packaged profile with dotted-path edits (`"keys.wait_s": 0.1`); a `None` value removes the key."""
    data = base()
    if version is not None:
        data["profile_version"] = version
    for dotted, value in edits.items():
        *parents, leaf = dotted.split("__")
        node = data
        for key in parents:
            node = node[key]
        if value is None:
            del node[leaf]
        else:
            node[leaf] = value
    return data


def as_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data).encode("utf-8")


# --- the default profile is today's behavior ----------------------------------------------------


def test_the_repo_copy_is_the_packaged_profile() -> None:
    assert (ROOT / "profiles" / "claude-desktop.json").read_bytes() == PACKAGED_JSON.read_bytes()


def test_the_default_profile_has_the_values_the_driver_had_hard_coded() -> None:
    p = load_packaged()
    assert (p.schema, p.profile_version) == (1, 1)
    assert p.window == dp.Matcher("WindowControl", class_prefix="Chrome_WidgetWin", name_equals="Claude")
    assert p.conversation.document_name("T") == "T - Claude Code"
    assert p.conversation.title_of("T - Claude Code") == "T"
    assert p.conversation.title_of("other") == "other"
    assert p.conversation.max_visible_lines == 400
    assert p.keys.sendkeys("clear_draft") == ("{Ctrl}a", "{Delete}")
    assert p.keys.sendkeys("paste") == ("{Ctrl}v",)
    assert p.keys.sendkeys("submit") == ("{Enter}",)
    assert (p.keys.interval_s, p.keys.wait_s) == (0.002, 0.05)
    assert (p.paste.verify_max_chars, p.paste.verify_head_chars, p.paste.unverified_wait_s) == (2000, 30, 0.5)
    t = p.timeouts
    assert (t.lock_wait_s, t.open_wait_s, t.foreground_wait_s, t.focus_wait_s) == (30.0, 5.0, 1.5, 1.0)
    assert (t.focus_prompt_wait_s, t.paste_wait_s, t.submit_wait_s, t.poll_s) == (3.0, 2.0, 1.5, 0.05)
    assert cd.LOCK_WAIT_S == t.lock_wait_s


def test_the_default_profile_finds_the_same_controls_as_the_old_code() -> None:
    p = load_packaged()
    group = FakeControl("GroupControl", "x dframe-sidebar y")
    assert p.sidebar.container.steps[0].match.matches(group)
    assert not p.sidebar.container.steps[0].match.matches(FakeControl("PaneControl", "dframe-sidebar"))
    row_class = "w-full shrink-0 border-none rounded"
    assert p.sidebar.row.match.matches(FakeControl("ButtonControl", row_class))
    assert not p.sidebar.row.match.matches(FakeControl("ButtonControl", "other"))
    assert p.sidebar.label.matches(FakeControl("GroupControl", "a dframe-fade-label"))
    assert p.sidebar.status.matches(FakeControl("StatusBarControl", ""))
    doc = p.conversation.document.steps[0].match
    assert doc.matches(FakeControl("DocumentControl", "", "RootWebArea", "T - Claude Code"))
    assert not doc.matches(FakeControl("DocumentControl", "", "RootWebArea", "T"))
    assert not doc.matches(FakeControl("DocumentControl", "", "other", "T - Claude Code"))
    assert p.prompt.editor.steps[0].match.matches(FakeControl("EditControl", "tiptap ProseMirror"))
    assert p.prompt.focus.matches(FakeControl("GroupControl", "ProseMirror"))
    assert not p.prompt.focus.matches(FakeControl("EditControl", "textarea"))


def test_the_profile_round_trips_through_json() -> None:
    p = load_packaged()
    assert p.to_dict() == base()
    assert parse_profile(p.to_json()) == p


# --- strict validation --------------------------------------------------------------------------


def _bad(data: dict[str, Any] | bytes | str, reason: str) -> None:
    raw = as_bytes(data) if isinstance(data, dict) else data
    with pytest.raises(ProfileError, match=reason):
        parse_profile(raw)


def test_unknown_keys_are_refused_at_every_level() -> None:
    _bad({**base(), "run": "calc.exe"}, "unknown key.*run")
    _bad(variant(window__command="calc"), "window.*unknown key.*command")
    _bad(variant(keys__alt_f4=["alt+f4"]), "keys.*unknown key")
    _bad(variant(timeouts__sleep_s=1), "timeouts.*unknown key")
    data = variant()
    data["sidebar"]["row"]["match"]["exec"] = "x"
    _bad(data, "unknown key.*exec")


def test_missing_keys_wrong_types_and_ranges_are_refused() -> None:
    _bad(variant(window=None), "missing key.*window")
    _bad(variant(profile_version="2"), "profile_version.*integer")
    _bad(variant(profile_version=True), "profile_version.*integer")
    _bad(variant(profile_version=0), "profile_version.*between")
    _bad(variant(timeouts__open_wait_s=3600), "open_wait_s.*between")
    _bad(variant(timeouts__poll_s=0), "poll_s.*between")
    _bad(variant(timeouts__open_wait_s="5"), "open_wait_s.*number")
    _bad(variant(paste__verify_max_chars=0), "verify_max_chars.*between")
    _bad(variant(conversation__max_visible_lines=10**9), "max_visible_lines.*between")
    _bad(variant(keys__wait_s=60), "wait_s.*between")
    data = variant()
    data["sidebar"]["row"]["max_depth"] = 500
    _bad(data, "max_depth.*between")


def test_schema_and_min_version() -> None:
    _bad(variant(schema=2), "schema 2 is not supported")
    _bad(variant(schema=0), "schema 0 is not supported")
    _bad(variant(min_voicemate="soon"), "min_voicemate")
    assert parse_profile(as_bytes(variant(min_voicemate="1.2"))).min_voicemate == "1.2"


def test_matchers_need_conditions_and_known_types() -> None:
    data = variant()
    data["sidebar"]["label"] = {}
    _bad(data, "at least one condition")
    _bad(variant(window={"control_type": "WindowControl"}), "class, id or name")
    _bad(variant(window={"control_type": "NotAControl", "name_equals": "x"}), "unknown control type")
    _bad(variant(prompt__focus={"control_type": "EditControl"}), "class, id or name")
    data = variant()
    data["prompt"]["editor"] = {"path": []}
    _bad(data, "1 to 4 steps")


def test_schema_1_has_no_regular_expressions() -> None:
    # Python's re has no timeout: a published `(a|a)*$` would hang the flash for good.
    for key in ("name_regex", "class_regex"):
        _bad(variant(window={"control_type": "WindowControl", key: "(a|a)*$"}), "unknown key")
        _bad(variant(sidebar__row={"match": {key: ".*"}, "max_depth": 3}), "unknown key")
    assert not {"name_regex", "class_regex"} & {f.name for f in fields(dp.Matcher)}


def test_the_title_format_needs_one_placeholder() -> None:
    for bad in ("Claude Code", "{title} {title}", "{title} {x}", "} {title}"):
        _bad(variant(conversation__title_format=bad), "title_format")
    good = parse_profile(as_bytes(variant(conversation__title_format="Claude / {title}")))
    assert good.conversation.document_name("T") == "Claude / T"
    assert good.conversation.title_of("Claude / T") == "T"


def test_keys_are_checked_against_the_allowlist_and_the_role() -> None:
    _bad(variant(keys__clear_draft=["alt+f4"]), "key not allowed")
    _bad(variant(keys__submit=["ctrl+enter"]), "key not allowed")
    _bad(variant(keys__submit=["delete"]), "key not allowed")  # allowed somewhere, not for this step
    _bad(variant(keys__paste=["ctrl+a"]), "key not allowed")
    _bad(variant(keys__clear_draft=[]), "1 to 4")
    _bad(variant(keys__clear_draft=["ctrl+a"] * 5), "1 to 4")
    _bad(variant(keys__clear_draft="ctrl+a"), "list")
    _bad(variant(keys__clear_draft=[1]), "key not allowed")
    assert set(dp.KEY_SENDKEYS) == {"ctrl+a", "delete", "ctrl+v", "enter", "escape"}
    ok = parse_profile(as_bytes(variant(keys__clear_draft=["escape", "ctrl+a", "delete"])))
    assert ok.keys.sendkeys("clear_draft") == ("{Esc}", "{Ctrl}a", "{Delete}")


def test_bad_json_is_refused() -> None:
    _bad(b"{not json", "not valid JSON")
    _bad(b"\xff\xfe\x00", "not valid JSON")
    _bad(b"[]", "expected an object")
    _bad('{"schema": 1, "schema": 1}', "duplicate key")
    _bad('{"schema": NaN}', "NaN")
    _bad(b"[" * 100_000, "not valid JSON|larger")
    _bad(b" " * (dp.MAX_PROFILE_BYTES + 1), "larger than")
    _bad(variant(app__notes="line\nbreak"), "control characters")


# --- the ProfileManager -------------------------------------------------------------------------


class FakeFetch:
    def __init__(self, *responses: FetchResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> FetchResponse:
        self.calls.append((url, dict(headers), timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(data: dict[str, Any], etag: str | None = '"v2"') -> FetchResponse:
    return FetchResponse(200, etag, as_bytes(data))


def manager(tmp_path: Path, *responses: FetchResponse | Exception, **kwargs: Json) -> tuple[ProfileManager, FakeFetch]:
    fetch = FakeFetch(*responses)
    return ProfileManager(tmp_path / "cache", url=URL, fetch=fetch, **kwargs), fetch


def test_without_anything_else_the_packaged_profile_is_used(tmp_path: Path) -> None:
    m, _ = manager(tmp_path)
    assert m.current() == load_packaged()
    assert m.source() == "packaged"
    assert m.last_error == ""


def test_a_newer_valid_remote_profile_is_adopted_cached_and_reloaded(tmp_path: Path) -> None:
    seen: list[DesktopProfile] = []
    m, fetch = manager(tmp_path, ok(variant(5, timeouts__open_wait_s=9)), changed=seen.append)
    result = m.check_for_update()
    assert (result.status, result.version) == ("updated", 5)
    assert m.source() == "remote"
    assert m.current().timeouts.open_wait_s == 9
    assert [p.profile_version for p in seen] == [5]
    assert fetch.calls[0] == (URL, {}, 5.0)
    again = ProfileManager(tmp_path / "cache", url=URL, fetch=FakeFetch())  # a restart reads the cache
    assert (again.source(), again.current().profile_version) == ("remote", 5)


def test_only_a_higher_version_is_adopted(tmp_path: Path) -> None:
    m, _ = manager(tmp_path, ok(variant(1)), ok(variant(1, timeouts__open_wait_s=9)), ok(variant(3)), ok(variant(2)))
    assert m.check_for_update().status == "not_newer"  # equal to the packaged one
    assert m.check_for_update().status == "not_newer"
    assert m.check_for_update().status == "updated"
    assert m.current().profile_version == 3
    assert m.check_for_update().status == "not_newer"  # older than the cached one
    assert m.current().profile_version == 3


def test_an_unsupported_schema_or_invalid_remote_profile_keeps_the_current_one(tmp_path: Path) -> None:
    m, _ = manager(tmp_path, ok(variant(9, schema=2)), ok(variant(9, window={"control_type": "WindowControl"})))
    for _ in range(2):
        result = m.check_for_update()
        assert result.status == "invalid"
        assert result.detail
    assert (m.source(), m.current().profile_version) == ("packaged", 1)
    assert not (tmp_path / "cache" / "remote-profile.json").exists()


def test_a_profile_that_needs_a_newer_voicemate_is_not_adopted(tmp_path: Path) -> None:
    m, _ = manager(tmp_path, ok(variant(7, min_voicemate="2.0.0")), voicemate_version="1.9.9")
    result = m.check_for_update()
    assert result.status == "incompatible"
    assert "2.0.0" in result.detail
    assert m.current().profile_version == 1


def test_the_etag_is_sent_back_and_a_304_changes_nothing(tmp_path: Path) -> None:
    changes: list[DesktopProfile] = []
    m, fetch = manager(tmp_path, ok(variant(4), etag='"abc"'), FetchResponse(304, None, b""), changed=changes.append)
    assert m.check_for_update().status == "updated"
    assert m.check_for_update().status == "unchanged"
    assert fetch.calls[0][1] == {}
    assert fetch.calls[1][1] == {"If-None-Match": '"abc"'}
    assert len(changes) == 1
    assert m.current().profile_version == 4


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("no network"),
        TimeoutError("timed out"),
        urllib.error.HTTPError(URL, 500, "boom", {}, None),  # type: ignore[arg-type]
        OSError("reset"),
        ValueError("anything"),
    ],
)
def test_a_network_failure_never_raises_and_keeps_the_profile(tmp_path: Path, failure: Exception) -> None:
    m, _ = manager(tmp_path, failure)
    result = m.check_for_update()
    assert result.status == "error"
    assert m.current() == load_packaged()


def test_an_unexpected_http_status_is_an_error(tmp_path: Path) -> None:
    m, _ = manager(tmp_path, FetchResponse(404, None, b"Not Found"))
    assert m.check_for_update().status == "error"


def test_auto_update_off_skips_automatic_checks_but_not_the_manual_one(tmp_path: Path) -> None:
    m, fetch = manager(tmp_path, ok(variant(3)), auto_update=False)
    assert m.check_for_update().status == "disabled"
    assert fetch.calls == []
    assert m.check_for_update(manual=True).status == "updated"
    m.auto_update = True
    assert m.auto_update is True


def test_a_corrupt_cache_is_ignored_with_a_reason(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "remote-profile.json").write_text("{broken", encoding="utf-8")
    m, _ = manager(tmp_path)
    assert (m.source(), m.current().profile_version) == ("packaged", 1)
    assert "cached profile ignored" in m.last_error


def test_the_url_must_be_https(tmp_path: Path) -> None:
    for url in ("http://example.test/p.json", "file:///c:/p.json", "ftp://example.test/p.json"):
        with pytest.raises(ValueError, match="HTTPS"):
            ProfileManager(tmp_path, url=url)
    assert dp.DEFAULT_PROFILE_URL.startswith("https://raw.githubusercontent.com/nano-br/voice-mate/main/")
    assert dp.DEFAULT_PROFILE_URL.endswith("/profiles/claude-desktop.json")


def test_redirects_to_plain_http_are_refused() -> None:
    handler = dp._HttpsOnlyRedirect()
    request = urllib.request.Request(URL)
    with pytest.raises(urllib.error.URLError, match="non-HTTPS"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://evil.test/p.json")
    follow = handler.redirect_request(request, None, 302, "Found", {}, "https://example.test/other.json")
    assert follow is not None
    assert follow.full_url == "https://example.test/other.json"


# --- the person's override ------------------------------------------------------------------------


def test_an_imported_override_wins_over_remote_and_packaged(tmp_path: Path) -> None:
    changes: list[DesktopProfile] = []
    m, _ = manager(tmp_path, ok(variant(8)), changed=changes.append)
    m.check_for_update()
    mine = tmp_path / "mine.json"
    mine.write_bytes(as_bytes(variant(2, timeouts__open_wait_s=11)))  # an older version still wins: it is hers
    imported = m.import_profile(mine)
    assert (m.source(), m.current()) == ("override", imported)
    assert m.current().timeouts.open_wait_s == 11
    assert m.current().profile_version == 2
    assert len(changes) == 2
    again = ProfileManager(tmp_path / "cache", url=URL, fetch=FakeFetch())
    assert again.source() == "override"  # it survives a restart
    m.clear_override()
    assert (m.source(), m.current().profile_version) == ("remote", 8)
    assert len(changes) == 3


def test_a_remote_update_does_not_replace_an_override_but_is_cached(tmp_path: Path) -> None:
    m, _ = manager(tmp_path, ok(variant(6)))
    mine = tmp_path / "mine.json"
    mine.write_bytes(as_bytes(variant(1, paste__verify_head_chars=10)))
    m.import_profile(mine)
    assert m.check_for_update().status == "updated"
    assert m.source() == "override"
    assert m.current().paste.verify_head_chars == 10
    m.clear_override()
    assert (m.source(), m.current().profile_version) == ("remote", 6)


def test_an_invalid_import_is_refused_and_the_previous_profile_stays(tmp_path: Path) -> None:
    m, _ = manager(tmp_path)
    good = tmp_path / "good.json"
    good.write_bytes(as_bytes(variant(3)))
    m.import_profile(good)
    bad = tmp_path / "bad.json"
    bad.write_bytes(as_bytes(variant(4, keys__submit=["alt+f4"])))
    with pytest.raises(ProfileError, match="keys.submit"):
        m.import_profile(bad)
    with pytest.raises(ProfileError, match="could not read"):
        m.import_profile(tmp_path / "missing.json")
    assert (m.source(), m.current().profile_version) == ("override", 3)


def test_a_broken_override_file_falls_back_with_a_reason(tmp_path: Path) -> None:
    override = tmp_path / "override.json"
    override.write_text("nope", encoding="utf-8")
    m = ProfileManager(tmp_path / "cache", url=URL, fetch=FakeFetch(), override_path=override)
    assert m.source() == "packaged"
    assert "override profile ignored" in m.last_error
    assert m.override_path == override


def test_export_writes_the_effective_profile_and_it_imports_back(tmp_path: Path) -> None:
    m, _ = manager(tmp_path, ok(variant(5, timeouts__submit_wait_s=4)))
    m.check_for_update()
    out = tmp_path / "out" / "exported.json"
    m.export_profile(out)
    assert parse_profile(out.read_bytes()) == m.current()
    assert out.read_text(encoding="utf-8").endswith("}\n")
    other, _ = manager(tmp_path / "other")
    other.import_profile(out)
    assert other.current() == m.current()
    assert other.source() == "override"


def test_clearing_without_an_override_is_harmless(tmp_path: Path) -> None:
    changes: list[DesktopProfile] = []
    m, _ = manager(tmp_path, changed=changes.append)
    m.clear_override()
    assert changes == []


def test_a_failing_changed_callback_does_not_break_the_manager(tmp_path: Path) -> None:
    def boom(_: DesktopProfile) -> None:
        raise RuntimeError("ui gone")

    m, _ = manager(tmp_path, ok(variant(2)), changed=boom)
    assert m.check_for_update().status == "updated"
    assert m.current().profile_version == 2


def test_the_default_fetcher_reads_a_304_as_an_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    class Opener:
        def open(self, request: urllib.request.Request, timeout: float) -> Json:
            assert request.get_header("If-none-match") == '"e"'
            assert timeout == 3
            raise urllib.error.HTTPError(URL, 304, "Not Modified", {"ETag": '"e"'}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(dp.urllib.request, "build_opener", lambda *_: Opener())
    response = dp.urllib_fetch(URL, {"If-None-Match": '"e"'}, 3)
    assert (response.status, response.etag, response.body) == (304, '"e"', b"")


# --- the guards no profile can lift --------------------------------------------------------------


class FakeControl:
    def __init__(
        self,
        control_type: str,
        class_name: str = "",
        automation_id: str = "",
        name: str = "",
        handle: int = 0,
        children: list[FakeControl] | None = None,
    ) -> None:
        self.ControlTypeName = control_type
        self.ClassName = class_name
        self.AutomationId = automation_id
        self.Name = name
        self.NativeWindowHandle = handle
        self.children = children or []

    def GetChildren(self) -> list[FakeControl]:  # noqa: N802 (the uiautomation API)
        return self.children


def fake_backend(
    windows: list[FakeControl], profile: DesktopProfile | None = None, sent: list[str] | None = None
) -> cd.UiaBackend:
    backend = cd.UiaBackend(profile)
    backend._auto = SimpleNamespace(
        GetRootControl=lambda: FakeControl("PaneControl", children=windows),
        UIAutomationInitializerInThread=nullcontext,
        SendKeys=lambda keys, **_: (sent if sent is not None else []).append(keys),
        GetFocusedControl=lambda: FakeControl("EditControl", "ProseMirror"),
    )
    return backend


@pytest.fixture
def images(monkeypatch: pytest.MonkeyPatch) -> dict[int, str]:
    table: dict[int, str] = {}
    monkeypatch.setattr(cd, "process_image_of", lambda handle: table.get(handle, ""))
    return table


def test_the_window_must_belong_to_the_claude_process(images: dict[int, str]) -> None:
    claude = FakeControl("WindowControl", "Chrome_WidgetWin_1", "", "Claude", 10)
    images[10] = r"C:\Program Files\WindowsApps\Claude_4.29.0.0_x64__pzs8sxrjxfjjc\app\claude.exe"
    assert fake_backend([claude]).find_window() is claude
    images[10] = r"C:\Program Files\Google\Chrome\Application\chrome.exe"  # a browser with the same title
    assert fake_backend([claude]).find_window() is None
    images[10] = ""  # unreadable (elevated process): refused too
    assert fake_backend([claude]).find_window() is None


def test_a_hostile_window_matcher_cannot_reach_another_program(images: dict[int, str]) -> None:
    editor = FakeControl("WindowControl", "Notepad", "", "Untitled - Notepad", 20)
    desktop = FakeControl("PaneControl", "Progman", "", "Program Manager", 21)
    images[20] = r"C:\Windows\System32\notepad.exe"
    images[21] = r"C:\Windows\explorer.exe"
    hostile = parse_profile(as_bytes(variant(9, window={"control_type": "WindowControl", "class_contains": "o"})))
    assert fake_backend([editor, desktop], hostile).find_window() is None
    wide = parse_profile(as_bytes(variant(9, window={"class_contains": "o"})))
    assert fake_backend([editor, desktop], wide).find_window() is None  # only top-level windows, always


class _WatchedWindow(FakeControl):
    """A window whose class and name reads are counted: the matcher must never see it."""

    def __init__(self, handle: int) -> None:
        super().__init__("WindowControl", handle=handle)
        self.reads = 0

    @property  # type: ignore[override]
    def ClassName(self) -> str:  # noqa: N802 (the uiautomation API)
        self.reads += 1
        return "Chrome_WidgetWin_1"

    @ClassName.setter
    def ClassName(self, value: str) -> None:  # noqa: N802
        pass

    @property  # type: ignore[override]
    def Name(self) -> str:  # noqa: N802
        self.reads += 1
        return "Claude"

    @Name.setter
    def Name(self, value: str) -> None:  # noqa: N802
        pass


def test_the_process_is_checked_before_the_matcher_reads_a_window(images: dict[int, str]) -> None:
    browser_tab = _WatchedWindow(30)  # its title is chosen by a web page
    images[30] = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    assert fake_backend([browser_tab]).find_window() is None
    assert browser_tab.reads == 0


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"C:\Users\a\AppData\Local\AnthropicClaude\app-1.0\claude.exe", True),
        (r"C:\Program Files\WindowsApps\Claude_4.29.0.0_x64__pzs8sxrjxfjjc\app\claude.exe", True),
        (r"c:\program files\windowsapps\claude_4.29.0.0_x64__pzs8sxrjxfjjc\app\Helper.EXE", True),
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", False),
        (r"C:\Windows\System32\notepad.exe", False),
        (r"C:\Users\a\Downloads\Claude_x\evil.exe", False),  # a look-alike folder outside WindowsApps
        (r"C:\Program Files\WindowsApps\Other_1.0\app.exe", False),
        (r"C:\tools\claude.exe.bak", False),
        ("", False),
    ],
)
def test_which_executables_count_as_the_claude_desktop(path: str, expected: bool) -> None:
    assert cd.is_claude_image(path) is expected


def test_only_allowlisted_keys_reach_the_keyboard() -> None:
    sent: list[str] = []
    backend = fake_backend([], sent=sent)
    for keys in ("{Ctrl}a", "{Delete}", "{Ctrl}v", "{Enter}", "{Esc}"):
        backend.press(keys)
    assert sent == ["{Ctrl}a", "{Delete}", "{Ctrl}v", "{Enter}", "{Esc}"]
    hostile_keys = ("{Alt}{F4}", "{Win}r", "hello", "{Ctrl}c", "{Enter}calc{Enter}", "", "{Ctrl}a{Alt}")
    # One key per press: even a sequence of allowed keys is refused, so a guard runs before each key.
    for hostile in (*hostile_keys, "{Ctrl}a{Delete}", "{Enter}{Enter}"):
        with pytest.raises(cd.DesktopError, match="allowlist"):
            backend.press(hostile)
    assert len(sent) == 5


def test_a_profile_cannot_smuggle_keys_through_the_names() -> None:
    for names in (["{Alt}{F4}"], ["ctrl+a", "{Win}"], ["Ctrl+A"], ["ctrl+a "]):
        _bad(variant(keys__clear_draft=names), "key not allowed")
    for text in (variant(window__name_equals="Claude\n{Win}r"),):
        _bad(text, "control characters")


def test_the_prompt_focus_rule_cannot_be_empty_or_type_only() -> None:
    _bad(variant(prompt__focus={}), "at least one condition")
    _bad(variant(prompt__focus={"control_type": "EditControl"}), "class, id or name")


def test_the_profile_has_no_field_that_runs_or_opens_anything() -> None:
    fields = _all_keys(base())
    for forbidden in ("command", "exec", "run", "url", "path_to", "shell", "script", "open", "launch", "file"):
        assert forbidden not in fields
    # `path` exists only as the list of search steps of a locator, validated as steps.
    data = variant()
    data["prompt"]["editor"] = {"path": ["C:\\Windows\\system32\\calc.exe"]}
    _bad(data, "expected an object")


def _all_keys(node: Json) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _all_keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _all_keys(v)}
    return set()


def test_sidebar_rows_follow_the_profile_selectors() -> None:
    def row(label_class: str, title: str) -> FakeControl:
        label = FakeControl("GroupControl", label_class, children=[FakeControl("TextControl", name=title)])
        status = FakeControl("StatusBarControl", children=[FakeControl("TextControl", name="Running")])
        return FakeControl("ButtonControl", "w-full shrink-0 border-none", children=[label, status])

    nav = FakeControl("ButtonControl", "w-full shrink-0 border-none", children=[FakeControl("GroupControl", "icon")])
    sidebar = FakeControl("GroupControl", "dframe-sidebar", children=[nav, row("dframe-fade-label", "Alpha")])
    window = FakeControl("WindowControl", children=[FakeControl("PaneControl", children=[sidebar])])
    default = fake_backend([]).sidebar_entries(window)
    assert [(e.title, e.status) for e in default] == [("Alpha", "Running")]
    # After an update that renames the label class, only the new profile finds the title.
    sidebar.children = [nav, row("label-v2", "Alpha")]
    assert fake_backend([]).sidebar_entries(window) == []
    renamed = parse_profile(
        as_bytes(variant(2, sidebar__label={"control_type": "GroupControl", "class_equals": "label-v2"}))
    )
    assert [e.title for e in fake_backend([], renamed).sidebar_entries(window)] == ["Alpha"]


def test_the_document_prompt_and_visible_text_follow_the_profile() -> None:
    doc = FakeControl(
        "DocumentControl",
        "",
        "RootWebArea",
        "Alpha - Claude Code",
        children=[
            FakeControl("TextControl", name=" one "),
            FakeControl("TextControl", name=""),
            FakeControl("TextControl", name="two"),
        ],
    )
    prompt = FakeControl(
        "EditControl",
        "tiptap ProseMirror",
        children=[FakeControl("TextControl", name="hel"), FakeControl("TextControl", name="lo ")],
    )
    window = FakeControl("WindowControl", children=[FakeControl("PaneControl", children=[doc, prompt])])
    backend = fake_backend([])
    assert backend.document_name(window) == "Alpha - Claude Code"
    assert backend.visible_text(window) == ["one", "two"]
    assert backend.prompt_text(window) == "hello"
    assert backend.focus_in_prompt() is True
    changed = parse_profile(
        as_bytes(
            variant(
                2,
                conversation__document={
                    "match": {"control_type": "DocumentControl", "name_suffix": "Code"},
                    "max_depth": 30,
                },
            )
        )
    )
    assert fake_backend([], changed).document_name(window) == "Alpha - Claude Code"
    path_profile = variant(2)
    path_profile["prompt"]["editor"] = {
        "path": [
            {"match": {"control_type": "PaneControl"}, "max_depth": 2},
            {"match": {"control_type": "EditControl", "class_contains": "ProseMirror"}, "max_depth": 2},
        ]
    }
    routed = parse_profile(as_bytes(path_profile))
    assert fake_backend([], routed).prompt_text(window) == "hello"
    assert routed.to_dict() == path_profile


def test_the_backend_snapshots_the_profile_per_session() -> None:
    profiles = [load_packaged(), parse_profile(as_bytes(variant(2)))]
    backend = fake_backend([])
    backend._source = lambda: profiles[0]
    with backend.session():
        profiles.reverse()  # an update lands midway: this session keeps the one it started with
        assert backend._p().profile_version == 1
    with backend.session():
        assert backend._p().profile_version == 2
