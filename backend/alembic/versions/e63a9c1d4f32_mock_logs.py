"""mock logs (outside mock tests the student records)

Revision ID: e63a9c1d4f32
Revises: d52f8b0c3e21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e63a9c1d4f32"
down_revision: Union[str, Sequence[str], None] = "d52f8b0c3e21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mock_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False, server_default=""),
        sa.Column("exam", sa.String(10), nullable=False, server_default="main"),
        sa.Column("test_date", sa.Date(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("max_score", sa.Float(), nullable=False),
        sa.Column("physics", sa.Float(), nullable=True),
        sa.Column("chemistry", sa.Float(), nullable=True),
        sa.Column("mathematics", sa.Float(), nullable=True),
        sa.Column("percentile", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mock_logs_user_id", "mock_logs", ["user_id"])


def downgrade() -> None:
    op.drop_table("mock_logs")
