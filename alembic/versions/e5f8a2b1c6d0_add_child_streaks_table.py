"""add child_streaks table

Revision ID: e5f8a2b1c6d0
Revises: cc3b44bbc3d2
Create Date: 2026-05-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "e5f8a2b1c6d0"
down_revision = "cc3b44bbc3d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "child_streaks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "child_id",
            UUID(as_uuid=True),
            sa.ForeignKey("children.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("current_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("longest_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_conversation_date", sa.Date(), nullable=True),
        sa.Column("streak_started_at", sa.Date(), nullable=True),
    )
    op.create_index("idx_child_streaks_child_id", "child_streaks", ["child_id"])


def downgrade():
    op.drop_index("idx_child_streaks_child_id", table_name="child_streaks")
    op.drop_table("child_streaks")
