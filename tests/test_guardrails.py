from datetime import date

from app.core.guardrails import avaliar_guardrails
from app.services.normalizacao import MovimentoNormalizado


def _movimento(texto: str = "Defiro o pedido de tutela de urgência.") -> MovimentoNormalizado:
    return MovimentoNormalizado(
        processo_numero="0001234-56.2024.8.26.0100",
        data=date(2026, 7, 10),
        tipo="Decisão",
        texto=texto,
    )


def test_resumo_valido_passa():
    movimento = _movimento()
    resumo = (
        "Em 10/07/2026, o juiz deferiu o pedido de tutela de urgência, "
        "suspendendo a cobrança até nova decisão."
    )
    resultado = avaliar_guardrails(resumo, movimento)
    assert resultado.passed
    assert resultado.violacoes == ()


def test_bloqueia_previsao_de_resultado():
    movimento = _movimento()
    resumo = (
        "Em 10/07/2026, o juiz deferiu a tutela de urgência. Suas chances de "
        "ganhar o processo agora são altas."
    )
    resultado = avaliar_guardrails(resumo, movimento)
    assert not resultado.passed
    assert any("proibida" in v for v in resultado.violacoes)


def test_bloqueia_estimativa_de_valor():
    movimento = _movimento()
    resumo = (
        "Em 10/07/2026, o juiz deferiu a tutela de urgência. O valor estimado "
        "da condenação deve ficar em torno de R$ 50 mil."
    )
    resultado = avaliar_guardrails(resumo, movimento)
    assert not resultado.passed


def test_bloqueia_orientacao_juridica():
    movimento = _movimento()
    resumo = "Em 10/07/2026 houve decisão sobre a tutela de urgência. Você deveria recorrer imediatamente."
    resultado = avaliar_guardrails(resumo, movimento)
    assert not resultado.passed


def test_bloqueia_opiniao_sobre_juiz():
    movimento = _movimento()
    resumo = "Em 10/07/2026, sobre a tutela de urgência, o juiz está a favor do seu lado no processo."
    resultado = avaliar_guardrails(resumo, movimento)
    assert not resultado.passed


def test_bloqueia_resumo_sem_data():
    movimento = _movimento()
    resumo = "O juiz deferiu o pedido de tutela de urgência apresentado pela parte."
    resultado = avaliar_guardrails(resumo, movimento)
    assert not resultado.passed
    assert any("data" in v for v in resultado.violacoes)


def test_bloqueia_resumo_sem_referencia_ao_texto_original():
    movimento = _movimento()
    resumo = "Em 10/07/2026, houve uma movimentação no seu processo."
    resultado = avaliar_guardrails(resumo, movimento)
    assert not resultado.passed
    assert any("origem" in v for v in resultado.violacoes)
