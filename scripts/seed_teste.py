"""Cria um Tenant Solo + Advogado (e opcionalmente Cliente/Processo) pra
teste manual — não faz parte do produto, é só provisionamento de dev/piloto
enquanto não existe onboarding de verdade (ver CLAUDE.md §8, "não crie
script que eu não pedi" — este foi pedido explicitamente na sessão de
2026-07-24 pra destravar as baterias de teste).

Os números passam por normalizar_numero antes de gravar: pode digitar com ou
sem o nono dígito. Sem isso, um cadastro escrito de um jeito nunca casa com o
número que chega do webhook escrito de outro, e o cliente cai pra sempre no
fluxo de "número não vinculado" (CLAUDE.md §4.6) — falha silenciosa.

Uso (PYTHONPATH=. necessário pra achar o pacote app/, fora do pytest):
    PYTHONPATH=. python scripts/seed_teste.py \
        --advogado-nome "Dra. Ana" --advogado-whatsapp 5511999999999 \
        --area-atuacao "Cível" [--oab "123456/SP"] \
        [--cliente-nome "João" --cliente-whatsapp 5511988888888 \
         --processo-numero "0000832-35.2018.4.01.3202" --tribunal-alias trf1]

Pra cadastrar cliente num tenant que já existe (caso normal depois do primeiro
seed — é o tenant que a instância da UAZAPI aponta):
    PYTHONPATH=. python scripts/seed_teste.py --tenant-id <uuid> \
        --cliente-nome "João" --cliente-whatsapp 5511988888888 \
        --processo-numero "0000832-35.2018.4.01.3202" --tribunal-alias trf1

Pra pendurar mais um processo num cliente que já existe, basta repetir com o
mesmo --cliente-whatsapp (o número é a identidade; o nome só é exigido quando
o cliente é novo):
    PYTHONPATH=. python scripts/seed_teste.py --tenant-id <uuid> \
        --cliente-whatsapp 5511988888888 \
        --processo-numero "0001234-56.2019.8.17.0001" --tribunal-alias tjpe

Rodar duas vezes o mesmo processo não duplica nada.
"""

import argparse
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.telefone import normalizar_numero
from app.db.base import SessionLocal
from app.db.models import Advogado, Cliente, Processo, Tenant
from app.db.rls import definir_tenant


def main() -> None:
    args = _parse_args()

    session = SessionLocal()
    try:
        tenant_id, advogado = _resolver_tenant_e_advogado(session, args)

        cliente_id = None
        processo_numero = None
        if args.cliente_whatsapp:
            cliente = _resolver_cliente(session, tenant_id, args)
            cliente_id = cliente.id

            if args.processo_numero:
                processo = _resolver_processo(session, tenant_id, cliente, advogado, args)
                processo_numero = processo.numero

        session.commit()

        print(f"tenant_id: {tenant_id}")
        print(f"advogado_id: {advogado.id}")
        print(f"webhook UAZAPI: /webhooks/uazapi/{tenant_id}")
        if cliente_id is not None:
            print(f"cliente_id: {cliente_id}")
        if processo_numero is not None:
            print(f"processo: {processo_numero}")
    finally:
        session.close()


def _resolver_cliente(session: Session, tenant_id: uuid.UUID, args: argparse.Namespace) -> Cliente:
    """O cliente que já existe com aquele número, ou um novo.

    Reaproveitar é o caso normal a partir do segundo processo: `cliente` tem
    unique em (tenant_id, whatsapp_numero), então criar de novo estouraria a
    constraint — e um cliente com vários processos não é exceção, é o caso
    comum de verdade.
    """
    numero = normalizar_numero(args.cliente_whatsapp)
    cliente = session.scalar(
        select(Cliente).where(Cliente.tenant_id == tenant_id, Cliente.whatsapp_numero == numero)
    )
    if cliente is not None:
        return cliente
    if not args.cliente_nome:
        raise SystemExit(f"cliente novo ({numero}) exige --cliente-nome")
    cliente = Cliente(tenant_id=tenant_id, nome=args.cliente_nome, whatsapp_numero=numero)
    session.add(cliente)
    session.flush()
    return cliente


def _resolver_processo(
    session: Session,
    tenant_id: uuid.UUID,
    cliente: Cliente,
    advogado: Advogado,
    args: argparse.Namespace,
) -> Processo:
    """Idempotente por (cliente, número): rodar duas vezes não duplica."""
    processo = session.scalar(
        select(Processo).where(
            Processo.cliente_id == cliente.id, Processo.numero == args.processo_numero
        )
    )
    if processo is not None:
        return processo
    processo = Processo(
        tenant_id=tenant_id,
        cliente_id=cliente.id,
        numero=args.processo_numero,
        tribunal_alias=args.tribunal_alias,
        advogado_responsavel_id=advogado.id,
    )
    session.add(processo)
    session.flush()
    return processo


def _resolver_tenant_e_advogado(
    session: Session, args: argparse.Namespace
) -> tuple[uuid.UUID, Advogado]:
    """Tenant novo, ou o que já existe quando --tenant-id é passado.

    Reaproveitar o tenant existente é o caso normal depois do primeiro seed: a
    instância da UAZAPI aponta pra /webhooks/uazapi/{tenant_id}, então criar um
    tenant novo só pra cadastrar um cliente deixaria o cadastro num tenant que
    não recebe mensagem nenhuma.
    """
    if args.tenant_id:
        tenant_id = uuid.UUID(args.tenant_id)
        definir_tenant(session, tenant_id)
        advogado = session.scalar(select(Advogado).where(Advogado.tenant_id == tenant_id).limit(1))
        if advogado is None:
            raise SystemExit(f"tenant {tenant_id} não existe ou não tem advogado cadastrado")
        return tenant_id, advogado

    tenant = Tenant(nome=f"Teste - {args.advogado_nome}", plano="solo")
    session.add(tenant)
    session.flush()
    definir_tenant(session, tenant.id)

    advogado = Advogado(
        tenant_id=tenant.id,
        nome=args.advogado_nome,
        oab=args.oab,
        whatsapp_numero=normalizar_numero(args.advogado_whatsapp),
        area_atuacao=args.area_atuacao,
        disponivel=True,
    )
    session.add(advogado)
    session.flush()
    return tenant.id, advogado


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=None, help="reaproveita um tenant já existente")
    parser.add_argument("--advogado-nome", default=None)
    parser.add_argument("--advogado-whatsapp", default=None)
    parser.add_argument("--area-atuacao", default=None)
    parser.add_argument("--oab", default=None)
    parser.add_argument("--cliente-nome", default=None)
    parser.add_argument("--cliente-whatsapp", default=None)
    parser.add_argument("--processo-numero", default=None)
    parser.add_argument("--tribunal-alias", default=None)

    args = parser.parse_args()
    # Os dados do advogado só são obrigatórios quando o tenant é novo — com
    # --tenant-id o advogado já existe e é reaproveitado.
    if not args.tenant_id:
        faltando = [
            flag
            for flag, valor in (
                ("--advogado-nome", args.advogado_nome),
                ("--advogado-whatsapp", args.advogado_whatsapp),
                ("--area-atuacao", args.area_atuacao),
            )
            if not valor
        ]
        if faltando:
            parser.error(f"obrigatório sem --tenant-id: {', '.join(faltando)}")
    if args.cliente_nome and not args.cliente_whatsapp:
        parser.error("--cliente-whatsapp é obrigatório junto de --cliente-nome")
    if args.processo_numero and not args.cliente_whatsapp:
        parser.error("--cliente-whatsapp é obrigatório junto de --processo-numero")
    if args.processo_numero and not args.tribunal_alias:
        parser.error("--tribunal-alias é obrigatório junto de --processo-numero")
    return args


if __name__ == "__main__":
    main()
