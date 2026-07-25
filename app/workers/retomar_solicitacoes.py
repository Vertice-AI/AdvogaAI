import asyncio
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels import get_channel_provider
from app.channels.base import ChannelProvider
from app.db.base import SessionLocal
from app.db.models import Advogado, Tenant
from app.db.rls import definir_tenant
from app.services.roteamento import notificar_proxima_solicitacao
from app.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="workers.retomar_solicitacoes_pendentes")
def retomar_solicitacoes_pendentes() -> None:
    asyncio.run(retomar_solicitacoes_de_todos_os_tenants())


async def retomar_solicitacoes_de_todos_os_tenants() -> None:
    channel = get_channel_provider()
    session = SessionLocal()
    try:
        tenants = session.scalars(select(Tenant)).all()
        for tenant in tenants:
            await retomar_solicitacoes_do_tenant(session, channel, tenant.id)
    finally:
        session.close()


async def retomar_solicitacoes_do_tenant(
    session: Session, channel: ChannelProvider, tenant_id: uuid.UUID
) -> None:
    definir_tenant(session, tenant_id)
    # Advogados disponíveis cuja última tentativa de notificação falhou (ex.:
    # UAZAPI fora do ar) ficam com solicitação presa em "aguardando" sem
    # nenhum gatilho — normalmente só destrava quando o advogado alterna
    # indisponível/disponível de novo. Esta varredura periódica cobre esse
    # caso (CLAUDE.md §4.7: "fila que segura o envio em vez de descartar").
    advogados = session.scalars(
        select(Advogado).where(
            Advogado.tenant_id == tenant_id,
            Advogado.disponivel.is_(True),
            Advogado.whatsapp_numero.is_not(None),
        )
    ).all()
    # valores primitivos, não objetos ORM: o rollback abaixo expira qualquer
    # instância presa à sessão (mesmo motivo documentado em sync_processual.py).
    advogado_ids = [advogado.id for advogado in advogados]
    session.rollback()  # leitura só, encerra a transação sem deixar nada pendente

    for advogado_id in advogado_ids:
        try:
            definir_tenant(session, tenant_id)
            advogado = session.get(Advogado, advogado_id)
            if advogado is None:
                continue
            await notificar_proxima_solicitacao(session, channel, tenant_id, advogado)
        except Exception:  # noqa: BLE001 — isola falha de 1 advogado sem derrubar os demais do tenant
            session.rollback()
            logger.error(
                "retomada_solicitacao_falhou",
                tenant_id=str(tenant_id),
                advogado_id=str(advogado_id),
            )
