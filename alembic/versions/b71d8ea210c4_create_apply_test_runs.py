"""Persist explicit Apply and test runs independently of page lifetime."""
from alembic import op
import sqlalchemy as sa

revision = "b71d8ea210c4"
down_revision = "a3c6e1f90b52"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("apply_test_runs",
        sa.Column("run_id", sa.String(80), primary_key=True),
        sa.Column("request_key", sa.String(80), nullable=False, unique=True),
        sa.Column("active_slot", sa.Integer(), nullable=True, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("apply_test_runs")
