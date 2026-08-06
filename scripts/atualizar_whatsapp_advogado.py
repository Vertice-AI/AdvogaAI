"""Atualiza o whatsapp_numero de um Advogado já existente — não faz parte do
produto, é ajuste pontual de dado de piloto/teste (mesmo espírito de
seed_teste.py; ver CLAUDE.md §8, "não crie script que eu não pedi" — este foi
pedido explicitamente na sessão de 2026-08-06 pra reconfigurar o teste manual
sem recriar o tenant do zero).

Uso (PYTHONPATH=. necessário pra achar o pacote app/, fora do pytest):
    PYTHONPATH=. python scripts/atualizar_whatsapp_advogado.py \
        --tenant-id <uuid> --advogado-id <uuid> --novo-whatsapp 5511999999999
"""

import argparse
import uuid

from app.db.base import SessionLocal
from app.db.models import Advogado
from app.db.rls import definir_tenant


def main() -> None:
    args = _parse_args()

    session = SessionLocal()
    try:
        definir_tenant(session, args.tenant_id)
        advogado = session.get(Advogado, args.advogado_id)
        if advogado is None:
            raise SystemExit(f"advogado {args.advogado_id} não encontrado nesse tenant")

        numero_antigo = advogado.whatsapp_numero
        advogado.whatsapp_numero = args.novo_whatsapp
        session.commit()

        print(f"advogado_id: {advogado.id}")
        print(f"whatsapp_numero: {numero_antigo} -> {args.novo_whatsapp}")
    finally:
        session.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--advogado-id", required=True, type=uuid.UUID)
    parser.add_argument("--novo-whatsapp", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    main()
