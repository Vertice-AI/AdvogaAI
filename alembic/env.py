import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.db.models  # noqa: F401  registra as tabelas em Base.metadata
from alembic import context
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Migrations rodam com credencial administrativa (dono das tabelas, cria a
# role de aplicação e as policies de RLS) — deliberadamente separada da
# credencial de runtime em ADVOGAI_DATABASE_URL (app/core/config.py), que é
# uma role restrita e sujeita às policies. Ver alembic/versions/0001.
_url = os.environ.get(
    "ADVOGAI_DATABASE_URL_MIGRACAO",
    "postgresql+psycopg://postgres:postgres@localhost:5432/advogai",
)
config.set_main_option("sqlalchemy.url", _url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
