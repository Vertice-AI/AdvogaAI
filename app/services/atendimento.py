import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.atendimento import AtendimentoAgent, Intencao
from app.channels.base import ChannelProvider, InboundMessage
from app.db.models import Advogado, Cliente, ConversaEstado, Movimento, Processo
from app.db.rls import definir_tenant

logger = structlog.get_logger()

_COMANDO_REATIVAR_IA = "/ia"
_MENU_TEXTO = "\n\n1) Consultar atualização do meu processo\n2) Falar com o advogado"


async def processar_mensagem(
    session: Session,
    channel: ChannelProvider,
    agent: AtendimentoAgent,
    tenant_id: uuid.UUID,
    inbound: InboundMessage,
) -> None:
    definir_tenant(session, tenant_id)

    if inbound.from_me:
        await _processar_mensagem_do_advogado(session, tenant_id, inbound)
        return

    estado = _obter_ou_criar_conversa_estado(session, tenant_id, inbound.from_number)

    if estado.atendimento_humano_desde is not None:
        # Regra do silêncio (CLAUDE.md §4.5): a IA não responde nada enquanto
        # um humano estiver com a conversa. Só volta com o comando /ia vindo
        # do próprio número do escritório (ver _processar_mensagem_do_advogado).
        return

    if not _saudou_hoje(estado.ultima_saudacao_em):
        await _enviar_saudacao(session, channel, tenant_id, inbound.from_number)
        estado.ultima_saudacao_em = datetime.now(UTC)
        session.commit()
        return

    intencao = await agent.classificar_intencao(inbound.text)

    if intencao == Intencao.CONSULTAR_PROCESSO:
        await _responder_consulta_processo(session, channel, tenant_id, inbound.from_number)
    elif intencao == Intencao.FALAR_ADVOGADO:
        # Ativa mesmo sem saber qual advogado especificamente — a regra do
        # silêncio não pode falhar por falta desse dado.
        estado.atendimento_humano_desde = datetime.now(UTC)
        session.commit()
        await channel.send_text(
            inbound.from_number,
            "Certo! Vou te colocar em contato com o advogado responsável — "
            "a partir de agora ele(a) continua essa conversa por aqui.",
        )
    else:
        await channel.send_text(inbound.from_number, "Não entendi essa opção." + _MENU_TEXTO)


async def _processar_mensagem_do_advogado(
    session: Session, tenant_id: uuid.UUID, inbound: InboundMessage
) -> None:
    if inbound.text.strip().lower() != _COMANDO_REATIVAR_IA:
        return
    estado = _obter_ou_criar_conversa_estado(session, tenant_id, inbound.from_number)
    estado.atendimento_humano_desde = None
    session.commit()
    logger.info("ia_reativada", tenant_id=str(tenant_id), numero=inbound.from_number)


def _obter_ou_criar_conversa_estado(
    session: Session, tenant_id: uuid.UUID, numero: str
) -> ConversaEstado:
    estado = session.scalar(
        select(ConversaEstado).where(
            ConversaEstado.tenant_id == tenant_id, ConversaEstado.whatsapp_numero == numero
        )
    )
    if estado is None:
        estado = ConversaEstado(tenant_id=tenant_id, whatsapp_numero=numero)
        session.add(estado)
        session.flush()
    return estado


def _saudou_hoje(ultima_saudacao_em: datetime | None) -> bool:
    if ultima_saudacao_em is None:
        return False
    return ultima_saudacao_em.astimezone(UTC).date() == datetime.now(UTC).date()


async def _enviar_saudacao(
    session: Session, channel: ChannelProvider, tenant_id: uuid.UUID, numero: str
) -> None:
    cliente = session.scalar(
        select(Cliente).where(Cliente.tenant_id == tenant_id, Cliente.whatsapp_numero == numero)
    )
    advogado = _advogado_do_cliente(session, cliente) if cliente is not None else None

    if advogado is not None:
        oab = f" (OAB {advogado.oab})" if advogado.oab else ""
        saudacao = (
            f"Olá! Sou o assistente de IA do escritório, em conjunto com {advogado.nome}{oab}."
        )
    else:
        saudacao = "Olá! Sou o assistente de IA do escritório e estou aqui para ajudar."

    await channel.send_text(numero, saudacao + _MENU_TEXTO)


def _advogado_do_cliente(session: Session, cliente: Cliente) -> Advogado | None:
    processo = session.scalar(
        select(Processo)
        .where(Processo.cliente_id == cliente.id, Processo.advogado_responsavel_id.is_not(None))
        .limit(1)
    )
    if processo is None or processo.advogado_responsavel_id is None:
        return None
    return session.get(Advogado, processo.advogado_responsavel_id)


async def _responder_consulta_processo(
    session: Session, channel: ChannelProvider, tenant_id: uuid.UUID, numero: str
) -> None:
    cliente = session.scalar(
        select(Cliente).where(Cliente.tenant_id == tenant_id, Cliente.whatsapp_numero == numero)
    )
    if cliente is None:
        # CLAUDE.md §4.6: número não vinculado nunca recebe informação processual.
        await channel.send_text(
            numero,
            "Esse número ainda não está vinculado a nenhum processo em nosso sistema. "
            "Entre em contato com o escritório para vincularmos.",
        )
        return

    processos = session.scalars(select(Processo).where(Processo.cliente_id == cliente.id)).all()
    if not processos:
        await channel.send_text(
            numero, "Não encontramos nenhum processo vinculado ao seu cadastro."
        )
        return

    partes = [_resumo_processo(session, processo) for processo in processos]
    await channel.send_text(numero, "\n\n".join(partes))


def _resumo_processo(session: Session, processo: Processo) -> str:
    movimento = session.scalar(
        select(Movimento)
        .where(Movimento.processo_id == processo.id, Movimento.relevante.is_(True))
        .order_by(Movimento.data.desc(), Movimento.criado_em.desc())
        .limit(1)
    )
    if movimento is not None and movimento.resumo:
        return f"Processo {processo.numero}:\n{movimento.resumo}"
    return f"Processo {processo.numero}: ainda não há atualização registrada."
