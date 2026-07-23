from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# CLAUDE.md 4.4: isolamento por Row Level Security no banco, não na aplicação.
# `set_config(..., true)` grava a variável só para a transação corrente — por
# isso precisa ser chamado dentro da mesma transação das queries que ela deve
# filtrar, antes de qualquer commit/rollback.
_TENANT_SETTING = "app.tenant_id"


def definir_tenant(session: Session, tenant_id: UUID) -> None:
    session.execute(
        text("SELECT set_config(:chave, :valor, true)"),
        {"chave": _TENANT_SETTING, "valor": str(tenant_id)},
    )
