"""remove_fake_analytics_columns

Revision ID: a3c9f1d72e44
Revises: 1f980c2333d9
Create Date: 2026-05-14 00:00:00.000000

Remove fq/vq/cq/mq/gq and other unused scoring columns from child_analytics
and analytics_history. Add breakdown_json to analytics_history.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a3c9f1d72e44'
down_revision: Union[str, None] = '1f980c2333d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------
    # child_analytics: remove fake scoring columns
    # -----------------------------------------------
    op.drop_column('child_analytics', 'fq')
    op.drop_column('child_analytics', 'vq')
    op.drop_column('child_analytics', 'cq')
    op.drop_column('child_analytics', 'mq')
    op.drop_column('child_analytics', 'gq')
    op.drop_column('child_analytics', 'velocity')
    op.drop_column('child_analytics', 'confidence')
    op.drop_column('child_analytics', 'trend_percent')
    op.drop_column('child_analytics', 'insight_json')
    op.drop_column('child_analytics', 'algorithm_version')

    # -----------------------------------------------
    # analytics_history: remove fake scoring columns
    #                     add breakdown_json
    # -----------------------------------------------
    op.drop_column('analytics_history', 'fq')
    op.drop_column('analytics_history', 'vq')
    op.drop_column('analytics_history', 'cq')
    op.drop_column('analytics_history', 'mq')
    op.drop_column('analytics_history', 'gq')

    op.add_column(
        'analytics_history',
        sa.Column('breakdown_json', postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    # analytics_history
    op.drop_column('analytics_history', 'breakdown_json')
    op.add_column('analytics_history', sa.Column('fq', sa.Float(), nullable=True))
    op.add_column('analytics_history', sa.Column('vq', sa.Float(), nullable=True))
    op.add_column('analytics_history', sa.Column('cq', sa.Float(), nullable=True))
    op.add_column('analytics_history', sa.Column('mq', sa.Float(), nullable=True))
    op.add_column('analytics_history', sa.Column('gq', sa.Float(), nullable=True))

    # child_analytics
    op.add_column('child_analytics', sa.Column('algorithm_version', sa.String(), nullable=True))
    op.add_column('child_analytics', sa.Column('insight_json', postgresql.JSONB(), nullable=True))
    op.add_column('child_analytics', sa.Column('trend_percent', sa.Float(), nullable=True))
    op.add_column('child_analytics', sa.Column('confidence', sa.Float(), nullable=True))
    op.add_column('child_analytics', sa.Column('velocity', sa.String(), nullable=True))
    op.add_column('child_analytics', sa.Column('gq', sa.Float(), nullable=False, server_default='0'))
    op.add_column('child_analytics', sa.Column('mq', sa.Float(), nullable=False, server_default='0'))
    op.add_column('child_analytics', sa.Column('cq', sa.Float(), nullable=False, server_default='0'))
    op.add_column('child_analytics', sa.Column('vq', sa.Float(), nullable=False, server_default='0'))
    op.add_column('child_analytics', sa.Column('fq', sa.Float(), nullable=False, server_default='0'))
