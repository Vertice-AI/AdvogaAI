"""solicitacao_atendimento (fila de roteamento pro "falar com advogado")

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solicitacao_atendimento",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False
        ),
        sa.Column("whatsapp_numero", sa.String(20), nullable=False),
        sa.Column(
            "cliente_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cliente.id"), nullable=True
        ),
        sa.Column(
            "advogado_designado_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("advogado.id"),
            nullable=False,
        ),
        sa.Column("resumo_caso", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("notificado_em", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON solicitacao_atendimento TO advogai_app")
    op.execute("ALTER TABLE solicitacao_atendimento ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON solicitacao_atendimento
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON solicitacao_atendimento")
    op.drop_table("solicitacao_atendimento")
