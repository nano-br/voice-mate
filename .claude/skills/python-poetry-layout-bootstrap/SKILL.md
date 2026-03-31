---
name: python-poetry-layout-bootstrap
description: Cria ou migra projetos Python para Poetry com estrutura de pastas clara (src/ ou app/), alinhamento de versão Python e padrões Docker multi-stage opcionais. Use sempre que o usuário quiser criar um novo serviço Python, migrar para Poetry, configurar pyproject.toml, criar um novo projeto de API/microsserviço, ou quando mencionar Poetry, pyproject.toml, estrutura de projeto Python, ou layout de pacote.
---

# Poetry layout bootstrap

## Princípios

- **Manifesto único**: `pyproject.toml` define o pacote da aplicação, dependências e configuração de ferramentas (Ruff, Mypy, pytest).
- **Um pacote de aplicação** com nome explícito (geralmente `app` ou `src/<nome>`):

```toml
[tool.poetry]
packages = [{ include = "app" }]
```

- **Dev dependencies**: ferramentas de qualidade (Ruff, Mypy, pytest, stub packages) ficam em `[tool.poetry.group.dev.dependencies]`.

## Alinhamento de versão Python

Mantenha estes três **em sincronia** (mesmo major.minor):

1. `python = "^3.12"` (ou range apropriado) em `[tool.poetry.dependencies]`.
2. **`.python-version`** na raiz do repo para pyenv, asdf ou instaladores de CI.
3. **Docker** `FROM python:3.12-slim` (ou família de tag correspondente) para stages de runtime/build.

Não hardcode versões patch específicas a menos que o usuário peça.

## Estrutura de pastas sugerida (API / serviço)

Template flexível — adapte os nomes ao domínio:

```
app/
├── main.py          # entry point (ex: ASGI app factory)
├── api/             # routers por versão ou área (v1, v2, system)
├── core/            # config, logging, segurança, middleware compartilhado
├── schemas/         # models Pydantic / DTOs de request-response
└── services/        # lógica de negócio e integrações
```

Alternativa: `src/<nome_do_pacote>/` com a mesma divisão interna.

## pyproject.toml de exemplo

```toml
[tool.poetry]
name = "meu-servico"
version = "0.1.0"
description = ""
packages = [{ include = "app" }]

[tool.poetry.dependencies]
python = "^3.12"

[tool.poetry.group.dev.dependencies]
ruff = "*"
mypy = "*"
pytest = "*"
pytest-asyncio = "*"
```

## Docker (opcional)

Padrão multi-stage: o stage builder roda `poetry export` para um `requirements.txt`, e o stage runtime faz apenas `pip install` desse arquivo. Mantém imagens menores e evita Poetry em produção.

```dockerfile
# Builder
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install poetry
COPY pyproject.toml poetry.lock ./
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes

# Runtime
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /build/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## README mínimo

Documente: instalação (`poetry install`), como rodar a aplicação e os comandos de qualidade (`make lint` / `make format` / `make test` se usar a skill de Makefile).

## Instruções para o Claude

Ao criar ou migrar um projeto para Poetry:

1. **Confirme** o nome do pacote principal (`app`, `src/<nome>`, etc.) com o usuário se não estiver óbvio.
2. **Gere o `.python-version`** junto com o `pyproject.toml`, alinhando a versão.
3. **Não pin** versões específicas de ferramentas (Ruff, Mypy, pytest) a menos que o usuário peça — deixe o Poetry resolver.
4. **Ofereça** a adição do Docker multi-stage e do Makefile (usando as skills correspondentes) se o contexto for um serviço ou API.
5. **Crie o README** com comandos básicos de setup e execução.
