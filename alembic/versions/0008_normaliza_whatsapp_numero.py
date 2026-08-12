"""normaliza whatsapp_numero: remove o nono dígito dos celulares brasileiros

O webhook passa a entregar o número em forma canônica (sem o nono dígito, que
é como o WhatsApp/UAZAPI identifica a conversa). Sem normalizar o que já está
gravado, cadastro antigo com 13 dígitos ("5581994065983") deixa de casar com o
número que chega ("558194065983") e o cliente cai pra sempre no fluxo de
"número não vinculado" (CLAUDE.md §4.6).

Só toca celular brasileiro com DDD a partir de 31 (DDI 55, 13 dígitos,
assinante começando em 9) — de 11 a 30 o WhatsApp mantém o nono dígito. Mesma
regra de app/core/telefone.py, escrita aqui em SQL de propósito: migration não
deve depender de código de aplicação que muda com o tempo.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABELAS = (
    "cliente",
    "advogado",
    "conversa_estado",
    "solicitacao_vinculo",
    "solicitacao_atendimento",
)


def upgrade() -> None:
    for tabela in _TABELAS:
        op.execute(
            f"""
            UPDATE {tabela}
               SET whatsapp_numero = substring(whatsapp_numero from 1 for 4)
                                  || substring(whatsapp_numero from 6)
             WHERE whatsapp_numero ~ '^55(3[1-9]|[4-9][0-9])9[0-9]{{8}}$'
            """
        )


def downgrade() -> None:
    # Irreversível por natureza: depois de remover o nono dígito não dá pra
    # saber quais números o tinham originalmente. Deixar passar em silêncio é
    # pior — quem precisar reverter tem que restaurar backup.
    raise NotImplementedError(
        "0008 não é reversível: a forma com nono dígito não é recuperável do dado normalizado"
    )
