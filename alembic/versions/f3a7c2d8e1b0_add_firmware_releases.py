"""add firmware_releases table

Revision ID: f3a7c2d8e1b0
Revises: e5f8a2b1c6d0
Create Date: 2026-05-18

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "f3a7c2d8e1b0"
down_revision = "e5f8a2b1c6d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "firmware_releases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("s3_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("is_stable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("release_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.String(), nullable=True),
    )
    op.create_index(
        "idx_firmware_version",
        "firmware_releases",
        ["version"],
        unique=True,
    )


def downgrade():
    op.drop_index("idx_firmware_version", table_name="firmware_releases")
    op.drop_table("firmware_releases")
