"""Persist runs, tasks and direct dependencies from a published workflow version."""

from alembic import op

revision = "0005_runs"
down_revision = "0004_workflows"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE workflow_runs (
      id UUID PRIMARY KEY, project_id UUID NOT NULL, workflow_id UUID NOT NULL,
      workflow_version_id UUID NOT NULL, created_by UUID NOT NULL REFERENCES users(id),
      status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELED')),
      input_json JSONB NOT NULL CHECK(jsonb_typeof(input_json)='object'),
      input_hash TEXT NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
      idempotency_key TEXT CHECK(idempotency_key ~ '^[A-Za-z0-9._~-]{1,128}$'),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), started_at TIMESTAMPTZ,
      finished_at TIMESTAMPTZ,
      CHECK((status IN ('SUCCEEDED','FAILED','CANCELED')) = (finished_at IS NOT NULL)),
      FOREIGN KEY(project_id,workflow_id) REFERENCES workflow_definitions(project_id,id),
      FOREIGN KEY(workflow_id,workflow_version_id) REFERENCES workflow_versions(workflow_id,id)
    )""")
    op.execute("""CREATE UNIQUE INDEX runs_project_key ON workflow_runs(project_id,idempotency_key)
      WHERE idempotency_key IS NOT NULL""")
    op.execute("""CREATE INDEX runs_project_created
      ON workflow_runs(project_id,created_at DESC,id DESC)""")
    op.execute("""CREATE TABLE task_runs (
      id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES workflow_runs(id),
      node_key TEXT NOT NULL CHECK(node_key ~ '^[a-z][a-z0-9_]{0,63}$'),
      status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN
        ('PENDING','READY','QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED','SKIPPED','CANCELED')),
      attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
      UNIQUE(run_id,node_key), UNIQUE(run_id,id)
    )""")
    op.execute("""CREATE TABLE task_dependencies (
      run_id UUID NOT NULL, predecessor_task_id UUID NOT NULL, successor_task_id UUID NOT NULL,
      PRIMARY KEY(run_id,predecessor_task_id,successor_task_id),
      CHECK(predecessor_task_id<>successor_task_id),
      FOREIGN KEY(run_id,predecessor_task_id) REFERENCES task_runs(run_id,id),
      FOREIGN KEY(run_id,successor_task_id) REFERENCES task_runs(run_id,id)
    )""")
    op.execute("CREATE INDEX dependencies_successor ON task_dependencies(run_id,successor_task_id)")


def downgrade():
    op.execute("DROP TABLE task_dependencies")
    op.execute("DROP TABLE task_runs")
    op.execute("DROP TABLE workflow_runs")
