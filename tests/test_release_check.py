"""tools/release_check.py: what `make release-check` and the release workflow verify."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import release_check
from tools.release_check import NOTES_LANGUAGES, Release

_ROOT = Path(__file__).resolve().parents[1]


def _tree(tmp_path: Path, version: str = "1.2.3", changelog: str | None = None, notes: bool = True) -> Release:
    """A repository with a pyproject, a CHANGELOG and every notes file of `version`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_bytes(f'[tool.poetry]\nname = "voice-mate"\nversion = "{version}"\n'.encode())
    if changelog is None:
        changelog = f"# Changelog\n\n## [Unreleased]\n\n## [{version}] - 2026-10-02\n\n### Added\n\n- Things.\n"
    (tmp_path / "CHANGELOG.md").write_bytes(changelog.encode())
    release = Release(release_check.project_version(tmp_path), root=tmp_path)
    if notes:
        release.notes_file.parent.mkdir(parents=True)
        for path in [release.notes_file] + [release.translated_notes_file(lang) for lang in NOTES_LANGUAGES]:
            path.write_bytes(b"# Notes\n")
    return release


def test_a_complete_release_has_no_problems(tmp_path: Path) -> None:
    release = _tree(tmp_path)
    assert release_check.check(release, tag="v1.2.3") == []


@pytest.mark.parametrize(
    "version",
    [
        "1.2.3",
        "0.1.0",
        "10.20.30",
        "0.2.0-rc.1",
        "1.0.0-alpha",
        "1.0.0-alpha.beta.1",
        "1.0.0+build.5",
        "1.0.0-rc.1+sha.1",
        "1.0.0--",  # a pre-release identifier of one hyphen is valid SemVer
    ],
)
def test_semver_versions_are_accepted(version: str) -> None:
    assert release_check.version_problems(version) == []


@pytest.mark.parametrize(
    "version",
    [
        "1.2",
        "v1.2.3",
        "01.2.3",
        "1.2.3-",
        "1.2.3-01",
        "1.2.3.4",
        "1.2.3 ",
        "1.2.3\n",
        "1.2.3-rc.1\n",
        "\n1.2.3",
    ],
)
def test_other_versions_are_refused(version: str) -> None:
    # A trailing newline included: it would shift the key=value lines of $GITHUB_OUTPUT.
    assert release_check.version_problems(version)


def test_a_trailing_newline_is_not_a_prerelease_either() -> None:
    assert Release("1.2.3-rc.1\n").prerelease is False


@pytest.mark.parametrize(
    ("version", "numbers"),
    [
        ("0.1.0", (0, 1, 0, 0)),
        ("10.20.30", (10, 20, 30, 0)),
        # A pre-release and the final release share the numeric version: only the text differs.
        ("0.2.0-rc.1", (0, 2, 0, 0)),
        ("0.2.0", (0, 2, 0, 0)),
        ("1.0.0-alpha.beta.1+sha.5", (1, 0, 0, 0)),
        ("1.0.0+build.5", (1, 0, 0, 0)),
    ],
)
def test_the_windows_file_version_is_major_minor_patch_zero(version: str, numbers: tuple[int, ...]) -> None:
    # The PyInstaller spec builds the exe's version resource with this function.
    assert release_check.numeric_version(version) == numbers


@pytest.mark.parametrize("version", ["0.2.0rc1", "01.2.3", "1.2", "1.2.3\n", ""])
def test_the_windows_file_version_refuses_what_is_not_semver(version: str) -> None:
    # `0.2.0rc1` would otherwise become 0.2.0.1, which sorts after the final 0.2.0.
    with pytest.raises(ValueError, match="SemVer"):
        release_check.numeric_version(version)


def test_the_spec_takes_the_version_rules_from_this_module() -> None:
    spec = (_ROOT / "packaging" / "windows" / "voicemate-companion.spec").read_text(encoding="utf-8")
    assert "from tools.release_check import numeric_version, version_problems" in spec
    assert "SEMVER" not in spec  # no second regex to drift


@pytest.mark.parametrize("version", ["0.2.0rc1", "0.2.0a1", "0.2.0b2", "0.2.0.dev3"])
def test_pep440_pre_releases_are_refused_with_a_hint(version: str) -> None:
    (problem,) = release_check.version_problems(version)
    assert "0.2.0-rc.1" in problem


@pytest.mark.parametrize(("version", "prerelease"), [("1.2.3", False), ("1.2.3+build.1", False), ("0.2.0-rc.1", True)])
def test_prerelease_comes_from_the_semver_suffix(version: str, prerelease: bool) -> None:
    assert Release(version).prerelease is prerelease


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2.4", "v1.2.3-rc.1", "release-1.2.3"])
def test_the_tag_must_be_v_plus_the_version(tmp_path: Path, tag: str) -> None:
    (problem,) = release_check.check(_tree(tmp_path), tag=tag)
    assert "expected 'v1.2.3'" in problem


def test_no_tag_checks_the_tree_alone(tmp_path: Path) -> None:
    assert release_check.check(_tree(tmp_path)) == []


@pytest.mark.parametrize(
    "changelog",
    [
        "# Changelog\n\n## [Unreleased]\n\n- Things.\n",  # still unreleased
        "# Changelog\n\n## [1.2.30] - 2026-10-02\n",  # another version
        "# Changelog\n\n## 1.2.3 - 2026-10-02\n",  # no brackets
        "# Changelog\n\n### [1.2.3] - 2026-10-02\n",  # not a level-2 heading
    ],
)
def test_the_changelog_needs_the_version_section(tmp_path: Path, changelog: str) -> None:
    (problem,) = release_check.check(_tree(tmp_path, changelog=changelog))
    assert "'## [1.2.3] - YYYY-MM-DD'" in problem


@pytest.mark.parametrize("date", ["2026-13-01", "02/10/2026", "2026-10-2", "TBD"])
def test_the_changelog_date_must_be_an_iso_date(tmp_path: Path, date: str) -> None:
    (problem,) = release_check.check(_tree(tmp_path, changelog=f"## [1.2.3] - {date}\n"))
    assert "is not YYYY-MM-DD" in problem


def test_a_missing_changelog_is_reported(tmp_path: Path) -> None:
    release = _tree(tmp_path)
    (tmp_path / "CHANGELOG.md").unlink()
    assert release_check.check(release) == ["CHANGELOG.md not found"]


def test_every_notes_file_is_required(tmp_path: Path) -> None:
    release = _tree(tmp_path, notes=False)
    assert release_check.check(release) == [
        "docs/releases/v1.2.3.md not found",
        "docs/releases/v1.2.3.en.md not found",
        "docs/releases/v1.2.3.pt-BR.md not found",
        "docs/releases/v1.2.3.es.md not found",
        "docs/releases/v1.2.3.ru.md not found",
        "docs/releases/v1.2.3.zh-CN.md not found",
    ]


def test_an_empty_notes_file_is_reported(tmp_path: Path) -> None:
    release = _tree(tmp_path)
    release.translated_notes_file("ru").write_bytes(b" \n")
    assert release_check.check(release) == ["docs/releases/v1.2.3.ru.md is empty"]


def test_skip_notes_still_checks_the_version_and_the_tag(tmp_path: Path) -> None:
    release = _tree(tmp_path, notes=False)
    (tmp_path / "CHANGELOG.md").unlink()
    assert release_check.check(release, notes="skip") == []
    assert release_check.check(release, tag="v9.9.9", notes="skip")
    assert release_check.check(Release("0.2.0rc1", root=tmp_path), notes="skip")


def test_notes_if_present_leaves_out_a_release_that_was_not_started(tmp_path: Path) -> None:
    # A version bump alone, or an ordinary pull request: no section, no notes file.
    release = _tree(tmp_path, notes=False, changelog="# Changelog\n\n## [Unreleased]\n\n- Things.\n")
    assert release_check.check(release, notes="if-present") == []
    assert release_check.check(release, notes="require")


def test_notes_if_present_validates_a_release_prep_pull_request(tmp_path: Path) -> None:
    # The CHANGELOG section is there, the notes are not written yet.
    release = _tree(tmp_path, notes=False)
    problems = release_check.check(release, notes="if-present")
    assert "docs/releases/v1.2.3.md not found" in problems
    assert len(problems) == 1 + len(NOTES_LANGUAGES)


def test_notes_if_present_validates_a_release_with_a_notes_file_only(tmp_path: Path) -> None:
    release = _tree(tmp_path, notes=False, changelog="# Changelog\n\n## [Unreleased]\n")
    release.notes_file.parent.mkdir(parents=True)
    release.notes_file.write_bytes(b"# Notes\n")
    problems = release_check.check(release, notes="if-present")
    assert "CHANGELOG.md has no '## [1.2.3] - YYYY-MM-DD' section" in problems
    assert "docs/releases/v1.2.3.es.md not found" in problems


def test_notes_if_present_passes_a_complete_release(tmp_path: Path) -> None:
    assert release_check.check(_tree(tmp_path), notes="if-present") == []


def test_the_notes_modes_are_exclusive_on_the_command_line() -> None:
    with pytest.raises(SystemExit):
        release_check.main(["--skip-notes", "--notes-if-present"])


def test_github_outputs_name_what_the_workflow_publishes(tmp_path: Path) -> None:
    outputs = release_check.github_outputs(_tree(tmp_path, version="0.2.0-rc.1", changelog=""))
    assert outputs["version"] == "0.2.0-rc.1"
    assert outputs["tag"] == "v0.2.0-rc.1"
    assert outputs["prerelease"] == "true"
    assert outputs["installer"] == "VoiceMate-Setup-0.2.0-rc.1.exe"
    assert outputs["notes"] == "docs/releases/v0.2.0-rc.1.md"


def test_stage_notes_copies_every_translation_under_its_asset_name(tmp_path: Path) -> None:
    release = _tree(tmp_path / "repo", version="0.2.0-rc.1")
    release.translated_notes_file("zh-CN").write_bytes("# 说明\n".encode())
    staged = release_check.stage_notes(release, tmp_path / "assets")
    assert [path.name for path in staged] == [
        "VoiceMate-0.2.0-rc.1-release-notes.en.md",
        "VoiceMate-0.2.0-rc.1-release-notes.pt-BR.md",
        "VoiceMate-0.2.0-rc.1-release-notes.es.md",
        "VoiceMate-0.2.0-rc.1-release-notes.ru.md",
        "VoiceMate-0.2.0-rc.1-release-notes.zh-CN.md",
    ]
    assert staged[-1].read_bytes() == "# 说明\n".encode()
    # The English body (vX.Y.Z.md) is the release text, not an asset.
    assert len(list((tmp_path / "assets").iterdir())) == len(NOTES_LANGUAGES)


@pytest.mark.parametrize("flag", ["--skip-notes", "--notes-if-present"])
def test_stage_notes_cannot_be_combined_with_a_notes_check_that_may_skip(tmp_path: Path, flag: str) -> None:
    with pytest.raises(SystemExit):
        release_check.main([flag, "--stage-notes", str(tmp_path)])


def test_main_writes_the_outputs_only_when_consistent(tmp_path: Path) -> None:
    # This repository's real version (the notes may not be written yet: --skip-notes).
    tag = Release(release_check.project_version()).tag
    output = tmp_path / "github_output"
    assert release_check.main(["--tag", "v999.0.0", "--skip-notes", "--github-output", str(output)]) == 1
    assert not output.exists()
    assert release_check.main(["--tag", tag, "--skip-notes", "--github-output", str(output)]) == 0
    lines = output.read_bytes().decode("utf-8").splitlines()
    assert f"tag={tag}" in lines
    assert b"\r" not in output.read_bytes()


def test_this_repository_has_a_semver_version_the_companion_announces() -> None:
    release = Release(release_check.project_version())
    assert release_check.version_problems(release.version) == []
    assert release_check.companion_problems(release) == []


def test_the_workflow_and_the_makefile_use_the_helper() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "python -m tools.release_check" in workflow
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "-m tools.release_check" in makefile


def test_the_release_workflow_publishes_last_and_serializes_a_tag() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    # Assets first, then the text and the publication, whether or not the release existed.
    upload = workflow.index('gh release upload "$TAG"')
    edit = workflow.index('gh release edit "$TAG"')
    assert upload < edit
    assert "--draft=false" in workflow[edit:]
    # A tag push and a dispatch of that tag are one concurrency group; re-runs overwrite the artifact.
    assert "inputs.tag || github.ref_name }}" in workflow
    assert "overwrite: true" in workflow
    # A release prep pull request is validated, and the tagged commit needs a passing CI run.
    assert "--notes-if-present" in workflow
    assert "gh run list --workflow ci.yml" in workflow
    for path in ("pyproject.toml", "CHANGELOG.md", "docs/releases/**"):
        assert f'- "{path}"' in workflow
