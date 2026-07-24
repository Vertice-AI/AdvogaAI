"""oab do advogado + conversa_estado (saudacao diaria e regra do silencio)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("advogado", sa.Column("oab", sa.String(20), nullable=True))

    op.create_table(
        "conversa_estado",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False
        ),
        sa.Column("whatsapp_numero", sa.String(20), nullable=False),
        sa.Column("ultima_saudacao_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("atendimento_humano_desde", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "whatsapp_numero", name="uq_conversa_estado_tenant_whatsapp"
        ),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON conversa_estado TO advogai_app")
    op.execute("ALTER TABLE conversa_estado ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON conversa_estado
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conversa_estado")
    op.drop_table("conversa_estado")
    op.drop_column("advogado", "oab")
