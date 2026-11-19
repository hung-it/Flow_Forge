"""Workflow definitions, editable drafts and immutable publications."""

from alembic import op

revision = "0004_workflows"
down_revision = "0003_auth_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE workflow_definitions (
      id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES projects(id),
      name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100 AND name=btrim(name)),
      created_by UUID NOT NULL REFERENCES users(id),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE(project_id,id))""")
    op.execute("CREATE INDEX workflows_project ON workflow_definitions(project_id,created_at,id)")
    op.execute("""CREATE TABLE workflow_drafts (
      workflow_id UUID PRIMARY KEY REFERENCES workflow_definitions(id),
      dag JSONB NOT NULL CHECK(jsonb_typeof(dag)='object'),
      revision BIGINT NOT NULL CHECK(revision>0), updated_by UUID NOT NULL REFERENCES users(id),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE workflow_versions (
      id UUID PRIMARY KEY, workflow_id UUID NOT NULL REFERENCES workflow_definitions(id),
      version INTEGER NOT NULL CHECK(version>0),
      dag JSONB NOT NULL CHECK(jsonb_typeof(dag)='object'),
      checksum TEXT NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'),
      handler_contracts JSONB NOT NULL CHECK(jsonb_typeof(handler_contracts)='object'),
      source_draft_revision BIGINT NOT NULL CHECK(source_draft_revision>0),
      created_by UUID NOT NULL REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE(workflow_id,version), UNIQUE(workflow_id,id))""")
    op.execute("""CREATE FUNCTION reject_version_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Published versions are immutable'; END $$""")
    op.execute("""CREATE TRIGGER immutable_versions BEFORE UPDATE OR DELETE ON workflow_versions
      FOR EACH ROW EXECUTE FUNCTION reject_version_mutation()""")


def downgrade():
    op.execute("DROP TABLE workflow_versions")
    op.execute("DROP FUNCTION reject_version_mutation()")
    op.execute("DROP TABLE workflow_drafts")
    op.execute("DROP TABLE workflow_definitions")
