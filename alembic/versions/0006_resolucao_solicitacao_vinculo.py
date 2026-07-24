"""resolvida_em na solicitacao_vinculo (comando vincular/descartar)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "solicitacao_vinculo", sa.Column("resolvida_em", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("solicitacao_vinculo", "resolvida_em")
