import json
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.agents.atendimento import AtendimentoAgent
from app.channels.base import InboundMessage, MessageId
from app.db.models import (
    Advogado,
    Cliente,
    ConversaEstado,
    Movimento,
    Processo,
    SolicitacaoAtendimento,
    SolicitacaoVinculo,
    Tenant,
)
from app.db.rls import definir_tenant
from app.services.atendimento import processar_mensagem

_NUMERO = "5511999997777"


class _ChannelFake:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send_text(self, to: str, text: str) -> MessageId:
        self.enviados.append((to, text))
        return "MSG-FAKE"

    async def send_template(self, to: str, template: str, params: dict[str, str]) -> MessageId:
        raise NotImplementedError

    def parse_webhook(self, payload: dict[str, object]) -> InboundMessage:
        raise NotImplementedError

    def verify_signature(self, payload: bytes, headers: dict[str, str]) -> bool:
        raise NotImplementedError


class _AnthropicClientFake:
    def __init__(self, intencao: str) -> None:
        self._intencao = intencao

    async def create_message(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        return json.dumps({"intencao": self._intencao})


def _agent(intencao: str = "outro") -> AtendimentoAgent:
    return AtendimentoAgent(_AnthropicClientFake(intencao), haiku_model="haiku-fake")


def _inbound(text: str, *, from_me: bool = False, numero: str = _NUMERO) -> InboundMessage:
    return InboundMessage(
        from_number=numero,
        text=text,
        message_id="msg-1",
        timestamp=datetime.now(UTC),
        from_me=from_me,
    )


def _criar_tenant(session: Session) -> Tenant:
    tenant = Tenant(nome="Escritorio Teste", plano="solo")
    session.add(tenant)
    session.flush()
    definir_tenant(session, tenant.id)
    return tenant


def _criar_estado_ja_saudado(
    session: Session, tenant_id: uuid.UUID, numero: str = _NUMERO, *, humano: bool = False
) -> None:
    session.add(
        ConversaEstado(
            tenant_id=tenant_id,
            whatsapp_numero=numero,
            ultima_saudacao_em=datetime.now(UTC),
            atendimento_humano_desde=datetime.now(UTC) if humano else None,
        )
    )
    session.commit()


async def test_primeiro_contato_numero_desconhecido_envia_saudacao_generica(
    db_engine: Engine,
) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        session.commit()

        await processar_mensagem(session, channel, _agent(), tenant_id, _inbound("oi"))

    assert len(channel.enviados) == 1
    destino, texto = channel.enviados[0]
    assert destino == _NUMERO
    assert "assistente de IA do escritório" in texto
    assert "Consultar atualização" in texto

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        estado = session.scalar(
            select(ConversaEstado).where(
                ConversaEstado.tenant_id == tenant_id, ConversaEstado.whatsapp_numero == _NUMERO
            )
        )
        assert estado is not None
        assert estado.ultima_saudacao_em is not None


async def test_primeiro_contato_cliente_conhecido_personaliza_saudacao(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        advogado = Advogado(
            tenant_id=tenant_id, nome="Dra. Ana Souza", oab="123456", area_atuacao="Cível"
        )
        session.add(advogado)
        session.flush()
        cliente = Cliente(tenant_id=tenant_id, nome="Cliente Teste", whatsapp_numero=_NUMERO)
        session.add(cliente)
        session.flush()
        processo = Processo(
            tenant_id=tenant_id,
            cliente_id=cliente.id,
            numero="0000832-35.2018.4.01.3202",
            tribunal_alias="trf1",
            advogado_responsavel_id=advogado.id,
        )
        session.add(processo)
        session.commit()

        await processar_mensagem(session, channel, _agent(), tenant_id, _inbound("oi"))

    texto = channel.enviados[0][1]
    assert "Dra. Ana Souza" in texto
    assert "OAB 123456" in texto


async def test_consulta_processo_com_movimento_responde_resumo(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        cliente = Cliente(tenant_id=tenant_id, nome="Cliente Teste", whatsapp_numero=_NUMERO)
        session.add(cliente)
        session.flush()
        processo = Processo(
            tenant_id=tenant_id,
            cliente_id=cliente.id,
            numero="0000832-35.2018.4.01.3202",
            tribunal_alias="trf1",
        )
        session.add(processo)
        session.flush()
        session.add(
            Movimento(
                tenant_id=tenant_id,
                processo_id=processo.id,
                data=date(2026, 7, 10),
                tipo="Decisão",
                texto_origem="Defiro o pedido.",
                relevante=True,
                resumo="Em 10/07/2026, o juiz deferiu o pedido.",
                guardrail_passou=True,
                decisao="auto_send",
            )
        )
        _criar_estado_ja_saudado(session, tenant_id)

        await processar_mensagem(
            session, channel, _agent("consultar_processo"), tenant_id, _inbound("2")
        )

    destino, texto = channel.enviados[0]
    assert destino == _NUMERO
    assert "0000832-35.2018.4.01.3202" in texto
    assert "Em 10/07/2026, o juiz deferiu o pedido." in texto


async def test_consulta_processo_sem_movimento_avisa_sem_atualizacao(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        cliente = Cliente(tenant_id=tenant_id, nome="Cliente Teste", whatsapp_numero=_NUMERO)
        session.add(cliente)
        session.flush()
        session.add(
            Processo(
                tenant_id=tenant_id,
                cliente_id=cliente.id,
                numero="0000111-11.2024.8.26.0100",
                tribunal_alias="tjsp",
            )
        )
        _criar_estado_ja_saudado(session, tenant_id)

        await processar_mensagem(
            session, channel, _agent("consultar_processo"), tenant_id, _inbound("1")
        )

    assert "ainda não há atualização registrada" in channel.enviados[0][1]


async def test_consulta_processo_numero_nao_vinculado_nunca_da_info_e_inicia_coleta(
    db_engine: Engine,
) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        _criar_estado_ja_saudado(session, tenant_id)

        await processar_mensagem(
            session, channel, _agent("consultar_processo"), tenant_id, _inbound("1")
        )

    texto = channel.enviados[0][1]
    assert "não está vinculado" in texto
    assert "Nome:" in texto and "CPF:" in texto

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        estado = session.scalar(
            select(ConversaEstado).where(
                ConversaEstado.tenant_id == tenant_id, ConversaEstado.whatsapp_numero == _NUMERO
            )
        )
        assert estado is not None
        assert estado.aguardando_dados_vinculo is True


async def test_dados_vinculo_validos_criam_solicitacao_e_notificam_advogados(
    db_engine: Engine,
) -> None:
    channel = _ChannelFake()
    numero_advogado = "5511988887777"
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        session.add(
            Advogado(
                tenant_id=tenant_id,
                nome="Dra. Ana",
                area_atuacao="Cível",
                whatsapp_numero=numero_advogado,
            )
        )
        session.add(
            ConversaEstado(
                tenant_id=tenant_id,
                whatsapp_numero=_NUMERO,
                ultima_saudacao_em=datetime.now(UTC),
                aguardando_dados_vinculo=True,
            )
        )
        session.commit()

        texto_dados = (
            "Nome: João da Silva\nCPF: 123.456.789-00\nProcesso: 0000832-35.2018.4.01.3202"
        )
        await processar_mensagem(session, channel, _agent(), tenant_id, _inbound(texto_dados))

    assert any(destino == numero_advogado for destino, _ in channel.enviados)
    mensagem_advogado = next(
        texto for destino, texto in channel.enviados if destino == numero_advogado
    )
    assert "João da Silva" in mensagem_advogado
    assert "12345678900" in mensagem_advogado
    assert "0000832-35.2018.4.01.3202" in mensagem_advogado
    assert any(destino == _NUMERO for destino, _ in channel.enviados)

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        estado = session.scalar(
            select(ConversaEstado).where(
                ConversaEstado.tenant_id == tenant_id, ConversaEstado.whatsapp_numero == _NUMERO
            )
        )
        assert estado is not None
        assert estado.aguardando_dados_vinculo is False
        assert estado.atendimento_humano_desde is not None

        solicitacao = session.scalar(
            select(SolicitacaoVinculo).where(SolicitacaoVinculo.tenant_id == tenant_id)
        )
        assert solicitacao is not None
        assert solicitacao.nome_informado == "João da Silva"
        assert solicitacao.cpf_informado == "12345678900"
        assert solicitacao.numero_processo_informado == "0000832-35.2018.4.01.3202"


async def test_dados_vinculo_invalidos_pede_para_reenviar_e_continua_aguardando(
    db_engine: Engine,
) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        session.add(
            ConversaEstado(
                tenant_id=tenant_id,
                whatsapp_numero=_NUMERO,
                ultima_saudacao_em=datetime.now(UTC),
                aguardando_dados_vinculo=True,
            )
        )
        session.commit()

        await processar_mensagem(session, channel, _agent(), tenant_id, _inbound("oi, sou eu"))

    assert "Não consegui identificar" in channel.enviados[0][1]

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        estado = session.scalar(
            select(ConversaEstado).where(
                ConversaEstado.tenant_id == tenant_id, ConversaEstado.whatsapp_numero == _NUMERO
            )
        )
        assert estado is not None
        assert estado.aguardando_dados_vinculo is True
        assert estado.atendimento_humano_desde is None


async def test_consulta_processo_lista_todos_quando_ha_mais_de_um(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        cliente = Cliente(tenant_id=tenant_id, nome="Cliente Teste", whatsapp_numero=_NUMERO)
        session.add(cliente)
        session.flush()
        session.add_all(
            [
                Processo(
                    tenant_id=tenant_id,
                    cliente_id=cliente.id,
                    numero="0000111-11.2024.8.26.0100",
                    tribunal_alias="tjsp",
                ),
                Processo(
                    tenant_id=tenant_id,
                    cliente_id=cliente.id,
                    numero="0000222-22.2024.8.26.0100",
                    tribunal_alias="tjsp",
                ),
            ]
        )
        _criar_estado_ja_saudado(session, tenant_id)

        await processar_mensagem(
            session, channel, _agent("consultar_processo"), tenant_id, _inbound("1")
        )

    texto = channel.enviados[0][1]
    assert "0000111-11.2024.8.26.0100" in texto
    assert "0000222-22.2024.8.26.0100" in texto


async def test_falar_advogado_ativa_modo_humano(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        _criar_estado_ja_saudado(session, tenant_id)

        await processar_mensagem(
            session, channel, _agent("falar_advogado"), tenant_id, _inbound("2")
        )

    assert len(channel.enviados) == 1
    assert "advogado" in channel.enviados[0][1].lower()

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        estado = session.scalar(
            select(ConversaEstado).where(
                ConversaEstado.tenant_id == tenant_id, ConversaEstado.whatsapp_numero == _NUMERO
            )
        )
        assert estado is not None
        assert estado.atendimento_humano_desde is not None


_NUMERO_ADVOGADO = "5511988887777"


async def test_falar_advogado_disponivel_notifica_advogado_com_resumo(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        advogado = Advogado(
            tenant_id=tenant_id,
            nome="Dra. Ana",
            area_atuacao="Cível",
            whatsapp_numero=_NUMERO_ADVOGADO,
            disponivel=True,
        )
        session.add(advogado)
        session.flush()
        advogado_id = advogado.id
        cliente = Cliente(tenant_id=tenant_id, nome="Cliente Teste", whatsapp_numero=_NUMERO)
        session.add(cliente)
        session.flush()
        session.add(
            Processo(
                tenant_id=tenant_id,
                cliente_id=cliente.id,
                numero="0000832-35.2018.4.01.3202",
                tribunal_alias="trf1",
                advogado_responsavel_id=advogado_id,
            )
        )
        session.commit()
        definir_tenant(session, tenant_id)
        _criar_estado_ja_saudado(session, tenant_id)

        await processar_mensagem(
            session,
            channel,
            _agent("falar_advogado"),
            tenant_id,
            _inbound("quero falar sobre meu processo"),
        )

    mensagem_advogado = next(t for d, t in channel.enviados if d == _NUMERO_ADVOGADO)
    assert "0000832-35.2018.4.01.3202" in mensagem_advogado
    assert "Cliente Teste" in mensagem_advogado
    assert "quero falar sobre meu processo" in mensagem_advogado

    mensagem_cliente = next(t for d, t in channel.enviados if d == _NUMERO)
    assert "continua essa conversa" in mensagem_cliente

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        solicitacao = session.scalar(
            select(SolicitacaoAtendimento).where(SolicitacaoAtendimento.tenant_id == tenant_id)
        )
        assert solicitacao is not None
        assert solicitacao.status == "notificado"
        assert solicitacao.advogado_designado_id == advogado_id
        assert solicitacao.notificado_em is not None


async def test_falar_advogado_indisponivel_enfileira_sem_notificar(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        advogado = Advogado(
            tenant_id=tenant_id,
            nome="Dr. João",
            area_atuacao="Cível",
            whatsapp_numero=_NUMERO_ADVOGADO,
            disponivel=False,
        )
        session.add(advogado)
        session.commit()
        definir_tenant(session, tenant_id)
        _criar_estado_ja_saudado(session, tenant_id)

        await processar_mensagem(
            session, channel, _agent("falar_advogado"), tenant_id, _inbound("2")
        )

    assert len(channel.enviados) == 1
    assert channel.enviados[0][0] == _NUMERO
    assert "indisponível" in channel.enviados[0][1].lower()

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        solicitacao = session.scalar(
            select(SolicitacaoAtendimento).where(SolicitacaoAtendimento.tenant_id == tenant_id)
        )
        assert solicitacao is not None
        assert solicitacao.status == "aguardando"
        assert solicitacao.notificado_em is None


async def test_modo_humano_ativo_ia_fica_em_silencio(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        _criar_estado_ja_saudado(session, tenant_id, humano=True)

        await processar_mensagem(
            session, channel, _agent("consultar_processo"), tenant_id, _inbound("oi, tudo bem?")
        )

    assert channel.enviados == []


async def test_comando_ia_do_proprio_numero_reativa_ia(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        _criar_estado_ja_saudado(session, tenant_id, humano=True)

        await processar_mensagem(
            session, channel, _agent(), tenant_id, _inbound("/ia", from_me=True)
        )

    assert channel.enviados == []  # comando não gera resposta ao cliente

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        estado = session.scalar(
            select(ConversaEstado).where(
                ConversaEstado.tenant_id == tenant_id, ConversaEstado.whatsapp_numero == _NUMERO
            )
        )
        assert estado is not None
        assert estado.atendimento_humano_desde is None


async def test_mensagem_do_advogado_sem_comando_e_ignorada(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        _criar_estado_ja_saudado(session, tenant_id, humano=True)

        await processar_mensagem(
            session, channel, _agent(), tenant_id, _inbound("beleza, já te ajudo", from_me=True)
        )

    assert channel.enviados == []

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        estado = session.scalar(
            select(ConversaEstado).where(
                ConversaEstado.tenant_id == tenant_id, ConversaEstado.whatsapp_numero == _NUMERO
            )
        )
        assert estado is not None
        assert estado.atendimento_humano_desde is not None  # continua em modo humano


async def test_mensagem_do_numero_do_advogado_vira_comando_nao_conversa_de_cliente(
    db_engine: Engine,
) -> None:
    channel = _ChannelFake()
    numero_advogado = "5511988887777"
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = _criar_tenant(session)
        tenant_id = tenant.id
        session.add(
            Advogado(
                tenant_id=tenant_id,
                nome="Dra. Ana",
                area_atuacao="Cível",
                whatsapp_numero=numero_advogado,
            )
        )
        session.commit()

        # Primeiro contato desse número: se fosse tratado como cliente, cairia
        # na saudação. Sendo advogado cadastrado, cai direto no comando.
        await processar_mensagem(
            session, channel, _agent(), tenant_id, _inbound("oi", numero=numero_advogado)
        )

    assert len(channel.enviados) == 1
    assert "Não entendi" in channel.enviados[0][1]  # "oi" não é aprovar/rejeitar

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        estado = session.scalar(
            select(ConversaEstado).where(
                ConversaEstado.tenant_id == tenant_id,
                ConversaEstado.whatsapp_numero == numero_advogado,
            )
        )
        assert estado is None  # nenhuma saudação/menu de cliente foi criada
