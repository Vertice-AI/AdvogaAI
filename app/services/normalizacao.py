from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MovimentoNormalizado:
    processo_numero: str
    data: date
    tipo: str
    texto: str


def normalizar(movimento_bruto: dict[str, str]) -> MovimentoNormalizado:
    return MovimentoNormalizado(
        processo_numero=movimento_bruto["processo_numero"].strip(),
        data=date.fromisoformat(movimento_bruto["data"]),
        tipo=movimento_bruto["tipo"].strip(),
        texto=movimento_bruto["texto"].strip(),
    )
