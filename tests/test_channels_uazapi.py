import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Self

import httpx
import pytest

from app.channels import uazapi as uazapi_module
from app.channels.uazapi import UazapiProvider


def _client_com_handler(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> UazapiProvider:
    return UazapiProvider(
        base_url="https://vrtice.uazapi.com",
        token="TOKEN_TESTE",
        webhook_secret="SEGREDO_TESTE",
        client=_client_com_handler(handler),
    )


class _FakeRedisLock:
    def __init__(self) -> None:
        self.entrou = False
        self.saiu = False

    async def __aenter__(self) -> Self:
        self.entrou = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.saiu = True


class _FakeRedis:
    def __init__(self) -> None:
        self.lock_chamado_com: tuple[str, int, float] | None = None
        self.lock_obj = _FakeRedisLock()

    def lock(self, name: str, timeout: int, blocking_timeout: float) -> _FakeRedisLock:
        self.lock_chamado_com = (name, timeout, blocking_timeout)
        return self.lock_obj

    async def aclose(self) -> None:
        pass


async def test_send_text_envia_corpo_e_headers_corretos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://vrtice.uazapi.com/send/text"
        assert request.headers["token"] == "TOKEN_TESTE"
        corpo = json.loads(request.content)
        assert corpo == {"number": "5511999998888", "text": "Olá!", "delay": 3000}
        return httpx.Response(200, json={"id": "MSG123"})

    message_id = await _provider(handler).send_text("5511999998888", "Olá!")

    assert message_id == "MSG123"


async def test_send_text_usa_messageid_quando_nao_ha_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messageid": "MSG456"})

    message_id = await _provider(handler).send_text("5511999998888", "Olá!")

    assert message_id == "MSG456"


async def test_send_text_sem_id_na_resposta_levanta_erro() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    with pytest.raises(ValueError, match="sem id de mensagem"):
        await _provider(handler).send_text("5511999998888", "Olá!")


async def test_send_template_renderiza_placeholders_localmente() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        assert corpo["text"] == "Olá João, seu processo teve uma atualização."
        return httpx.Response(200, json={"id": "MSG789"})

    message_id = await _provider(handler).send_template(
        "5511999998888",
        "Olá {nome}, seu processo teve uma atualização.",
        {"nome": "João"},
    )

    assert message_id == "MSG789"


async def test_rate_limit_espaca_envios_consecutivos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uazapi_module, "_INTERVALO_MINIMO_SEGUNDOS", 0.1)
    monkeypatch.setattr(uazapi_module, "_JITTER_MAXIMO_SEGUNDOS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "MSG123"})

    provider = _provider(handler)
    loop = asyncio.get_running_loop()

    inicio = loop.time()
    await provider.send_text("5511999998888", "primeira")
    await provider.send_text("5511888887777", "segunda")
    duracao = loop.time() - inicio

    assert duracao >= 0.1


async def test_send_text_usa_lock_distribuido_quando_redis_configurado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regressão: sem lock distribuído, duas tasks concorrentes (worker com
    # concurrency>1) enviam ao mesmo tempo sem espaçamento nenhum — foi a
    # causa raiz do ReadTimeout intermitente em produção (2026-08-07). Cada
    # UazapiProvider é uma instância nova por task, então o lock precisa vir
    # de um recurso compartilhado (Redis), não de um asyncio.Lock local.
    fake_redis = _FakeRedis()
    monkeypatch.setattr(uazapi_module.aioredis, "from_url", lambda *a, **kw: fake_redis)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "MSG123"})

    provider = UazapiProvider(
        base_url="https://vrtice.uazapi.com",
        token="TOKEN_TESTE",
        webhook_secret="SEGREDO_TESTE",
        client=_client_com_handler(handler),
        redis_url="redis://fake:6379",
    )

    await provider.send_text("5511999998888", "oi")

    assert fake_redis.lock_chamado_com is not None
    assert fake_redis.lock_chamado_com[0] == "advogai:uazapi:envio_lock"
    assert fake_redis.lock_obj.entrou is True
    assert fake_redis.lock_obj.saiu is True


def test_lock_ttl_maior_que_pior_caso_do_envio() -> None:
    # Regressão: TTL de 20s (primeira versão do lock) era menor que o pior
    # caso de _chamar_com_retry (3 tentativas x 10s de timeout + ~1.5s de
    # backoff entre elas ≈ 31.5s, mais até 5s de espera do rate limit ≈
    # 36.5s) — o lock expirava sozinho com o envio ainda em andamento,
    # causando LockNotOwnedError na hora de liberar (bug real em produção,
    # 2026-08-07). Se alguém mexer nesses números de novo sem ajustar o TTL
    # junto, este teste quebra antes de virar bug em produção.
    pior_caso_estimado_segundos = 37.0
    assert uazapi_module._LOCK_TTL_SEGUNDOS > pior_caso_estimado_segundos


async def test_parse_webhook_mensagem_valida() -> None:
    # Schema real da UAZAPI (confirmado em produção, diverge da doc oficial):
    # evento em "EventType", corpo da mensagem em "message" — não "event"/"data".
    payload = {
        "EventType": "messages",
        "instanceName": "inst-123",
        "message": {
            "sender": "5511999998888@s.whatsapp.net",
            "chatid": "5511999998888@s.whatsapp.net",
            "text": "Como está meu processo?",
            "messageid": "3EB0ABC123",
            "messageTimestamp": 1752192000000,
            "fromMe": False,
        },
    }

    inbound = _provider(lambda r: httpx.Response(200)).parse_webhook(payload)

    assert inbound.from_number == "5511999998888"
    assert inbound.text == "Como está meu processo?"
    assert inbound.from_me is False
    assert inbound.message_id == "3EB0ABC123"
    assert inbound.timestamp == datetime.fromtimestamp(1752192000000 / 1000, tz=UTC)


async def test_parse_webhook_mensagem_do_proprio_numero_marca_from_me() -> None:
    payload = {
        "EventType": "messages",
        "instanceName": "inst-123",
        "message": {
            "sender": "5511999998888@s.whatsapp.net",
            "chatid": "5511999998888@s.whatsapp.net",
            "text": "/ia",
            "messageid": "3EB0DEF456",
            "messageTimestamp": 1752192000000,
            "fromMe": True,
        },
    }

    inbound = _provider(lambda r: httpx.Response(200)).parse_webhook(payload)

    assert inbound.from_me is True


async def test_parse_webhook_evento_diferente_de_messages_levanta_erro() -> None:
    payload = {
        "EventType": "connection",
        "instanceName": "inst-123",
        "message": {"state": "connected"},
    }

    with pytest.raises(ValueError, match="'messages'"):
        _provider(lambda r: httpx.Response(200)).parse_webhook(payload)


async def test_parse_webhook_sem_timestamp_levanta_erro() -> None:
    payload = {
        "EventType": "messages",
        "instanceName": "inst-123",
        "message": {"sender": "5511999998888@s.whatsapp.net", "text": "oi"},
    }

    with pytest.raises(ValueError, match="incompleto"):
        _provider(lambda r: httpx.Response(200)).parse_webhook(payload)


async def test_verify_signature_aceita_segredo_correto_e_rejeita_incorreto() -> None:
    provider = _provider(lambda r: httpx.Response(200))

    assert provider.verify_signature(b"corpo", {"webhook_secret": "SEGREDO_TESTE"}) is True
    assert provider.verify_signature(b"corpo", {"webhook_secret": "errado"}) is False
    assert provider.verify_signature(b"corpo", {}) is False


async def test_verificar_status_retorna_estado() -> None:
    # Schema real da UAZAPI (confirmado na doc oficial): o estado que
    # queremos mora em instance.status. O campo "status" de nível raiz é
    # um OBJETO diferente ({"connected": bool, "loggedIn": bool, "jid": ...})
    # — não confundir os dois.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://vrtice.uazapi.com/instance/status"
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "instance": {"status": "connected"},
                "status": {
                    "connected": True,
                    "loggedIn": True,
                    "jid": "558100000000@s.whatsapp.net",
                },
            },
        )

    estado = await _provider(handler).verificar_status()

    assert estado == "connected"


async def test_verificar_status_retry_em_erro_5xx_ate_ter_sucesso() -> None:
    chamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas["n"] += 1
        if chamadas["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "instance": {"status": "connecting"},
                "status": {"connected": False, "loggedIn": False, "jid": None},
            },
        )

    estado = await _provider(handler).verificar_status()

    assert chamadas["n"] == 3
    assert estado == "connecting"
