"""add rollout tracking to applied changes and create applied change analyses

Revision ID: f4b91c2d7e03
Revises: e7a1d3b58c20
Create Date: 2026-10-23 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b91c2d7e03'
down_revision: Union[str, None] = 'e7a1d3b58c20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('applied_changes') as batch:
        batch.add_column(sa.Column('rollout', sa.JSON(), nullable=True))
    op.create_table('applied_change_analyses',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('analysis_id', sa.String(length=80), nullable=False),
    sa.Column('application_id', sa.String(length=80), nullable=False),
    sa.Column('recommendation_id', sa.String(length=100), nullable=False),
    sa.Column('direction', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('basis', sa.JSON(), nullable=False),
    sa.Column('baseline', sa.JSON(), nullable=False),
    sa.Column('predicted', sa.JSON(), nullable=True),
    sa.Column('observed', sa.JSON(), nullable=True),
    sa.Column('comparison', sa.JSON(), nullable=True),
    sa.Column('error', sa.JSON(), nullable=True),
    sa.Column('label', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('applied_change_analyses') as batch:
        batch.create_index(batch.f('ix_applied_change_analyses_analysis_id'), ['analysis_id'], unique=True)
        batch.create_index(batch.f('ix_applied_change_analyses_application_id'), ['application_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('applied_change_analyses') as batch:
        batch.drop_index(batch.f('ix_applied_change_analyses_application_id'))
        batch.drop_index(batch.f('ix_applied_change_analyses_analysis_id'))
    op.drop_table('applied_change_analyses')
    with op.batch_alter_table('applied_changes') as batch:
        batch.drop_column('rollout')
