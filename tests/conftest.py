import os
import subprocess
from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy import Engine, create_engine

_ADMIN_URL = os.environ.get(
    "ADVOGAI_DATABASE_URL_ADMIN", "postgresql://postgres:postgres@localhost:5432/postgres"
)
_APP_DB_PASSWORD = os.environ.get("ADVOGAI_APP_DB_PASSWORD", "advogai_app")
_TEST_DB = "advogai_test"
_APP_TEST_URL = f"postgresql+psycopg://advogai_app:{_APP_DB_PASSWORD}@localhost:5432/{_TEST_DB}"


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    _recriar_banco_de_teste()
    _rodar_migrations()
    engine = create_engine(_APP_TEST_URL)
    yield engine
    engine.dispose()


def _recriar_banco_de_teste() -> None:
    with psycopg.connect(_ADMIN_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {_TEST_DB} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {_TEST_DB}")


def _rodar_migrations() -> None:
    url_admin_no_banco_de_teste = _ADMIN_URL.rsplit("/", 1)[0] + f"/{_TEST_DB}"
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True,
        env={
            **os.environ,
            "ADVOGAI_DATABASE_URL_MIGRACAO": url_admin_no_banco_de_teste.replace(
                "postgresql://", "postgresql+psycopg://"
            ),
            "ADVOGAI_APP_DB_PASSWORD": _APP_DB_PASSWORD,
        },
    )
