# Releasing VoiceMate

How VoiceMate is versioned, where the version comes from, and the steps that turn a
merged `main` into a GitHub release with the Windows installer.

## Versioning policy

VoiceMate follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html):
`MAJOR.MINOR.PATCH`, tagged `vMAJOR.MINOR.PATCH` (for example `v0.1.0`).

The public contract that a version number speaks about is:

- the configuration: `~/.config/voicemate/config.toml` and the companion's settings file
  (keys, meaning, defaults);
- the command line of `voice-mate` and `voice-mate-tray` (flags and their behavior);
- the engine's HTTP API v2 (`app/protocol`, `API_VERSION`): endpoints, payloads, the
  bearer token, what a companion of the same major version may rely on.

| Change | 1.0.0 and later | While 0.x |
| --- | --- | --- |
| Incompatible change to the contract above (a key or flag removed or renamed, an API field or endpoint removed or changed, `API_VERSION` raised without keeping the older one) | MAJOR | MINOR |
| New feature or backward-compatible addition (a new flow, setting, flag, API field or language, a new platform) | MINOR | MINOR |
| Bug fix, translation fix, packaging fix, performance work, docs | PATCH | PATCH |

**0.x means the contract may still change between minors.** Until 1.0.0, read a MINOR
bump as "may need you to adjust your config or scripts" (the release notes say what);
PATCH releases never do.

### Pre-releases

A pre-release adds a SemVer suffix: `0.2.0-rc.1`, `0.2.0-beta.2`, tagged `v0.2.0-rc.1`.
The release workflow marks it as a pre-release on GitHub (never "Latest").

Write the SemVer form by hand in `pyproject.toml`. The PEP 440 spelling (`0.2.0rc1`,
which `poetry version prerelease` produces) is refused by `make release-check`, the
release workflow and the PyInstaller spec: Windows file versions are four numbers, and
taking every digit of `0.2.0rc1` would make the numeric version `0.2.0.1`, which sorts
after the final `0.2.0` (`0.2.0.0`). With the SemVer form the numeric version is
`MAJOR.MINOR.PATCH.0` for the pre-releases and the final release alike; the text
versions (and the installer's name) keep the suffix. The installer copies its files
with `ignoreversion`, so an equal numeric version never leaves an older file behind.

## The version has one source

`pyproject.toml`, `[tool.poetry] version`. Everything else derives from it:

| Where | How |
| --- | --- |
| Engine `/health` (`engine_version`) | `importlib.metadata.version("voice-mate")` in `app/daemon/server.py`, `0.0.0` when the package is not installed |
| Companion (`COMPANION_VERSION`, sent when it registers with the engine) | `app/companion/version.py`: the `VERSION` file the Windows build bakes into the bundle, else `pyproject.toml` in a source checkout, else the installed package metadata, else `0.0.0` |
| `VoiceMate.exe` version resource (Explorer's Details tab) | `packaging/windows/voicemate-companion.spec` reads `pyproject.toml`: text versions are the full version, the numeric one is `MAJOR.MINOR.PATCH.0` |
| `app/companion/VERSION` inside the build | written by the spec into the build folder (never into the source tree) |
| Installer version and name, `VoiceMate-Setup-<version>.exe` | `packaging/windows/voicemate-companion.iss` reads the exe's `ProductVersion` |
| Git tag | `v` + the version, checked by `make release-check TAG=...` and the release workflow |
| Release assets | `VoiceMate-Setup-<version>.exe`, `.exe.sha256`, `VoiceMate-<version>-release-notes.<lang>.md` |

Not derived, and not bumped: the `Project-Id-Version: voice-mate 0.1.0` header of the
`.po` catalogs. It was written when the catalogs were created, `pybabel update` keeps
it as it is, and gettext ignores it at runtime. It is cosmetic.

Never write the version anywhere else: `tests/companion/test_companion_version.py` and
`make release-check` fail when the companion disagrees with `pyproject.toml`.

## Release checklist

Example for `0.2.0`; replace it everywhere.

1. **Pick the version** with the policy above, from the `[Unreleased]` entries of
   `CHANGELOG.md`.
2. **Bump** `version = "0.2.0"` in `pyproject.toml` (by hand, or `poetry version 0.2.0`
   for a final version). `poetry.lock` does not change.
3. **Update `CHANGELOG.md`** ([Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)):
   move the `[Unreleased]` entries under a new `## [0.2.0] - YYYY-MM-DD` heading (the
   date the tag is pushed, ISO 8601), leave an empty `## [Unreleased]` above it, and
   update the compare links at the bottom.
4. **Write the release notes** in `docs/releases/`:
   - `v0.2.0.md`: the English release body shown on GitHub. Do not write the checksum:
     the workflow appends it.
   - `v0.2.0.en.md`, `v0.2.0.pt-BR.md`, `v0.2.0.es.md`, `v0.2.0.ru.md`, `v0.2.0.zh-CN.md`:
     the notes plus the step-by-step installation, one file per language. They are
     published as `VoiceMate-0.2.0-release-notes.<lang>.md`. Quote UI labels exactly as
     that language's `.po` has them.
5. **Check**: `make release-check` (SemVer version, CHANGELOG section with a valid date,
   the six notes files present and not empty, the companion agrees).
6. **Pull request** with these changes. CI must be green; when the PR touches the
   packaging, the Release workflow also builds the installer as a workflow artifact
   (download it from the run to try it). Merge it.
7. **Tag `main`**, annotated, and push the tag:

   ```bash
   git switch main
   git pull --ff-only
   make release-check TAG=v0.2.0
   git tag -a v0.2.0 -m "VoiceMate 0.2.0"
   git push origin v0.2.0
   ```

8. **The Release workflow** (`.github/workflows/release.yml`) runs on the tag:
   - *build* (Windows, read-only): checks that the tag is `v` + the version, that it is
     on `main` and that the notes exist (`python -m tools.release_check --tag v0.2.0`),
     installs the pinned Inno Setup, runs `make companion-installer`, writes
     `VoiceMate-Setup-0.2.0.exe.sha256` (`<hash>  <file name>`) and stages the assets;
   - *publish* (Linux, the only job allowed to write): verifies the checksum, creates
     the release as a draft with `v0.2.0.md` plus the SHA-256 as its body, uploads the
     installer, the `.sha256` and the five notes files, then publishes it (as a
     pre-release when the version has a suffix).
9. **Verify** the published release: the assets are all there, and the checksum matches
   the downloaded installer:

   ```powershell
   (Get-FileHash .\VoiceMate-Setup-0.2.0.exe -Algorithm SHA256).Hash
   ```

   ```bash
   sha256sum -c VoiceMate-Setup-0.2.0.exe.sha256
   ```

   Then install it on a Windows account and check the version in `VoiceMate.exe`'s
   Properties, Details tab.

## Re-running a release

- **The build or the publish failed** (runner trouble, Inno Setup download): re-run the
  failed jobs from the Actions tab, or start the workflow again for the tag:

  ```bash
  gh workflow run release.yml -f tag=v0.2.0
  ```

  A re-run builds the tag itself (not the branch it was started from), applies the same
  checks, updates the existing release in place and replaces its assets.
- **The release is published and only its text is wrong**: fix the notes in a PR, then
  edit the release directly instead of rebuilding (a rebuilt installer has a different
  checksum than the one people may have already verified):

  ```bash
  gh release edit v0.2.0 --notes-file body.md        # body.md = fixed v0.2.0.md + the SHA-256 section
  gh release upload v0.2.0 VoiceMate-0.2.0-release-notes.es.md --clobber
  ```

- **The installer is broken**: release a new PATCH version. Never move or reuse a tag
  that was published.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`:

- *companion* (Windows, Python 3.13): `make companion-venv`, then `make companion-lint`
  and `make companion-test` with `QT_QPA_PLATFORM=offscreen`.
- *engine* (Linux, Python 3.12): a plain `poetry install` (the core and the dev tools,
  no extras and no PyTorch), then `make lint`, `ruff format --check` and `make test`.
  Tests that need an extra (Claude, PyTorch, PySide6) or Windows skip themselves.

The Release workflow also runs on pull requests that touch `packaging/`,
`tools/build_installer.py`, `tools/release_check.py`, `requirements/companion*`,
`app/companion/version.py` or the workflow itself: it builds the installer and keeps it as a workflow artifact for 7 days,
without publishing anything.
