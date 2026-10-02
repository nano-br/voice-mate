"""companion.toml: read with tomllib, written by a small writer of our own.

Layout and rules: docs/companion-app.md, "Settings file". Invalid or unknown values
fall back to their defaults with a log line (never an exception); a file written by a
newer version is loaded read-only. `validate` is the check applied before saving, and
returns localized messages for the settings window.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sys
import threading
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Final, TypeVar, get_args

from app.companion.chords import display_chord, normalize_chord
from app.companion.contract import (
    CUE_NAMES,
    SETTINGS_VERSION,
    CompanionSettings,
    CueName,
    CuePreset,
    CueSettings,
    CueSource,
    DictationLanguage,
    EngineMode,
    HotkeyBinding,
    NotifyLevel,
    UiLanguage,
    WslRestartPolicy,
)
from app.companion.cues import MAX_CUSTOM_SECONDS, check_cue_file, cue_label
from app.i18n import _

log = logging.getLogger(__name__)

# The spawn command is `bash -lc 'cd "$HOME/<engine_dir>" && ...'`: these would break out
# of the double quotes or run code (docs/companion-app.md, "Supervisor (WSL2)"); a
# backslash would escape the closing quote.
ENGINE_DIR_FORBIDDEN: Final = frozenset('"$`\\\n\r')
_DISTRO_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FLOW_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_CLIENT_KEY_RE: Final = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
PORT_MIN: Final = 1024
PORT_MAX: Final = 65535
BROKEN_SUFFIX: Final = ".broken"

_T = TypeVar("_T")


def new_client_key() -> str:
    return secrets.token_hex(16)


def engine_dir_is_valid(value: str) -> bool:
    return not any(char in ENGINE_DIR_FORBIDDEN for char in value)


def wsl_distro_is_valid(value: str) -> bool:
    return value == "" or _DISTRO_RE.match(value) is not None


def platform_engine_mode(platform: str) -> EngineMode:
    """The default engine mode of a platform (`sys.platform` value)."""
    return "wsl2" if platform == "win32" else "local"


def engine_mode_supported(mode: str, platform: str) -> bool:
    """wsl2 needs Windows, local needs a non-Windows host; external works everywhere."""
    if mode == "external":
        return True
    return mode == platform_engine_mode(platform)


def field_label(field_name: str) -> str:
    """Localized name of a setting, for validation messages."""
    cue = next((name for name in CUE_NAMES if field_name == f"cues.{name}"), None)
    if cue is not None:
        return cue_label(cue)
    labels = {
        "language": _("Language"),
        "engine_mode": _("Engine mode"),
        "wsl_restart_policy": _("WSL restart"),
        "notify_level": _("Notifications"),
        "hotkeys": _("Hotkeys"),
    }
    return labels.get(field_name, field_name)


# --- reading -------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadResult:
    settings: CompanionSettings
    read_only: bool  # written by a newer version: never overwrite it
    problems: tuple[str, ...]  # English log lines (values that fell back to defaults)
    missing: bool = False  # no file yet
    broken: bool = False  # not readable as TOML at all: defaults, the file is kept aside


class _Reader:
    def __init__(self, data: Mapping[str, object]) -> None:
        self.data = data
        self.problems: list[str] = []

    def problem(self, text: str) -> None:
        self.problems.append(text)

    def literal(self, key: str, options: tuple[_T, ...], default: _T, table: Mapping[str, object] | None = None) -> _T:
        source = self.data if table is None else table
        if key not in source:
            return default
        value = source[key]
        for option in options:
            if value == option:
                return option
        self.problem(f"{key}: invalid value {value!r}, using {default!r}")
        return default

    def boolean(self, key: str, default: bool, table: Mapping[str, object] | None = None) -> bool:
        source = self.data if table is None else table
        if key not in source:
            return default
        value = source[key]
        if isinstance(value, bool):
            return value
        self.problem(f"{key}: expected true/false, got {value!r}")
        return default

    def text(self, key: str, default: str, table: Mapping[str, object] | None = None) -> str:
        source = self.data if table is None else table
        if key not in source:
            return default
        value = source[key]
        if isinstance(value, str):
            return value
        self.problem(f"{key}: expected a string, got {value!r}")
        return default

    def unit_float(self, key: str, default: float, table: Mapping[str, object] | None = None) -> float:
        source = self.data if table is None else table
        if key not in source:
            return default
        value = source[key]
        if isinstance(value, int | float) and not isinstance(value, bool) and 0.0 <= float(value) <= 1.0:
            return float(value)
        self.problem(f"{key}: expected a number from 0 to 1, got {value!r}")
        return default


def _read_hotkeys(reader: _Reader, default: tuple[HotkeyBinding, ...]) -> tuple[HotkeyBinding, ...]:
    if "hotkeys" not in reader.data:
        return default
    raw = reader.data["hotkeys"]
    if not isinstance(raw, list):
        reader.problem(f"hotkeys: expected an array of tables, got {raw!r}")
        return default
    bindings: list[HotkeyBinding] = []
    seen_flows: set[str] = set()
    seen_chords: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            reader.problem(f"hotkeys: ignoring {entry!r}")
            continue
        flow = entry.get("flow")
        chord = normalize_chord(entry["chord"]) if isinstance(entry.get("chord"), str) else None
        if not isinstance(flow, str) or _FLOW_RE.match(flow) is None or chord is None:
            reader.problem(f"hotkeys: ignoring invalid entry {entry!r}")
            continue
        if flow in seen_flows or chord in seen_chords:
            reader.problem(f"hotkeys: ignoring duplicate entry {entry!r}")
            continue
        seen_flows.add(flow)
        seen_chords.add(chord)
        bindings.append(HotkeyBinding(flow, chord))
    return tuple(bindings)


def _read_cues(reader: _Reader) -> dict[CueName, CueSettings]:
    cues: dict[CueName, CueSettings] = {name: CueSettings() for name in CUE_NAMES}
    raw = reader.data.get("cues", {})
    if not isinstance(raw, dict):
        reader.problem(f"cues: expected tables, got {raw!r}")
        return cues
    base = CueSettings()
    for name, table in raw.items():
        cue = next((cue for cue in CUE_NAMES if cue == name), None)
        if cue is None or not isinstance(table, dict):
            reader.problem(f"cues: ignoring unknown cue {name!r}")
            continue
        cues[cue] = CueSettings(
            enabled=reader.boolean("enabled", base.enabled, table),
            source=reader.literal("source", get_args(CueSource), base.source, table),
            preset=reader.literal("preset", get_args(CuePreset), base.preset, table),
            file=reader.text("file", base.file, table),
            volume=reader.unit_float("volume", base.volume, table),
        )
    return cues


def parse_settings(text: str, platform: str = sys.platform) -> LoadResult:
    """Parse companion.toml text. Never raises: broken values fall back to defaults."""
    defaults = CompanionSettings(engine_mode=platform_engine_mode(platform))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return LoadResult(defaults, False, (f"companion.toml is not valid TOML ({exc}); using defaults",), broken=True)
    reader = _Reader(data)
    version = data.get("version", SETTINGS_VERSION)
    read_only = False
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        reader.problem(f"version: invalid value {version!r}, assuming {SETTINGS_VERSION}")
        version = SETTINGS_VERSION
    elif version > SETTINGS_VERSION:
        read_only = True
        reader.problem(f"version {version} is newer than {SETTINGS_VERSION}: settings are read-only")

    client_key = reader.text("client_key", "")
    if client_key and _CLIENT_KEY_RE.match(client_key) is None:
        reader.problem("client_key: invalid, a new one will be generated")
        client_key = ""
    engine_dir = reader.text("engine_dir", defaults.engine_dir)
    if not engine_dir_is_valid(engine_dir):
        reader.problem(f"engine_dir: forbidden characters in {engine_dir!r}")
        engine_dir = defaults.engine_dir
    wsl_distro = reader.text("wsl_distro", defaults.wsl_distro)
    if not wsl_distro_is_valid(wsl_distro):
        reader.problem(f"wsl_distro: invalid name {wsl_distro!r}")
        wsl_distro = defaults.wsl_distro
    port = data.get("daemon_port", defaults.daemon_port)
    if not isinstance(port, int) or isinstance(port, bool) or not PORT_MIN <= port <= PORT_MAX:
        reader.problem(f"daemon_port: invalid value {port!r}")
        port = defaults.daemon_port
    engine_mode = reader.literal("engine_mode", get_args(EngineMode), defaults.engine_mode)
    if not engine_mode_supported(engine_mode, platform):
        reader.problem(f"engine_mode: {engine_mode!r} is not available on {platform}, using {defaults.engine_mode!r}")
        engine_mode = defaults.engine_mode

    settings = CompanionSettings(
        version=version,
        client_key=client_key,
        language=reader.literal("language", get_args(UiLanguage), defaults.language),
        dictation_language=reader.literal(
            "dictation_language", get_args(DictationLanguage), defaults.dictation_language
        ),
        engine_mode=engine_mode,
        wsl_distro=wsl_distro,
        engine_dir=engine_dir,
        daemon_port=port,
        hotkeys=_read_hotkeys(reader, defaults.hotkeys),
        cues_enabled=reader.boolean("cues_enabled", defaults.cues_enabled),
        master_volume=reader.unit_float("master_volume", defaults.master_volume),
        cues=_read_cues(reader),
        wsl_restart_policy=reader.literal(
            "wsl_restart_policy", get_args(WslRestartPolicy), defaults.wsl_restart_policy
        ),
        notify_level=reader.literal("notify_level", get_args(NotifyLevel), defaults.notify_level),
        start_at_login=reader.boolean("start_at_login", defaults.start_at_login),
        tray_icon_visible=reader.boolean("tray_icon_visible", defaults.tray_icon_visible),
    )
    known = {field.name for field in fields(CompanionSettings)}
    for key in data:
        if key not in known:
            reader.problem(f"ignoring unknown setting {key!r}")
    return LoadResult(settings, read_only, tuple(reader.problems))


# --- writing -------------------------------------------------------------------------


def _toml_string(value: str) -> str:
    out = ['"']
    for char in value:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char == "\r":
            out.append("\\r")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(float(value))
    if isinstance(value, str):
        return _toml_string(value)
    raise TypeError(f"unsupported TOML value: {value!r}")


def dump_settings(settings: CompanionSettings) -> str:
    """The documented layout (top-level keys, [[hotkeys]], then one [cues.<name>] per cue)."""
    lines = [
        f"version = {_toml_value(settings.version)}",
        f"client_key = {_toml_value(settings.client_key)}",
        f"language = {_toml_value(settings.language)}",
        f"dictation_language = {_toml_value(settings.dictation_language)}",
        f"engine_mode = {_toml_value(settings.engine_mode)}",
        f"wsl_distro = {_toml_value(settings.wsl_distro)}",
        f"engine_dir = {_toml_value(settings.engine_dir)}",
        f"daemon_port = {_toml_value(settings.daemon_port)}",
        f"cues_enabled = {_toml_value(settings.cues_enabled)}",
        f"master_volume = {_toml_value(float(settings.master_volume))}",
        f"wsl_restart_policy = {_toml_value(settings.wsl_restart_policy)}",
        f"notify_level = {_toml_value(settings.notify_level)}",
        f"start_at_login = {_toml_value(settings.start_at_login)}",
        f"tray_icon_visible = {_toml_value(settings.tray_icon_visible)}",
    ]
    if not settings.hotkeys:
        lines.append("hotkeys = []")  # explicit: no hotkeys at all (absent = defaults)
    for binding in settings.hotkeys:
        lines += ["", "[[hotkeys]]", f"flow = {_toml_value(binding.flow)}", f"chord = {_toml_value(binding.chord)}"]
    for cue in CUE_NAMES:
        cue_settings = settings.cues.get(cue, CueSettings())
        lines += [
            "",
            f"[cues.{cue}]",
            f"enabled = {_toml_value(cue_settings.enabled)}",
            f"source = {_toml_value(cue_settings.source)}",
            f"preset = {_toml_value(cue_settings.preset)}",
            f"file = {_toml_value(cue_settings.file)}",
            f"volume = {_toml_value(float(cue_settings.volume))}",
        ]
    return "\n".join(lines) + "\n"


# --- validation ------------------------------------------------------------------------


def validate_settings(
    settings: CompanionSettings, *, check_files: bool = True, platform: str = sys.platform
) -> list[str]:
    """Localized problems that block saving; empty = valid."""
    errors: list[str] = []

    def literal_ok(value: object, options: tuple[object, ...]) -> bool:
        return value in options

    for field_name, value, options in (
        ("language", settings.language, get_args(UiLanguage)),
        ("dictation_language", settings.dictation_language, get_args(DictationLanguage)),
        ("engine_mode", settings.engine_mode, get_args(EngineMode)),
        ("wsl_restart_policy", settings.wsl_restart_policy, get_args(WslRestartPolicy)),
        ("notify_level", settings.notify_level, get_args(NotifyLevel)),
    ):
        if not literal_ok(value, options):
            errors.append(_("Invalid value for {setting}.").format(setting=field_label(field_name)))
    if literal_ok(settings.engine_mode, get_args(EngineMode)) and not engine_mode_supported(
        settings.engine_mode, platform
    ):
        errors.append(_("This engine mode is not available on this system."))
    if not engine_dir_is_valid(settings.engine_dir):
        errors.append(_("The engine folder cannot contain quotes, $, backticks, backslashes or line breaks."))
    if not wsl_distro_is_valid(settings.wsl_distro):
        errors.append(_("The WSL distribution name is not valid."))
    if not PORT_MIN <= settings.daemon_port <= PORT_MAX:
        errors.append(_("The port must be between {low} and {high}.").format(low=PORT_MIN, high=PORT_MAX))
    if not 0.0 <= settings.master_volume <= 1.0:
        errors.append(_("The volume must be between 0 and 1."))

    seen_flows: set[str] = set()
    seen_chords: set[str] = set()
    for binding in settings.hotkeys:
        chord = normalize_chord(binding.chord)
        if chord is None:
            errors.append(_("{hotkey} is not a valid hotkey.").format(hotkey=binding.chord))
            continue
        if _FLOW_RE.match(binding.flow) is None:
            errors.append(_("Invalid value for {setting}.").format(setting=field_label("hotkeys")))
            continue
        if binding.flow in seen_flows:
            errors.append(_("The action {flow} has more than one hotkey.").format(flow=binding.flow))
        if chord in seen_chords:
            errors.append(_("{hotkey} is assigned to more than one action.").format(hotkey=display_chord(chord)))
        seen_flows.add(binding.flow)
        seen_chords.add(chord)

    for cue in CUE_NAMES:
        cue_settings = settings.cues.get(cue, CueSettings())
        if not 0.0 <= cue_settings.volume <= 1.0:
            errors.append(_("The volume must be between 0 and 1."))
        if not literal_ok(cue_settings.source, get_args(CueSource)) or not literal_ok(
            cue_settings.preset, get_args(CuePreset)
        ):
            errors.append(_("Invalid value for {setting}.").format(setting=field_label(f"cues.{cue}")))
            continue
        if cue_settings.source != "file" or not check_files:
            continue
        problem = check_cue_file(cue_settings.file)
        label = cue_label(cue)
        if problem == "missing":
            errors.append(_("The sound file for the {cue} cue was not found.").format(cue=label))
        elif problem == "format":
            errors.append(_("The sound file for the {cue} cue must be a PCM 8 or 16-bit WAV file.").format(cue=label))
        elif problem == "too_long":
            errors.append(
                _("The sound file for the {cue} cue is longer than {seconds} seconds.").format(
                    cue=label, seconds=int(MAX_CUSTOM_SECONDS)
                )
            )
    # One message per distinct problem (the volume check may repeat per cue).
    return list(dict.fromkeys(errors))


def normalized(settings: CompanionSettings) -> CompanionSettings:
    """Canonical chords ("Ctrl+Alt+V" -> "ctrl+alt+v") and engine_dir without a leading "~/"."""
    hotkeys = tuple(HotkeyBinding(b.flow, normalize_chord(b.chord) or b.chord) for b in settings.hotkeys)
    engine_dir = settings.engine_dir.strip()
    if engine_dir == "~":
        engine_dir = ""
    elif engine_dir.startswith("~/"):
        engine_dir = engine_dir[2:]
    return replace(settings, hotkeys=hotkeys, engine_dir=engine_dir, wsl_distro=settings.wsl_distro.strip())


# --- the store -----------------------------------------------------------------------------


class SettingsStore:
    """Loads once, hands out immutable settings, writes atomically (temp file + replace).

    A file that is not readable as TOML is moved aside to `<name>.broken-<timestamp>` (never
    overwritten) before the defaults are written: `broken_backup` says where it went."""

    def __init__(self, path: Path, *, platform: str = sys.platform) -> None:
        self._path = path
        self.platform = platform
        self._lock = threading.Lock()
        self.broken_backup: Path | None = None
        result = self._load()
        # No settings file existed yet: this is the first run (first-run tips).
        self.first_run = result.missing
        self._settings = result.settings
        self._read_only = result.read_only

    @property
    def path(self) -> Path:
        return self._path

    @property
    def read_only(self) -> bool:
        return self._read_only

    def get(self) -> CompanionSettings:
        """Cached in memory (no I/O): the latest normalized settings, including values the
        core fills in itself (client_key, a detected engine_dir, the OS autostart state)."""
        return self._settings

    def _load(self) -> LoadResult:
        defaults = CompanionSettings(engine_mode=platform_engine_mode(self.platform))
        writable = True
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            result = LoadResult(defaults, False, (), missing=True)
        except UnicodeDecodeError as exc:
            result = LoadResult(defaults, False, (f"not UTF-8 text ({exc}); using defaults",), broken=True)
        except OSError as exc:
            # Cannot even read it (permissions?): use the defaults, but never write over it.
            result = LoadResult(defaults, False, (f"unreadable ({exc}); using defaults",))
            writable = False
        else:
            result = parse_settings(text, self.platform)
        for problem in result.problems:
            log.warning("settings: %s", problem)
        if result.broken:
            writable = self._move_aside()
        settings = normalized(result.settings)
        if not settings.client_key:
            settings = replace(settings, client_key=new_client_key())
            if not result.read_only and writable:
                try:
                    self._write(settings)
                except OSError as exc:
                    log.warning("settings: cannot write %s (%s)", self._path, exc)
        return replace(result, settings=settings)

    def _move_aside(self) -> bool:
        # Timestamped, so a second broken file never replaces the first backup.
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self._path.with_name(f"{self._path.name}{BROKEN_SUFFIX}-{stamp}")
        counter = 1
        while backup.exists():
            backup = self._path.with_name(f"{self._path.name}{BROKEN_SUFFIX}-{stamp}-{counter}")
            counter += 1
        try:
            os.replace(self._path, backup)
        except OSError as exc:
            log.error("settings: %s is broken and could not be moved aside (%s); not overwriting it", self._path, exc)
            return False
        log.error("settings: %s was not readable; kept as %s, starting from the defaults", self._path, backup)
        self.broken_backup = backup
        return True

    def _write(self, settings: CompanionSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dump_settings(settings))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self._path)

    def save(self, settings: CompanionSettings) -> None:
        """Persist (raises OSError, or PermissionError when the file is read-only)."""
        with self._lock:
            if self._read_only:
                raise PermissionError("settings file was written by a newer version")
            settings = normalized(settings)
            # The client key is ours to keep: a UI round trip must not drop it.
            if not settings.client_key:
                settings = replace(settings, client_key=self._settings.client_key)
            self._write(settings)
            self._settings = settings

    def update_cached(self, settings: CompanionSettings) -> None:
        """Replace the in-memory copy without writing (e.g. detected values on a read-only file)."""
        with self._lock:
            self._settings = normalized(settings)
