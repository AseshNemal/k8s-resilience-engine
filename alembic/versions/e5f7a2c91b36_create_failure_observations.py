"""Persist observations of failures the developer starts themselves (read-only watches)."""
from alembic import op
import sqlalchemy as sa

revision = "e5f7a2c91b36"
down_revision = "d4e8b1f60a23"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("failure_observations",
        sa.Column("observation_id", sa.String(80), primary_key=True),
        sa.Column("active_slot", sa.Integer(), nullable=True, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("namespace", sa.String(253), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("failure_observations")
