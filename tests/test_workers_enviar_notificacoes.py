import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.channels.base import InboundMessage, MessageId
from app.db.models import Cliente, Movimento, Processo, Tenant
from app.db.rls import definir_tenant
from app.services.pipeline_resumo import DecisaoEnvio
from app.workers.enviar_notificacoes import enviar_notificacoes_do_tenant


class _ChannelFake:
    def __init__(self, falha_para: set[str] | None = None) -> None:
        self.enviados: list[tuple[str, str]] = []
        self._falha_para = falha_para or set()

    async def send_text(self, to: str, text: str) -> MessageId:
        if to in self._falha_para:
            raise RuntimeError("uazapi indisponível")
        self.enviados.append((to, text))
        return "MSG-FAKE"

    async def send_template(self, to: str, template: str, params: dict[str, str]) -> MessageId:
        raise NotImplementedError

    def parse_webhook(self, payload: dict[str, object]) -> InboundMessage:
        raise NotImplementedError

    def verify_signature(self, payload: bytes, headers: dict[str, str]) -> bool:
        raise NotImplementedError


def _criar_movimento(
    session: Session,
    *,
    decisao: DecisaoEnvio,
    resumo: str | None,
    enviado_em: datetime | None = None,
    numero: str = "5511999997777",
) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = Tenant(nome="Escritorio Teste", plano="solo")
    session.add(tenant)
    session.flush()
    definir_tenant(session, tenant.id)
    cliente = Cliente(tenant_id=tenant.id, nome="Cliente Teste", whatsapp_numero=numero)
    session.add(cliente)
    session.flush()
    processo = Processo(
        tenant_id=tenant.id,
        cliente_id=cliente.id,
        numero="0000832-35.2018.4.01.3202",
        tribunal_alias="trf1",
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
        resumo=resumo,
        guardrail_passou=True,
        decisao=decisao.value,
        enviado_em=enviado_em,
    )
    session.add(movimento)
    session.flush()
    session.commit()
    return movimento.id, tenant.id


async def test_envia_movimento_auto_send_pendente_e_marca_enviado_em(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        movimento_id, tenant_id = _criar_movimento(
            session, decisao=DecisaoEnvio.AUTO_SEND, resumo="Resumo pronto para envio."
        )

        await enviar_notificacoes_do_tenant(session, channel, tenant_id)

    assert channel.enviados == [("5511999997777", "Resumo pronto para envio.")]

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        movimento = session.get(Movimento, movimento_id)
        assert movimento is not None
        assert movimento.enviado_em is not None


async def test_nao_reenvia_movimento_ja_enviado(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        _, tenant_id = _criar_movimento(
            session,
            decisao=DecisaoEnvio.AUTO_SEND,
            resumo="Já foi enviado antes.",
            enviado_em=datetime.now(UTC),
        )

        await enviar_notificacoes_do_tenant(session, channel, tenant_id)

    assert channel.enviados == []


async def test_nao_envia_movimento_needs_approval(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        _, tenant_id = _criar_movimento(
            session, decisao=DecisaoEnvio.NEEDS_APPROVAL, resumo="Precisa de aprovação humana."
        )

        await enviar_notificacoes_do_tenant(session, channel, tenant_id)

    assert channel.enviados == []


async def test_isola_falha_de_uma_notificacao_sem_derrubar_as_demais(db_engine: Engine) -> None:
    channel = _ChannelFake(falha_para={"5511111110000"})
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = Tenant(nome="Escritorio Multi", plano="escritorio")
        session.add(tenant)
        session.flush()
        definir_tenant(session, tenant.id)

        def _movimento(numero: str) -> Movimento:
            cliente = Cliente(tenant_id=tenant.id, nome=f"Cliente {numero}", whatsapp_numero=numero)
            session.add(cliente)
            session.flush()
            processo = Processo(
                tenant_id=tenant.id,
                cliente_id=cliente.id,
                numero=f"000{numero[-4:]}-11.2024.8.26.0100",
                tribunal_alias="tjsp",
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
                resumo=f"Resumo para {numero}",
                guardrail_passou=True,
                decisao=DecisaoEnvio.AUTO_SEND.value,
            )
            session.add(movimento)
            session.flush()
            return movimento

        movimento_falha = _movimento("5511111110000")
        movimento_ok = _movimento("5511222220000")
        movimento_falha_id = movimento_falha.id
        movimento_ok_id = movimento_ok.id
        session.commit()
        # capturado antes: enviar_notificacoes_do_tenant faz rollback()
        # internamente (isolamento por notificação), o que expira os objetos
        # ORM da sessão — mesmo motivo documentado em sync_processual.py.
        tenant_id = tenant.id

        await enviar_notificacoes_do_tenant(session, channel, tenant_id)

    assert channel.enviados == [("5511222220000", "Resumo para 5511222220000")]

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        falha = session.get(Movimento, movimento_falha_id)
        ok = session.get(Movimento, movimento_ok_id)
        assert falha is not None and falha.enviado_em is None
        assert ok is not None and ok.enviado_em is not None
