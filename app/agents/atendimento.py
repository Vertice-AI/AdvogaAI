import json
from enum import Enum
from pathlib import Path

from app.agents.processual import AnthropicClient

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
        dados = json.loads(resposta)
        try:
            return Intencao(dados["intencao"])
        except ValueError:
            return Intencao.OUTRO
