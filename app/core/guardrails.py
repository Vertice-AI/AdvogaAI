import re
from dataclasses import dataclass

from app.services.normalizacao import MovimentoNormalizado

_PADROES_PROIBIDOS: tuple[tuple[str, str], ...] = (
    (r"\bchance(s)? de (ganhar|perder|sucesso|êxito)\b", "previsão de resultado do processo"),
    (r"\bé provável que (você|o processo|a ação)\b", "previsão de resultado do processo"),
    (r"\b(vai|irá) (ganhar|perder) (o processo|a ação|o caso)\b", "previsão de resultado do processo"),
    (r"\bvalor (estimado|previsto) d[ae] (condenação|indenização|multa)\b", "estimativa de valor de condenação"),
    (r"\b(ótima|boa|excelente|péssima|má) notícia\b", "opinião sobre o mérito da decisão"),
    (r"\bchance (é|está) (boa|ruim|alta|baixa)\b", "opinião sobre a chance de êxito"),
    (r"\bvocê deve(ria)?\b", "orientação jurídica"),
    (r"\brecomend(o|amos|a-se)\b", "orientação jurídica"),
    (r"\bpra(z|s)o (estimado|previsto) de\b", "estimativa de prazo"),
    (r"\bo processo deve (terminar|encerrar|ser concluído)\b", "estimativa de prazo/resultado"),
    (r"\bo juiz (está|parece) (a favor|contra)\b", "interpretação do mérito/motivação do juiz"),
)

_TAMANHO_MINIMO_PALAVRA = 4


@dataclass(frozen=True)
class GuardrailResult:
    passed: bool
    violacoes: tuple[str, ...]


def avaliar_guardrails(resumo: str, movimento: MovimentoNormalizado) -> GuardrailResult:
    lowered = resumo.lower()
    violacoes: list[str] = []

    for padrao, motivo in _PADROES_PROIBIDOS:
        if re.search(padrao, lowered):
            violacoes.append(f"expressão proibida detectada ({motivo})")

    data_fmt = movimento.data.strftime("%d/%m/%Y")
    if data_fmt not in resumo:
        violacoes.append("resumo não cita a data do movimento original")

    palavras_texto = _palavras_relevantes(movimento.texto)
    palavras_resumo = _palavras_relevantes(resumo)
    if not (palavras_texto & palavras_resumo):
        violacoes.append("resumo não referencia o texto de origem")

    return GuardrailResult(passed=not violacoes, violacoes=tuple(violacoes))


def _palavras_relevantes(texto: str) -> set[str]:
    return set(re.findall(rf"\w{{{_TAMANHO_MINIMO_PALAVRA},}}", texto.lower()))
