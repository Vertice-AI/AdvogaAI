"""Diagnóstico pontual — testa o envio via UazapiProvider.send_text de verdade
(mesmo cliente httpx.AsyncClient assíncrono do código de produção, não um
httpx.post() síncrono avulso), medindo o tempo real. Não faz parte do produto
(ver CLAUDE.md §8, "não crie script que eu não pedi" — pedido explicitamente
na sessão de 2026-08-06 pra isolar se um ReadTimeout persistente em produção
é específico do cliente assíncrono ou de rodar dentro do worker do Celery).

Uso (PYTHONPATH=. necessário fora do pytest):
    PYTHONPATH=. python scripts/testar_envio_uazapi.py --numero 5581994065983
"""

import argparse
import asyncio
import time

from app.channels.uazapi import UazapiProvider
from app.core.config import settings


async def _testar(numero: str) -> None:
    provider = UazapiProvider(
        base_url=settings.uazapi_base_url,
        token=settings.uazapi_token,
        webhook_secret=settings.uazapi_webhook_secret,
    )
    inicio = time.monotonic()
    try:
        message_id = await provider.send_text(numero, "teste de diagnóstico async (Railway)")
        duracao = time.monotonic() - inicio
        print(f"sucesso: message_id={message_id} duração={duracao:.2f}s")
    except Exception as erro:  # noqa: BLE001 — script de diagnóstico, quer ver qualquer falha
        duracao = time.monotonic() - inicio
        print(f"ERRO após {duracao:.2f}s: {type(erro).__name__}: {erro}")


def main() -> None:
    args = _parse_args()
    asyncio.run(_testar(args.numero))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--numero", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
