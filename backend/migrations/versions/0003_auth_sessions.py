"""Revocable sessions, refresh history and shared login rate counters."""
from alembic import op

revision = "0003_auth_sessions"
down_revision = "0002_identity_guards"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE auth_sessions (
        id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES users(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ, CHECK (expires_at > created_at))""")
    op.execute("CREATE INDEX auth_sessions_user ON auth_sessions(user_id)")
    op.execute("""CREATE TABLE refresh_tokens (
        token_hash TEXT PRIMARY KEY, session_id UUID NOT NULL REFERENCES auth_sessions(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), consumed_at TIMESTAMPTZ)""")
    op.execute("CREATE INDEX refresh_tokens_session ON refresh_tokens(session_id)")
    op.execute("""CREATE TABLE login_counters (
        ip TEXT NOT NULL, window_start TIMESTAMPTZ NOT NULL,
        attempts INTEGER NOT NULL CHECK (attempts > 0), PRIMARY KEY(ip, window_start))""")
    op.execute("CREATE INDEX login_counters_window ON login_counters(window_start)")


def downgrade():
    op.drop_table("login_counters")
    op.drop_table("refresh_tokens")
    op.drop_table("auth_sessions")
