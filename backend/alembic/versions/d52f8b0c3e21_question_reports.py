"""question reports ("Report a problem" on a result)

Revision ID: d52f8b0c3e21
Revises: c41e7a9b2d10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d52f8b0c3e21"
down_revision: Union[str, Sequence[str], None] = "c41e7a9b2d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False, server_default=""),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_question_reports_question_id", "question_reports", ["question_id"])
    op.create_index("ix_question_reports_user_id", "question_reports", ["user_id"])
    op.create_index("ix_question_reports_status", "question_reports", ["status"])


def downgrade() -> None:
    op.drop_table("question_reports")
