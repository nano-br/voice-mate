"""Check that a release is consistent before it is tagged (`make release-check`).

The version has one source, pyproject.toml ([tool.poetry] version); docs/releasing.md
lists everything derived from it. For that version this checks:

- it is SemVer 2.0 in the form the Windows build accepts (MAJOR.MINOR.PATCH[-pre]);
- the tag, when one is given, is exactly "v" + the version;
- CHANGELOG.md has its section, `## [X.Y.Z] - YYYY-MM-DD` (Keep a Changelog 1.1.0);
- docs/releases/vX.Y.Z.md (the English release body) and its five translated files,
  docs/releases/vX.Y.Z.<lang>.md, exist and are not empty;
- the companion announces the same version (app/companion/version.py).

Standard library only, so any Python 3.11+ runs it without the project's environment.
The release workflow (.github/workflows/release.yml) runs it with `--github-output`
to get the names it publishes; `--skip-notes` leaves out the CHANGELOG and notes checks
for builds that publish nothing (the installer build of a pull request).
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
# semver.org's regex, without the leading "v": MAJOR.MINOR.PATCH[-pre-release][+build].
SEMVER: Final = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
# The languages of the translated notes, as GitHub and the READMEs spell them.
NOTES_LANGUAGES: Final = ("en", "pt-BR", "es", "ru", "zh-CN")


@dataclass(frozen=True)
class Release:
    version: str
    root: Path = ROOT

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def prerelease(self) -> bool:
        match = SEMVER.match(self.version)
        return bool(match and match.group(4))

    @property
    def installer_name(self) -> str:
        return f"VoiceMate-Setup-{self.version}.exe"

    @property
    def notes_file(self) -> Path:
        return self.root / "docs" / "releases" / f"{self.tag}.md"

    def translated_notes_file(self, language: str) -> Path:
        return self.root / "docs" / "releases" / f"{self.tag}.{language}.md"

    def notes_asset_name(self, language: str) -> str:
        return f"VoiceMate-{self.version}-release-notes.{language}.md"


def project_version(root: Path = ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as file:
        return str(tomllib.load(file)["tool"]["poetry"]["version"])


def version_problems(version: str) -> list[str]:
    if SEMVER.match(version):
        return []
    hint = ""
    if re.fullmatch(r"\d+\.\d+\.\d+(a|b|rc|\.dev|\.post)\d*", version):
        hint = " (a PEP 440 pre-release: write it the SemVer way, for example 0.2.0-rc.1)"
    return [f"pyproject.toml version {version!r} is not SemVer 2.0 MAJOR.MINOR.PATCH[-pre]{hint}"]


def tag_problems(release: Release, tag: str) -> list[str]:
    if tag == release.tag:
        return []
    return [f"tag {tag!r} does not match pyproject.toml version {release.version!r}: expected {release.tag!r}"]


def changelog_problems(release: Release) -> list[str]:
    changelog = release.root / "CHANGELOG.md"
    try:
        text = changelog.read_text(encoding="utf-8")
    except OSError:
        return [f"{changelog.name} not found"]
    heading = re.compile(rf"^## \[{re.escape(release.version)}\] - (\S+)[ \t]*$", re.MULTILINE)
    match = heading.search(text)
    if not match:
        return [f"{changelog.name} has no '## [{release.version}] - YYYY-MM-DD' section"]
    if not _is_iso_date(match.group(1)):
        return [f"{changelog.name}: the date of [{release.version}] is not YYYY-MM-DD: {match.group(1)!r}"]
    return []


def _is_iso_date(text: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def notes_problems(release: Release) -> list[str]:
    files = [release.notes_file] + [release.translated_notes_file(language) for language in NOTES_LANGUAGES]
    problems = []
    for path in files:
        relative = path.relative_to(release.root).as_posix()
        if not path.is_file():
            problems.append(f"{relative} not found")
        elif not path.read_text(encoding="utf-8").strip():
            problems.append(f"{relative} is empty")
    return problems


def companion_problems(release: Release) -> list[str]:
    """The companion derives its version (it used to hard-code a second copy)."""
    if release.root != ROOT:
        return []  # another tree (the tests): its app package is not the one importable here
    sys.path.insert(0, str(ROOT))
    try:
        from app.companion.client import COMPANION_VERSION
    except ImportError as error:
        return [f"cannot import the companion's version: {error}"]
    finally:
        sys.path.remove(str(ROOT))
    if COMPANION_VERSION != release.version:
        return [f"the companion announces {COMPANION_VERSION!r}, pyproject.toml says {release.version!r}"]
    return []


def check(release: Release, tag: str | None = None, skip_notes: bool = False) -> list[str]:
    problems = version_problems(release.version)
    if tag is not None:
        problems += tag_problems(release, tag)
    if not skip_notes:
        problems += changelog_problems(release) + notes_problems(release)
    return problems + companion_problems(release)


def github_outputs(release: Release) -> dict[str, str]:
    """What the release workflow needs, as `key=value` lines for $GITHUB_OUTPUT."""
    return {
        "version": release.version,
        "tag": release.tag,
        "prerelease": "true" if release.prerelease else "false",
        "installer": release.installer_name,
        "notes": release.notes_file.relative_to(release.root).as_posix(),
    }


def stage_notes(release: Release, out_dir: Path) -> list[Path]:
    """Copy the translated notes under their asset names (VoiceMate-X.Y.Z-release-notes.<lang>.md)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for language in NOTES_LANGUAGES:
        target = out_dir / release.notes_asset_name(language)
        target.write_bytes(release.translated_notes_file(language).read_bytes())
        staged.append(target)
    return staged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--tag", help="the git tag being released, e.g. v0.1.0 (must be 'v' + the version)")
    parser.add_argument(
        "--skip-notes",
        action="store_true",
        help="skip the CHANGELOG and release notes checks (builds that publish nothing)",
    )
    parser.add_argument("--github-output", type=Path, help="append the release names to this file ($GITHUB_OUTPUT)")
    parser.add_argument("--stage-notes", type=Path, metavar="DIR", help="copy the translated notes into DIR as assets")
    args = parser.parse_args(argv)
    if args.stage_notes and args.skip_notes:
        parser.error("--stage-notes needs the notes: drop --skip-notes")

    release = Release(project_version())
    problems = check(release, args.tag, args.skip_notes)
    if problems:
        print(f"Release {release.tag} is not ready:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if args.github_output:
        lines = "".join(f"{key}={value}\n" for key, value in github_outputs(release).items())
        with args.github_output.open("ab") as file:
            file.write(lines.encode("utf-8"))
    if args.stage_notes:
        for path in stage_notes(release, args.stage_notes):
            print(f"Staged {path.name}")
    kind = "pre-release" if release.prerelease else "release"
    print(f"Release {release.tag} ({kind}) is consistent: {release.installer_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
