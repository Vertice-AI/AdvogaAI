"""Diagnóstico pontual — testa POST /send/text da UAZAPI com o mesmo cliente
httpx/timeout do UazapiProvider, medindo o tempo real. Não faz parte do
produto (ver CLAUDE.md §8, "não crie script que eu não pedi" — pedido
explicitamente na sessão de 2026-08-06 pra isolar se um ReadTimeout
persistente em produção é da rede Railway->UAZAPI ou do nosso código).

Uso (PYTHONPATH=. necessário fora do pytest):
    PYTHONPATH=. python scripts/testar_envio_uazapi.py --numero 5581994065983
"""

import argparse
import time

import httpx

from app.core.config import settings


def main() -> None:
    args = _parse_args()

    inicio = time.monotonic()
    try:
        resposta = httpx.post(
            f"{settings.uazapi_base_url}/send/text",
            headers={"token": settings.uazapi_token, "Content-Type": "application/json"},
            json={"number": args.numero, "text": "teste de diagnóstico de latência (Railway)"},
            timeout=30.0,
        )
        duracao = time.monotonic() - inicio
        print(f"status: {resposta.status_code}")
        print(f"duração: {duracao:.2f}s")
        print(f"corpo: {resposta.text[:500]}")
    except httpx.HTTPError as erro:
        duracao = time.monotonic() - inicio
        print(f"ERRO após {duracao:.2f}s: {type(erro).__name__}: {erro}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--numero", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
