import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.channels.base import InboundMessage, MessageId
from app.db.models import Advogado, Cliente, Movimento, Processo, Tenant
from app.db.rls import definir_tenant
from app.services.aprovacoes import codigo_curto
from app.services.pipeline_resumo import DecisaoEnvio
from app.workers.solicitar_aprovacoes import solicitar_aprovacoes_do_tenant


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


def _criar_movimento_pendente(
    session: Session,
    *,
    advogado_numero: str | None = "5511988887777",
    aprovacao_solicitada_em: datetime | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = Tenant(nome="Escritorio Teste", plano="solo")
    session.add(tenant)
    session.flush()
    definir_tenant(session, tenant.id)
    advogado = Advogado(
        tenant_id=tenant.id, nome="Dra. Ana", area_atuacao="Cível", whatsapp_numero=advogado_numero
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
        aprovacao_solicitada_em=aprovacao_solicitada_em,
    )
    session.add(movimento)
    session.flush()
    session.commit()
    return movimento.id, tenant.id


async def test_solicita_aprovacao_e_marca_solicitada(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        movimento_id, tenant_id = _criar_movimento_pendente(session)

        await solicitar_aprovacoes_do_tenant(session, channel, tenant_id)

    assert len(channel.enviados) == 1
    destino, texto = channel.enviados[0]
    assert destino == "5511988887777"
    assert codigo_curto(movimento_id) in texto
    assert "0000832-35.2018.4.01.3202" in texto

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        movimento = session.get(Movimento, movimento_id)
        assert movimento is not None
        assert movimento.aprovacao_solicitada_em is not None


async def test_nao_solicita_de_novo_se_ja_solicitado(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        _, tenant_id = _criar_movimento_pendente(session, aprovacao_solicitada_em=datetime.now(UTC))

        await solicitar_aprovacoes_do_tenant(session, channel, tenant_id)

    assert channel.enviados == []


async def test_nao_solicita_sem_advogado_com_numero_cadastrado(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        _, tenant_id = _criar_movimento_pendente(session, advogado_numero=None)

        await solicitar_aprovacoes_do_tenant(session, channel, tenant_id)

    assert channel.enviados == []
