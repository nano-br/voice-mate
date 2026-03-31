---
name: vscode-python-ruff-mypy-workspace
description: Configura settings de workspace VS Code/Cursor para Python com Ruff como formatter padrão, save actions, hints de ambiente Poetry e extensão Mypy opcional. Use sempre que o usuário quiser configurar o VS Code ou Cursor para Python, configurar format on save, configurar .vscode/settings.json ou extensions.json, ou quando mencionar Ruff no editor, Pylance, Mypy no IDE, configurações de workspace, ou onboarding de novos devs no editor.
---

# VS Code / Cursor: Python + Ruff + Mypy

## Extensões (recomendar, não pin versões)

Versione `.vscode/extensions.json` com `recommendations` apenas (sem campos de versão):

```json
{
  "recommendations": [
    "ms-python.python",
    "charliermarsh.ruff",
    "ms-python.mypy-type-checker"
  ]
}
```

`ms-python.mypy-type-checker` é opcional — adicione apenas se o time usar Mypy no editor.

## `.vscode/settings.json` baseline

```json
{
  "flake8.enabled": false,
  "python.analysis.typeCheckingMode": "off",
  "mypy-type-checker.importStrategy": "fromEnvironment",
  "editor.formatOnSave": true,
  "editor.codeActionsOnSave": {
    "source.fixAll": "explicit",
    "source.organizeImports": "explicit"
  },
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.codeActionsOnSave": {
      "source.fixAll": "explicit",
      "source.organizeImports": "explicit"
    }
  },
  "ruff.enable": true,
  "ruff.organizeImports": true,
  "files.exclude": {
    "**/__pycache__": true,
    "**/*.pyc": true,
    ".pytest_cache": true
  },
  "python-envs.defaultEnvManager": "ms-python.python:poetry",
  "python-envs.defaultPackageManager": "ms-python.python:poetry"
}
```

Remova as chaves Poetry se usar uv/pip. Ajuste `typeCheckingMode` se o time usar Pylance como checker primário.

## Formatter e save actions

- `[python].editor.defaultFormatter` → Ruff (`charliermarsh.ruff`).
- `editor.formatOnSave` → true.
- `editor.codeActionsOnSave` com `source.fixAll` e `source.organizeImports` em `"explicit"` para que Ruff corrija e ordene imports ao salvar.
- `ruff.enable` e `ruff.organizeImports` → true.

## Conflitos

Desative **Flake8** no workspace se Ruff for o linter (`flake8.enabled: false`) para evitar diagnósticos duplicados.

## Type checking: Pylance vs Mypy

Duas estratégias válidas — escolha uma por repo e documente no README:

**Estratégia 1 — Mypy como fonte da verdade (CLI + extensão opcional)**
- `python.analysis.typeCheckingMode`: `"off"` (evita conflito entre dois inference engines).
- Opcionalmente: `mypy-type-checker.importStrategy`: `"fromEnvironment"` para usar o venv do projeto.

**Estratégia 2 — Pylance / Pyright como checker primário**
- Ative basic ou standard type checking no IDE e alinhe `pyrightconfig` / Pylance com as regras do time.
- Continue rodando **Mypy no CI** se o time padronizou nele, ou standardize em um único checker.

## Poetry no editor

Se usar Poetry, configure hints de ambiente:

```json
"python-envs.defaultEnvManager": "ms-python.python:poetry",
"python-envs.defaultPackageManager": "ms-python.python:poetry"
```

(Chaves exatas podem variar por versão da extensão; prefira a integração Poetry atual da extensão Python.)

## Qualidade de vida

- `files.exclude`: oculte `__pycache__`, `*.pyc`, `.pytest_cache`.
- `python.analysis.diagnosticSeverityOverrides`: ex: rebaixe `reportUnusedImport` para `information` se Ruff já sinaliza.
- **cSpell** `cSpell.words`: adicione termos do domínio para reduzir ruído em prose e strings.

## Instruções para o Claude

Ao criar ou atualizar `.vscode/settings.json`:

1. **Crie ambos** `settings.json` e `extensions.json` quando estiver configurando do zero.
2. **Desative** Flake8 explicitamente se encontrar `flake8.enabled: true` existente.
3. **Pergunte** qual estratégia de type checking o usuário prefere (Mypy ou Pylance) se não estiver claro no projeto.
4. **Remova** as chaves Poetry se o projeto usar `uv` ou `pip` como package manager.
5. **Não pin** versões de extensões em `extensions.json` — apenas recomende os IDs.
