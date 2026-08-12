import json
from enum import Enum
from pathlib import Path

import structlog

from app.agents.processual import AnthropicClient

logger = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class Intencao(str, Enum):
    CONSULTAR_PROCESSO = "consultar_processo"
    FALAR_ADVOGADO = "falar_advogado"
    OUTRO = "outro"


class AtendimentoAgent:
    def __init__(self, client: AnthropicClient, haiku_model: str) -> None:
        self._client = client
        self._haiku_model = haiku_model
        self._prompt_classificacao = (
            _PROMPTS_DIR / "atendimento_classificacao_system.md"
        ).read_text(encoding="utf-8")

    async def classificar_intencao(self, texto: str) -> Intencao:
        resposta = await self._client.create_message(
            system=self._prompt_classificacao,
            user=texto,
            model=self._haiku_model,
            max_tokens=50,
        )
        return _interpretar_intencao(resposta)


def _interpretar_intencao(resposta: str) -> Intencao:
    """Lê a intenção da resposta do modelo, tolerando embrulho ao redor do JSON.

    O prompt pede JSON puro, mas o modelo devolve em cerca de markdown
    (```json ... ```) — visto em produção em 2026-08-12, com o `json.loads`
    direto estourando JSONDecodeError, derrubando a task e deixando o cliente
    sem resposta. Recortar do primeiro `{` ao último `}` cobre cerca, prosa
    antes/depois e espaço em branco de uma vez só.

    Qualquer coisa que ainda não dê pra interpretar vira OUTRO: o agente
    responde "não entendi" e repete o menu, que é honesto e seguro (CLAUDE.md
    §2). Adivinhar a intenção seria pior do que perguntar de novo.
    """
    inicio, fim = resposta.find("{"), resposta.rfind("}")
    if inicio != -1 and fim > inicio:
        try:
            dados = json.loads(resposta[inicio : fim + 1])
            return Intencao(dados["intencao"])
        except (ValueError, KeyError, TypeError):
            pass
    # Loga a saída do modelo (artefato nosso, não relato do cliente — CLAUDE.md
    # §6), truncada: sem isso, diagnosticar formato inesperado vira adivinhação.
    logger.warning("classificacao_intencao_ilegivel", resposta=resposta[:200])
    return Intencao.OUTRO
