import asyncio
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.processual import AnthropicMessagesClient, ProcessualAgent
from app.core.config import settings
from app.db.base import SessionLocal
from app.db.models import Movimento, Processo, Tenant
from app.db.rls import definir_tenant
from app.providers import get_process_provider
from app.providers.base import ProcessProvider
from app.services.normalizacao import normalizar
from app.services.pipeline_resumo import NivelAutonomia, processar_e_persistir_movimento
from app.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="workers.sincronizar_processos")
def sincronizar_processos() -> None:
    asyncio.run(sincronizar_todos_os_tenants())


async def sincronizar_todos_os_tenants() -> None:
    provider = get_process_provider()
    agent = ProcessualAgent(
        client=AnthropicMessagesClient(settings.anthropic_api_key),
        haiku_model=settings.haiku_model,
        sonnet_model=settings.sonnet_model,
    )
    session = SessionLocal()
    try:
        # `tenant` não tem RLS (é gerida por fluxo administrativo, não por
        # tenant_id — ver comentário na migration 0001), então essa leitura
        # enxerga todos os tenants sem precisar de definir_tenant antes.
        tenants = session.scalars(select(Tenant)).all()
        for tenant in tenants:
            await sincronizar_tenant(session, provider, agent, tenant)
    finally:
        session.close()


async def sincronizar_tenant(
    session: Session, provider: ProcessProvider, agent: ProcessualAgent, tenant: Tenant
) -> None:
    nivel_autonomia = NivelAutonomia(tenant.nivel_autonomia_padrao)
    tenant_id = tenant.id
    definir_tenant(session, tenant_id)
    processos = session.scalars(select(Processo).where(Processo.tenant_id == tenant_id)).all()
    # ids capturados como valor puro antes do rollback: `processo` é
    # protegido por RLS, e o rollback expira o objeto — um acesso a
    # `processo.id` depois disso tentaria um refresh que a RLS bloqueia
    # (app.tenant_id da transação já foi descartado), mascarando o erro
    # original com um ObjectDeletedError.
    processos_com_id = [(processo, processo.id) for processo in processos]
    session.rollback()  # leitura só, encerra a transação sem deixar nada pendente

    for processo, processo_id in processos_com_id:
        try:
            await sincronizar_processo(
                session, provider, agent, tenant_id, processo, nivel_autonomia
            )
        except Exception:
            session.rollback()
            logger.error(
                "sync_processo_falhou", tenant_id=str(tenant_id), processo_id=str(processo_id)
            )


async def sincronizar_processo(
    session: Session,
    provider: ProcessProvider,
    agent: ProcessualAgent,
    tenant_id: uuid.UUID,
    processo: Processo,
    nivel_autonomia: NivelAutonomia,
) -> None:
    # set_config(..., true) é local à transação (app/db/rls.py) — como cada
    # processo comita sua própria transação, precisa redefinir a cada um.
    definir_tenant(session, tenant_id)
    movimentos_brutos = await provider.buscar_movimentos(processo.numero, processo.tribunal_alias)
    movimentos_novos = filtrar_movimentos_novos(session, processo.id, movimentos_brutos)

    for movimento_bruto in movimentos_novos:
        await processar_e_persistir_movimento(
            movimento_bruto, agent, nivel_autonomia, session, tenant_id, processo.id
        )
    session.commit()
    logger.info(
        "sync_processo_concluido",
        tenant_id=str(tenant_id),
        processo_id=str(processo.id),
        movimentos_novos=len(movimentos_novos),
    )


def filtrar_movimentos_novos(
    session: Session, processo_id: uuid.UUID, movimentos_brutos: list[dict[str, str]]
) -> list[dict[str, str]]:
    existentes = session.execute(
        select(Movimento.data, Movimento.tipo, Movimento.texto_origem).where(
            Movimento.processo_id == processo_id
        )
    ).all()
    chaves_existentes = {(data, tipo, texto) for data, tipo, texto in existentes}

    novos: list[dict[str, str]] = []
    for movimento_bruto in movimentos_brutos:
        normalizado = normalizar(movimento_bruto)
        chave = (normalizado.data, normalizado.tipo, normalizado.texto)
        if chave not in chaves_existentes:
            novos.append(movimento_bruto)
    return novos
