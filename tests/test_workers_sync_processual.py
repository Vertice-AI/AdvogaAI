import json
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.agents.processual import ProcessualAgent
from app.db.models import Cliente, Movimento, Processo, Tenant
from app.db.rls import definir_tenant
from app.providers.base import ProcessProvider
from app.workers.sync_processual import sincronizar_tenant

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "movimentos"


def _carregar_fixture(nome: str) -> dict[str, str]:
    return json.loads((_FIXTURES_DIR / nome).read_text(encoding="utf-8"))


class _ProviderFake:
    def __init__(self, movimentos: list[dict[str, str]]) -> None:
        self._movimentos = movimentos
        self.chamadas = 0

    async def buscar_movimentos(
        self, numero_processo: str, tribunal_alias: str
    ) -> list[dict[str, str]]:
        self.chamadas += 1
        return self._movimentos


class _AnthropicClientFake:
    async def create_message(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        if model == "haiku-fake":
            return json.dumps({"relevante": True, "motivo": "fixture de teste"})
        return (
            "Em 10/07/2026, o juiz deferiu o pedido de tutela de urgência, "
            "suspendendo a cobrança até nova decisão."
        )


def _agent() -> ProcessualAgent:
    return ProcessualAgent(
        _AnthropicClientFake(), haiku_model="haiku-fake", sonnet_model="sonnet-fake"
    )


def _criar_processo(session: Session) -> Processo:
    tenant = Tenant(nome="Escritorio Teste", plano="solo")
    session.add(tenant)
    session.flush()
    definir_tenant(session, tenant.id)
    cliente = Cliente(tenant_id=tenant.id, nome="Cliente Teste", whatsapp_numero="5511999997777")
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
    session.commit()
    return processo


async def test_sincronizar_tenant_persiste_movimentos_novos(db_engine: Engine) -> None:
    movimento_bruto = _carregar_fixture("despacho_com_decisao.json")
    provider: ProcessProvider = _ProviderFake([movimento_bruto])

    with Session(db_engine, expire_on_commit=False) as session:
        processo = _criar_processo(session)
        definir_tenant(session, processo.tenant_id)
        tenant = session.get(Tenant, processo.tenant_id)
        assert tenant is not None

        await sincronizar_tenant(session, provider, _agent(), tenant)

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, processo.tenant_id)
        movimentos = session.scalars(
            select(Movimento).where(Movimento.processo_id == processo.id)
        ).all()
        assert len(movimentos) == 1
        assert movimentos[0].resumo is not None


async def test_sincronizar_tenant_nao_duplica_movimento_ja_sincronizado(db_engine: Engine) -> None:
    movimento_bruto = _carregar_fixture("despacho_com_decisao.json")
    provider: ProcessProvider = _ProviderFake([movimento_bruto])

    with Session(db_engine, expire_on_commit=False) as session:
        processo = _criar_processo(session)
        definir_tenant(session, processo.tenant_id)
        tenant = session.get(Tenant, processo.tenant_id)
        assert tenant is not None

        await sincronizar_tenant(session, provider, _agent(), tenant)
        # provider devolve a mesma lista inteira de novo, como a API real faz.
        await sincronizar_tenant(session, provider, _agent(), tenant)

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, processo.tenant_id)
        movimentos = session.scalars(
            select(Movimento).where(Movimento.processo_id == processo.id)
        ).all()
        assert len(movimentos) == 1
    assert provider.chamadas == 2


async def test_sincronizar_tenant_isola_falha_de_um_processo_sem_derrubar_os_outros(
    db_engine: Engine,
) -> None:
    movimento_bruto = _carregar_fixture("despacho_com_decisao.json")

    _NUMERO_QUE_FALHA = "0001111-11.2024.8.26.0100"

    class _ProviderFalhaPorNumero(ProcessProvider):
        async def buscar_movimentos(
            self, numero_processo: str, tribunal_alias: str
        ) -> list[dict[str, str]]:
            if numero_processo == _NUMERO_QUE_FALHA:
                raise RuntimeError("tribunal fora do ar")
            return [movimento_bruto]

    with Session(db_engine, expire_on_commit=False) as session:
        tenant = Tenant(nome="Escritorio Multi", plano="escritorio")
        session.add(tenant)
        session.flush()
        definir_tenant(session, tenant.id)
        cliente = Cliente(tenant_id=tenant.id, nome="Cliente X", whatsapp_numero="5511999996666")
        session.add(cliente)
        session.flush()
        processo_com_falha = Processo(
            tenant_id=tenant.id,
            cliente_id=cliente.id,
            numero=_NUMERO_QUE_FALHA,
            tribunal_alias="tjsp",
        )
        processo_ok = Processo(
            tenant_id=tenant.id,
            cliente_id=cliente.id,
            numero="0002222-22.2024.8.26.0100",
            tribunal_alias="tjsp",
        )
        session.add_all([processo_com_falha, processo_ok])
        session.flush()
        session.commit()
        # capturados antes de sincronizar_tenant: ela faz rollback() internamente
        # (isolamento por processo), o que expira os objetos ORM da sessão.
        tenant_id = tenant.id
        processo_com_falha_id = processo_com_falha.id
        processo_ok_id = processo_ok.id

        await sincronizar_tenant(session, _ProviderFalhaPorNumero(), _agent(), tenant)

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, tenant_id)
        movimentos_falha = session.scalars(
            select(Movimento).where(Movimento.processo_id == processo_com_falha_id)
        ).all()
        movimentos_ok = session.scalars(
            select(Movimento).where(Movimento.processo_id == processo_ok_id)
        ).all()
        assert movimentos_falha == []
        assert len(movimentos_ok) == 1
