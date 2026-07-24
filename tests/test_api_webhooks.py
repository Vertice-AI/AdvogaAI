import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.workers.processar_mensagem import processar_mensagem_recebida


@pytest.fixture(autouse=True)
def _segredo_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "uazapi_webhook_secret", "SEGREDO_TESTE")


def _payload_mensagem() -> dict[str, Any]:
    return {
        "event": "messages",
        "instance": "inst-1",
        "data": {
            "sender": "5511999998888@s.whatsapp.net",
            "chatid": "5511999998888@s.whatsapp.net",
            "text": "oi",
            "messageid": "MSG1",
            "messageTimestamp": 1752192000000,
            "fromMe": False,
        },
    }


def test_webhook_com_segredo_correto_enfileira_task(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []
    monkeypatch.setattr(processar_mensagem_recebida, "delay", lambda **kw: chamadas.append(kw))
    tenant_id = uuid.uuid4()

    with TestClient(app) as client:
        resposta = client.post(
            f"/webhooks/uazapi/{tenant_id}",
            params={"secret": "SEGREDO_TESTE"},
            json=_payload_mensagem(),
        )

    assert resposta.status_code == 200
    assert len(chamadas) == 1
    assert chamadas[0]["tenant_id"] == str(tenant_id)
    assert chamadas[0]["from_number"] == "5511999998888"
    assert chamadas[0]["text"] == "oi"
    assert chamadas[0]["from_me"] is False


def test_webhook_com_segredo_errado_retorna_401(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[dict[str, Any]] = []
    monkeypatch.setattr(processar_mensagem_recebida, "delay", lambda **kw: chamadas.append(kw))

    with TestClient(app) as client:
        resposta = client.post(
            f"/webhooks/uazapi/{uuid.uuid4()}",
            params={"secret": "errado"},
            json=_payload_mensagem(),
        )

    assert resposta.status_code == 401
    assert chamadas == []


def test_webhook_evento_diferente_de_messages_retorna_200_sem_enfileirar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[dict[str, Any]] = []
    monkeypatch.setattr(processar_mensagem_recebida, "delay", lambda **kw: chamadas.append(kw))
    payload = {"event": "connection", "instance": "inst-1", "data": {"state": "connected"}}

    with TestClient(app) as client:
        resposta = client.post(
            f"/webhooks/uazapi/{uuid.uuid4()}", params={"secret": "SEGREDO_TESTE"}, json=payload
        )

    assert resposta.status_code == 200
    assert chamadas == []
