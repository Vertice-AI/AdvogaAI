import asyncio
import uuid
from datetime import datetime

import structlog

from app.agents.atendimento import AtendimentoAgent
from app.agents.processual import AnthropicMessagesClient
from app.channels import get_channel_provider
from app.channels.base import InboundMessage
from app.core.config import settings
from app.db.base import SessionLocal
from app.services.atendimento import processar_mensagem
from app.workers.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(name="workers.processar_mensagem_recebida")
def processar_mensagem_recebida(
    tenant_id: str,
    from_number: str,
    text: str,
    message_id: str,
    timestamp_iso: str,
    from_me: bool,
) -> None:
    asyncio.run(
        _processar_mensagem_recebida_async(
            tenant_id, from_number, text, message_id, timestamp_iso, from_me
        )
    )


async def _processar_mensagem_recebida_async(
    tenant_id: str,
    from_number: str,
    text: str,
    message_id: str,
    timestamp_iso: str,
    from_me: bool,
) -> None:
    channel = get_channel_provider()
    agent = AtendimentoAgent(
        client=AnthropicMessagesClient(settings.anthropic_api_key),
        haiku_model=settings.haiku_model,
    )
    inbound = InboundMessage(
        from_number=from_number,
        text=text,
        message_id=message_id,
        timestamp=datetime.fromisoformat(timestamp_iso),
        from_me=from_me,
    )
    session = SessionLocal()
    try:
        await processar_mensagem(session, channel, agent, uuid.UUID(tenant_id), inbound)
    except Exception:  # noqa: BLE001 — task de fila: loga e desiste da mensagem em vez de derrubar o worker
        session.rollback()
        logger.error("processar_mensagem_falhou", tenant_id=tenant_id, message_id=message_id)
    finally:
        session.close()
