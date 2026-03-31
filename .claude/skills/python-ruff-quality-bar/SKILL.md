---
name: python-ruff-quality-bar
description: Configura Ruff como linter e formatter único para Python (substitui Black, Isort e Flake8), incluindo organização de imports e nudges de anotação de tipos. Use sempre que o usuário quiser configurar lint ou formatação em Python, substituir Black/Isort/Flake8, configurar pyproject.toml com Ruff, perguntar sobre organização de imports, ou quando mencionar ruff, lint, format, qualidade de código Python.
---

# Ruff quality bar

## Posição no stack

- **Uma ferramenta**: Ruff substitui Black, Isort e Flake8 para uso diário.
- Adicione **Mypy** separadamente para correção estática de tipos (ver skill `python-strict-mypy-pydantic`).

## Famílias de regras recomendadas

Em `[tool.ruff.lint]`, um conjunto padrão forte:

| Família | O que cobre |
|---------|-------------|
| `E`, `F` | pycodestyle + pyflakes (correção e estilo baseline) |
| `I`      | ordenação de imports (compatível com isort) |
| `UP`     | pyupgrade (sintaxe moderna) |
| `ANN`    | nudges para anotações de tipo ausentes (Mypy ainda decide se são válidas) |

- `line-length` definido explicitamente (ex: 120).
- `ignore = ["E501"]` frequentemente usado para que linhas longas sejam tratadas pelo formatter, não pelo pycodestyle.
- `target-version` alinhado ao Python mínimo suportado pelo projeto.

## Configuração de exemplo

```toml
[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "ANN"]
ignore = ["E501"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

Ajuste `target-version` e `line-length` por projeto.

## Comandos principais

```bash
ruff format .          # aplica formatação
ruff check .           # lint apenas (sem escrita)
ruff check --fix .     # auto-fixes seguros incluindo ordenação de imports
```

## Workflow diário

1. `ruff format .`
2. `ruff check --fix .`
3. `ruff check .` (deve estar limpo)
4. `mypy .` (da skill separada / Makefile)

## ANN vs Mypy

`ANN` captura omissões óbvias cedo; a **correção e validação** dos tipos é responsabilidade do Mypy. Se uma regra ANN for muito ruidosa para um padrão aceito pelo time, prefira um `ignore` direcionado em `[tool.ruff.lint]` em vez de desabilitar ANN completamente.

## Instruções para o Claude

Ao configurar Ruff em um projeto:

1. **Adicione** o bloco `[tool.ruff]` ao `pyproject.toml` existente — não crie um arquivo `ruff.toml` separado a menos que o usuário peça.
2. **Alinhe** `target-version` com a versão Python em `[tool.poetry.dependencies]` ou `[tool.python]`.
3. **Remova** configurações de Black, Isort e Flake8 se existirem (seções `[tool.black]`, `[tool.isort]`, `[tool.flake8]`) para evitar conflitos.
4. **Oriente** que o `format` deve rodar antes do `lint` (o formatter pode introduzir estilo que o linter verificaria).
5. Se o projeto já usa **Mypy**, lembre que `ANN` e Mypy são complementares — não substitutos.
