"""Require canonical email and at most one OWNER membership per project.

Existing noncanonical data is rejected, never silently changed or merged.
"""
from alembic import op

revision = "0002_identity_guards"
down_revision = "0001_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE users ADD CONSTRAINT users_email_canonical
        CHECK (email = lower(btrim(email)) AND length(email) BETWEEN 3 AND 254)""")
    op.execute("""CREATE UNIQUE INDEX project_one_owner
        ON project_members(project_id) WHERE role = 'OWNER'""")


def downgrade():
    op.execute("DROP INDEX project_one_owner")
    op.drop_constraint("users_email_canonical", "users", type_="check")
