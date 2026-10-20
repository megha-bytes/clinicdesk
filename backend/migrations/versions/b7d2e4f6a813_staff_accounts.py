"""staff accounts, sessions, conversation thread keys (Day 21)

Revision ID: b7d2e4f6a813
Revises: 9c1e7a4b2d10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2e4f6a813"
down_revision: Union[str, Sequence[str], None] = "9c1e7a4b2d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_user",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("clinic_id", sa.String(32), sa.ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(200), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("doctor_id", sa.String(32), sa.ForeignKey("doctor.id", ondelete="SET NULL")),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("demo", sa.Boolean(), nullable=False),
        sa.Column("failed_logins", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner','receptionist','doctor')", name="ck_staff_role"),
    )
    op.create_index("ix_staff_user_clinic_id", "staff_user", ["clinic_id"])
    op.create_table(
        "staff_session",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("staff_user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_staff_session_user_id", "staff_session", ["user_id"])
    with op.batch_alter_table("conversation") as b:
        b.add_column(sa.Column("thread_key_hash", sa.String(64)))


def downgrade() -> None:
    with op.batch_alter_table("conversation") as b:
        b.drop_column("thread_key_hash")
    op.drop_index("ix_staff_session_user_id", "staff_session")
    op.drop_table("staff_session")
    op.drop_index("ix_staff_user_clinic_id", "staff_user")
    op.drop_table("staff_user")
