import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from anthropic import AsyncAnthropic

from app.services.normalizacao import MovimentoNormalizado

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class AnthropicClient(Protocol):
    async def create_message(self, *, system: str, user: str, model: str, max_tokens: int) -> str: ...


@dataclass(frozen=True)
class RelevanciaClassificacao:
    relevante: bool
    motivo: str


class ProcessualAgent:
    def __init__(self, client: AnthropicClient, haiku_model: str, sonnet_model: str) -> None:
        self._client = client
        self._haiku_model = haiku_model
        self._sonnet_model = sonnet_model
        self._prompt_relevancia = (_PROMPTS_DIR / "relevancia_system.md").read_text(encoding="utf-8")
        self._prompt_resumo = (_PROMPTS_DIR / "resumo_system.md").read_text(encoding="utf-8")

    async def classificar_relevancia(self, movimento: MovimentoNormalizado) -> RelevanciaClassificacao:
        resposta = await self._client.create_message(
            system=self._prompt_relevancia,
            user=_montar_input_movimento(movimento),
            model=self._haiku_model,
            max_tokens=200,
        )
        dados = json.loads(resposta)
        return RelevanciaClassificacao(relevante=bool(dados["relevante"]), motivo=str(dados["motivo"]))

    async def resumir(self, movimento: MovimentoNormalizado) -> str:
        return await self._client.create_message(
            system=self._prompt_resumo,
            user=_montar_input_movimento(movimento),
            model=self._sonnet_model,
            max_tokens=400,
        )


class AnthropicMessagesClient:
    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def create_message(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        resposta = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        bloco = resposta.content[0]
        if bloco.type != "text":
            raise ValueError(f"resposta inesperada do modelo: bloco do tipo {bloco.type!r}")
        return bloco.text


def _montar_input_movimento(movimento: MovimentoNormalizado) -> str:
    return (
        f"Processo: {movimento.processo_numero}\n"
        f"Data: {movimento.data.isoformat()}\n"
        f"Tipo: {movimento.tipo}\n"
        f"Texto: {movimento.texto}"
    )
