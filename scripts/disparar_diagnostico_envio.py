"""Dispara workers.diagnosticar_envio (app/workers/diagnostico.py) na fila de
verdade — quem executa é o Worker rodando normalmente, não este processo.
Roda e sai na hora; o resultado aparece no log do Worker, não aqui. Não é
parte do produto (ver CLAUDE.md §8 — pedido explícito na sessão de 2026-08-07
pra reproduzir o ReadTimeout dentro da execução real de uma task).

Uso (PYTHONPATH=. necessário fora do pytest):
    PYTHONPATH=. python scripts/disparar_diagnostico_envio.py --numero 5581994065983
    PYTHONPATH=. python scripts/disparar_diagnostico_envio.py --numero 5581994065983 --com-agent
"""

import argparse

from app.workers.diagnostico import diagnosticar_envio, diagnosticar_envio_com_agent


def main() -> None:
    args = _parse_args()
    task = diagnosticar_envio_com_agent if args.com_agent else diagnosticar_envio
    resultado = task.delay(numero=args.numero)
    print(f"task enfileirada: {resultado.id} — acompanhe o log do Worker")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--numero", required=True)
    parser.add_argument(
        "--com-agent",
        action="store_true",
        help="usa diagnosticar_envio_com_agent (constrói AtendimentoAgent antes do send_text)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
