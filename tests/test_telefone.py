import pytest

from app.core.telefone import normalizar_numero


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        # DDD 81 (>= 31): WhatsApp usa a forma sem o nono dígito — confirmado em
        # produção, a UAZAPI devolveu chatid "558194065983" para este número.
        ("5581994065983", "558194065983"),
        ("558194065983", "558194065983"),
        # As duas grafias do MESMO número têm que colapsar no mesmo valor, senão
        # cadastro à mão nunca casa com o que chega do webhook (CLAUDE.md §4.6).
        ("5581994065983@s.whatsapp.net", "558194065983"),
        ("+55 (81) 99406-5983", "558194065983"),
        # DDD 11 (<= 30): o nono dígito é mantido.
        ("5511999998888", "5511999998888"),
        ("5511999998888@s.whatsapp.net", "5511999998888"),
        # Fixo (assinante não começa com 9) fica intacto.
        ("558133334444", "558133334444"),
        # Fora do padrão brasileiro: só remove separadores, sem inventar regra.
        ("+1 (415) 555-0132", "14155550132"),
    ],
)
def test_normalizar_numero(bruto: str, esperado: str) -> None:
    assert normalizar_numero(bruto) == esperado


def test_normalizacao_e_idempotente() -> None:
    # A forma canônica passa pelo normalizador de novo sem mudar — importante
    # porque o número é normalizado na entrada (webhook) e de novo na saída
    # (send_text).
    assert normalizar_numero(normalizar_numero("5581994065983")) == "558194065983"
