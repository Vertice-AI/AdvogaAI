import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.channels.base import InboundMessage, MessageId
from app.db.models import (
    Advogado,
    Cliente,
    ConversaEstado,
    Movimento,
    Processo,
    SolicitacaoVinculo,
    Tenant,
)
from app.db.rls import definir_tenant
from app.services.aprovacoes import advogado_por_numero, codigo_curto, processar_comando_advogado
from app.services.pipeline_resumo import DecisaoEnvio

_NUMERO_ADVOGADO = "5511988887777"


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


def _inbound(text: str, numero: str = _NUMERO_ADVOGADO) -> InboundMessage:
    return InboundMessage(
        from_number=numero,
        text=text,
        message_id="m1",
        timestamp=datetime.now(UTC),
        from_me=False,
    )


def _criar_cenario(session: Session) -> tuple[uuid.UUID, Advogado, uuid.UUID]:
    tenant = Tenant(nome="Escritorio Teste", plano="solo")
    session.add(tenant)
    session.flush()
    definir_tenant(session, tenant.id)
    advogado = Advogado(
        tenant_id=tenant.id, nome="Dra. Ana", area_atuacao="Cível", whatsapp_numero=_NUMERO_ADVOGADO
    )
    session.add(advogado)
    session.flush()
    cliente = Cliente(tenant_id=tenant.id, nome="Cliente Teste", whatsapp_numero="5511999997777")
    session.add(cliente)
    session.flush()
    processo = Processo(
        tenant_id=tenant.id,
        cliente_id=cliente.id,
        numero="0000832-35.2018.4.01.3202",
        tribunal_alias="trf1",
        advogado_responsavel_id=advogado.id,
    )
    session.add(processo)
    session.flush()
    movimento = Movimento(
        tenant_id=tenant.id,
        processo_id=processo.id,
        data=date(2026, 7, 10),
        tipo="Decisão",
        texto_origem="Defiro o pedido.",
        relevante=True,
        resumo="Resumo pendente de aprovação.",
        guardrail_passou=True,
        decisao=DecisaoEnvio.NEEDS_APPROVAL.value,
    )
    session.add(movimento)
    session.flush()
    session.commit()
    # definir_tenant é SET LOCAL (só vale pra transação atual) — o commit
    # acima encerrou a transação anterior, então redefine pra quem chamar
    # em seguida (advogado_por_numero/processar_comando_advogado direto,
    # sem passar por processar_mensagem, que já faz isso no topo).
    definir_tenant(session, tenant.id)
    return tenant.id, advogado, movimento.id


def test_codigo_curto_e_deterministico_e_com_6_caracteres() -> None:
    movimento_id = uuid.uuid4()

    assert len(codigo_curto(movimento_id)) == 6
    assert codigo_curto(movimento_id) == codigo_curto(movimento_id)


async def test_advogado_por_numero_encontra_cadastrado(db_engine: Engine) -> None:
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, advogado, _ = _criar_cenario(session)

        encontrado = advogado_por_numero(session, tenant_id, _NUMERO_ADVOGADO)

    assert encontrado is not None
    assert encontrado.id == advogado.id


async def test_advogado_por_numero_nao_encontra_numero_desconhecido(db_engine: Engine) -> None:
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, _, _ = _criar_cenario(session)

        assert advogado_por_numero(session, tenant_id, "5511000000000") is None


async def test_aprovar_com_codigo_correto_muda_decisao_para_auto_send(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, advogado, movimento_id = _criar_cenario(session)
        codigo = codigo_curto(movimento_id)

        await processar_comando_advogado(
            session, channel, tenant_id, advogado, _inbound(f"aprovar {codigo}")
        )

    assert "Aprovado" in channel.enviados[0][1]

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        movimento = session.get(Movimento, movimento_id)
        assert movimento is not None
        assert movimento.decisao == DecisaoEnvio.AUTO_SEND.value


async def test_rejeitar_com_codigo_correto_muda_decisao_para_blocked(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, advogado, movimento_id = _criar_cenario(session)
        codigo = codigo_curto(movimento_id)

        await processar_comando_advogado(
            session, channel, tenant_id, advogado, _inbound(f"rejeitar {codigo}")
        )

    assert "Rejeitado" in channel.enviados[0][1]

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        movimento = session.get(Movimento, movimento_id)
        assert movimento is not None
        assert movimento.decisao == DecisaoEnvio.BLOCKED.value


async def test_codigo_incorreto_nao_altera_nada(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, advogado, movimento_id = _criar_cenario(session)

        await processar_comando_advogado(
            session, channel, tenant_id, advogado, _inbound("aprovar ZZZZZZ")
        )

    assert "Não encontrei" in channel.enviados[0][1]

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        movimento = session.get(Movimento, movimento_id)
        assert movimento is not None
        assert movimento.decisao == DecisaoEnvio.NEEDS_APPROVAL.value


async def test_comando_sem_codigo_pede_codigo(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, advogado, _ = _criar_cenario(session)

        await processar_comando_advogado(session, channel, tenant_id, advogado, _inbound("aprovar"))

    assert "código" in channel.enviados[0][1].lower()


async def test_comando_desconhecido_pede_para_repetir(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, advogado, _ = _criar_cenario(session)

        await processar_comando_advogado(
            session, channel, tenant_id, advogado, _inbound("oi tudo bem")
        )

    assert "Não entendi" in channel.enviados[0][1]


_NUMERO_SOLICITANTE = "5511977776666"


def _criar_cenario_vinculo(
    session: Session, *, numero_processo_informado: str | None = "0000832-35.2018.4.01.3202"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Retorna (tenant_id, cliente_id, solicitacao_id)."""
    tenant = Tenant(nome="Escritorio Teste", plano="solo")
    session.add(tenant)
    session.flush()
    definir_tenant(session, tenant.id)
    session.add(
        Advogado(
            tenant_id=tenant.id,
            nome="Dra. Ana",
            area_atuacao="Cível",
            whatsapp_numero=_NUMERO_ADVOGADO,
        )
    )
    cliente = Cliente(tenant_id=tenant.id, nome="Cliente Antigo", whatsapp_numero="5511900000000")
    session.add(cliente)
    session.flush()
    session.add(
        Processo(
            tenant_id=tenant.id,
            cliente_id=cliente.id,
            numero="0000832-35.2018.4.01.3202",
            tribunal_alias="trf1",
        )
    )
    session.add(
        ConversaEstado(
            tenant_id=tenant.id,
            whatsapp_numero=_NUMERO_SOLICITANTE,
            ultima_saudacao_em=datetime.now(UTC),
            atendimento_humano_desde=datetime.now(UTC),
        )
    )
    solicitacao = SolicitacaoVinculo(
        tenant_id=tenant.id,
        whatsapp_numero=_NUMERO_SOLICITANTE,
        nome_informado="João da Silva",
        cpf_informado="12345678900",
        numero_processo_informado=numero_processo_informado,
    )
    session.add(solicitacao)
    session.flush()
    session.commit()
    definir_tenant(session, tenant.id)
    return tenant.id, cliente.id, solicitacao.id


def _advogado_de(session: Session, tenant_id: uuid.UUID) -> Advogado:
    encontrado = advogado_por_numero(session, tenant_id, _NUMERO_ADVOGADO)
    assert encontrado is not None
    return encontrado


async def test_vincular_com_processo_correto_vincula_cliente_e_libera_solicitante(
    db_engine: Engine,
) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, cliente_id, solicitacao_id = _criar_cenario_vinculo(session)
        advogado = _advogado_de(session, tenant_id)
        codigo = codigo_curto(solicitacao_id)

        await processar_comando_advogado(
            session,
            channel,
            tenant_id,
            advogado,
            _inbound(f"vincular {codigo}", numero=_NUMERO_ADVOGADO),
        )

    mensagem_advogado = next(t for d, t in channel.enviados if d == _NUMERO_ADVOGADO)
    assert "Vinculado" in mensagem_advogado
    assert "substituiu 5511900000000" in mensagem_advogado
    mensagem_solicitante = next(t for d, t in channel.enviados if d == _NUMERO_SOLICITANTE)
    assert "vinculado ao processo" in mensagem_solicitante

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        cliente = session.get(Cliente, cliente_id)
        assert cliente is not None
        assert cliente.whatsapp_numero == _NUMERO_SOLICITANTE
        assert cliente.verificado_em is not None

        solicitacao = session.get(SolicitacaoVinculo, solicitacao_id)
        assert solicitacao is not None
        assert solicitacao.resolvida_em is not None

        estado = session.scalar(
            select(ConversaEstado).where(
                ConversaEstado.tenant_id == tenant_id,
                ConversaEstado.whatsapp_numero == _NUMERO_SOLICITANTE,
            )
        )
        assert estado is not None
        assert estado.atendimento_humano_desde is None


async def test_descartar_marca_resolvida_e_avisa_solicitante(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, cliente_id, solicitacao_id = _criar_cenario_vinculo(session)
        advogado = _advogado_de(session, tenant_id)
        codigo = codigo_curto(solicitacao_id)

        await processar_comando_advogado(
            session,
            channel,
            tenant_id,
            advogado,
            _inbound(f"descartar {codigo}", numero=_NUMERO_ADVOGADO),
        )

    assert "Descartado" in next(t for d, t in channel.enviados if d == _NUMERO_ADVOGADO)
    assert "Não conseguimos confirmar" in next(
        t for d, t in channel.enviados if d == _NUMERO_SOLICITANTE
    )

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        solicitacao = session.get(SolicitacaoVinculo, solicitacao_id)
        assert solicitacao is not None
        assert solicitacao.resolvida_em is not None
        cliente = session.get(Cliente, cliente_id)
        assert cliente is not None
        assert cliente.whatsapp_numero == "5511900000000"  # não mudou


async def test_vincular_sem_processo_informado_nao_vincula(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, cliente_id, solicitacao_id = _criar_cenario_vinculo(
            session, numero_processo_informado=None
        )
        advogado = _advogado_de(session, tenant_id)
        codigo = codigo_curto(solicitacao_id)

        await processar_comando_advogado(
            session,
            channel,
            tenant_id,
            advogado,
            _inbound(f"vincular {codigo}", numero=_NUMERO_ADVOGADO),
        )

    assert "não informou número de processo" in channel.enviados[0][1]

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        solicitacao = session.get(SolicitacaoVinculo, solicitacao_id)
        assert solicitacao is not None
        assert solicitacao.resolvida_em is None
        cliente = session.get(Cliente, cliente_id)
        assert cliente is not None
        assert cliente.whatsapp_numero == "5511900000000"


async def test_vincular_processo_nao_cadastrado(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, _, solicitacao_id = _criar_cenario_vinculo(
            session, numero_processo_informado="9999999-99.2024.8.26.0100"
        )
        advogado = _advogado_de(session, tenant_id)
        codigo = codigo_curto(solicitacao_id)

        await processar_comando_advogado(
            session,
            channel,
            tenant_id,
            advogado,
            _inbound(f"vincular {codigo}", numero=_NUMERO_ADVOGADO),
        )

    assert "Não encontrei o processo" in channel.enviados[0][1]


async def test_vincular_codigo_desconhecido(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, _, _ = _criar_cenario_vinculo(session)
        advogado = _advogado_de(session, tenant_id)

        await processar_comando_advogado(
            session,
            channel,
            tenant_id,
            advogado,
            _inbound("vincular ZZZZZZ", numero=_NUMERO_ADVOGADO),
        )

    assert "Não encontrei nenhuma solicitação pendente" in channel.enviados[0][1]
