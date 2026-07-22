import json
from pathlib import Path

from app.agents.processual import ProcessualAgent
from app.services.pipeline_resumo import DecisaoEnvio, NivelAutonomia, processar_movimento

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "movimentos"


def _carregar_fixture(nome: str) -> dict[str, str]:
    return json.loads((_FIXTURES_DIR / nome).read_text(encoding="utf-8"))


class _FakeAnthropicClient:
    def __init__(self, relevante: bool, resumo: str) -> None:
        self._relevante = relevante
        self._resumo = resumo

    async def create_message(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        if model == "haiku-fake":
            return json.dumps({"relevante": self._relevante, "motivo": "fixture de teste"})
        return self._resumo


def _agent(relevante: bool, resumo: str) -> ProcessualAgent:
    client = _FakeAnthropicClient(relevante=relevante, resumo=resumo)
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
