.PHONY: all setup configure doctor setup_env setup_env_minimal setup_env_claude setup_env_tts setup_env_custom lock \
        format lint test stt-eval run run-large run-turbo run-vozes-aleatorias run-reset-voz \
        i18n-extract i18n-init-pt i18n-init-en i18n-init-es i18n-init-ru i18n-init-zh i18n-update i18n-compile i18n-mo clean \
        companion-venv companion-test companion-lint run-tray companion-build companion-installer \
        release-check

all: format lint test

# Compiled catalogs (.mo) are gitignored: build each one from its .po whenever the
# .po changes, so a fresh clone speaks pt-BR, en, es, ru and zh-CN instead of only English.
MO_FILES := $(patsubst %.po,%.mo,$(wildcard app/i18n/locales/*/LC_MESSAGES/voicemate.po))

# ─── Setup recomendado ───────────────────────────────────────────────────────
# Detecta a GPU (NVIDIA/AMD/CPU), confirma com você, instala o torch certo
# (CUDA cu128 / ROCm / CPU) + os módulos escolhidos, e lembra a escolha em
# ~/.config/voicemate/config.toml. Funciona em PowerShell e Git Bash.
setup:
	poetry install
	$(MAKE) i18n-compile
	poetry run python -m app.setup.gpu_bootstrap

# Re-pergunta vendor/módulo/TTS e reinstala o torch certo (sem mexer no resto).
configure:
	poetry run python -m app.setup.gpu_bootstrap --reconfigure

# Diagnóstico do ambiente: áudio/mic (WSLg), grupo input (evdev), whisper.cpp,
# Claude CLI, torch+GPU. Nunca aborta — imprime ✓/✗ com a correção de cada item.
doctor:
	poetry run python -m app.setup.doctor

# ─── Setup legado (assume NVIDIA) ────────────────────────────────────────────
# Compat com o fluxo antigo: instala tudo + torch CUDA. Prefira `make setup`,
# que também cobre AMD/CPU.
setup_env:
	poetry install --extras all
	poetry run python -m app.setup.gpu_bootstrap --vendor nvidia --yes --extras all

# Só core (transcrição + clipboard). Sem Claude, sem TTS.
setup_env_minimal:
	poetry install

setup_env_claude:
	poetry install --extras claude

setup_env_tts:
	poetry install --extras tts

# Instalação custom: make setup_env_custom EXTRAS="claude tts"
setup_env_custom:
	poetry install --extras "$(EXTRAS)"

lock:
	poetry lock

# ─── i18n (gettext + Babel) ──────────────────────────────────────────────────
# Extrai strings marcadas com _() para um catálogo .pot, gera/atualiza os .po
# de cada idioma e compila para .mo (que o gettext carrega em runtime).

i18n-extract:
	poetry run pybabel extract -F app/i18n/babel.cfg -o app/i18n/locales/voicemate.pot app

i18n-init-pt:
	poetry run pybabel init -i app/i18n/locales/voicemate.pot -d app/i18n/locales -D voicemate -l pt_BR

i18n-init-en:
	poetry run pybabel init -i app/i18n/locales/voicemate.pot -d app/i18n/locales -D voicemate -l en

i18n-init-es:
	poetry run pybabel init -i app/i18n/locales/voicemate.pot -d app/i18n/locales -D voicemate -l es

i18n-init-ru:
	poetry run pybabel init -i app/i18n/locales/voicemate.pot -d app/i18n/locales -D voicemate -l ru

i18n-init-zh:
	poetry run pybabel init -i app/i18n/locales/voicemate.pot -d app/i18n/locales -D voicemate -l zh_CN

i18n-update:
	poetry run pybabel update -i app/i18n/locales/voicemate.pot -d app/i18n/locales -D voicemate

app/i18n/locales/%/LC_MESSAGES/voicemate.mo: app/i18n/locales/%/LC_MESSAGES/voicemate.po
	poetry run pybabel compile -d app/i18n/locales -D voicemate -l $*

# Rebuild only the .mo files whose .po changed (used by the systemd unit too).
i18n-mo: $(MO_FILES)

i18n-compile:
	poetry run pybabel compile -d app/i18n/locales -D voicemate

format:
	poetry run ruff format .
	poetry run ruff check --fix .

lint:
	poetry run ruff check .
	poetry run mypy .

test:
	poetry run pytest -v

# Gate de qualidade STT: WER + palavras quebradas por backend, contra amostras
# locais (samples/ptbr/*.wav + .ref.txt). Ex.:
#   make stt-eval ARGS="--backends faster-whisper --save-baseline"  # gravar baseline (main/NVIDIA)
#   make stt-eval ARGS="--backends whispercpp"                      # comparar
stt-eval:
	poetry run python -m tools.stt_eval $(ARGS)

run: $(MO_FILES)
	poetry run voice-mate $(ARGS)

# Engine for the companion app (docs/companion-app.md): stops when stdin closes, serves
# the HTTP API v2 (next to the native hotkeys on Linux; on WSL2 the API is the trigger)
# and requires the bearer token from ~/.config/voicemate/api-token. Port and the rest:
#   make run-engine ARGS="--daemon-port 47821"
.PHONY: run-engine
run-engine: $(MO_FILES)
	poetry run voice-mate --supervised --api --api-token $(ARGS)

run-large: $(MO_FILES)
	poetry run voice-mate --model large-v3 $(ARGS)

run-turbo: $(MO_FILES)
	poetry run voice-mate --model large-v3-turbo $(ARGS)

# Atalhos para os modos de voz mais comuns
run-vozes-aleatorias: $(MO_FILES)
	poetry run voice-mate --tts-voice-seed-mode off $(ARGS)

run-reset-voz: $(MO_FILES)
	poetry run voice-mate --tts-reset-seed $(ARGS)

# ─── Companion (tray app) ────────────────────────────────────────────────────
# Windows: its own venv, .venv-companion (`make companion-venv`), since the engine
# lives in WSL. Linux: .venv-companion when it exists, otherwise the poetry env with
# the `ui` extra (`poetry install --extras ui`). Override with COMPANION_PY=...
# Every recipe is one plain command with forward slashes, so it runs the same whether
# make hands it to cmd.exe (PowerShell) or to sh (Git Bash).
ifeq ($(OS),Windows_NT)
COMPANION_VENV_PY := .venv-companion/Scripts/python.exe
COMPANION_BOOTSTRAP ?= py -3
COMPANION_FALLBACK_PY :=
else
COMPANION_VENV_PY := .venv-companion/bin/python
COMPANION_BOOTSTRAP ?= python3
COMPANION_FALLBACK_PY := poetry run python
endif
# Expanded when a recipe runs, not when the Makefile is read, so one invocation such
# as `make companion-venv run-tray` already uses the venv it has just created.
COMPANION_PY ?= $(if $(wildcard $(COMPANION_VENV_PY)),"$(COMPANION_VENV_PY)",$(COMPANION_FALLBACK_PY))
# The interpreter, or an error that stops only the target that needs it.
companion_py = $(or $(COMPANION_PY),$(error No companion environment: run "make companion-venv" first))
windows_only = $(if $(filter Windows_NT,$(OS)),,$(error "make $@" builds the Windows app: run it on Windows))
# The release helper too: the release workflow runs it on Windows.
COMPANION_TESTS := $(wildcard tests/companion) tests/test_import_boundary.py tests/test_companion_packaging.py \
                   tests/test_release_check.py
COMPANION_SOURCES := app/companion app/protocol $(COMPANION_TESTS) \
                     tools/gen_icon.py tools/build_installer.py tools/release_check.py \
                     packaging/windows/voicemate_launcher.py

# Pinned (requirements/companion-constraints.txt), so builds are reproducible.
companion-venv:
	$(COMPANION_BOOTSTRAP) -m venv .venv-companion
	"$(COMPANION_VENV_PY)" -m pip install -r requirements/companion-dev.txt -c requirements/companion-constraints.txt

companion-test:
	$(companion_py) -m pytest -q $(COMPANION_TESTS)

companion-lint:
	$(companion_py) -m ruff check $(COMPANION_SOURCES)
	$(companion_py) -m ruff format --check $(COMPANION_SOURCES)
	$(companion_py) -m mypy $(COMPANION_SOURCES)

# Flags go through ARGS, e.g. make run-tray ARGS="--demo".
run-tray:
	$(companion_py) -m babel.messages.frontend compile -d app/i18n/locales -D voicemate
	$(companion_py) -m app.companion.main $(ARGS)

# dist/VoiceMate/VoiceMate.exe (PyInstaller onedir, packaging/windows/voicemate-companion.spec).
companion-build:
	$(windows_only)$(companion_py) -m PyInstaller --noconfirm --clean --distpath dist --workpath build/companion packaging/windows/voicemate-companion.spec

# dist/installer/VoiceMate-Setup-<version>.exe. Needs Inno Setup 6.3+ (ISCC.exe); when it
# is missing this explains how to get it (winget install JRSoftware.InnoSetup).
# A custom location: make companion-installer ISCC="C:/path/to/ISCC.exe".
companion-installer: companion-build
	$(companion_py) -m tools.build_installer $(if $(ISCC),--iscc "$(ISCC)")

# ─── Release (docs/releasing.md) ─────────────────────────────────────────────
# Before tagging: the pyproject version is SemVer, CHANGELOG.md has its dated section,
# docs/releases/vX.Y.Z.md and its five translations exist, the companion agrees.
# Standard library only, so no environment is needed. Check a tag too:
#   make release-check TAG=v0.1.0
ifeq ($(OS),Windows_NT)
RELEASE_PY ?= py -3
else
RELEASE_PY ?= python3
endif

release-check:
	$(RELEASE_PY) -m tools.release_check $(if $(TAG),--tag $(TAG))

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +

# Nota Windows: targets com rm/find requerem Git Bash ou WSL.
# No PowerShell use: Remove-Item -Recurse -Force .pytest_cache, .ruff_cache, .mypy_cache
