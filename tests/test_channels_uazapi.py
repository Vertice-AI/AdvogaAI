import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone

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


async def test_send_text_envia_corpo_e_headers_corretos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://vrtice.uazapi.com/send/text"
        assert request.headers["token"] == "TOKEN_TESTE"
        corpo = json.loads(request.content)
        assert corpo == {"number": "5511999998888", "text": "Olá!"}
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


async def test_parse_webhook_mensagem_valida() -> None:
    payload = {
        "event": "messages",
        "instance": "inst-123",
        "data": {
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
    assert inbound.message_id == "3EB0ABC123"
    assert inbound.timestamp == datetime.fromtimestamp(1752192000000 / 1000, tz=timezone.utc)


async def test_parse_webhook_evento_diferente_de_messages_levanta_erro() -> None:
    payload = {"event": "connection", "instance": "inst-123", "data": {"state": "connected"}}

    with pytest.raises(ValueError, match="'messages'"):
        _provider(lambda r: httpx.Response(200)).parse_webhook(payload)


async def test_parse_webhook_sem_timestamp_levanta_erro() -> None:
    payload = {
        "event": "messages",
        "instance": "inst-123",
        "data": {"sender": "5511999998888@s.whatsapp.net", "text": "oi"},
    }

    with pytest.raises(ValueError, match="incompleto"):
        _provider(lambda r: httpx.Response(200)).parse_webhook(payload)


async def test_verify_signature_aceita_segredo_correto_e_rejeita_incorreto() -> None:
    provider = _provider(lambda r: httpx.Response(200))

    assert provider.verify_signature(b"corpo", {"webhook_secret": "SEGREDO_TESTE"}) is True
    assert provider.verify_signature(b"corpo", {"webhook_secret": "errado"}) is False
    assert provider.verify_signature(b"corpo", {}) is False


async def test_verificar_status_retorna_estado() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://vrtice.uazapi.com/instance/status"
        assert request.method == "GET"
        return httpx.Response(200, json={"status": "connected"})

    estado = await _provider(handler).verificar_status()

    assert estado == "connected"


async def test_verificar_status_retry_em_erro_5xx_ate_ter_sucesso() -> None:
    chamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas["n"] += 1
        if chamadas["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"status": "connecting"})

    estado = await _provider(handler).verificar_status()

    assert chamadas["n"] == 3
    assert estado == "connecting"
