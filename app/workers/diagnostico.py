"""Task de diagnóstico temporária — reproduz o ReadTimeout intermitente do
envio via UAZAPI dentro da execução REAL de uma task do Celery (pool
prefork, processo forkado), diferente dos scripts em scripts/testar_envio_uazapi.py
que rodam como entrypoint do container e nunca passam pelo prefork de verdade.
Não é parte do produto — remover depois que o bug for identificado (CLAUDE.md
§8; pedido explícito na sessão de 2026-08-06/07 depois que o fix de aclose()
não resolveu o timeout).
"""

import asyncio
import time

import structlog

from app.channels.uazapi import UazapiProvider
from app.core.config import settings
from app.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="workers.diagnosticar_envio")
def diagnosticar_envio(numero: str) -> None:
    asyncio.run(_diagnosticar_envio_async(numero))


async def _diagnosticar_envio_async(numero: str) -> None:
    provider = UazapiProvider(
        base_url=settings.uazapi_base_url,
        token=settings.uazapi_token,
        webhook_secret=settings.uazapi_webhook_secret,
    )
    inicio = time.monotonic()
    try:
        message_id = await provider.send_text(numero, "diagnóstico via worker real")
        duracao = time.monotonic() - inicio
        logger.warning(
            "diagnostico_envio_sucesso", message_id=message_id, duracao=f"{duracao:.2f}s"
        )
    except Exception as erro:  # noqa: BLE001 — task de diagnóstico, quer ver qualquer falha
        duracao = time.monotonic() - inicio
        logger.warning(
            "diagnostico_envio_falhou",
            tipo=type(erro).__name__,
            erro=str(erro),
            duracao=f"{duracao:.2f}s",
        )
    finally:
        await provider.aclose()
