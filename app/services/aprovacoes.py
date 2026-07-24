import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.base import ChannelProvider, InboundMessage
from app.db.models import Advogado, Cliente, ConversaEstado, Movimento, Processo, SolicitacaoVinculo
from app.db.rls import definir_tenant
from app.services.pipeline_resumo import DecisaoEnvio

_TAMANHO_CODIGO = 6
_ACOES_MOVIMENTO = {"aprovar": DecisaoEnvio.AUTO_SEND, "rejeitar": DecisaoEnvio.BLOCKED}
_VERBOS_VINCULO = {"vincular", "descartar"}


def codigo_curto(entidade_id: uuid.UUID) -> str:
    return entidade_id.hex[:_TAMANHO_CODIGO].upper()


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
    verbo = partes[0] if partes else ""

    if verbo in _ACOES_MOVIMENTO:
        await _processar_comando_movimento(
            session, channel, tenant_id, advogado.id, numero_advogado, verbo, partes
        )
    elif verbo in _VERBOS_VINCULO:
        await _processar_comando_vinculo(
            session, channel, tenant_id, numero_advogado, verbo, partes
        )
    else:
        await channel.send_text(
            numero_advogado,
            "Não entendi. Responda 'aprovar/rejeitar <código>' (movimentação pendente) "
            "ou 'vincular/descartar <código>' (solicitação de contato).",
        )


async def _processar_comando_movimento(
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    advogado_id: uuid.UUID,
    numero_advogado: str,
    verbo: str,
    partes: list[str],
) -> None:
    if len(partes) < 2:
        await channel.send_text(
            numero_advogado, f"Inclua o código da pendência, ex: '{verbo} A3F2B1'."
        )
        return

    codigo = partes[1].upper()
    movimento = _buscar_movimento_por_codigo(session, tenant_id, advogado_id, codigo)
    if movimento is None:
        await channel.send_text(
            numero_advogado, f"Não encontrei nenhuma pendência sua com o código {codigo}."
        )
        return

    processo = session.get(Processo, movimento.processo_id)
    movimento.decisao = _ACOES_MOVIMENTO[verbo].value
    session.commit()

    numero_processo = processo.numero if processo is not None else "desconhecido"
    resultado = "Aprovado" if verbo == "aprovar" else "Rejeitado"
    await channel.send_text(numero_advogado, f"{resultado}: processo {numero_processo} ({codigo}).")


def _buscar_movimento_por_codigo(
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


async def _processar_comando_vinculo(
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    numero_advogado: str,
    verbo: str,
    partes: list[str],
) -> None:
    if len(partes) < 2:
        await channel.send_text(
            numero_advogado, f"Inclua o código da solicitação, ex: '{verbo} A3F2B1'."
        )
        return

    codigo = partes[1].upper()
    solicitacao = _buscar_solicitacao_por_codigo(session, tenant_id, codigo)
    if solicitacao is None:
        await channel.send_text(
            numero_advogado, f"Não encontrei nenhuma solicitação pendente com o código {codigo}."
        )
        return

    if verbo == "descartar":
        solicitacao.resolvida_em = datetime.now(UTC)
        session.commit()
        await channel.send_text(numero_advogado, f"Descartado ({codigo}).")
        await _encerrar_modo_humano(session, tenant_id, solicitacao.whatsapp_numero)
        await channel.send_text(
            solicitacao.whatsapp_numero,
            "Não conseguimos confirmar seu vínculo com nenhum processo. "
            "Entre em contato diretamente com o escritório.",
        )
        return

    await _vincular(session, channel, tenant_id, numero_advogado, codigo, solicitacao)


async def _vincular(
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    numero_advogado: str,
    codigo: str,
    solicitacao: SolicitacaoVinculo,
) -> None:
    if solicitacao.numero_processo_informado is None:
        await channel.send_text(
            numero_advogado,
            f"Essa solicitação ({codigo}) não informou número de processo — não dá pra "
            "vincular automaticamente. Cadastre o cliente e o processo antes.",
        )
        return

    processo = session.scalar(
        select(Processo).where(
            Processo.tenant_id == tenant_id,
            Processo.numero == solicitacao.numero_processo_informado,
        )
    )
    if processo is None:
        await channel.send_text(
            numero_advogado,
            f"Não encontrei o processo {solicitacao.numero_processo_informado} cadastrado ({codigo}).",
        )
        return

    cliente = session.get(Cliente, processo.cliente_id)
    if cliente is None:
        await channel.send_text(
            numero_advogado, f"Processo encontrado, mas sem cliente vinculado ({codigo})."
        )
        return

    conflito = session.scalar(
        select(Cliente).where(
            Cliente.tenant_id == tenant_id,
            Cliente.whatsapp_numero == solicitacao.whatsapp_numero,
            Cliente.id != cliente.id,
        )
    )
    if conflito is not None:
        await channel.send_text(
            numero_advogado,
            f"Esse número já está vinculado a outro cliente ({conflito.nome}). "
            f"Resolva isso antes de vincular ({codigo}).",
        )
        return

    numero_anterior = cliente.whatsapp_numero
    cliente.whatsapp_numero = solicitacao.whatsapp_numero
    cliente.verificado_em = datetime.now(UTC)
    solicitacao.resolvida_em = datetime.now(UTC)
    session.commit()

    aviso = (
        f" (substituiu {numero_anterior})"
        if numero_anterior and numero_anterior != solicitacao.whatsapp_numero
        else ""
    )
    await channel.send_text(
        numero_advogado,
        f"Vinculado! {solicitacao.whatsapp_numero} agora é o WhatsApp de {cliente.nome} "
        f"(processo {processo.numero}){aviso}.",
    )

    await _encerrar_modo_humano(session, tenant_id, solicitacao.whatsapp_numero)
    await channel.send_text(
        solicitacao.whatsapp_numero,
        f"Seu número foi vinculado ao processo {processo.numero}! "
        "Pode perguntar sobre ele quando quiser.",
    )


async def _encerrar_modo_humano(session: Session, tenant_id: uuid.UUID, numero: str) -> None:
    # definir_tenant de novo de propósito: quem chama sempre vem logo depois
    # de um session.commit() (vincular/descartar), que encerra a transação
    # com o tenant setado — skill escrever-com-rls-advogai.
    definir_tenant(session, tenant_id)
    estado = session.scalar(
        select(ConversaEstado).where(
            ConversaEstado.tenant_id == tenant_id, ConversaEstado.whatsapp_numero == numero
        )
    )
    if estado is not None:
        estado.atendimento_humano_desde = None
        session.commit()


def _buscar_solicitacao_por_codigo(
    session: Session, tenant_id: uuid.UUID, codigo: str
) -> SolicitacaoVinculo | None:
    pendentes = session.scalars(
        select(SolicitacaoVinculo).where(
            SolicitacaoVinculo.tenant_id == tenant_id, SolicitacaoVinculo.resolvida_em.is_(None)
        )
    ).all()
    for solicitacao in pendentes:
        if codigo_curto(solicitacao.id) == codigo:
            return solicitacao
    return None
