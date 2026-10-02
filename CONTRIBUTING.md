# Contributing to VoiceMate

Purpose: how to set up a development environment, the quality bar every change must meet, and the rules for translations, documentation and commits. Back to the [README](README.md).

## Setup

Every dev command goes through the Makefile.

- **Engine** (Python 3.12, Poetry, GNU `make`): `make setup`, then `make doctor`. On WSL2 or Linux this is also where you run the engine tests.
- **Companion on Windows** (Python 3.12 or 3.13): `make companion-venv` once. It creates `.venv-companion` with the pinned versions from `requirements/companion-constraints.txt`. On Linux the companion can also use the Poetry environment with `poetry install --extras ui`.

How the pieces fit together: [docs/architecture.md](docs/architecture.md) and [docs/companion-app.md](docs/companion-app.md).

## Makefile targets

| Target | What it does |
|---|---|
| `make setup` / `make configure` / `make doctor` | Guided install, re-run of its questions, environment diagnostics |
| `make run` / `make run-engine` | The engine with its own hotkeys / the engine as the companion starts it |
| `make format` | `ruff format` and `ruff check --fix` |
| `make lint` | `ruff check` and `mypy` (strict) |
| `make test` | The whole pytest suite |
| `make all` | `format`, `lint` and `test` |
| `make companion-lint` / `make companion-test` | Ruff, format check and mypy / pytest for the companion code |
| `make run-tray` | The companion from source (`ARGS="--demo"` for the demo with a fake engine) |
| `make companion-build` / `make companion-installer` | PyInstaller bundle / Inno Setup installer (Windows only) |
| `make docs-screenshots` | Renders the README screenshots in every language (Windows, `tools/render_screenshots.py`) |
| `make stt-eval` | Speech-to-text quality gate (word error rate and split words) against local samples |
| `make i18n-extract` / `make i18n-update` / `make i18n-compile` | Translation catalogs (see below) |

## Quality bar

A change is ready when these pass:

```bash
make format lint test                  # engine (and the whole repository)
make companion-lint companion-test     # companion
```

- **Ruff** is the only linter and formatter (rules `E`, `F`, `I`, `UP`, `ANN`, line length 120, Python 3.12 target).
- **mypy** runs in strict mode (`disallow_untyped_defs`, `disallow_any_generics`, `warn_return_any`, and more in `pyproject.toml`). Every function has type annotations.
- Discrete options are `Literal` types (or enums), never free strings.
- **Tests** come with every behavior change. UI tests run headless (`QT_QPA_PLATFORM=offscreen`, set by `tests/companion/ui/conftest.py`) and nothing may appear on screen. Companion tests skip without PySide6.
- `tests/test_import_boundary.py` keeps the companion free of the engine stack (no numpy, torch, `app.core`, `app.features`, `app.cli`, `app.setup` or `app.daemon` imports), and keeps Windows-only and Linux-only code in their packages.
- Code, comments, identifiers and log messages are in English. Files use LF line endings (`.gitattributes` enforces it).

## Translations (i18n)

Every user-facing string exists in 5 languages: English (`en`), Brazilian Portuguese (`pt_BR`), Spanish (`es`), Russian (`ru`) and Simplified Chinese (`zh_CN`). The catalogs are `app/i18n/locales/<lang>/LC_MESSAGES/voicemate.po`, managed with gettext and Babel.

To add or change a string:

1. Mark it in the code with `_()` (or `N_()`), with the English text as the `msgid`.
2. Run `make i18n-extract` (updates `app/i18n/locales/voicemate.pot`), then `make i18n-update` (adds the `msgid` to every `.po`).
3. Translate it in `pt_BR`, `es`, `ru` and `zh_CN`. In `en`, leave `msgstr` empty: the `msgid` is the text.
4. Remove any `fuzzy` flag, keep every placeholder (`{name}`), command and file name exactly as in the `msgid`, and use no em dash or en dash.
5. Run `make i18n-compile` and `make test`. `tests/test_i18n.py` fails when a string is missing, untranslated, fuzzy, or loses a placeholder.

The Windows installer messages live in `packaging/windows/voicemate-companion.iss` and the Linux launcher texts in `packaging/linux/voicemate-companion.desktop`: update all 5 languages there too.

## Documentation

- `README.md` is the English source. `README.pt-BR.md`, `README.es.md`, `README.ru.md` and `README.zh-CN.md` tell the same story in each language; keep their structure in step with the English one.
- When the README quotes a UI label, quote it exactly as the UI shows it in that language (take it from that language's `.po`).
- Details belong in `docs/`. Every doc starts with a one-line purpose and a link back to the README.
- When the code changes a behavior that a doc describes, update the doc in the same pull request.
- After a UI change, regenerate the screenshots with `make docs-screenshots` (Windows).
- The five diagrams in the README (inside `<details>`: system context, containers, one dictation, tray states, supervisor) are simplified overviews of sections 1, 2, 4, 5 and 6 of [docs/architecture.md](docs/architecture.md). They hide details but must never contradict the full diagrams: when the architecture changes, update both in the same pull request.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/) in English: `feat(companion): ...`, `fix: ...`, `docs: ...`, `ci: ...`, `chore: ...`.
- The commit body explains the context: what changed and why.
- Keep commits small and logical. A pull request describes the change, how it was tested, and any upgrade note.
- Releases: see [docs/releasing.md](docs/releasing.md) and [CHANGELOG.md](CHANGELOG.md).
