"""Cria um Tenant Solo + Advogado (e opcionalmente Cliente/Processo) pra
teste manual — não faz parte do produto, é só provisionamento de dev/piloto
enquanto não existe onboarding de verdade (ver CLAUDE.md §8, "não crie
script que eu não pedi" — este foi pedido explicitamente na sessão de
2026-07-24 pra destravar as baterias de teste).

Uso (PYTHONPATH=. necessário pra achar o pacote app/, fora do pytest):
    PYTHONPATH=. python scripts/seed_teste.py \
        --advogado-nome "Dra. Ana" --advogado-whatsapp 5511999999999 \
        --area-atuacao "Cível" [--oab "123456/SP"] \
        [--cliente-nome "João" --cliente-whatsapp 5511988888888 \
         --processo-numero "0000832-35.2018.4.01.3202" --tribunal-alias trf1]
"""

import argparse

from app.db.base import SessionLocal
from app.db.models import Advogado, Cliente, Processo, Tenant
from app.db.rls import definir_tenant


def main() -> None:
    args = _parse_args()

    session = SessionLocal()
    try:
        tenant = Tenant(nome=f"Teste - {args.advogado_nome}", plano="solo")
        session.add(tenant)
        session.flush()
        definir_tenant(session, tenant.id)

        advogado = Advogado(
            tenant_id=tenant.id,
            nome=args.advogado_nome,
            oab=args.oab,
            whatsapp_numero=args.advogado_whatsapp,
            area_atuacao=args.area_atuacao,
            disponivel=True,
        )
        session.add(advogado)
        session.flush()

        cliente_id = None
        processo_numero = None
        if args.cliente_nome:
            cliente = Cliente(
                tenant_id=tenant.id, nome=args.cliente_nome, whatsapp_numero=args.cliente_whatsapp
            )
            session.add(cliente)
            session.flush()
            cliente_id = cliente.id

            if args.processo_numero:
                processo = Processo(
                    tenant_id=tenant.id,
                    cliente_id=cliente.id,
                    numero=args.processo_numero,
                    tribunal_alias=args.tribunal_alias,
                    advogado_responsavel_id=advogado.id,
                )
                session.add(processo)
                session.flush()
                processo_numero = processo.numero

        session.commit()

        print(f"tenant_id: {tenant.id}")
        print(f"advogado_id: {advogado.id}")
        print(f"webhook UAZAPI: /webhooks/uazapi/{tenant.id}")
        if cliente_id is not None:
            print(f"cliente_id: {cliente_id}")
        if processo_numero is not None:
            print(f"processo: {processo_numero}")
    finally:
        session.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--advogado-nome", required=True)
    parser.add_argument("--advogado-whatsapp", required=True)
    parser.add_argument("--area-atuacao", required=True)
    parser.add_argument("--oab", default=None)
    parser.add_argument("--cliente-nome", default=None)
    parser.add_argument("--cliente-whatsapp", default=None)
    parser.add_argument("--processo-numero", default=None)
    parser.add_argument("--tribunal-alias", default=None)

    args = parser.parse_args()
    if args.cliente_nome and not args.cliente_whatsapp:
        parser.error("--cliente-whatsapp é obrigatório junto de --cliente-nome")
    if args.processo_numero and not args.tribunal_alias:
        parser.error("--tribunal-alias é obrigatório junto de --processo-numero")
    return args


if __name__ == "__main__":
    main()
