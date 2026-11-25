"""saved institutes and institute reviews

Revision ID: f74b0d2e5a43
Revises: e63a9c1d4f32
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f74b0d2e5a43"
down_revision: Union[str, Sequence[str], None] = "e63a9c1d4f32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_institutes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("institute_id", sa.Integer(), sa.ForeignKey("institutes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "institute_id"),
    )
    op.create_index("ix_saved_institutes_user_id", "saved_institutes", ["user_id"])
    op.create_table(
        "institute_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("institute_id", sa.Integer(), sa.ForeignKey("institutes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(12), nullable=False, server_default="aspirant"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "institute_id"),
    )
    op.create_index("ix_institute_reviews_institute_id", "institute_reviews", ["institute_id"])
    op.create_index("ix_institute_reviews_user_id", "institute_reviews", ["user_id"])
    op.create_index("ix_institute_reviews_status", "institute_reviews", ["status"])


def downgrade() -> None:
    op.drop_table("institute_reviews")
    op.drop_table("saved_institutes")
