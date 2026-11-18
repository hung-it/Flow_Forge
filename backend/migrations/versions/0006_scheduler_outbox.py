"""Store the current dispatch generation and durable outbox."""

from alembic import op

revision = "0006_scheduler_outbox"
down_revision = "0005_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE task_runs ADD COLUMN dispatch_generation INTEGER
                  NOT NULL DEFAULT 0 CHECK (dispatch_generation >= 0)""")
    op.execute("""CREATE TABLE outbox_events (
        id UUID PRIMARY KEY,
        task_run_id UUID NOT NULL REFERENCES task_runs(id),
        dispatch_generation INTEGER NOT NULL CHECK (dispatch_generation > 0),
        event_type TEXT NOT NULL CHECK (event_type = 'task.execute'),
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        status TEXT NOT NULL DEFAULT 'PENDING'
            CHECK (status IN ('PENDING', 'CLAIMED', 'PUBLISHED', 'OBSOLETE')),
        available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        claim_token UUID,
        claim_until TIMESTAMPTZ,
        published_at TIMESTAMPTZ,
        last_redriven_at TIMESTAMPTZ,
        publish_attempts INTEGER NOT NULL DEFAULT 0 CHECK (publish_attempts >= 0),
        UNIQUE (task_run_id, dispatch_generation, event_type),
        CHECK ((claim_token IS NOT NULL) = (status = 'CLAIMED')),
        CHECK ((claim_until IS NOT NULL) = (status = 'CLAIMED')),
        CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL)
    )""")
    op.execute("""CREATE INDEX outbox_pending ON outbox_events(available_at,id)
                  WHERE status='PENDING'""")
    op.execute("""CREATE INDEX outbox_expired_claim ON outbox_events(claim_until,id)
                  WHERE status='CLAIMED'""")
    op.execute("""CREATE INDEX outbox_redrive ON outbox_events(published_at,id)
                  WHERE status='PUBLISHED'""")
    op.execute("""CREATE INDEX scheduler_active_runs ON workflow_runs(created_at,id)
                  WHERE status IN ('PENDING','RUNNING')""")


def downgrade():
    op.execute("DROP INDEX scheduler_active_runs")
    op.execute("DROP TABLE outbox_events")
    op.execute("ALTER TABLE task_runs DROP COLUMN dispatch_generation")
