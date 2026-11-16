"""Append-only hybrid workflow editor and recording revisions.

Revision ID: d4e8b1f60a23
Revises: c9d2f7a41e86
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e8b1f60a23"
down_revision = "c9d2f7a41e86"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workflow_management_revisions",
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("identity", sa.String(100), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False))


def downgrade():
    op.drop_table("workflow_management_revisions")
