import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import structlog
from anthropic import AsyncAnthropic

from app.services.normalizacao import MovimentoNormalizado

logger = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_TIMEOUT_SEGUNDOS = 30.0
_MAX_TENTATIVAS = 3


class AnthropicClient(Protocol):
    async def create_message(
        self, *, system: str, user: str, model: str, max_tokens: int
    ) -> str: ...


@dataclass(frozen=True)
class RelevanciaClassificacao:
    relevante: bool
    motivo: str
    # False quando não deu pra interpretar a resposta do modelo. Quem consome
    # decide o que fazer — o pipeline força revisão humana (pipeline_resumo.py).
    legivel: bool = True


class ProcessualAgent:
    def __init__(self, client: AnthropicClient, haiku_model: str, sonnet_model: str) -> None:
        self._client = client
        self._haiku_model = haiku_model
        self._sonnet_model = sonnet_model
        self._prompt_relevancia = (_PROMPTS_DIR / "relevancia_system.md").read_text(
            encoding="utf-8"
        )
        self._prompt_resumo = (_PROMPTS_DIR / "resumo_system.md").read_text(encoding="utf-8")

    async def classificar_relevancia(
        self, movimento: MovimentoNormalizado
    ) -> RelevanciaClassificacao:
        resposta = await self._client.create_message(
            system=self._prompt_relevancia,
            user=_montar_input_movimento(movimento),
            model=self._haiku_model,
            max_tokens=200,
        )
        return _interpretar_relevancia(resposta)

    async def resumir(self, movimento: MovimentoNormalizado) -> str:
        return await self._client.create_message(
            system=self._prompt_resumo,
            user=_montar_input_movimento(movimento),
            model=self._sonnet_model,
            max_tokens=400,
        )


class AnthropicMessagesClient:
    def __init__(self, api_key: str) -> None:
        # CLAUDE.md §6: timeout e retry explícitos, não o default implícito
        # do SDK — mesmo padrão de UazapiProvider/DataJudProvider (o SDK já
        # faz o backoff exponencial internamente, não precisa reescrever).
        self._client = AsyncAnthropic(
            api_key=api_key, timeout=_TIMEOUT_SEGUNDOS, max_retries=_MAX_TENTATIVAS
        )

    async def aclose(self) -> None:
        # Mesmo vazamento já corrigido no UazapiProvider em 1038247: o SDK abre
        # um httpx.AsyncClient que ninguém fecha. Cada task do Celery roda seu
        # próprio asyncio.run(), e o loop morre antes do cliente — daí o
        # "Event loop is closed" no log do worker, e as conexões acumulando num
        # processo de vida longa. Quem cria, fecha.
        await self._client.close()

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


def _interpretar_relevancia(resposta: str) -> RelevanciaClassificacao:
    """Lê a classificação, tolerando embrulho ao redor do JSON.

    Mesmo problema já visto no classificador de intenção em 2026-08-12
    (`ccb15d2`): o prompt pede JSON puro e o Haiku devolve em cerca de markdown
    (```json ... ```). Ali o `json.loads` direto derrubava a resposta ao
    cliente; aqui derrubava o sync inteiro do processo — 11 movimentos perdidos
    por causa do primeiro. Recortar do primeiro `{` ao último `}` cobre cerca,
    prosa em volta e espaço em branco de uma vez.

    Ilegível não é "irrelevante": descartar em silêncio poderia esconder do
    cliente uma audiência marcada. Marca como relevante e `legivel=False`, o
    que faz o pipeline exigir revisão humana mesmo em modo automático — a
    dúvida vai pra uma pessoa, nunca direto pro cliente.
    """
    inicio, fim = resposta.find("{"), resposta.rfind("}")
    if inicio != -1 and fim > inicio:
        try:
            dados = json.loads(resposta[inicio : fim + 1])
            return RelevanciaClassificacao(
                relevante=bool(dados["relevante"]), motivo=str(dados["motivo"])
            )
        except (ValueError, KeyError, TypeError):
            pass
    # Saída do modelo é artefato nosso, não relato do cliente (CLAUDE.md §6);
    # truncada porque sem ela diagnosticar formato inesperado vira adivinhação.
    logger.warning("classificacao_relevancia_ilegivel", resposta=resposta[:200])
    return RelevanciaClassificacao(
        relevante=True, motivo="classificação ilegível — enviado para revisão humana", legivel=False
    )


def _montar_input_movimento(movimento: MovimentoNormalizado) -> str:
    return (
        f"Processo: {movimento.processo_numero}\n"
        f"Data: {movimento.data.isoformat()}\n"
        f"Tipo: {movimento.tipo}\n"
        f"Texto: {movimento.texto}"
    )
