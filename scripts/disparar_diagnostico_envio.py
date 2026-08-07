"""Dispara workers.diagnosticar_envio (app/workers/diagnostico.py) na fila de
verdade — quem executa é o Worker rodando normalmente, não este processo.
Roda e sai na hora; o resultado aparece no log do Worker, não aqui. Não é
parte do produto (ver CLAUDE.md §8 — pedido explícito na sessão de 2026-08-07
pra reproduzir o ReadTimeout dentro da execução real de uma task).

Uso (PYTHONPATH=. necessário fora do pytest):
    PYTHONPATH=. python scripts/disparar_diagnostico_envio.py --numero 5581994065983
"""

import argparse

from app.workers.diagnostico import diagnosticar_envio


def main() -> None:
    args = _parse_args()
    resultado = diagnosticar_envio.delay(numero=args.numero)
    print(f"task enfileirada: {resultado.id} — acompanhe o log do Worker")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--numero", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
