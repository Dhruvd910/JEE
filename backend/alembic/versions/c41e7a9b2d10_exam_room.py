"""exam room: palette state, time per question, instructions start, focus count

Revision ID: c41e7a9b2d10
Revises: 49a5f3abe22f
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c41e7a9b2d10"
down_revision: Union[str, Sequence[str], None] = "49a5f3abe22f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("attempts") as b:
        b.add_column(sa.Column("begun_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("focus_lost", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("attempt_items") as b:
        b.add_column(sa.Column("visited", sa.Boolean(), nullable=False, server_default="0"))
        b.add_column(sa.Column("marked", sa.Boolean(), nullable=False, server_default="0"))
        b.add_column(sa.Column("seconds", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("attempt_items") as b:
        b.drop_column("seconds")
        b.drop_column("marked")
        b.drop_column("visited")
    with op.batch_alter_table("attempts") as b:
        b.drop_column("focus_lost")
        b.drop_column("begun_at")
