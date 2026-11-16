"""add the after-snapshot to applied changes

Additive: one nullable column. An existing row keeps NULL, which Reset reads as "what this change set is unknown".

Revision ID: c9d2f7a41e86
Revises: b71d8ea210c4
Create Date: 2026-11-16 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d2f7a41e86'
down_revision: Union[str, None] = 'b71d8ea210c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('applied_changes') as batch:
        batch.add_column(sa.Column('after', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('applied_changes') as batch:
        batch.drop_column('after')
