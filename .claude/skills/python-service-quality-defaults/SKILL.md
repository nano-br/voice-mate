---
name: python-service-quality-defaults
description: Aplica um bootstrap completo e consistente para serviços Python — Poetry, Ruff, Mypy estrito, Makefile e configurações VS Code/Cursor — na ordem recomendada. Use sempre que o usuário quiser criar um novo serviço ou API Python do zero, aplicar uma barra de qualidade padrão a um projeto existente, ou quando mencionar "novo projeto Python", "setup completo", "quality bar", "stack Python padrão", ou quiser replicar um stack de referência sem especificar cada ferramenta individualmente.
---

# Python service quality defaults (meta-skill)

Esta é uma **meta-skill** que orquestra as outras skills de qualidade Python. Ao ser acionada, aplique-as **nesta ordem**:

## Ordem de execução

### 1. `python-poetry-layout-bootstrap`
Configure `pyproject.toml`, declare o pacote (`app` ou `src/`), crie o grupo dev para ferramentas, alinhe `.python-version` e documente comandos no README.

### 2. `python-ruff-quality-bar`
Adicione `[tool.ruff]` com lint + format; integre ao workflow diário.

### 3. `python-strict-mypy-pydantic`
Configure `[tool.mypy]` com baseline estrito; adicione `pydantic.mypy` plugin quando usar Pydantic v2.

### 4. `makefile-python-dev-targets`
Crie os targets `format` / `lint` / `test` / `run` / `clean` via `poetry run`.

### 5. `vscode-python-ruff-mypy-workspace`
Gere `.vscode/settings.json` + `extensions.json` com as recomendações corretas.

## Checklist mínimo

- [ ] `poetry install` funciona; módulo da aplicação é importável.
- [ ] `make format` e depois `make lint` passam localmente (ou scripts equivalentes).
- [ ] CI roda **lint + testes** antes do deploy (recomendado).
- [ ] Versão Python consistente entre Poetry, `.python-version` e família da imagem Docker.

## Instruções para o Claude

Ao usar esta skill:

1. **Leia e aplique** cada uma das 5 skills acima em sequência. Não pule etapas.
2. **Confirme** com o usuário o nome do pacote principal e a versão Python antes de começar.
3. **Não** hardcode versões de dependências de ferramentas (Ruff, Mypy, pytest) — deixe o Poetry resolver.
4. **Adapte** a estrutura de pastas (`api/`, `core/`, `schemas/`, `services/`) ao domínio do projeto.
5. **Ao final**, mostre um resumo do que foi criado/alterado e aponte quais comandos o usuário pode rodar agora (`poetry install`, `make format`, `make lint`, `make test`).

## Restrições

- Não embuta pins de versão de dependências nas instruções a menos que o usuário forneça.
- O layout de pastas é **orientação**, não cópia rígida — adapte ao domínio.
