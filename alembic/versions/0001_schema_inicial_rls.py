"""schema inicial + isolamento multi-tenant via RLS

Revision ID: 0001
Revises:
Create Date: 2026-07-22
"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tabelas de negócio: toda linha pertence a um tenant e a policy abaixo
# garante que a query só enxerga o tenant setado em app.tenant_id na sessão
# (app/db/rls.py). `tenant` fica de fora de propósito — é a raiz, gerida por
# fluxo administrativo, não por código de atendimento ao cliente.
_TABELAS_COM_RLS = ("cliente", "advogado", "processo", "movimento")

# Senha da role de runtime só tem valor fixo aqui por conveniência de dev
# local (docker-compose). Em qualquer ambiente real, defina
# ADVOGAI_APP_DB_PASSWORD antes de rodar a migration — CLAUDE.md §6 proíbe
# segredo hardcoded fora de default de desenvolvimento.
_SENHA_APP = os.environ.get("ADVOGAI_APP_DB_PASSWORD", "advogai_app")


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("plano", sa.String(20), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("plano IN ('solo', 'escritorio')", name="ck_tenant_plano"),
    )

    op.create_table(
        "cliente",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False
        ),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("whatsapp_numero", sa.String(20), nullable=False),
        sa.Column("verificado_em", sa.DateTime(timezone=True), nullable=True),
        # Único por tenant, não global: o mesmo número pode ser cliente de
        # dois escritórios diferentes.
        sa.UniqueConstraint("tenant_id", "whatsapp_numero", name="uq_cliente_tenant_whatsapp"),
    )

    op.create_table(
        "advogado",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False
        ),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("area_atuacao", sa.String(100), nullable=False),
        sa.Column("disponivel", sa.Boolean, nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "processo",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False
        ),
        sa.Column(
            "cliente_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cliente.id"), nullable=False
        ),
        sa.Column("numero", sa.String(30), nullable=False),
        sa.Column("tribunal_alias", sa.String(20), nullable=False),
        sa.Column(
            "advogado_responsavel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("advogado.id"),
            nullable=True,
        ),
    )

    op.create_table(
        "movimento",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False
        ),
        sa.Column(
            "processo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("processo.id"),
            nullable=False,
        ),
        sa.Column("data", sa.Date, nullable=False),
        sa.Column("tipo", sa.String(100), nullable=False),
        sa.Column("texto_origem", sa.Text, nullable=False),
        sa.Column("relevante", sa.Boolean, nullable=False),
        sa.Column("resumo", sa.Text, nullable=True),
        sa.Column("guardrail_passou", sa.Boolean, nullable=True),
        sa.Column("decisao", sa.String(20), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("enviado_em", sa.DateTime(timezone=True), nullable=True),
    )

    # DO $$ ... $$ é uma string literal para o parser — bind param não é
    # substituído dentro dela. A senha vem de env var confiável (não input de
    # usuário final), então escapar aspas simples já é suficiente aqui.
    _senha_escapada = _SENHA_APP.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'advogai_app') THEN
                CREATE ROLE advogai_app LOGIN PASSWORD '{_senha_escapada}';
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO advogai_app")
    op.execute("GRANT SELECT, INSERT ON tenant TO advogai_app")

    for tabela in _TABELAS_COM_RLS:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabela} TO advogai_app")
        op.execute(f"ALTER TABLE {tabela} ENABLE ROW LEVEL SECURITY")
        # NULLIF trata os dois jeitos de "sem tenant setado": GUC nunca
        # referenciada nesta conexão (current_setting devolve NULL) e GUC
        # cujo escopo local (SET LOCAL/set_config(...,true)) já expirou no
        # fim da transação anterior — nesse caso o Postgres não volta pra
        # NULL, volta pra string vazia. Sem o NULLIF, um cast direto pra
        # ::uuid quebra com erro em vez de simplesmente negar a linha.
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {tabela}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            """
        )


def downgrade() -> None:
    for tabela in reversed(_TABELAS_COM_RLS):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tabela}")
    op.drop_table("movimento")
    op.drop_table("processo")
    op.drop_table("advogado")
    op.drop_table("cliente")
    op.drop_table("tenant")

    op.execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM advogai_app")
    # advogai_app é uma role de cluster, não de banco — se o mesmo cluster
    # também tem o banco de dev/teste irmão com grants pendentes pra essa
    # role, DROP ROLE falha por dependência cross-database. Não dá pra
    # resolver isso a partir de uma migration presa a um único banco, então
    # engolimos esse caso specific e deixamos a role órfã (sem privilégio
    # nenhum neste banco) em vez de quebrar o downgrade.
    op.execute(
        """
        DO $$
        BEGIN
            DROP ROLE advogai_app;
        EXCEPTION WHEN dependent_objects_still_exist THEN
            NULL;
        END
        $$;
        """
    )
