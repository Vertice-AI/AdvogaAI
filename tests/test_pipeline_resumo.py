import json
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.agents.processual import ProcessualAgent
from app.db.models import Cliente, Processo, Tenant
from app.db.rls import definir_tenant
from app.services.pipeline_resumo import (
    DecisaoEnvio,
    NivelAutonomia,
    processar_e_persistir_movimento,
    processar_movimento,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "movimentos"


def _carregar_fixture(nome: str) -> dict[str, str]:
    return json.loads((_FIXTURES_DIR / nome).read_text(encoding="utf-8"))


class _FakeAnthropicClient:
    """Devolve a resposta CRUA do classificador, não o dict já interpretado.

    Um fake que sempre devolvia JSON limpo foi o que deixou passar a cerca de
    markdown até produção (2026-08-22) — o mesmo erro de fake que `ccb15d2`
    já tinha corrigido no classificador de intenção.
    """

    def __init__(self, resposta_relevancia: str, resumo: str) -> None:
        self._resposta_relevancia = resposta_relevancia
        self._resumo = resumo

    async def create_message(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        if model == "haiku-fake":
            return self._resposta_relevancia
        return self._resumo


def _resposta_relevancia(relevante: bool) -> str:
    return json.dumps({"relevante": relevante, "motivo": "fixture de teste"})


def _agent(relevante: bool, resumo: str) -> ProcessualAgent:
    return _agent_com_resposta(_resposta_relevancia(relevante), resumo)


def _agent_com_resposta(resposta_relevancia: str, resumo: str) -> ProcessualAgent:
    client = _FakeAnthropicClient(resposta_relevancia=resposta_relevancia, resumo=resumo)
    return ProcessualAgent(client, haiku_model="haiku-fake", sonnet_model="sonnet-fake")


async def test_movimento_relevante_com_resumo_valido_vai_para_aprovacao():
    movimento_bruto = _carregar_fixture("despacho_com_decisao.json")
    resumo_valido = (
        "Em 10/07/2026, o juiz deferiu o pedido de tutela de urgência, "
        "suspendendo a cobrança até nova decisão."
    )
    agent = _agent(relevante=True, resumo=resumo_valido)

    resultado = await processar_movimento(movimento_bruto, agent, NivelAutonomia.APROVACAO_MANUAL)

    assert resultado.relevante
    assert resultado.decisao == DecisaoEnvio.NEEDS_APPROVAL
    assert resultado.guardrail is not None and resultado.guardrail.passed


async def test_movimento_relevante_autonomia_automatica_envia_direto():
    movimento_bruto = _carregar_fixture("audiencia_marcada.json")
    resumo_valido = (
        "Em 05/08/2026, foi designada audiência de conciliação por "
        "videoconferência para o seu processo."
    )
    agent = _agent(relevante=True, resumo=resumo_valido)

    resultado = await processar_movimento(movimento_bruto, agent, NivelAutonomia.AUTOMATICO)

    assert resultado.decisao == DecisaoEnvio.AUTO_SEND


async def test_movimento_nao_relevante_e_bloqueado_sem_gerar_resumo():
    movimento_bruto = _carregar_fixture("conclusao_interna.json")
    agent = _agent(relevante=False, resumo="não deveria ser usado")

    resultado = await processar_movimento(movimento_bruto, agent, NivelAutonomia.AUTOMATICO)

    assert not resultado.relevante
    assert resultado.resumo is None
    assert resultado.decisao == DecisaoEnvio.BLOCKED


async def test_resumo_que_viola_guardrail_e_bloqueado():
    movimento_bruto = _carregar_fixture("sentenca.json")
    resumo_invalido = (
        "Em 20/07/2026, o juiz julgou procedente o pedido inicial. Suas chances "
        "de ganhar o recurso agora são altas."
    )
    agent = _agent(relevante=True, resumo=resumo_invalido)

    resultado = await processar_movimento(movimento_bruto, agent, NivelAutonomia.AUTOMATICO)

    assert resultado.decisao == DecisaoEnvio.BLOCKED
    assert resultado.guardrail is not None and not resultado.guardrail.passed


def _criar_processo(session: Session) -> Processo:
    tenant = Tenant(nome="Escritorio Teste", plano="solo")
    session.add(tenant)
    session.flush()
    # cliente/processo são protegidos por RLS (tenant não é) — precisa
    # definir o tenant antes de inserir qualquer linha nessas tabelas.
    definir_tenant(session, tenant.id)
    cliente = Cliente(tenant_id=tenant.id, nome="Cliente Teste", whatsapp_numero="5511999998888")
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
    return processo


async def test_processar_e_persistir_grava_movimento_relevante(db_engine: Engine):
    movimento_bruto = _carregar_fixture("despacho_com_decisao.json")
    resumo_valido = (
        "Em 10/07/2026, o juiz deferiu o pedido de tutela de urgência, "
        "suspendendo a cobrança até nova decisão."
    )
    agent = _agent(relevante=True, resumo=resumo_valido)

    with Session(db_engine, expire_on_commit=False) as session:
        processo = _criar_processo(session)
        definir_tenant(session, processo.tenant_id)

        movimento_salvo = await processar_e_persistir_movimento(
            movimento_bruto,
            agent,
            NivelAutonomia.APROVACAO_MANUAL,
            session,
            processo.tenant_id,
            processo.id,
        )
        session.commit()

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, processo.tenant_id)
        do_banco = session.get(type(movimento_salvo), movimento_salvo.id)
        assert do_banco is not None
        assert do_banco.relevante
        assert do_banco.resumo == resumo_valido
        assert do_banco.guardrail_passou
        assert do_banco.decisao == DecisaoEnvio.NEEDS_APPROVAL.value
        assert do_banco.processo_id == processo.id


async def test_processar_e_persistir_grava_movimento_irrelevante_sem_resumo(db_engine: Engine):
    movimento_bruto = _carregar_fixture("conclusao_interna.json")
    agent = _agent(relevante=False, resumo="não deveria ser usado")

    with Session(db_engine, expire_on_commit=False) as session:
        processo = _criar_processo(session)
        definir_tenant(session, processo.tenant_id)

        movimento_salvo = await processar_e_persistir_movimento(
            movimento_bruto,
            agent,
            NivelAutonomia.AUTOMATICO,
            session,
            processo.tenant_id,
            processo.id,
        )
        session.commit()

    with Session(db_engine, expire_on_commit=False) as session:
        definir_tenant(session, processo.tenant_id)
        do_banco = session.get(type(movimento_salvo), movimento_salvo.id)
        assert do_banco is not None
        assert not do_banco.relevante
        assert do_banco.resumo is None
        assert do_banco.decisao == DecisaoEnvio.BLOCKED.value


_RESUMO_AUDIENCIA = (
    "Em 05/08/2026, foi designada audiência de conciliação por "
    "videoconferência para o seu processo."
)


async def test_classificacao_em_cerca_de_markdown_e_interpretada():
    # Formato real observado em produção (2026-08-22): o Haiku embrulha o JSON
    # em ```json ... ```, e o json.loads direto derrubava o sync do processo.
    movimento_bruto = _carregar_fixture("audiencia_marcada.json")
    resposta = '```json\n{"relevante": true, "motivo": "audiência designada"}\n```'
    agent = _agent_com_resposta(resposta, _RESUMO_AUDIENCIA)

    resultado = await processar_movimento(movimento_bruto, agent, NivelAutonomia.APROVACAO_MANUAL)

    assert resultado.relevante
    assert resultado.decisao == DecisaoEnvio.NEEDS_APPROVAL


async def test_classificacao_com_prosa_em_volta_e_interpretada():
    movimento_bruto = _carregar_fixture("conclusao_interna.json")
    resposta = 'Claro! Segue:\n{"relevante": false, "motivo": "trâmite interno"}\nAté mais.'
    agent = _agent_com_resposta(resposta, "não deveria ser usado")

    resultado = await processar_movimento(movimento_bruto, agent, NivelAutonomia.APROVACAO_MANUAL)

    assert not resultado.relevante
    assert resultado.decisao == DecisaoEnvio.BLOCKED


async def test_classificacao_ilegivel_vai_para_revisao_humana_mesmo_em_modo_automatico():
    # Ilegível não é "irrelevante": descartar em silêncio esconderia do cliente
    # uma audiência marcada. Vai pra revisão humana ainda que a autonomia seja
    # automática — o que ninguém classificou não sai sozinho pro cliente.
    movimento_bruto = _carregar_fixture("audiencia_marcada.json")
    agent = _agent_com_resposta("desculpe, não consegui analisar", _RESUMO_AUDIENCIA)

    resultado = await processar_movimento(movimento_bruto, agent, NivelAutonomia.AUTOMATICO)

    assert resultado.relevante
    assert resultado.decisao == DecisaoEnvio.NEEDS_APPROVAL
