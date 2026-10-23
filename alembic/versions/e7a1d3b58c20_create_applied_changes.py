"""create applied changes

Revision ID: e7a1d3b58c20
Revises: c4f8e2a91d07
Create Date: 2026-10-23 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a1d3b58c20'
down_revision: Union[str, None] = 'c4f8e2a91d07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('applied_changes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('application_id', sa.String(length=80), nullable=False),
    sa.Column('recommendation_id', sa.String(length=100), nullable=False),
    sa.Column('approval_id', sa.Integer(), nullable=False),
    sa.Column('tier', sa.String(length=20), nullable=False),
    sa.Column('namespace', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('yaml_applied', sa.String(), nullable=False),
    sa.Column('yaml_sha256', sa.String(length=64), nullable=False),
    sa.Column('before', sa.JSON(), nullable=False),
    sa.Column('cluster', sa.JSON(), nullable=False),
    sa.Column('events', sa.JSON(), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=True),
    sa.Column('applied_by', sa.String(length=100), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reset_by', sa.String(length=100), nullable=True),
    sa.Column('reset_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_applied_changes_application_id'), 'applied_changes', ['application_id'], unique=True)
    op.create_index(op.f('ix_applied_changes_recommendation_id'), 'applied_changes', ['recommendation_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_applied_changes_recommendation_id'), table_name='applied_changes')
    op.drop_index(op.f('ix_applied_changes_application_id'), table_name='applied_changes')
    op.drop_table('applied_changes')
