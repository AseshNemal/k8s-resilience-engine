"""create validation loop runs and experiments

Revision ID: a3c6e1f90b52
Revises: f4b91c2d7e03
Create Date: 2026-10-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c6e1f90b52'
down_revision: Union[str, None] = 'f4b91c2d7e03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('validation_loop_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_key', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('target_kind', sa.String(length=30), nullable=False),
    sa.Column('target_name', sa.String(length=100), nullable=False),
    sa.Column('cluster_uid', sa.String(length=100), nullable=False),
    sa.Column('namespace', sa.String(length=100), nullable=False),
    sa.Column('workflow_id', sa.String(length=100), nullable=False),
    sa.Column('approval', sa.JSON(), nullable=False),
    sa.Column('plan', sa.JSON(), nullable=False),
    sa.Column('kills_used', sa.Integer(), nullable=False),
    sa.Column('abort_requested', sa.Boolean(), nullable=False),
    sa.Column('current', sa.JSON(), nullable=True),
    sa.Column('restore_points', sa.JSON(), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('recovery', sa.JSON(), nullable=True),
    sa.Column('stop_reason', sa.String(), nullable=True),
    sa.Column('events', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_validation_loop_runs_run_key'), 'validation_loop_runs', ['run_key'], unique=True)
    op.create_index(op.f('ix_validation_loop_runs_status'), 'validation_loop_runs', ['status'], unique=False)
    op.create_table('validation_loop_experiments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_key', sa.String(length=80), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('candidate_id', sa.String(length=100), nullable=True),
    sa.Column('worker', sa.String(length=100), nullable=False),
    sa.Column('repeat', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('experiment_id', sa.String(length=80), nullable=True),
    sa.Column('outcome', sa.JSON(), nullable=True),
    sa.Column('duration_s', sa.Float(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_validation_loop_experiments_run_key'), 'validation_loop_experiments', ['run_key'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_validation_loop_experiments_run_key'), table_name='validation_loop_experiments')
    op.drop_table('validation_loop_experiments')
    op.drop_index(op.f('ix_validation_loop_runs_status'), table_name='validation_loop_runs')
    op.drop_index(op.f('ix_validation_loop_runs_run_key'), table_name='validation_loop_runs')
    op.drop_table('validation_loop_runs')
