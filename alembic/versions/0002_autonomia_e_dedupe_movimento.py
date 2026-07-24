"""nivel de autonomia padrao do tenant + dedupe de movimento

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column(
            "nivel_autonomia_padrao",
            sa.String(20),
            nullable=False,
            server_default="aprovacao_manual",
        ),
    )
    op.create_check_constraint(
        "ck_tenant_nivel_autonomia_padrao",
        "tenant",
        "nivel_autonomia_padrao IN ('automatico', 'aprovacao_manual')",
    )
    op.create_unique_constraint(
        "uq_movimento_dedupe", "movimento", ["processo_id", "data", "tipo", "texto_origem"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_movimento_dedupe", "movimento", type_="unique")
    op.drop_constraint("ck_tenant_nivel_autonomia_padrao", "tenant", type_="check")
    op.drop_column("tenant", "nivel_autonomia_padrao")
