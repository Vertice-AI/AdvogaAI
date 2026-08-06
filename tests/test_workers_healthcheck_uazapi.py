import httpx
import structlog

from app.channels.uazapi import UazapiProvider
from app.workers.healthcheck_uazapi import verificar_saude


def _provider(estado: str) -> UazapiProvider:
    # Schema real da UAZAPI: o estado (connected/disconnected/...) mora em
    # instance.status, não no "status" de nível raiz (esse é um objeto
    # diferente) — ver app/channels/uazapi.py:verificar_status.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"instance": {"status": estado}})

    return UazapiProvider(
        base_url="https://vrtice.uazapi.com",
        token="TOKEN_TESTE",
        webhook_secret="SEGREDO_TESTE",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class _FakeStore:
    def __init__(self, inicial: str | None = None) -> None:
        self.estado = inicial

    async def ler(self) -> str | None:
        return self.estado

    async def gravar(self, estado: str) -> None:
        self.estado = estado


class _Recorder:
    def __init__(self) -> None:
        self.alertas: list[str] = []

    async def __call__(self, texto: str) -> None:
        self.alertas.append(texto)


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


async def test_alerta_dispara_na_transicao_saudavel_para_caiu() -> None:
    store = _FakeStore(inicial="connected")
    recorder = _Recorder()

    await verificar_saude(_provider("disconnected"), store=store, alertar=recorder)

    assert len(recorder.alertas) == 1
    assert "caiu" in recorder.alertas[0]
    assert store.estado == "disconnected"


async def test_alerta_nao_repete_enquanto_segue_caido() -> None:
    store = _FakeStore(inicial="disconnected")
    recorder = _Recorder()

    await verificar_saude(_provider("disconnected"), store=store, alertar=recorder)

    assert recorder.alertas == []


async def test_alerta_de_recuperacao_na_volta() -> None:
    store = _FakeStore(inicial="disconnected")
    recorder = _Recorder()

    await verificar_saude(_provider("connected"), store=store, alertar=recorder)

    assert len(recorder.alertas) == 1
    assert "reconectada" in recorder.alertas[0]
    assert store.estado == "connected"


async def test_primeira_execucao_ja_caida_alerta() -> None:
    store = _FakeStore(inicial=None)
    recorder = _Recorder()

    await verificar_saude(_provider("disconnected"), store=store, alertar=recorder)

    assert len(recorder.alertas) == 1


async def test_primeira_execucao_saudavel_nao_alerta() -> None:
    store = _FakeStore(inicial=None)
    recorder = _Recorder()

    await verificar_saude(_provider("connected"), store=store, alertar=recorder)

    assert recorder.alertas == []


async def test_falha_de_canal_nao_derruba_healthcheck() -> None:
    store = _FakeStore(inicial="connected")

    async def alertar_que_falha(texto: str) -> None:
        raise RuntimeError("canal indisponível")

    # Não deve levantar; o estado ainda é atualizado apesar da falha no alerta.
    await verificar_saude(_provider("disconnected"), store=store, alertar=alertar_que_falha)

    assert store.estado == "disconnected"
