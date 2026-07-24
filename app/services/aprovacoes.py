import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.base import ChannelProvider, InboundMessage
from app.db.models import Advogado, Movimento, Processo
from app.services.pipeline_resumo import DecisaoEnvio

_TAMANHO_CODIGO = 6
_ACOES = {"aprovar": DecisaoEnvio.AUTO_SEND, "rejeitar": DecisaoEnvio.BLOCKED}


def codigo_curto(movimento_id: uuid.UUID) -> str:
    return movimento_id.hex[:_TAMANHO_CODIGO].upper()


def advogado_por_numero(session: Session, tenant_id: uuid.UUID, numero: str) -> Advogado | None:
    return session.scalar(
        select(Advogado).where(Advogado.tenant_id == tenant_id, Advogado.whatsapp_numero == numero)
    )


async def processar_comando_advogado(
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    advogado: Advogado,
    inbound: InboundMessage,
) -> None:
    numero_advogado = advogado.whatsapp_numero
    if numero_advogado is None:
        return  # não deveria acontecer: só chega aqui se veio de advogado_por_numero

    partes = inbound.text.strip().lower().split()
    acao = partes[0] if partes else ""
    if acao not in _ACOES:
        await channel.send_text(
            numero_advogado, "Não entendi. Responda 'aprovar <código>' ou 'rejeitar <código>'."
        )
        return

    if len(partes) < 2:
        await channel.send_text(
            numero_advogado, f"Inclua o código da pendência, ex: '{acao} A3F2B1'."
        )
        return

    codigo = partes[1].upper()
    movimento = _buscar_pendencia_por_codigo(session, tenant_id, advogado.id, codigo)
    if movimento is None:
        await channel.send_text(
            numero_advogado, f"Não encontrei nenhuma pendência sua com o código {codigo}."
        )
        return

    processo = session.get(Processo, movimento.processo_id)
    movimento.decisao = _ACOES[acao].value
    session.commit()

    numero_processo = processo.numero if processo is not None else "desconhecido"
    verbo = "Aprovado" if acao == "aprovar" else "Rejeitado"
    await channel.send_text(numero_advogado, f"{verbo}: processo {numero_processo} ({codigo}).")


def _buscar_pendencia_por_codigo(
    session: Session, tenant_id: uuid.UUID, advogado_id: uuid.UUID, codigo: str
) -> Movimento | None:
    pendentes = session.scalars(
        select(Movimento)
        .join(Processo, Movimento.processo_id == Processo.id)
        .where(
            Movimento.tenant_id == tenant_id,
            Movimento.decisao == DecisaoEnvio.NEEDS_APPROVAL.value,
            Processo.advogado_responsavel_id == advogado_id,
        )
    ).all()
    for movimento in pendentes:
        if codigo_curto(movimento.id) == codigo:
            return movimento
    return None
