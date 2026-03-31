.PHONY: all setup_env format lint test run run-large run-turbo clean

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
	poetry run voice-mate

run-large:
	poetry run voice-mate --model large-v3

run-turbo:
	poetry run voice-mate --model large-v3-turbo

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +

# Nota Windows: targets com rm/find requerem Git Bash ou WSL.
# No PowerShell use: Remove-Item -Recurse -Force .pytest_cache, .ruff_cache, .mypy_cache
