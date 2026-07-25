import uuid

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.channels.base import InboundMessage, MessageId
from app.db.models import Advogado, SolicitacaoAtendimento, Tenant
from app.db.rls import definir_tenant
from app.services.roteamento import StatusSolicitacaoAtendimento
from app.workers.retomar_solicitacoes import retomar_solicitacoes_do_tenant


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


def _criar_advogado_com_solicitacao_presa(
    session: Session, *, numero_advogado: str, disponivel: bool
) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = Tenant(nome="Escritorio Teste", plano="solo")
    session.add(tenant)
    session.flush()
    definir_tenant(session, tenant.id)
    advogado = Advogado(
        tenant_id=tenant.id,
        nome="Dra. Ana",
        area_atuacao="Cível",
        whatsapp_numero=numero_advogado,
        disponivel=disponivel,
    )
    session.add(advogado)
    session.flush()
    solicitacao = SolicitacaoAtendimento(
        tenant_id=tenant.id,
        whatsapp_numero="5511900001111",
        advogado_designado_id=advogado.id,
        resumo_caso="Cliente quer falar com você pelo WhatsApp.\nNome: não identificado",
        status=StatusSolicitacaoAtendimento.AGUARDANDO.value,
    )
    session.add(solicitacao)
    session.commit()
    return tenant.id, solicitacao.id


async def test_retoma_solicitacao_presa_quando_advogado_disponivel(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, solicitacao_id = _criar_advogado_com_solicitacao_presa(
            session, numero_advogado="5511988887777", disponivel=True
        )

        await retomar_solicitacoes_do_tenant(session, channel, tenant_id)

    assert channel.enviados == [
        ("5511988887777", "Cliente quer falar com você pelo WhatsApp.\nNome: não identificado")
    ]
    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        solicitacao = session.get(SolicitacaoAtendimento, solicitacao_id)
        assert solicitacao is not None
        assert solicitacao.status == StatusSolicitacaoAtendimento.NOTIFICADO.value
        assert solicitacao.notificado_em is not None


async def test_nao_retoma_se_advogado_indisponivel(db_engine: Engine) -> None:
    channel = _ChannelFake()
    with Session(db_engine, expire_on_commit=False) as session:
        tenant_id, solicitacao_id = _criar_advogado_com_solicitacao_presa(
            session, numero_advogado="5511988887777", disponivel=False
        )

        await retomar_solicitacoes_do_tenant(session, channel, tenant_id)

    assert channel.enviados == []
    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        solicitacao = session.get(SolicitacaoAtendimento, solicitacao_id)
        assert solicitacao is not None
        assert solicitacao.status == StatusSolicitacaoAtendimento.AGUARDANDO.value


async def test_isola_falha_de_um_advogado_sem_derrubar_os_demais(db_engine: Engine) -> None:
    channel = _ChannelFake(falha_para={"5511111110000"})
    with Session(db_engine, expire_on_commit=False) as session:
        tenant = Tenant(nome="Escritorio Multi", plano="escritorio")
        session.add(tenant)
        session.flush()
        definir_tenant(session, tenant.id)

        def _advogado_com_solicitacao(numero: str) -> uuid.UUID:
            advogado = Advogado(
                tenant_id=tenant.id,
                nome=f"Advogado {numero}",
                area_atuacao="Cível",
                whatsapp_numero=numero,
                disponivel=True,
            )
            session.add(advogado)
            session.flush()
            solicitacao = SolicitacaoAtendimento(
                tenant_id=tenant.id,
                whatsapp_numero="5511900001111",
                advogado_designado_id=advogado.id,
                resumo_caso=f"Solicitação para {numero}",
                status=StatusSolicitacaoAtendimento.AGUARDANDO.value,
            )
            session.add(solicitacao)
            session.flush()
            return solicitacao.id

        solicitacao_falha_id = _advogado_com_solicitacao("5511111110000")
        solicitacao_ok_id = _advogado_com_solicitacao("5511222220000")
        session.commit()
        tenant_id = tenant.id

        await retomar_solicitacoes_do_tenant(session, channel, tenant_id)

    assert channel.enviados == [("5511222220000", "Solicitação para 5511222220000")]

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        falha = session.get(SolicitacaoAtendimento, solicitacao_falha_id)
        ok = session.get(SolicitacaoAtendimento, solicitacao_ok_id)
        assert falha is not None and falha.status == StatusSolicitacaoAtendimento.AGUARDANDO.value
        assert ok is not None and ok.status == StatusSolicitacaoAtendimento.NOTIFICADO.value
