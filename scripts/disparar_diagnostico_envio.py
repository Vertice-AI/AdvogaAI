"""Dispara workers.diagnosticar_envio (app/workers/diagnostico.py) na fila de
verdade — quem executa é o Worker rodando normalmente, não este processo.
Roda e sai na hora; o resultado aparece no log do Worker, não aqui. Não é
parte do produto (ver CLAUDE.md §8 — pedido explícito na sessão de 2026-08-07
pra reproduzir o ReadTimeout dentro da execução real de uma task).

Uso (PYTHONPATH=. necessário fora do pytest):
    PYTHONPATH=. python scripts/disparar_diagnostico_envio.py --numero 5581994065983
    PYTHONPATH=. python scripts/disparar_diagnostico_envio.py --numero 5581994065983 --com-agent
    PYTHONPATH=. python scripts/disparar_diagnostico_envio.py --numero 5581994065983 --com-db --tenant-id <uuid>
"""

import argparse

from app.workers.diagnostico import (
    diagnosticar_envio,
    diagnosticar_envio_com_agent,
    diagnosticar_envio_com_db,
)


def main() -> None:
    args = _parse_args()
    if args.com_db:
        if not args.tenant_id:
            raise SystemExit("--com-db exige --tenant-id")
        resultado = diagnosticar_envio_com_db.delay(numero=args.numero, tenant_id=args.tenant_id)
    elif args.com_agent:
        resultado = diagnosticar_envio_com_agent.delay(numero=args.numero)
    else:
        resultado = diagnosticar_envio.delay(numero=args.numero)
    print(f"task enfileirada: {resultado.id} — acompanhe o log do Worker")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--numero", required=True)
    parser.add_argument(
        "--com-agent",
        action="store_true",
        help="usa diagnosticar_envio_com_agent (constrói AtendimentoAgent antes do send_text)",
    )
    parser.add_argument(
        "--com-db",
        action="store_true",
        help="usa diagnosticar_envio_com_db (Session síncrona aberta + SET LOCAL + SELECT antes do send_text)",
    )
    parser.add_argument("--tenant-id", help="UUID do tenant, exigido com --com-db")
    return parser.parse_args()


if __name__ == "__main__":
    main()
