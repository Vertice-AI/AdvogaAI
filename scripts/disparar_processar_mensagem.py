"""Dispara workers.processar_mensagem_recebida (a task real) isoladamente na
fila, sem passar pelo webhook — pra saber se o ReadTimeout reproduz mesmo
sem duas mensagens concorrentes. Quem executa é o Worker rodando normalmente.
Não é parte do produto (CLAUDE.md §8 — pedido explícito na sessão de
2026-08-07 pra isolar a causa do timeout intermitente).

Uso (PYTHONPATH=. necessário fora do pytest):
    PYTHONPATH=. python scripts/disparar_processar_mensagem.py \
        --tenant-id 8f1f0ad6-e6d5-4fda-9814-da92efda7775 \
        --numero 5581994065983 --texto oi
"""

import argparse
import uuid
from datetime import UTC, datetime

from app.workers.processar_mensagem import processar_mensagem_recebida


def main() -> None:
    args = _parse_args()
    resultado = processar_mensagem_recebida.delay(
        tenant_id=str(args.tenant_id),
        from_number=args.numero,
        text=args.texto,
        message_id=f"DIAG-{uuid.uuid4()}",
        timestamp_iso=datetime.now(UTC).isoformat(),
        from_me=False,
    )
    print(f"task enfileirada: {resultado.id} — acompanhe o log do Worker")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--numero", required=True)
    parser.add_argument("--texto", default="oi")
    return parser.parse_args()


if __name__ == "__main__":
    main()
