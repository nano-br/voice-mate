"""The Windows hotkeys script and its gettext registration must stay in sync.

`scripts/windows/voicemate-hotkeys.ps1` translates its messages at runtime from
the project's `.po` catalogs, looking each English msgid up with its
`Get-LocalizedText` function. Those msgids only reach the catalogs through
`app/i18n/windows_script_messages.py` (pybabel never scans the script), so a
message added to one side and not the other would silently stay in English.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po

from app.i18n.windows_script_messages import MESSAGES

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "windows" / "voicemate-hotkeys.ps1"
_LOCALES_DIR = _REPO_ROOT / "app" / "i18n" / "locales"

_LOOKUP = "Get-LocalizedText"
# Any mention of the lookup function as a whole word (PowerShell names may contain "-").
_LOOKUP_WORD = re.compile(rf"(?<![\w-]){_LOOKUP}(?![\w-])")
# A lookup call: the function name and a single-quoted literal ('' is an escaped quote).
_LOOKUP_CALL = re.compile(rf"{_LOOKUP}\s+'((?:[^']|'')*)'")
# .NET composite format items: {0}, {1,-8}, {2:N1}.
_FORMAT_ITEM = re.compile(r"\{\d+(?:,-?\d+)?(?::[^{}]*)?\}")
_DASHES = (chr(0x2014), chr(0x2013))  # em dash, en dash


def _script_source() -> str:
    return _SCRIPT.read_bytes().decode("utf-8-sig")


def _script_msgids() -> set[str]:
    return {literal.replace("''", "'") for literal in _LOOKUP_CALL.findall(_script_source())}


def _load_catalog(locale: str) -> Catalog:
    with (_LOCALES_DIR / locale / "LC_MESSAGES" / "voicemate.po").open("rb") as po_file:
        return read_po(po_file, locale=locale)


def test_script_and_registry_list_the_same_msgids() -> None:
    script = _script_msgids()
    registry = set(MESSAGES)
    assert script == registry, (
        f"only in the script: {sorted(script - registry)}; only in windows_script_messages: {sorted(registry - script)}"
    )


def test_registry_has_no_duplicates() -> None:
    duplicates = [msgid for msgid, count in Counter(MESSAGES).items() if count > 1]
    assert not duplicates, duplicates


def test_every_lookup_takes_a_single_quoted_literal() -> None:
    # A variable or double-quoted (expandable) argument would escape extraction.
    offenders: list[str] = []
    for number, line in enumerate(_script_source().splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        for match in _LOOKUP_WORD.finditer(line):
            if line[: match.start()].rstrip().endswith("function"):
                continue
            if not _LOOKUP_CALL.match(line, match.start()):
                offenders.append(f"{number}: {line.strip()}")
    assert not offenders, offenders


def test_script_source_is_ascii() -> None:
    # Translations come from the .po files at runtime, never from the script itself.
    non_ascii = [
        f"{number}: {line.strip()}"
        for number, line in enumerate(_script_source().splitlines(), start=1)
        if not line.isascii()
    ]
    assert not non_ascii, non_ascii


def test_msgids_are_plain_ascii() -> None:
    assert all(msgid.isascii() for msgid in MESSAGES), [msgid for msgid in MESSAGES if not msgid.isascii()]


def test_en_catalog_lists_every_script_msgid() -> None:
    known = {message.id for message in _load_catalog("en") if message.id}
    missing = sorted(set(MESSAGES) - known)
    assert not missing, f"run the pybabel extract/update steps: {missing}"


@pytest.mark.parametrize("locale", ["pt_BR", "es"])
def test_every_script_msgid_is_translated(locale: str) -> None:
    catalog = _load_catalog(locale)
    problems: list[str] = []
    for msgid in MESSAGES:
        message = catalog.get(msgid)
        if message is None:
            problems.append(f"missing: {msgid!r}")
        elif message.fuzzy:
            problems.append(f"fuzzy: {msgid!r}")
        elif not isinstance(message.string, str) or not message.string.strip():
            problems.append(f"untranslated: {msgid!r}")
    assert not problems, f"{locale}: {problems}"


@pytest.mark.parametrize("locale", ["pt_BR", "es"])
def test_translations_are_valid_dotnet_format_strings(locale: str) -> None:
    # The script feeds each translation to `-f` (String.Format): the format items must
    # match the msgid's and any other brace would make String.Format throw.
    catalog = _load_catalog(locale)
    problems: list[str] = []
    for msgid in MESSAGES:
        message = catalog.get(msgid)
        if message is None or not isinstance(message.string, str):
            continue
        translation = message.string
        if sorted(_FORMAT_ITEM.findall(translation)) != sorted(_FORMAT_ITEM.findall(msgid)):
            problems.append(f"format items differ: {msgid!r} -> {translation!r}")
        if "{" in _FORMAT_ITEM.sub("", translation) or "}" in _FORMAT_ITEM.sub("", translation):
            problems.append(f"stray brace: {translation!r}")
        if any(dash in translation for dash in _DASHES):
            problems.append(f"em/en dash: {translation!r}")
    assert not problems, f"{locale}: {problems}"


def test_msgids_are_valid_dotnet_format_strings() -> None:
    """English has no catalog check at runtime: the msgid goes straight to `-f`, so a
    stray brace would throw FormatException in en (and in any untranslated fallback)."""
    stray = [msgid for msgid in MESSAGES if {"{", "}"} & set(_FORMAT_ITEM.sub("", msgid))]
    assert not stray, stray


# `(Get-LocalizedText '...') -f a, b`: the literal, then everything after `-f`.
_FORMAT_CALL = re.compile(rf"\({_LOOKUP}\s+'((?:[^']|'')*)'\)\s+-f\s+(.*)$")


def _count_format_args(tail: str) -> int:
    """Top-level comma-separated arguments of `-f`, up to the enclosing `)` or end of line."""
    depth = 0
    count = 1
    quote: str | None = None
    for char in tail:
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            count += 1
    return count


def test_format_calls_pass_one_argument_per_placeholder() -> None:
    """A placeholder without its `-f` argument throws at runtime; an extra argument
    is a silent bug. Each `-f` must supply exactly max(index) + 1 values."""
    mismatches = []
    for line in _script_source().splitlines():
        match = _FORMAT_CALL.search(line)
        if match is None:
            continue
        msgid = match.group(1).replace("''", "'")
        indexes = [int(item[1:].split(",")[0].split(":")[0].rstrip("}")) for item in _FORMAT_ITEM.findall(msgid)]
        expected = max(indexes) + 1 if indexes else 0
        if _count_format_args(match.group(2)) != expected:
            mismatches.append((msgid, match.group(2).strip()))
    assert not mismatches, mismatches
