---
name: python-strict-mypy-pydantic
description: Configura tipagem estática estrita com Mypy para serviços Python, incluindo integração com Pydantic v2 e estratégia de stubs. Use sempre que o usuário quiser configurar ou apertar tipagem em Python, usar Mypy, integrar Mypy com Pydantic ou FastAPI, configurar pyproject.toml com type checking, ou quando mencionar mypy, tipagem estrita, type hints, type checking, anotações de tipo, ou erros de tipo.
---

# Python strict typing (Mypy + Pydantic)

## Objetivos

- Toda função e método **público** tem tipos explícitos (parâmetros e retorno).
- Evitar `Any` solto em genéricos e posições de retorno onde um tipo concreto é pretendido.
- **Mypy é a fonte da verdade** em CI/scripts locais; checkers de IDE são opcionais e não devem contradizer a config do Mypy.

## Flags Mypy baseline (projeto inteiro)

Configure em `[tool.mypy]` (ajuste `python_version` para o runtime):

```toml
[tool.mypy]
python_version = "3.12"
plugins = ["pydantic.mypy"]

disallow_untyped_defs = true
disallow_any_generics = true
no_implicit_optional = true
check_untyped_defs = true
warn_return_any = true
ignore_missing_imports = true
```

| Flag | O que faz |
|------|-----------|
| `disallow_untyped_defs` | Defs sem tipo são erros |
| `disallow_any_generics` | `list`/`dict` sem type args são erros; use `list[str]`, etc. |
| `no_implicit_optional` | `def f(x: int = None)` deve ser `Optional[int]` ou `int \| None` |
| `check_untyped_defs` | Ainda verifica o corpo quando inferência se aplica |
| `warn_return_any` | Retornar `Any` onde um tipo específico foi prometido gera warning |

**Com Pydantic v2**, adicione: `plugins = ["pydantic.mypy"]`

Sem Pydantic, omita `plugins`.

## Bibliotecas de terceiros sem tipos

- Use `ignore_missing_imports = true` como **padrão** para pacotes desconhecidos.
- **Afine** com `[[tool.mypy.overrides]]` para pacotes que você controla ou que têm stubs oficiais.
- Para SDKs grandes sem tipos, adicione **pacotes de stubs opcionais de dev** (ex: `types-*` ou stubs da comunidade) quando existirem — não pin versões.

```toml
# Exemplo: apertar um pacote específico uma vez que stubs existam
[[tool.mypy.overrides]]
module = "some_sdk.*"
ignore_missing_imports = false
```

## Escapes por módulo (usar com parcimônia)

Documente o motivo ao usar:

- `# type: ignore[<código>]` com o **menor** código possível (ex: `misc`, `import-untyped`).
- `[[tool.mypy.overrides]]` para módulos inteiros que são legados ou gerados.

## Overlap com Ruff ANN

Ruff `ANN` dá **feedback rápido** sobre anotações faltando; Mypy permanece autoritativo para **correção** dos tipos. Se Ruff e Mypy discordarem, prefira corrigir o código para satisfazer o Mypy.

## Instruções para o Claude

Ao configurar Mypy em um projeto:

1. **Adicione** o bloco `[tool.mypy]` ao `pyproject.toml` — não crie um `mypy.ini` separado, a menos que o usuário peça.
2. **Detecte** se o projeto usa Pydantic v2 (procure `pydantic>=2` em dependências) e adicione `plugins = ["pydantic.mypy"]` apenas se for o caso.
3. **Não force** todos os flags de uma vez em projetos legados com muitos erros existentes — sugira introduzir `disallow_untyped_defs = true` primeiro e os demais gradualmente.
4. **Oriente** que `mypy .` deve ser rodado após `ruff check .`, nunca antes do formatter.
5. Se houver erros existentes, liste-os brevemente e pergunte se o usuário quer corrigi-los agora ou adotar via `overrides` temporariamente.
