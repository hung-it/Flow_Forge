"""Persist fenced worker attempts, results and retry timing."""

from alembic import op

revision = "0007_worker_execution"
down_revision = "0006_scheduler_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE task_runs
        ADD COLUMN lease_token UUID,
        ADD COLUMN lease_until TIMESTAMPTZ,
        ADD COLUMN hard_deadline TIMESTAMPTZ,
        ADD COLUMN next_run_at TIMESTAMPTZ,
        ADD COLUMN result_json JSONB,
        ADD CONSTRAINT task_lease_state CHECK (
            (status='RUNNING')=(lease_token IS NOT NULL)
            AND (status='RUNNING')=(lease_until IS NOT NULL)
            AND (status='RUNNING')=(hard_deadline IS NOT NULL)
            AND (lease_until IS NULL OR lease_until<=hard_deadline)),
        ADD CONSTRAINT task_retry_state CHECK ((status='RETRY_WAIT')=(next_run_at IS NOT NULL)),
        ADD CONSTRAINT task_result_state CHECK (result_json IS NULL OR
            (status='SUCCEEDED' AND jsonb_typeof(result_json)='object'))
    """)
    op.execute("""CREATE TABLE task_attempts (
        id UUID PRIMARY KEY,
        task_run_id UUID NOT NULL REFERENCES task_runs(id),
        attempt_no INTEGER NOT NULL CHECK (attempt_no>0),
        dispatch_generation INTEGER NOT NULL CHECK (dispatch_generation>0),
        worker_id TEXT NOT NULL,
        lease_token UUID NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','LOST','CANCELED')),
        error_code TEXT,
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ,
        CHECK ((status='RUNNING')=(finished_at IS NULL)),
        CHECK (finished_at IS NULL OR finished_at>=started_at),
        UNIQUE(task_run_id,attempt_no))""")
    op.execute("""CREATE UNIQUE INDEX one_running_task_attempt ON task_attempts(task_run_id)
        WHERE status='RUNNING'""")
    op.execute("""CREATE INDEX worker_expired_lease ON task_runs(lease_until,id)
        WHERE status='RUNNING'""")
    op.execute("""CREATE INDEX worker_due_retry ON task_runs(next_run_at,id)
        WHERE status='RETRY_WAIT'""")


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM task_runs WHERE status IN ('RUNNING','RETRY_WAIT')) THEN
            RAISE EXCEPTION 'Drain active worker state before downgrading';
        END IF;
    END $$""")
    op.execute("DROP INDEX worker_due_retry")
    op.execute("DROP INDEX worker_expired_lease")
    op.execute("DROP TABLE task_attempts")
    op.execute("""ALTER TABLE task_runs DROP CONSTRAINT task_lease_state,
        DROP CONSTRAINT task_retry_state,DROP CONSTRAINT task_result_state,
        DROP COLUMN lease_token,DROP COLUMN lease_until,DROP COLUMN hard_deadline,
        DROP COLUMN next_run_at,DROP COLUMN result_json""")
