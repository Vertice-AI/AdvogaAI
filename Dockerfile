# Imagem de produção do agente — Python 3.12 slim, usuário não-root.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instala dependências primeiro (aproveita cache de camada).
COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

# Migrations e provisionamento rodam de dentro da imagem em produção
# (comando pontual no easypanel), por isso alembic e scripts vão junto.
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

# Não rode como root.
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
