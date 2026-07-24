import uuid

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.channels.base import InboundMessage, MessageId
from app.db.models import Advogado, Cliente, Processo, SolicitacaoAtendimento, Tenant
from app.db.rls import definir_tenant
from app.services.roteamento import StatusSolicitacaoAtendimento, rotear_solicitacao_atendimento

_NUMERO_ADVOGADO = "5511988887777"
_NUMERO_PROSPECT = "5511900001111"


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


class _ChannelQueCai:
    """Simula a UAZAPI fora do ar: send_text sempre falha."""

    async def send_text(self, to: str, text: str) -> MessageId:
        raise ConnectionError("instância UAZAPI indisponível")

    async def send_template(self, to: str, template: str, params: dict[str, str]) -> MessageId:
        raise NotImplementedError

    def parse_webhook(self, payload: dict[str, object]) -> InboundMessage:
        raise NotImplementedError

    def verify_signature(self, payload: bytes, headers: dict[str, str]) -> bool:
        raise NotImplementedError


def _criar_tenant_com_advogado(
    session: Session, *, disponivel: bool = True
) -> tuple[uuid.UUID, Advogado]:
    tenant = Tenant(nome="Escritorio Teste", plano="solo")
    session.add(tenant)
    session.flush()
    definir_tenant(session, tenant.id)
    advogado = Advogado(
        tenant_id=tenant.id,
        nome="Dra. Ana",
        area_atuacao="Cível",
        whatsapp_numero=_NUMERO_ADVOGADO,
        disponivel=disponivel,
    )
    session.add(advogado)
    session.commit()
    definir_tenant(session, tenant.id)
    return tenant.id, advogado


async def test_prospect_sem_cadastro_cai_no_fallback_do_unico_advogado(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, advogado = _criar_tenant_com_advogado(session)

        status = await rotear_solicitacao_atendimento(
            session, channel, tenant_id, _NUMERO_PROSPECT, "quero saber sobre um contrato"
        )

    assert status == StatusSolicitacaoAtendimento.NOTIFICADO
    mensagem_advogado = next(t for d, t in channel.enviados if d == _NUMERO_ADVOGADO)
    assert "não identificado" in mensagem_advogado
    assert "quero saber sobre um contrato" in mensagem_advogado

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        solicitacao = session.scalar(
            select(SolicitacaoAtendimento).where(SolicitacaoAtendimento.tenant_id == tenant_id)
        )
        assert solicitacao is not None
        assert solicitacao.cliente_id is None
        assert solicitacao.advogado_designado_id == advogado.id


async def test_processo_sem_advogado_responsavel_cai_no_fallback(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, _ = _criar_tenant_com_advogado(session)
        cliente = Cliente(
            tenant_id=tenant_id, nome="Cliente Sem Advogado", whatsapp_numero=_NUMERO_PROSPECT
        )
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
        session.commit()
        definir_tenant(session, tenant_id)

        status = await rotear_solicitacao_atendimento(
            session, channel, tenant_id, _NUMERO_PROSPECT, "quero atualização"
        )

    assert status == StatusSolicitacaoAtendimento.NOTIFICADO
    mensagem_advogado = next(t for d, t in channel.enviados if d == _NUMERO_ADVOGADO)
    assert "Cliente Sem Advogado" in mensagem_advogado


async def test_falha_no_envio_nao_marca_como_notificado(db_engine: Engine) -> None:
    channel = _ChannelQueCai()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, _ = _criar_tenant_com_advogado(session)

        try:
            await rotear_solicitacao_atendimento(
                session, channel, tenant_id, _NUMERO_PROSPECT, "quero falar"
            )
            raised = False
        except ConnectionError:
            raised = True

    assert raised

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        solicitacao = session.scalar(
            select(SolicitacaoAtendimento).where(SolicitacaoAtendimento.tenant_id == tenant_id)
        )
        assert solicitacao is not None
        assert solicitacao.status == "aguardando"
        assert solicitacao.notificado_em is None


async def test_tenant_sem_advogado_cadastrado_nao_quebra_e_nao_cria_solicitacao(
    db_engine: Engine,
) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = Tenant(nome="Escritorio Sem Advogado", plano="solo")
        session.add(tenant)
        session.flush()
        definir_tenant(session, tenant.id)
        session.commit()
        definir_tenant(session, tenant.id)
        tenant_id = tenant.id

        status = await rotear_solicitacao_atendimento(
            session, channel, tenant_id, _NUMERO_PROSPECT, "alguém aí?"
        )

    assert status is None
    assert channel.enviados == []

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        solicitacao = session.scalar(
            select(SolicitacaoAtendimento).where(SolicitacaoAtendimento.tenant_id == tenant_id)
        )
        assert solicitacao is None
