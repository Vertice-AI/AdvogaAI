import asyncio

import structlog

from app.channels.uazapi import UazapiProvider
from app.core.config import settings
from app.workers.celery_app import celery_app

logger = structlog.get_logger()

_ESTADO_SAUDAVEL = "connected"


@celery_app.task(name="workers.verificar_saude_uazapi")
def verificar_saude_uazapi() -> None:
    asyncio.run(_verificar_saude_uazapi_async())


async def _verificar_saude_uazapi_async() -> None:
    # Task específica da UAZAPI (não passa por get_channel_provider): o
    # healthcheck de instância é um conceito da UAZAPI, não parte do
    # ChannelProvider genérico — não existe equivalente óbvio no
    # MetaCloudProvider (CLAUDE.md §4.7).
    provider = UazapiProvider(
        base_url=settings.uazapi_base_url,
        token=settings.uazapi_token,
        webhook_secret=settings.uazapi_webhook_secret,
    )
    await verificar_saude(provider)


async def verificar_saude(provider: UazapiProvider) -> None:
    estado = await provider.verificar_status()
    if estado != _ESTADO_SAUDAVEL:
        logger.error("uazapi_instancia_nao_saudavel", estado=estado)
    else:
        logger.info("uazapi_instancia_saudavel", estado=estado)
