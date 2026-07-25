from unittest.mock import AsyncMock, patch

import pytest

from app.workers.celery_app import celery_app
from app.workers.processar_mensagem import processar_mensagem_recebida

_ARGS = (
    "11111111-1111-1111-1111-111111111111",
    "5511999997777",
    "oi",
    "MSG-1",
    "2026-07-25T10:00:00+00:00",
    False,
)


@pytest.fixture(autouse=True)
def _modo_eager() -> None:
    # task_eager_propagates=False (default): deixa o `.apply()` reexecutar a
    # task sincronamente a cada Retry, em vez de levantar na primeira
    # tentativa — só assim dá pra testar o loop de retry sem worker de verdade.
    celery_app.conf.task_always_eager = True
    yield
    celery_app.conf.task_always_eager = False


def test_tenta_de_novo_ate_dar_certo() -> None:
    execucao = AsyncMock(side_effect=[RuntimeError("uazapi indisponível"), None])
    with (
        patch("app.workers.processar_mensagem._processar_mensagem_recebida_async", execucao),
        patch("app.workers.processar_mensagem._BACKOFF_BASE_SEGUNDOS", 0),
    ):
        resultado = processar_mensagem_recebida.apply(args=_ARGS)
        resultado.get()

    assert execucao.await_count == 2


def test_desiste_apos_esgotar_tentativas() -> None:
    execucao = AsyncMock(side_effect=RuntimeError("uazapi indisponível"))
    with (
        patch("app.workers.processar_mensagem._processar_mensagem_recebida_async", execucao),
        patch.object(processar_mensagem_recebida, "max_retries", 2),
        patch("app.workers.processar_mensagem._BACKOFF_BASE_SEGUNDOS", 0),
        pytest.raises(RuntimeError, match="uazapi indisponível"),
    ):
        processar_mensagem_recebida.apply(args=_ARGS).get()

    assert execucao.await_count == 3
