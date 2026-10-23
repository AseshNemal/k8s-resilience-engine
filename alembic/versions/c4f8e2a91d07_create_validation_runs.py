"""create validation runs

Revision ID: c4f8e2a91d07
Revises: ad9a50b92535
Create Date: 2026-10-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f8e2a91d07'
down_revision: Union[str, None] = 'ad9a50b92535'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('validation_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('experiment_id', sa.String(length=80), nullable=False),
    sa.Column('recommendation_id', sa.String(length=100), nullable=False),
    sa.Column('approval_id', sa.Integer(), nullable=True),
    sa.Column('workflow_id', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('validated', sa.Boolean(), nullable=True),
    sa.Column('is_measurement', sa.Boolean(), nullable=False),
    sa.Column('evidence_class', sa.String(length=30), nullable=False),
    sa.Column('cluster', sa.JSON(), nullable=False),
    sa.Column('workflow', sa.JSON(), nullable=False),
    sa.Column('before_evidence', sa.JSON(), nullable=False),
    sa.Column('recommendation', sa.JSON(), nullable=False),
    sa.Column('proposal', sa.JSON(), nullable=False),
    sa.Column('predicted', sa.JSON(), nullable=False),
    sa.Column('observed', sa.JSON(), nullable=True),
    sa.Column('comparison', sa.JSON(), nullable=True),
    sa.Column('prediction_error', sa.Float(), nullable=True),
    sa.Column('failure', sa.JSON(), nullable=True),
    sa.Column('events', sa.JSON(), nullable=False),
    sa.Column('provenance', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_validation_runs_experiment_id'), 'validation_runs', ['experiment_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_validation_runs_experiment_id'), table_name='validation_runs')
    op.drop_table('validation_runs')
