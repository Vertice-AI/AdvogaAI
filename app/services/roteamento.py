import uuid
from datetime import UTC, datetime
from enum import Enum

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.base import ChannelProvider
from app.db.models import Advogado, Cliente, Processo, SolicitacaoAtendimento

logger = structlog.get_logger()


class StatusSolicitacaoAtendimento(str, Enum):
    NOTIFICADO = "notificado"
    AGUARDANDO = "aguardando"


def advogado_do_cliente(session: Session, cliente: Cliente) -> Advogado | None:
    resolvido = _processo_advogado_responsavel(session, cliente)
    return resolvido[1] if resolvido is not None else None


async def rotear_solicitacao_atendimento(
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    numero: str,
    texto_motivador: str,
) -> StatusSolicitacaoAtendimento | None:
    cliente = session.scalar(
        select(Cliente).where(Cliente.tenant_id == tenant_id, Cliente.whatsapp_numero == numero)
    )

    processo: Processo | None = None
    advogado: Advogado | None = None
    if cliente is not None:
        resolvido = _processo_advogado_responsavel(session, cliente)
        if resolvido is not None:
            processo, advogado = resolvido

    if advogado is None:
        # Modelo Solo: sem processo/advogado responsável já resolvido, cai
        # pro único advogado do tenant com WhatsApp pessoal cadastrado.
        advogado = session.scalar(
            select(Advogado)
            .where(Advogado.tenant_id == tenant_id, Advogado.whatsapp_numero.is_not(None))
            .limit(1)
        )

    if advogado is None or advogado.whatsapp_numero is None:
        logger.warning("roteamento_sem_advogado_disponivel", tenant_id=str(tenant_id))
        return None

    resumo = _montar_resumo_caso(cliente, processo, texto_motivador)
    status = (
        StatusSolicitacaoAtendimento.NOTIFICADO
        if advogado.disponivel
        else StatusSolicitacaoAtendimento.AGUARDANDO
    )
    solicitacao = SolicitacaoAtendimento(
        tenant_id=tenant_id,
        whatsapp_numero=numero,
        cliente_id=cliente.id if cliente is not None else None,
        advogado_designado_id=advogado.id,
        resumo_caso=resumo,
        status=status.value,
        notificado_em=datetime.now(UTC)
        if status == StatusSolicitacaoAtendimento.NOTIFICADO
        else None,
    )
    session.add(solicitacao)
    session.commit()

    if status == StatusSolicitacaoAtendimento.NOTIFICADO:
        await channel.send_text(advogado.whatsapp_numero, resumo)

    return status


async def notificar_proxima_solicitacao(
    session: Session, channel: ChannelProvider, tenant_id: uuid.UUID, advogado: Advogado
) -> None:
    if advogado.whatsapp_numero is None:
        return

    solicitacao = session.scalar(
        select(SolicitacaoAtendimento)
        .where(
            SolicitacaoAtendimento.tenant_id == tenant_id,
            SolicitacaoAtendimento.advogado_designado_id == advogado.id,
            SolicitacaoAtendimento.status == StatusSolicitacaoAtendimento.AGUARDANDO.value,
        )
        .order_by(SolicitacaoAtendimento.criado_em.asc())
        .limit(1)
    )
    if solicitacao is None:
        return

    solicitacao.status = StatusSolicitacaoAtendimento.NOTIFICADO.value
    solicitacao.notificado_em = datetime.now(UTC)
    session.commit()
    await channel.send_text(advogado.whatsapp_numero, solicitacao.resumo_caso)


def _processo_advogado_responsavel(
    session: Session, cliente: Cliente
) -> tuple[Processo, Advogado] | None:
    processo = session.scalar(
        select(Processo)
        .where(Processo.cliente_id == cliente.id, Processo.advogado_responsavel_id.is_not(None))
        .limit(1)
    )
    if processo is None or processo.advogado_responsavel_id is None:
        return None
    advogado = session.get(Advogado, processo.advogado_responsavel_id)
    if advogado is None:
        return None
    return processo, advogado


def _montar_resumo_caso(
    cliente: Cliente | None, processo: Processo | None, texto_motivador: str
) -> str:
    linhas = ["Cliente quer falar com você pelo WhatsApp."]
    linhas.append(f"Nome: {cliente.nome}" if cliente is not None else "Nome: não identificado")
    if processo is not None:
        linhas.append(f"Processo: {processo.numero}")
    linhas.append(f"Mensagem: {texto_motivador}")
    return "\n".join(linhas)
