import json

import pytest

from app.agents.atendimento import AtendimentoAgent, Intencao


class _FakeAnthropicClient:
    def __init__(self, resposta: str) -> None:
        self._resposta = resposta

    async def create_message(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        return self._resposta


def _agent(intencao: str) -> AtendimentoAgent:
    return _agent_com_resposta(json.dumps({"intencao": intencao}))


def _agent_com_resposta(resposta: str) -> AtendimentoAgent:
    return AtendimentoAgent(_FakeAnthropicClient(resposta), haiku_model="haiku-fake")


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


@pytest.mark.parametrize(
    "resposta",
    [
        # Formato real devolvido pelo Haiku em produção (2026-08-12): o prompt
        # pede JSON puro e o modelo embrulha em cerca de markdown. O json.loads
        # direto estourava e derrubava a task, deixando o cliente sem resposta.
        '```json\n{"intencao": "falar_advogado"}\n```',
        '```\n{"intencao": "falar_advogado"}\n```',
        'Claro! {"intencao": "falar_advogado"}',
        '  {"intencao": "falar_advogado"}  \n',
    ],
)
async def test_le_intencao_mesmo_com_embrulho_ao_redor_do_json(resposta: str) -> None:
    assert await _agent_com_resposta(resposta).classificar_intencao("2") == Intencao.FALAR_ADVOGADO


@pytest.mark.parametrize(
    "resposta",
    ["desculpe, não consegui classificar", "", "{sem json valido aqui}", '{"outra_chave": 1}'],
)
async def test_resposta_ilegivel_cai_em_outro_em_vez_de_derrubar_a_task(resposta: str) -> None:
    # Falhar fechado: o agente repete o menu em vez de adivinhar a intenção
    # (CLAUDE.md §2) — e, principalmente, em vez de deixar o cliente sem
    # resposta nenhuma enquanto a task entra em retry.
    assert await _agent_com_resposta(resposta).classificar_intencao("2") == Intencao.OUTRO
