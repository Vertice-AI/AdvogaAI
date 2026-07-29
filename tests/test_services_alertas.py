import json

import httpx

from app.services.alertas import enviar_alerta


async def test_enviar_alerta_posta_texto_no_webhook() -> None:
    capturado: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["url"] = str(request.url)
        capturado["json"] = json.loads(request.content)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await enviar_alerta("instância caiu", "https://hooks.exemplo/abc", client=client)

    assert capturado["url"] == "https://hooks.exemplo/abc"
    assert capturado["json"] == {"text": "instância caiu"}


async def test_enviar_alerta_sem_url_e_noop() -> None:
    chamado = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chamado
        chamado = True
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await enviar_alerta("qualquer coisa", "", client=client)

    assert chamado is False
