"""Preserve trace context across the durable workflow boundary."""

from alembic import op

revision = "0008_run_trace_context"
down_revision = "0007_worker_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE workflow_runs ADD COLUMN traceparent VARCHAR(55)")


def downgrade():
    op.execute("ALTER TABLE workflow_runs DROP COLUMN traceparent")
