import re
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.atendimento import AtendimentoAgent, Intencao
from app.channels.base import ChannelProvider, InboundMessage
from app.db.models import Advogado, Cliente, ConversaEstado, Movimento, Processo, SolicitacaoVinculo
from app.db.rls import definir_tenant
from app.services.aprovacoes import advogado_por_numero, processar_comando_advogado

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

    advogado = advogado_por_numero(session, tenant_id, inbound.from_number)
    if advogado is not None:
        # Número pessoal de um advogado cadastrado: trata como comando de
        # aprovação, nunca como conversa de cliente (app/services/aprovacoes.py).
        await processar_comando_advogado(session, channel, tenant_id, advogado, inbound)
        return

    estado = _obter_ou_criar_conversa_estado(session, tenant_id, inbound.from_number)

    if estado.atendimento_humano_desde is not None:
        # Regra do silêncio (CLAUDE.md §4.5): a IA não responde nada enquanto
        # um humano estiver com a conversa. Só volta com o comando /ia vindo
        # do próprio número do escritório (ver _processar_mensagem_do_advogado).
        return

    if estado.aguardando_dados_vinculo:
        # Checado antes da saudação de propósito: se a pessoa demorar dias
        # pra responder e cair numa nova saudação diária, a próxima mensagem
        # dela ainda precisa ser tratada como os dados pendentes, não como
        # uma pergunta nova — senão o fluxo trava esperando algo que nunca
        # vai ser interpretado.
        await _processar_dados_vinculo(session, channel, tenant_id, estado, inbound)
        return

    if not _saudou_hoje(estado.ultima_saudacao_em):
        await _enviar_saudacao(session, channel, tenant_id, inbound.from_number)
        estado.ultima_saudacao_em = datetime.now(UTC)
        session.commit()
        return

    intencao = await agent.classificar_intencao(inbound.text)

    if intencao == Intencao.CONSULTAR_PROCESSO:
        await _responder_consulta_processo(session, channel, tenant_id, estado, inbound.from_number)
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
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    estado: ConversaEstado,
    numero: str,
) -> None:
    cliente = session.scalar(
        select(Cliente).where(Cliente.tenant_id == tenant_id, Cliente.whatsapp_numero == numero)
    )
    if cliente is None:
        # CLAUDE.md §4.6: número não vinculado nunca recebe informação
        # processual — o agente não decide sozinho a partir de dado
        # digitado no chat. Só coleta e repassa pro advogado decidir.
        await _iniciar_coleta_dados_vinculo(session, channel, estado, numero)
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


async def _iniciar_coleta_dados_vinculo(
    session: Session, channel: ChannelProvider, estado: ConversaEstado, numero: str
) -> None:
    estado.aguardando_dados_vinculo = True
    session.commit()
    await channel.send_text(
        numero,
        "Esse número ainda não está vinculado a nenhum processo em nosso sistema.\n\n"
        "Pra te ajudarmos, envie numa mensagem só, neste formato:\n"
        "Nome: seu nome completo\n"
        "CPF: seu CPF\n"
        "Processo: número do processo (se souber)\n\n"
        "Vou repassar pro advogado confirmar e liberar o acompanhamento.",
    )


async def _processar_dados_vinculo(
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    estado: ConversaEstado,
    inbound: InboundMessage,
) -> None:
    dados = _extrair_dados_vinculo(inbound.text)
    if dados is None:
        await channel.send_text(
            inbound.from_number,
            "Não consegui identificar os dados. Envie novamente numa mensagem só, assim:\n"
            "Nome: seu nome completo\nCPF: seu CPF\nProcesso: número do processo (se souber)",
        )
        return

    nome, cpf, numero_processo = dados
    solicitacao = SolicitacaoVinculo(
        tenant_id=tenant_id,
        whatsapp_numero=inbound.from_number,
        nome_informado=nome,
        cpf_informado=cpf,
        numero_processo_informado=numero_processo,
    )
    session.add(solicitacao)
    estado.aguardando_dados_vinculo = False
    # Encaminhado pra humano: a IA fica em silêncio aqui também (mesma regra
    # do §4.5 já usada em "falar com advogado") até alguém vincular/atender.
    estado.atendimento_humano_desde = datetime.now(UTC)
    session.commit()
    # definir_tenant é SET LOCAL — o commit acima encerrou a transação que
    # tinha o tenant setado (skill escrever-com-rls-advogai). Sem isso, a
    # busca de advogados abaixo roda sem RLS satisfeita e não acha ninguém.
    definir_tenant(session, tenant_id)

    await _notificar_advogados_sobre_solicitacao(session, channel, tenant_id, solicitacao)
    await channel.send_text(
        inbound.from_number,
        "Recebi! Vou repassar pro advogado confirmar e liberar o acompanhamento. "
        "Em breve alguém entra em contato por aqui.",
    )


_CAMPOS_VINCULO = {"nome", "cpf", "processo"}


def _extrair_dados_vinculo(texto: str) -> tuple[str, str, str | None] | None:
    campos: dict[str, str] = {}
    for linha in texto.strip().splitlines():
        chave, separador, valor = linha.partition(":")
        if not separador:
            continue
        chave_normalizada = chave.strip().lower()
        if chave_normalizada in _CAMPOS_VINCULO:
            campos[chave_normalizada] = valor.strip()

    nome = campos.get("nome")
    cpf_bruto = campos.get("cpf")
    if not nome or not cpf_bruto:
        return None

    # Só formato (11 dígitos), sem dígito verificador — decisão consciente
    # de manter simples nesta fatia (nenhuma decisão automática depende
    # disso, é só o que chega pro advogado conferir).
    cpf = re.sub(r"\D", "", cpf_bruto)
    if len(cpf) != 11:
        return None

    numero_processo = campos.get("processo") or None
    return nome, cpf, numero_processo


async def _notificar_advogados_sobre_solicitacao(
    session: Session,
    channel: ChannelProvider,
    tenant_id: uuid.UUID,
    solicitacao: SolicitacaoVinculo,
) -> None:
    # Sem roteamento de verdade ainda (dor 2, CLAUDE.md §1): notifica todos
    # os advogados do tenant com WhatsApp cadastrado, não só um específico.
    advogados = session.scalars(
        select(Advogado).where(
            Advogado.tenant_id == tenant_id, Advogado.whatsapp_numero.is_not(None)
        )
    ).all()
    texto = (
        f"Nova solicitação de contato pelo WhatsApp {solicitacao.whatsapp_numero}:\n"
        f"Nome informado: {solicitacao.nome_informado}\n"
        f"CPF informado: {solicitacao.cpf_informado}\n"
        f"Processo informado: {solicitacao.numero_processo_informado or 'não informado'}\n\n"
        "Confirme se é cliente cadastrado e vincule o número, ou entre em contato diretamente."
    )
    for advogado in advogados:
        if advogado.whatsapp_numero is not None:
            await channel.send_text(advogado.whatsapp_numero, texto)
