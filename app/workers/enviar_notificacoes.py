import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels import get_channel_provider
from app.channels.base import ChannelProvider
from app.db.base import SessionLocal
from app.db.models import Cliente, Movimento, Processo, Tenant
from app.db.rls import definir_tenant
from app.services.pipeline_resumo import DecisaoEnvio
from app.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="workers.enviar_notificacoes_pendentes")
def enviar_notificacoes_pendentes() -> None:
    asyncio.run(enviar_notificacoes_de_todos_os_tenants())


async def enviar_notificacoes_de_todos_os_tenants() -> None:
    channel = get_channel_provider()
    session = SessionLocal()
    try:
        # `tenant` não tem RLS — ver comentário equivalente em sync_processual.py.
        tenants = session.scalars(select(Tenant)).all()
        for tenant in tenants:
            await enviar_notificacoes_do_tenant(session, channel, tenant.id)
    finally:
        session.close()


async def enviar_notificacoes_do_tenant(
    session: Session, channel: ChannelProvider, tenant_id: uuid.UUID
) -> None:
    definir_tenant(session, tenant_id)
    pendentes = session.execute(
        select(Movimento.id, Cliente.whatsapp_numero, Movimento.resumo)
        .join(Processo, Movimento.processo_id == Processo.id)
        .join(Cliente, Processo.cliente_id == Cliente.id)
        .where(
            Movimento.decisao == DecisaoEnvio.AUTO_SEND.value,
            Movimento.enviado_em.is_(None),
        )
    ).all()
    # valores primitivos, não objetos ORM: o rollback abaixo expira qualquer
    # instância presa à sessão (mesmo motivo documentado em sync_processual.py).
    itens = [(movimento_id, numero, resumo) for movimento_id, numero, resumo in pendentes]
    session.rollback()  # leitura só, encerra a transação sem deixar nada pendente

    for movimento_id, numero, resumo in itens:
        try:
            await enviar_notificacao(session, channel, tenant_id, movimento_id, numero, resumo)
        except Exception:
            session.rollback()
            logger.error(
                "envio_notificacao_falhou", tenant_id=str(tenant_id), movimento_id=str(movimento_id)
            )


async def enviar_notificacao(
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    movimento_id: uuid.UUID,
    numero: str,
    resumo: str | None,
) -> None:
    if not resumo:
        raise ValueError("movimento auto_send sem resumo — não deveria acontecer pelo pipeline")

    await channel.send_text(numero, resumo)

    definir_tenant(session, tenant_id)
    movimento = session.get(Movimento, movimento_id)
    if movimento is None:
        raise RuntimeError(f"movimento {movimento_id} sumiu entre a leitura e o envio")
    movimento.enviado_em = datetime.now(timezone.utc)
    session.commit()
    logger.info("notificacao_enviada", tenant_id=str(tenant_id), movimento_id=str(movimento_id))
