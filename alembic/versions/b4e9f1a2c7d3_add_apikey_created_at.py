"""add api_keys.created_at

Revision ID: b4e9f1a2c7d3
Revises: f3a7c2d8e1b0
Create Date: 2026-06-01

"""
from alembic import op
import sqlalchemy as sa

revision = "b4e9f1a2c7d3"
down_revision = "f3a7c2d8e1b0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "api_keys",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,  # temporarily nullable so the backfill can run first
        ),
    )
    # Back-fill existing rows before tightening the constraint.
    op.execute(
        "UPDATE api_keys SET created_at = NOW() WHERE created_at IS NULL"
    )
    # Now that every row has a value, enforce NOT NULL at the DB level so the
    # model's nullable=False declaration is accurate and autogenerate won't drift.
    op.alter_column("api_keys", "created_at", nullable=False)


def downgrade():
    op.drop_column("api_keys", "created_at")
