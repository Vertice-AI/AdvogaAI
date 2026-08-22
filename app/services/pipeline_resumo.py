import uuid
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from app.agents.processual import ProcessualAgent
from app.core.guardrails import GuardrailResult, avaliar_guardrails
from app.db.models import Movimento
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

    # `legivel=False` ganha do nível de autonomia de propósito: se não deu pra
    # interpretar a classificação, ninguém decidiu que aquilo era relevante —
    # e o que ninguém decidiu não vai sozinho pro cliente (CLAUDE.md §2).
    decisao = (
        DecisaoEnvio.AUTO_SEND
        if nivel_autonomia == NivelAutonomia.AUTOMATICO and classificacao.legivel
        else DecisaoEnvio.NEEDS_APPROVAL
    )
    return ResultadoPipeline(
        movimento=movimento,
        relevante=True,
        resumo=resumo,
        guardrail=guardrail,
        decisao=decisao,
    )


def persistir_resultado(
    session: Session,
    tenant_id: uuid.UUID,
    processo_id: uuid.UUID,
    resultado: ResultadoPipeline,
) -> Movimento:
    movimento = Movimento(
        tenant_id=tenant_id,
        processo_id=processo_id,
        data=resultado.movimento.data,
        tipo=resultado.movimento.tipo,
        texto_origem=resultado.movimento.texto,
        relevante=resultado.relevante,
        resumo=resultado.resumo,
        guardrail_passou=resultado.guardrail.passed if resultado.guardrail else None,
        decisao=resultado.decisao.value,
    )
    session.add(movimento)
    session.flush()
    return movimento


async def processar_e_persistir_movimento(
    movimento_bruto: dict[str, str],
    agent: ProcessualAgent,
    nivel_autonomia: NivelAutonomia,
    session: Session,
    tenant_id: uuid.UUID,
    processo_id: uuid.UUID,
) -> Movimento:
    """Roda o pipeline e grava o resultado em `movimento` na mesma transação.

    Não comita. `session` precisa já estar com `app.db.rls.definir_tenant`
    aplicado nesta transação — RLS exige (ver app/db/rls.py e o comentário
    da policy na migration 0001). Quem chama controla quando comitar, o que
    permite processar vários movimentos do mesmo tenant numa única transação
    sem precisar redefinir o tenant a cada linha.
    """
    resultado = await processar_movimento(movimento_bruto, agent, nivel_autonomia)
    return persistir_resultado(session, tenant_id, processo_id, resultado)
