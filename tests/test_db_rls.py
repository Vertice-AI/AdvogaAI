import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.db.models import Cliente, Tenant
from app.db.rls import definir_tenant


def _sessao(db_engine: Engine) -> Session:
    # expire_on_commit=False replica app/db/base.py: sem isso, um segundo
    # commit() na mesma sessão expira atributos de objetos já commitados
    # anteriormente (ex.: tenant_a fica inacessível após criar tenant_b).
    return Session(db_engine, expire_on_commit=False)


def _criar_tenant(session: Session, nome: str) -> Tenant:
    tenant = Tenant(nome=nome, plano="solo")
    session.add(tenant)
    session.commit()
    return tenant


def test_tenant_nao_ve_cliente_de_outro_tenant(db_engine: Engine) -> None:
    with _sessao(db_engine) as session:
        tenant_a = _criar_tenant(session, "Escritorio A")
        tenant_b = _criar_tenant(session, "Escritorio B")

    with _sessao(db_engine) as session:
        definir_tenant(session, tenant_a.id)
        session.add(
            Cliente(tenant_id=tenant_a.id, nome="Cliente A", whatsapp_numero="5511999990000")
        )
        session.commit()

    with _sessao(db_engine) as session:
        definir_tenant(session, tenant_b.id)
        assert session.query(Cliente).all() == []

    with _sessao(db_engine) as session:
        definir_tenant(session, tenant_a.id)
        resultado = session.query(Cliente).all()
        assert len(resultado) == 1
        assert resultado[0].nome == "Cliente A"


def test_sem_tenant_definido_nenhuma_linha_e_visivel(db_engine: Engine) -> None:
    with _sessao(db_engine) as session:
        tenant = _criar_tenant(session, "Escritorio C")
        definir_tenant(session, tenant.id)
        session.add(Cliente(tenant_id=tenant.id, nome="Cliente C", whatsapp_numero="5511999990001"))
        session.commit()

    with _sessao(db_engine) as session:
        # sessão nova, sem chamar definir_tenant: app.tenant_id não está setado
        assert session.query(Cliente).all() == []


def test_bloqueia_insercao_cross_tenant(db_engine: Engine) -> None:
    with _sessao(db_engine) as session:
        tenant_a = _criar_tenant(session, "Escritorio D")
        tenant_b = _criar_tenant(session, "Escritorio E")

    with _sessao(db_engine) as session:
        definir_tenant(session, tenant_a.id)
        session.add(
            Cliente(tenant_id=tenant_b.id, nome="Cliente Vazado", whatsapp_numero="5511999990002")
        )
        with pytest.raises(ProgrammingError, match="row-level security policy"):
            session.commit()
