"""llm_call.tier fits "lightning" (DEVLOG #45)

Revision ID: 9c1e7a4b2d10
Revises: 5281dfa49c85
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9c1e7a4b2d10"
down_revision: Union[str, Sequence[str], None] = "5281dfa49c85"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_call") as b:
        b.alter_column("tier", existing_type=sa.String(8), type_=sa.String(16), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("llm_call") as b:
        b.alter_column("tier", existing_type=sa.String(16), type_=sa.String(8), existing_nullable=False)
