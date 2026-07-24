import asyncio
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels import get_channel_provider
from app.channels.base import ChannelProvider
from app.db.base import SessionLocal
from app.db.models import Advogado, Movimento, Processo, Tenant
from app.db.rls import definir_tenant
from app.services.aprovacoes import codigo_curto
from app.services.pipeline_resumo import DecisaoEnvio
from app.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="workers.solicitar_aprovacoes_pendentes")
def solicitar_aprovacoes_pendentes() -> None:
    asyncio.run(solicitar_aprovacoes_de_todos_os_tenants())


async def solicitar_aprovacoes_de_todos_os_tenants() -> None:
    channel = get_channel_provider()
    session = SessionLocal()
    try:
        tenants = session.scalars(select(Tenant)).all()
        for tenant in tenants:
            await solicitar_aprovacoes_do_tenant(session, channel, tenant.id)
    finally:
        session.close()


async def solicitar_aprovacoes_do_tenant(
    session: Session, channel: ChannelProvider, tenant_id: uuid.UUID
) -> None:
    definir_tenant(session, tenant_id)
    # Só considera movimentos cujo processo tem advogado responsável com
    # número de WhatsApp cadastrado — sem isso não há como pedir aprovação
    # por aqui (fica pendente até alguém cadastrar o número do advogado).
    pendentes = session.execute(
        select(Movimento.id, Movimento.resumo, Processo.numero, Advogado.whatsapp_numero)
        .join(Processo, Movimento.processo_id == Processo.id)
        .join(Advogado, Processo.advogado_responsavel_id == Advogado.id)
        .where(
            Movimento.decisao == DecisaoEnvio.NEEDS_APPROVAL.value,
            Movimento.aprovacao_solicitada_em.is_(None),
            Advogado.whatsapp_numero.is_not(None),
        )
    ).all()
    # valores primitivos, não objetos ORM: o rollback abaixo expira qualquer
    # instância presa à sessão (mesmo motivo documentado em sync_processual.py).
    itens = [
        (movimento_id, resumo, numero_processo, numero_advogado)
        for movimento_id, resumo, numero_processo, numero_advogado in pendentes
    ]
    session.rollback()  # leitura só, encerra a transação sem deixar nada pendente

    for movimento_id, resumo, numero_processo, numero_advogado in itens:
        try:
            await _solicitar_aprovacao(
                session, channel, tenant_id, movimento_id, resumo, numero_processo, numero_advogado
            )
        except Exception:  # noqa: BLE001 — isola falha de 1 solicitação sem derrubar as demais do tenant
            session.rollback()
            logger.error(
                "solicitacao_aprovacao_falhou",
                tenant_id=str(tenant_id),
                movimento_id=str(movimento_id),
            )


async def _solicitar_aprovacao(
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    movimento_id: uuid.UUID,
    resumo: str | None,
    numero_processo: str,
    numero_advogado: str,
) -> None:
    if not resumo:
        raise ValueError(
            f"movimento {movimento_id} needs_approval sem resumo — não deveria acontecer"
        )

    codigo = codigo_curto(movimento_id)
    texto = (
        f"Pendente de aprovação — Processo {numero_processo}:\n{resumo}\n\n"
        f"Responda 'aprovar {codigo}' ou 'rejeitar {codigo}'."
    )
    await channel.send_text(numero_advogado, texto)

    definir_tenant(session, tenant_id)
    movimento = session.get(Movimento, movimento_id)
    if movimento is None:
        raise RuntimeError(
            f"movimento {movimento_id} sumiu entre a leitura e o pedido de aprovação"
        )
    movimento.aprovacao_solicitada_em = datetime.now(UTC)
    session.commit()
    logger.info(
        "aprovacao_solicitada",
        tenant_id=str(tenant_id),
        movimento_id=str(movimento_id),
        codigo=codigo,
    )
