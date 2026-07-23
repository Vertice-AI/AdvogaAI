import httpx
import structlog

from app.channels.uazapi import UazapiProvider
from app.workers.healthcheck_uazapi import verificar_saude


def _provider(estado: str) -> UazapiProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": estado})

    return UazapiProvider(
        base_url="https://vrtice.uazapi.com",
        token="TOKEN_TESTE",
        webhook_secret="SEGREDO_TESTE",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_verificar_saude_loga_erro_quando_desconectado() -> None:
    with structlog.testing.capture_logs() as logs:
        await verificar_saude(_provider("disconnected"))

    eventos_erro = [log for log in logs if log["event"] == "uazapi_instancia_nao_saudavel"]
    assert len(eventos_erro) == 1
    assert eventos_erro[0]["log_level"] == "error"


async def test_verificar_saude_loga_info_quando_conectado() -> None:
    with structlog.testing.capture_logs() as logs:
        await verificar_saude(_provider("connected"))

    assert any(log["event"] == "uazapi_instancia_saudavel" for log in logs)
