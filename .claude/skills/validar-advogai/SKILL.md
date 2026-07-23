---
name: validar-advogai
description: Use ao terminar de escrever ou editar código Python no AdvogAI, antes de reportar a tarefa como concluída — especialmente quando a mudança toca app/db/, alembic/, ou qualquer teste que precise de Postgres real (RLS, persistência). Sobe o Postgres local via docker-compose, cria um venv efêmero via uv (o Mac só tem Python de sistema mais velho que o exigido), roda as migrations Alembic, pytest, ruff e mypy --strict, e limpa o venv/caches no final sem derrubar o Postgres. Gatilhos — "valida o AdvogAI", "roda os testes do AdvogAI", "confirma que passa", "roda a suíte inteira", "roda migration e testa", ou uma instrução de projeto do tipo "não me diga que terminou se não passou" (CLAUDE.md §8).
---

# Validar o AdvogAI (Postgres + venv efêmero + testes/lint/types)

Ritual completo para este repositório: além do Python mais novo que o do
sistema, o AdvogAI precisa de Postgres real rodando (RLS não dá pra testar
com mock/SQLite — ver `app/db/rls.py` e `alembic/versions/0001_*.py`).

## Passo 1 — Subir o Postgres do projeto

```bash
eval "$(/usr/local/bin/brew shellenv)"   # garante brew/colima/docker no PATH
cd <raiz do AdvogAI>
docker compose up -d
docker compose ps   # confirma "Up" antes de seguir
```

Colima já está com autostart (`brew services start colima`), então
normalmente já está no ar — só rodar `docker compose up -d` mesmo assim
(idempotente, não recria se já existir). Ver memória `advogai-docker-local`
se `brew`/`docker` não forem encontrados.

## Passo 2 — Venv efêmero com a versão certa de Python

```bash
UV_BIN=$(command -v uv || echo ~/Library/Python/3.9/bin/uv)
cd <raiz do AdvogAI>
rm -rf .venv   # se sobrou de uma sessão anterior, `uv venv` recusa recriar
$UV_BIN venv --python 3.12 .venv
source .venv/bin/activate
$UV_BIN pip install -q anthropic pydantic pydantic-settings structlog httpx \
  sqlalchemy "psycopg[binary]" alembic pytest pytest-asyncio mypy ruff
```

Confira o `pyproject.toml` antes — se `[project.dependencies]` ou o grupo
`dev` tiverem mudado desde a última vez (novo provider, Celery, FastAPI
etc.), ajuste a lista do `pip install` de acordo. Não use `pip install -e
".[dev]"` (mesmo motivo do `validar-python`: quebra pelo Python de sistema
antigo antes mesmo de usar o venv certo). `pythonpath = ["."]` no
`pyproject.toml` já resolve o import de `app` sem precisar de install
editável.

## Passo 3 — Aplicar migrations no banco de dev

```bash
export ADVOGAI_DATABASE_URL_MIGRACAO="postgresql+psycopg://postgres:postgres@localhost:5432/advogai"
alembic upgrade head
```

Isso usa a credencial admin (`postgres`), separada da credencial de runtime
restrita (`advogai_app`) que o app e os testes usam — ver comentário em
`alembic/env.py`. Os testes (`tests/conftest.py`) recriam seu **próprio**
banco `advogai_test` do zero a cada execução via `alembic upgrade head`
também, então esse passo é só para o banco de dev ficar coerente com o
schema mais recente, não é pré-requisito pro pytest passar.

Se mudou algo em `alembic/versions/`, rode `alembic downgrade base &&
alembic upgrade head` para confirmar que a migration é reversível de
verdade, não só que aplica.

## Passo 4 — Testes, lint, types

```bash
python -m pytest -q
python -m ruff check .
python -m mypy app alembic
```

Reporte falhas com o output relevante. Só diga que terminou depois que os
três passarem — CLAUDE.md §8 é explícito sobre isso.

### Formatação (`ruff format`)

Nem todo arquivo do repo está `ruff format`-limpo (alguns arquivos
pré-existentes não são reformatados por padrão, pra não gerar diff sem
relação com a tarefa). Rode `ruff format --check .` e reformate **só os
arquivos que você criou ou editou nesta tarefa**, não o repositório inteiro:

```bash
python -m ruff format --check .
python -m ruff format <arquivos que você tocou>
```

## Passo 5 — Limpar o venv (mas não o Postgres)

```bash
cd <raiz do AdvogAI>
rm -rf .venv .mypy_cache .pytest_cache .ruff_cache ./*.egg-info
find . -name "__pycache__" -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null
git status   # confirma que só sobraram os arquivos do entregável real
```

Diferente do `validar-python` genérico: **não** derrube o `docker compose`
no final. O Postgres é infraestrutura persistente do projeto (como um banco
de dev normal), não um artefato efêmero da validação — deixe rodando para a
próxima sessão/tarefa.
