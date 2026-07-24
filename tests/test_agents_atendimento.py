import json

from app.agents.atendimento import AtendimentoAgent, Intencao


class _FakeAnthropicClient:
    def __init__(self, intencao: str) -> None:
        self._intencao = intencao

    async def create_message(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        return json.dumps({"intencao": self._intencao})


def _agent(intencao: str) -> AtendimentoAgent:
    return AtendimentoAgent(_FakeAnthropicClient(intencao), haiku_model="haiku-fake")


async def test_classifica_consultar_processo() -> None:
    resultado = await _agent("consultar_processo").classificar_intencao("como está meu processo?")

    assert resultado == Intencao.CONSULTAR_PROCESSO


async def test_classifica_falar_advogado() -> None:
    resultado = await _agent("falar_advogado").classificar_intencao("quero falar com o advogado")

    assert resultado == Intencao.FALAR_ADVOGADO


async def test_classifica_outro() -> None:
    resultado = await _agent("outro").classificar_intencao("qual a previsão de eu ganhar?")

    assert resultado == Intencao.OUTRO


async def test_intencao_desconhecida_cai_em_outro() -> None:
    resultado = await _agent("algo_nao_mapeado").classificar_intencao("texto qualquer")

    assert resultado == Intencao.OUTRO
