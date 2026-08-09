from unittest.mock import AsyncMock, patch

import pytest

from app.channels.base import InboundMessage, MessageId
from app.workers.celery_app import celery_app
from app.workers.processar_mensagem import (
    _processar_mensagem_recebida_async,
    processar_mensagem_recebida,
)

_ARGS = (
    "11111111-1111-1111-1111-111111111111",
    "5511999997777",
    "oi",
    "MSG-1",
    "2026-07-25T10:00:00+00:00",
    False,
)


class _ChannelFake:
    """Só rastreia se aclose() foi chamado — os demais métodos não são
    exercitados porque processar_mensagem (o service) é mockado nos testes
    que usam este fake."""

    def __init__(self) -> None:
        self.fechado = False

    async def send_text(self, to: str, text: str) -> MessageId:
        raise NotImplementedError

    async def send_template(self, to: str, template: str, params: dict[str, str]) -> MessageId:
        raise NotImplementedError

    def parse_webhook(self, payload: dict[str, object]) -> InboundMessage:
        raise NotImplementedError

    def verify_signature(self, payload: bytes, headers: dict[str, str]) -> bool:
        raise NotImplementedError

    async def aclose(self) -> None:
        self.fechado = True


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


def test_alerta_advogado_quando_desiste_de_vez() -> None:
    # Sem isso, uma mensagem de cliente que esgota as tentativas fica sem
    # resposta e ninguém no escritório fica sabendo (só existia log) — achado
    # em produção em 2026-08-08/09, ReadTimeout persistente vindo da UAZAPI.
    texto_do_cliente = "relato-confidencial-do-cliente"
    args = (*_ARGS[:2], texto_do_cliente, *_ARGS[3:])
    execucao = AsyncMock(side_effect=RuntimeError("uazapi indisponível"))
    alerta = AsyncMock()
    with (
        patch("app.workers.processar_mensagem._processar_mensagem_recebida_async", execucao),
        patch.object(processar_mensagem_recebida, "max_retries", 0),
        patch("app.workers.processar_mensagem._BACKOFF_BASE_SEGUNDOS", 0),
        patch("app.workers.processar_mensagem.enviar_alerta", alerta),
        pytest.raises(RuntimeError, match="uazapi indisponível"),
    ):
        processar_mensagem_recebida.apply(args=args).get()

    assert alerta.await_count == 1
    texto, _webhook_url = alerta.await_args.args
    assert "5511999997777" in texto
    assert texto_do_cliente not in texto


async def test_fecha_o_canal_mesmo_quando_processamento_falha() -> None:
    # Regressão: UazapiProvider mantém um httpx.AsyncClient interno; sem
    # aclose() no finally, cada task vaza uma conexão — inofensivo num
    # processo curto, mas um worker de vida longa acumula até travar novas
    # chamadas (foi exatamente o bug do ReadTimeout intermitente em produção,
    # 2026-08-06). O teste cobre o caminho de falha porque é o mais fácil de
    # esquecer o `finally`.
    channel_fake = _ChannelFake()
    with (
        patch(
            "app.workers.processar_mensagem.get_channel_provider",
            return_value=channel_fake,
        ),
        patch(
            "app.workers.processar_mensagem.processar_mensagem",
            AsyncMock(side_effect=RuntimeError("falhou")),
        ),
        pytest.raises(RuntimeError, match="falhou"),
    ):
        await _processar_mensagem_recebida_async(*_ARGS)

    assert channel_fake.fechado is True
