from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import structlog
from celery import Task
from celery.exceptions import Retry

from app.agents.atendimento import AtendimentoAgent
from app.agents.processual import AnthropicMessagesClient
from app.channels import get_channel_provider
from app.channels.base import InboundMessage
from app.core.config import settings
from app.db.base import SessionLocal
from app.services.atendimento import processar_mensagem
from app.workers.celery_app import celery_app

logger = structlog.get_logger()

_MAX_TENTATIVAS = 5
_BACKOFF_BASE_SEGUNDOS = 10


@celery_app.task(name="workers.processar_mensagem_recebida", bind=True, max_retries=_MAX_TENTATIVAS)
def processar_mensagem_recebida(
    self: Task[..., None],
    tenant_id: str,
    from_number: str,
    text: str,
    message_id: str,
    timestamp_iso: str,
    from_me: bool,
) -> None:
    try:
        asyncio.run(
            _processar_mensagem_recebida_async(
                tenant_id, from_number, text, message_id, timestamp_iso, from_me
            )
        )
    except Exception as erro:  # noqa: BLE001 — decide entre retry (fila segura o envio, CLAUDE.md §4.7) e desistir
        try:
            raise self.retry(
                exc=erro, countdown=_BACKOFF_BASE_SEGUNDOS * (2**self.request.retries)
            ) from erro
        except Retry:
            raise
        except Exception:
            # self.retry() com exc= definido relança o próprio `erro` (não
            # MaxRetriesExceededError) quando as tentativas se esgotam —
            # qualquer coisa que não seja Retry aqui significa "desistiu".
            logger.error(
                "processar_mensagem_falhou_definitivamente",
                tenant_id=tenant_id,
                message_id=message_id,
            )
            _alertar_falha_definitiva(tenant_id, from_number)
            raise


def _alertar_falha_definitiva(tenant_id: str, from_number: str) -> None:
    # Depois de esgotar as tentativas, o cliente fica sem resposta automática
    # — sem isso, ninguém no escritório fica sabendo (só existia log). Decisão
    # consciente de 2026-08-09: manda pelo próprio WhatsApp (settings.
    # alert_whatsapp_numero) em vez do webhook fora-de-banda de
    # app/services/alertas.py — o bug de hoje (ReadTimeout ao responder logo
    # após receber) não é queda de instância, então o WhatsApp costuma
    # continuar disponível pra esse alerta específico. Aceita o risco de não
    # chegar se a instância cair de vez; esse caso continua coberto pelo
    # alerta fora-de-banda do healthcheck (app/workers/healthcheck_uazapi.py).
    # Não inclui o texto da mensagem do cliente (CLAUDE.md §6) — só o número,
    # pra quem for atender manualmente encontrar a conversa.
    if not settings.alert_whatsapp_numero:
        logger.info("alerta_sem_numero_configurado", tenant_id=tenant_id)
        return
    try:
        asyncio.run(_enviar_alerta_whatsapp_async(tenant_id, from_number))
    except Exception as erro:  # noqa: BLE001 — alerta é best-effort (mesmo padrão de app/workers/healthcheck_uazapi.py)
        logger.error("falha_ao_enviar_alerta_processamento", erro=str(erro))


async def _enviar_alerta_whatsapp_async(tenant_id: str, from_number: str) -> None:
    channel = get_channel_provider()
    try:
        await channel.send_text(
            settings.alert_whatsapp_numero,
            f"🔴 AdvogAI: não conseguimos responder automaticamente ao "
            f"WhatsApp {from_number} (tenant {tenant_id}) depois de "
            f"{_MAX_TENTATIVAS} tentativas. Confira e responda manualmente.",
        )
    finally:
        await channel.aclose()


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
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        await channel.aclose()
