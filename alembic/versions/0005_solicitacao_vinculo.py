"""solicitacao_vinculo (coleta de dados pra vinculo humano) + flag na conversa_estado

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversa_estado",
        sa.Column(
            "aguardando_dados_vinculo", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )

    op.create_table(
        "solicitacao_vinculo",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=False
        ),
        sa.Column("whatsapp_numero", sa.String(20), nullable=False),
        sa.Column("nome_informado", sa.String(200), nullable=False),
        sa.Column("cpf_informado", sa.String(11), nullable=False),
        sa.Column("numero_processo_informado", sa.String(30), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON solicitacao_vinculo TO advogai_app")
    op.execute("ALTER TABLE solicitacao_vinculo ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON solicitacao_vinculo
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON solicitacao_vinculo")
    op.drop_table("solicitacao_vinculo")
    op.drop_column("conversa_estado", "aguardando_dados_vinculo")
