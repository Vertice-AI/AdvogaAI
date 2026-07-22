from dataclasses import dataclass
from enum import Enum

from app.agents.processual import ProcessualAgent
from app.core.guardrails import GuardrailResult, avaliar_guardrails
from app.services.normalizacao import MovimentoNormalizado, normalizar


class NivelAutonomia(str, Enum):
    AUTOMATICO = "automatico"
    APROVACAO_MANUAL = "aprovacao_manual"


class DecisaoEnvio(str, Enum):
    AUTO_SEND = "auto_send"
    NEEDS_APPROVAL = "needs_approval"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ResultadoPipeline:
    movimento: MovimentoNormalizado
    relevante: bool
    resumo: str | None
    guardrail: GuardrailResult | None
    decisao: DecisaoEnvio


async def processar_movimento(
    movimento_bruto: dict[str, str],
    agent: ProcessualAgent,
    nivel_autonomia: NivelAutonomia,
) -> ResultadoPipeline:
    movimento = normalizar(movimento_bruto)

    classificacao = await agent.classificar_relevancia(movimento)
    if not classificacao.relevante:
        return ResultadoPipeline(
            movimento=movimento,
            relevante=False,
            resumo=None,
            guardrail=None,
            decisao=DecisaoEnvio.BLOCKED,
        )

    resumo = await agent.resumir(movimento)
    guardrail = avaliar_guardrails(resumo, movimento)
    if not guardrail.passed:
        return ResultadoPipeline(
            movimento=movimento,
            relevante=True,
            resumo=resumo,
            guardrail=guardrail,
            decisao=DecisaoEnvio.BLOCKED,
        )

    decisao = (
        DecisaoEnvio.AUTO_SEND
        if nivel_autonomia == NivelAutonomia.AUTOMATICO
        else DecisaoEnvio.NEEDS_APPROVAL
    )
    return ResultadoPipeline(
        movimento=movimento,
        relevante=True,
        resumo=resumo,
        guardrail=guardrail,
        decisao=decisao,
    )
