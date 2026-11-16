"""Identity foundation; authentication behavior follows in milestone 1."""
from alembic import op

revision = "0001_identity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE users (
        id UUID PRIMARY KEY, email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE projects (
        id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES users(id),
        name TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE project_members (
        project_id UUID NOT NULL REFERENCES projects(id),
        user_id UUID NOT NULL REFERENCES users(id),
        role TEXT NOT NULL CHECK (role IN ('OWNER','DEVELOPER','VIEWER')),
        PRIMARY KEY(project_id,user_id)
    );
    """)


def downgrade():
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
