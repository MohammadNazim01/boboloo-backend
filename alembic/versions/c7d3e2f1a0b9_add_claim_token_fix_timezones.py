"""fix timezone-naive datetime columns

Revision ID: c7d3e2f1a0b9
Revises: f3a7c2d8e1b0
Create Date: 2026-06-09

Changes:
  - messages.created_at, audit_logs.created_at, child_analytics.updated_at,
    analytics_history.created_at, children.created_at, children.updated_at:
    converted from TIMESTAMP WITHOUT TIME ZONE to TIMESTAMP WITH TIME ZONE.
    Existing values are interpreted as UTC (which is how they were stored).
"""

from alembic import op
import sqlalchemy as sa

revision = "c7d3e2f1a0b9"
down_revision = "f3a7c2d8e1b0"
branch_labels = None
depends_on = None


def upgrade():
    for table, column in [
        ("messages",          "created_at"),
        ("audit_logs",        "created_at"),
        ("child_analytics",   "updated_at"),
        ("analytics_history", "created_at"),
        ("children",          "created_at"),
        ("children",          "updated_at"),
    ]:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=True),
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            existing_nullable=True,
        )


def downgrade():
    for table, column in [
        ("messages",          "created_at"),
        ("audit_logs",        "created_at"),
        ("child_analytics",   "updated_at"),
        ("analytics_history", "created_at"),
        ("children",          "created_at"),
        ("children",          "updated_at"),
    ]:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=False),
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            existing_nullable=True,
        )
