import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.providers.datajud import DataJudProvider

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "datajud" / "resposta_busca.json").read_text(
        encoding="utf-8"
    )
)


def _client_com_handler(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_busca_mapeia_movimentos_corretamente() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api_publica_trf1/_search"
        assert request.headers["authorization"] == "ApiKey chave-de-teste"
        corpo = json.loads(request.content)
        assert corpo == {"query": {"match": {"numeroProcesso": "00008323520184013202"}}}
        return httpx.Response(200, json=_FIXTURE)

    provider = DataJudProvider(api_key="chave-de-teste", client=_client_com_handler(handler))

    movimentos = await provider.buscar_movimentos("0000832-35.2018.4.01.3202", "trf1")

    assert len(movimentos) == 2
    assert movimentos[0]["processo_numero"] == "0000832-35.2018.4.01.3202"
    assert movimentos[0]["tipo"] == "Conclusão"
    assert movimentos[0]["texto"] == "Conclusão (Magistrado)"
    assert movimentos[0]["data"] == "2026-07-10"
    assert movimentos[1]["texto"] == "Julgado procedente o pedido"


async def test_busca_sem_resultado_retorna_lista_vazia() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": {"hits": []}})

    provider = DataJudProvider(api_key="chave-de-teste", client=_client_com_handler(handler))

    movimentos = await provider.buscar_movimentos("0000000-00.2024.8.26.0100", "tjsp")

    assert movimentos == []


async def test_retry_em_erro_5xx_ate_ter_sucesso() -> None:
    chamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas["n"] += 1
        if chamadas["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=_FIXTURE)

    provider = DataJudProvider(api_key="chave-de-teste", client=_client_com_handler(handler))

    movimentos = await provider.buscar_movimentos("0000832-35.2018.4.01.3202", "trf1")

    assert chamadas["n"] == 3
    assert len(movimentos) == 2


async def test_erro_4xx_nao_tenta_novamente() -> None:
    chamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas["n"] += 1
        return httpx.Response(401)

    provider = DataJudProvider(api_key="chave-errada", client=_client_com_handler(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.buscar_movimentos("0000832-35.2018.4.01.3202", "trf1")

    assert chamadas["n"] == 1
