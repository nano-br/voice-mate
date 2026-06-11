.PHONY: all setup configure doctor setup_env setup_env_minimal setup_env_claude setup_env_tts setup_env_custom lock \
        format lint test stt-eval run run-large run-turbo run-vozes-aleatorias run-reset-voz \
        i18n-extract i18n-init-pt i18n-init-en i18n-update i18n-compile clean

all: format lint test

# ─── Setup recomendado ───────────────────────────────────────────────────────
# Detecta a GPU (NVIDIA/AMD/CPU), confirma com você, instala o torch certo
# (CUDA cu128 / ROCm / CPU) + os módulos escolhidos, e lembra a escolha em
# ~/.config/voicemate/config.toml. Funciona em PowerShell e Git Bash.
setup:
	poetry install
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

i18n-update:
	poetry run pybabel update -i app/i18n/locales/voicemate.pot -d app/i18n/locales -D voicemate

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

run:
	poetry run voice-mate $(ARGS)

run-large:
	poetry run voice-mate --model large-v3 $(ARGS)

run-turbo:
	poetry run voice-mate --model large-v3-turbo $(ARGS)

# Atalhos para os modos de voz mais comuns
run-vozes-aleatorias:
	poetry run voice-mate --tts-voice-seed-mode off $(ARGS)

run-reset-voz:
	poetry run voice-mate --tts-reset-seed $(ARGS)

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +

# Nota Windows: targets com rm/find requerem Git Bash ou WSL.
# No PowerShell use: Remove-Item -Recurse -Force .pytest_cache, .ruff_cache, .mypy_cache
