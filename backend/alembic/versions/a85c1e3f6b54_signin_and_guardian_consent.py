"""phone and Google sign-in, guardian consent for under-18s

Revision ID: a85c1e3f6b54
Revises: f74b0d2e5a43
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a85c1e3f6b54"
down_revision: Union[str, Sequence[str], None] = "f74b0d2e5a43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.alter_column("email", existing_type=sa.String(254), nullable=True)
        b.add_column(sa.Column("phone", sa.String(20), nullable=True))
        b.add_column(sa.Column("tekurious_user_id", sa.String(64), nullable=True))
        b.add_column(sa.Column("google_sub", sa.String(64), nullable=True))
        b.add_column(sa.Column("is_minor", sa.Boolean(), nullable=False, server_default="0"))
        b.add_column(sa.Column("guardian_consent_status", sa.String(20), nullable=False, server_default="not_required"))
        b.add_column(sa.Column("guardian_email", sa.String(254), nullable=True))
        b.add_column(sa.Column("guardian_token_hash", sa.String(64), nullable=True))
        b.add_column(sa.Column("guardian_token_expires_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("guardian_consent_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("guardian_consent_by", sa.String(200), nullable=True))
        b.add_column(sa.Column("privacy_accepted", sa.String(20), nullable=True))
        b.create_index("ix_users_phone", ["phone"], unique=True)
        b.create_index("ix_users_google_sub", ["google_sub"], unique=True)
        b.create_index("ix_users_guardian_token_hash", ["guardian_token_hash"])
        b.create_unique_constraint("uq_users_tekurious_user_id", ["tekurious_user_id"])
    # Students who signed up before the age question was asked answer it once at their next visit.
    op.execute("UPDATE users SET guardian_consent_status = 'ask' WHERE role = 'student'")


def downgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.drop_constraint("uq_users_tekurious_user_id", type_="unique")
        b.drop_index("ix_users_guardian_token_hash")
        b.drop_index("ix_users_google_sub")
        b.drop_index("ix_users_phone")
        for c in ("privacy_accepted", "guardian_consent_by", "guardian_consent_at", "guardian_token_expires_at",
                  "guardian_token_hash", "guardian_email", "guardian_consent_status", "is_minor", "google_sub",
                  "tekurious_user_id", "phone"):
            b.drop_column(c)
