"""whatsapp do advogado + solicitacao de aprovacao no movimento

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("advogado", sa.Column("whatsapp_numero", sa.String(20), nullable=True))
    op.create_unique_constraint(
        "uq_advogado_tenant_whatsapp", "advogado", ["tenant_id", "whatsapp_numero"]
    )
    op.add_column(
        "movimento", sa.Column("aprovacao_solicitada_em", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("movimento", "aprovacao_solicitada_em")
    op.drop_constraint("uq_advogado_tenant_whatsapp", "advogado", type_="unique")
    op.drop_column("advogado", "whatsapp_numero")
