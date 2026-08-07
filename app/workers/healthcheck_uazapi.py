import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

import redis.asyncio as aioredis
import structlog

from app.channels.uazapi import UazapiProvider
from app.core.config import settings
from app.services.alertas import enviar_alerta
from app.workers.celery_app import celery_app

logger = structlog.get_logger()

_ESTADO_SAUDAVEL = "connected"
_CHAVE_ESTADO = "advogai:uazapi:ultimo_estado_saude"


class EstadoSaudeStore(Protocol):
    """Persiste o último estado observado entre execuções do job (a cada 5 min,
    em processos separados) pra alertar só na transição, não a cada ciclo."""

    async def ler(self) -> str | None: ...
    async def gravar(self, estado: str) -> None: ...


class RedisEstadoSaudeStore:
    def __init__(self, redis_url: str) -> None:
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    async def ler(self) -> str | None:
        return cast(str | None, await self._redis.get(_CHAVE_ESTADO))

    async def gravar(self, estado: str) -> None:
        await self._redis.set(_CHAVE_ESTADO, estado)


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
    store = RedisEstadoSaudeStore(settings.redis_url)

    async def _alertar(texto: str) -> None:
        await enviar_alerta(texto, settings.alert_webhook_url)

    try:
        await verificar_saude(provider, store=store, alertar=_alertar)
    finally:
        await provider.aclose()


async def verificar_saude(
    provider: UazapiProvider,
    store: EstadoSaudeStore | None = None,
    alertar: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    estado = await provider.verificar_status()
    saudavel = estado == _ESTADO_SAUDAVEL
    if saudavel:
        logger.info("uazapi_instancia_saudavel", estado=estado)
    else:
        logger.error("uazapi_instancia_nao_saudavel", estado=estado)

    if store is None:
        return

    anterior = await store.ler()
    # anterior None = primeira execução sem estado prévio; trata como "estava
    # saudável" pra não disparar alerta de recuperação sem uma queda anterior,
    # mas ainda alertar se já subir com a instância caída.
    era_saudavel = anterior is None or anterior == _ESTADO_SAUDAVEL

    if era_saudavel and not saudavel:
        await _tentar_alertar(
            alertar,
            f"🔴 AdvogAI: instância UAZAPI caiu (estado: {estado}). "
            "Mensagens não entram nem saem até a sessão reconectar.",
        )
    elif not era_saudavel and saudavel:
        await _tentar_alertar(alertar, "🟢 AdvogAI: instância UAZAPI reconectada.")

    # Atualiza o estado mesmo se o alerta falhou: a de-dup é sobre transição de
    # estado, não garantia de entrega. Uma falha do canal já foi logada; uma
    # queda persistente reenvia o alerta quando o estado voltar e cair de novo.
    await store.gravar(estado)


async def _tentar_alertar(alertar: Callable[[str], Awaitable[None]] | None, texto: str) -> None:
    if alertar is None:
        return
    try:
        await alertar(texto)
    except Exception as erro:  # noqa: BLE001
        # Alerta é best-effort: o canal fora-de-banda pode estar indisponível,
        # e isso não pode derrubar o healthcheck. Loga alto, não engole calado.
        logger.error("falha_ao_enviar_alerta_saude", erro=str(erro))
