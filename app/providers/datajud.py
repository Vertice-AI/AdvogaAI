import asyncio
import re
from datetime import date
from typing import Any, cast

import httpx

_TIMEOUT_SEGUNDOS = 10.0
_MAX_TENTATIVAS = 3
_BACKOFF_BASE_SEGUNDOS = 0.5


class DataJudProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api-publica.datajud.cnj.jus.br",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_SEGUNDOS)

    async def buscar_movimentos(
        self, numero_processo: str, tribunal_alias: str
    ) -> list[dict[str, str]]:
        numero_limpo = re.sub(r"\D", "", numero_processo)
        url = f"{self._base_url}/api_publica_{tribunal_alias}/_search"
        corpo = {"query": {"match": {"numeroProcesso": numero_limpo}}}
        headers = {
            "Authorization": f"ApiKey {self._api_key}",
            "Content-Type": "application/json",
        }

        resposta = await self._chamar_com_retry(url, corpo, headers)
        hits = resposta.get("hits", {}).get("hits", [])
        if not hits:
            return []

        movimentos_brutos = hits[0].get("_source", {}).get("movimentos", [])
        return [_mapear_movimento(movimento, numero_processo) for movimento in movimentos_brutos]

    async def _chamar_com_retry(
        self, url: str, corpo: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        for tentativa in range(_MAX_TENTATIVAS):
            ultima_tentativa = tentativa == _MAX_TENTATIVAS - 1
            try:
                resposta = await self._client.post(url, json=corpo, headers=headers)
                resposta.raise_for_status()
                return cast(dict[str, Any], resposta.json())
            except httpx.HTTPStatusError as erro:
                if erro.response.status_code < 500 or ultima_tentativa:
                    raise
            except (httpx.TimeoutException, httpx.TransportError):
                if ultima_tentativa:
                    raise
            await asyncio.sleep(_BACKOFF_BASE_SEGUNDOS * (2**tentativa))
        raise RuntimeError("loop de retry terminou sem retornar nem levantar exceção")


def _mapear_movimento(movimento: dict[str, Any], numero_processo: str) -> dict[str, str]:
    # A API pública do DataJud só expõe o nome categorizado do movimento (tabela
    # TPU) e complementos estruturados — não há texto livre/prosa. O "texto" aqui
    # é a melhor aproximação disponível nesta fonte (por isso ela é "base e
    # fallback", não fonte primária, conforme CLAUDE.md seção 4.2).
    complementos = movimento.get("complementosTabelados") or []
    nomes_complemento = [c["nome"] for c in complementos if c.get("nome")]
    texto = movimento["nome"]
    if nomes_complemento:
        texto = f"{texto} ({', '.join(nomes_complemento)})"

    data = date.fromisoformat(movimento["dataHora"][:10])

    return {
        "processo_numero": numero_processo,
        "data": data.isoformat(),
        "tipo": movimento["nome"],
        "texto": texto,
    }
