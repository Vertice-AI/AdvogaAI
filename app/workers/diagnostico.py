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
import uuid

import structlog
from sqlalchemy import select

from app.agents.atendimento import AtendimentoAgent
from app.agents.processual import AnthropicMessagesClient
from app.channels.uazapi import UazapiProvider
from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import Cliente
from app.db.rls import definir_tenant
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
        redis_url=settings.redis_url,
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


@celery_app.task(name="workers.diagnosticar_envio_com_db")
def diagnosticar_envio_com_db(numero: str, tenant_id: str) -> None:
    asyncio.run(_diagnosticar_envio_com_db_async(numero, tenant_id))


async def _diagnosticar_envio_com_db_async(numero: str, tenant_id: str) -> None:
    # Isola a outra suspeita que sobrou depois de diagnosticar_envio_com_agent
    # (deu sucesso em 1.30s — não é o AtendimentoAgent): o fluxo real
    # (_processar_mensagem_recebida_async → _enviar_saudacao) abre uma Session
    # síncrona do SQLAlchemy, roda definir_tenant (SET LOCAL, dentro de
    # transação aberta) e um SELECT, e só DEPOIS chama send_text — sem
    # commitar/fechar a sessão antes do envio. Aqui reproduzimos exatamente
    # essa sequência pra ver se a sessão/transação aberta é o que trava o
    # ReadTimeout na UAZAPI.
    provider = UazapiProvider(
        base_url=settings.uazapi_base_url,
        token=settings.uazapi_token,
        webhook_secret=settings.uazapi_webhook_secret,
        redis_url=settings.redis_url,
    )
    session = SessionLocal()
    inicio = time.monotonic()
    try:
        definir_tenant(session, uuid.UUID(tenant_id))
        session.scalar(select(Cliente).where(Cliente.tenant_id == uuid.UUID(tenant_id)).limit(1))
        message_id = await provider.send_text(numero, "diagnóstico com sessão de DB aberta")
        duracao = time.monotonic() - inicio
        logger.warning(
            "diagnostico_envio_com_db_sucesso", message_id=message_id, duracao=f"{duracao:.2f}s"
        )
    except Exception as erro:  # noqa: BLE001 — task de diagnóstico, quer ver qualquer falha
        duracao = time.monotonic() - inicio
        logger.warning(
            "diagnostico_envio_com_db_falhou",
            tipo=type(erro).__name__,
            erro=str(erro),
            duracao=f"{duracao:.2f}s",
        )
    finally:
        session.close()
        await provider.aclose()


@celery_app.task(name="workers.diagnosticar_envio_com_agent")
def diagnosticar_envio_com_agent(numero: str) -> None:
    asyncio.run(_diagnosticar_envio_com_agent_async(numero))


async def _diagnosticar_envio_com_agent_async(numero: str) -> None:
    # Isola a única diferença de código entre este diagnóstico e o de cima:
    # construir (sem usar) o AtendimentoAgent/AnthropicMessagesClient antes
    # do send_text — é o que o fluxo real (_processar_mensagem_recebida_async)
    # faz e a task diagnosticar_envio não faz. Se isso sozinho reproduzir o
    # ReadTimeout, achamos a causa; se não, o problema está em outro lugar
    # do fluxo real (DB/RLS).
    provider = UazapiProvider(
        base_url=settings.uazapi_base_url,
        token=settings.uazapi_token,
        webhook_secret=settings.uazapi_webhook_secret,
        redis_url=settings.redis_url,
    )
    _agent = AtendimentoAgent(
        client=AnthropicMessagesClient(settings.anthropic_api_key),
        haiku_model=settings.haiku_model,
    )
    inicio = time.monotonic()
    try:
        message_id = await provider.send_text(numero, "diagnóstico com agent construído antes")
        duracao = time.monotonic() - inicio
        logger.warning(
            "diagnostico_envio_com_agent_sucesso", message_id=message_id, duracao=f"{duracao:.2f}s"
        )
    except Exception as erro:  # noqa: BLE001 — task de diagnóstico, quer ver qualquer falha
        duracao = time.monotonic() - inicio
        logger.warning(
            "diagnostico_envio_com_agent_falhou",
            tipo=type(erro).__name__,
            erro=str(erro),
            duracao=f"{duracao:.2f}s",
        )
    finally:
        await provider.aclose()
