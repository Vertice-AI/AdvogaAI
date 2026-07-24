import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.channels.base import InboundMessage, MessageId
from app.db.models import Advogado, Cliente, Movimento, Processo, Tenant
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
