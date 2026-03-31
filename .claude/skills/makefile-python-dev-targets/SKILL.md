---
name: makefile-python-dev-targets
description: Cria ou atualiza Makefile com targets padrão para projetos Python com Poetry (setup_env, format, lint, test, run, clean). Use sempre que o usuário mencionar Makefile, quiser padronizar comandos de desenvolvimento, configurar quality gates, automatizar o workflow local, ou perguntar sobre make, dev workflow, ou CI para projetos Python com Poetry.
---

# Makefile targets (Poetry + Ruff + Mypy)

## Contrato de targets

| Target      | Propósito |
|------------|-----------|
| `setup_env` | Instala dependências do toolchain (ex: `poetry install`). |
| `format`    | **Modifica** arquivos: `ruff format` + `ruff check --fix`. |
| `lint`      | Verificações **somente leitura**: `ruff check` + `mypy` (sem fix). |
| `test`      | `pytest` (ou o test runner do projeto). |
| `run`       | Inicia o servidor de desenvolvimento (ex: uvicorn com reload). |
| `clean`     | Remove caches: `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `__pycache__`. |
| `all`       | Opcional: `format` → `lint` → `test` (ordem importa: format antes de lint). |

Execute todos os comandos via **`poetry run`** para garantir uso do venv correto.

## Exemplo de Makefile

```makefile
.PHONY: all setup_env lint format test clean run

all: format lint test

setup_env:
	poetry install

format:
	poetry run ruff format .
	poetry run ruff check --fix .

lint:
	poetry run ruff check .
	poetry run mypy .

test:
	poetry run pytest -v

run:
	poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 3000

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
```

Ajuste o caminho do módulo e a porta conforme o projeto.

## Instruções para o Claude

Ao criar ou atualizar um Makefile:

1. **Pergunte** qual o runner do servidor (`uvicorn`, `gunicorn`, `flask run`, etc.) e a porta padrão, se não informado.
2. **Sempre use** `poetry run` em todos os targets para garantir isolamento de ambiente.
3. **Mantenha a ordem**: `format` antes de `lint` no target `all` — o formatter pode gerar código que o linter precisaria apontar.
4. **Oriente sobre CI**: recomende rodar apenas `lint` + `test` em pull requests (sem `format` em CI, a menos que seja para checar drift). Sinalize se o projeto não tiver nenhuma quality gate antes do deploy.

## Nota Windows

Targets com `rm`, `find` ou paths Unix podem falhar no PowerShell. Alternativas: Git Bash, WSL, ou duplicar `clean` com `pwsh`/`Remove-Item` em um `Makefile.windows` ou seção do README.
