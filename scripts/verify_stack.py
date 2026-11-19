"""Build and exercise a new disposable Compose project; never touches other stacks."""
import json
import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return str(sock.getsockname()[1])


def main():
    project = "ff-foundation-" + uuid.uuid4().hex[:12]
    env = {**os.environ, "API_PORT": free_port(), "WEB_PORT": free_port(),
           "GOMEMLIMIT": "64MiB", "GOMAXPROCS": "2"}
    # Use only this repository's development credentials/network/volumes.
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value
    env["FLOWFORGE_JWT_SECRET"] = secrets.token_urlsafe(48)
    env["FLOWFORGE_GRAFANA_ADMIN_PASSWORD"] = secrets.token_urlsafe(32)
    env["GRAFANA_PORT"] = free_port()
    env["JAEGER_PORT"] = free_port()
    base = ["docker", "compose", "--env-file", ".env.example", "-p", project]
    overlay = ["-f", "compose.yaml", "-f", "compose.observability.yaml",
               "--profile", "observability"]

    def compose(*args, expected=0, input_text=None, telemetry=False):
        result = subprocess.run(base + (overlay if telemetry else []) + list(args),
                                check=False, cwd=ROOT, env=env,
                                input=input_text,
                                encoding="utf-8", errors="replace",
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode != expected:
            raise RuntimeError(f"compose {args}: exit {result.returncode}\n{result.stdout}")
        return result.stdout

    def status(path):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{env['API_PORT']}{path}", timeout=8
            ) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def eventually(check, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(2)
        raise AssertionError("Recovery did not complete within deadline")

    def sql(query):
        return compose("exec", "-T", "postgres", "psql", "-U", "flowforge", "-d",
                       "flowforge", "-At", "-v", "ON_ERROR_STOP=1", "-c", query).strip()

    reports = ROOT / "reports" / project

    def dashboard(phase):
        accounts = compose("exec", "-T", "api", "python", "integration/dashboard_accounts.py")
        output = compose("run", "--rm", "--no-deps", "-T", "dashboard-smoke", phase,
                         input_text=accounts.splitlines()[-1])
        with (reports / "dashboard-smoke.log").open("a", encoding="utf-8") as log:
            log.write(output)
        print(output, flush=True)

    def healthy(role):
        try:
            compose("exec", "-T", role, "python", "-m", "flowforge.process", role, "--health")
            return True
        except RuntimeError:
            return False

    def observability():
        def run(*args):
            return compose(*args, telemetry=True)

        def phase(name):
            output = run("exec", "-T", "-e", "FLOWFORGE_GRAFANA_ADMIN_PASSWORD", "api",
                         "python", "integration/telemetry_acceptance.py", name)
            with (reports / "telemetry-smoke.log").open("a", encoding="utf-8") as log:
                log.write(output)
            print(output, flush=True)

        compose("stop", "publisher", "worker")
        run("up", "-d", "--no-build", "--wait",
            "api", "scheduler", "rabbitmq", "prometheus", "jaeger", "grafana")
        phase("queued")
        run("up", "-d", "--no-build", "--wait", "publisher")
        phase("broker")
        run("up", "-d", "--no-build", "--wait", "--scale", "worker=2", "worker")
        phase("completed")
        run("stop", "prometheus", "jaeger")
        phase("offline")
        run("up", "-d", "--no-build", "--wait", "prometheus", "jaeger")
        phase("restored")
        logs = run("logs", "--no-color", "api", "scheduler", "publisher", "worker")
        assert "flowforge-observability-smoke-private-input" not in logs
        assert '"event": "run.created"' in logs and '"trace_id":' in logs

    reports.mkdir(parents=True)
    print(f"Disposable project: {project}", flush=True)
    try:
        compose("build", "api")
        compose("build", "frontend")
        compose("--profile", "smoke", "build", "dashboard-smoke")
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
        assert status("/health/ready") == 200
        with urllib.request.urlopen(
            f"http://127.0.0.1:{env['WEB_PORT']}/health/ready", timeout=8
        ) as response:
            assert json.load(response)["status"] == "ready"
        # Auth tests need API/DB only. Avoid idle process/probe memory overlapping Argon2 tests.
        compose("stop", "scheduler", "publisher", "worker", "frontend")
        print(compose("exec", "-T", "api", "pytest", "tests", "integration", "-q",
                      "--cov=flowforge", "--cov-report=json:/tmp/coverage.json"), flush=True)
        print(compose("exec", "-T", "api", "python", "-m", "flowforge.coverage_gate",
                      "/tmp/coverage.json"), flush=True)
        compose("cp", "api:/tmp/coverage.json", str(reports / "coverage.json"))
        print(compose("exec", "-T", "api", "python", "integration/http_acceptance.py"), flush=True)
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180",
                "scheduler", "publisher", "frontend")

        compose("stop", "rabbitmq")
        compose("exec", "-T", "api", "python", "-m", "flowforge.process",
                "worker", "--probe", expected=1)
        assert status("/health/ready") == 200
        compose("exec", "-T", "api", "python", "-m", "flowforge.process",
                "scheduler", "--probe")
        print(compose("exec", "-T", "api", "python", "integration/dispatch_acceptance.py",
                      "outage"), flush=True)
        compose("up", "-d", "--wait", "rabbitmq")
        eventually(lambda: healthy("publisher"))
        print(compose("exec", "-T", "api", "python", "integration/dispatch_acceptance.py",
                      "recovery"), flush=True)
        print("Broker outage/recovery passed", flush=True)

        compose("stop", "publisher")
        print(compose("exec", "-T", "api", "python", "integration/dispatch_acceptance.py",
                      "backlog"), flush=True)
        compose("stop", "api", "scheduler", "frontend")
        print(compose("run", "--rm", "--no-deps", "api", "python",
                      "integration/migration_acceptance.py", "worker"), flush=True)
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
        print(compose("exec", "-T", "api", "python", "integration/dispatch_acceptance.py",
                      "execution"), flush=True)

        dashboard("full")
        compose("stop", "scheduler", "publisher", "worker")
        dashboard("cancel")
        compose("up", "-d", "--no-build", "--wait", "scheduler", "publisher", "worker")

        compose("stop", "postgres")
        assert status("/health/live") == 200
        assert status("/health/ready") == 503
        compose("exec", "-T", "api", "python", "-m", "flowforge.process",
                "scheduler", "--probe", expected=1)
        compose("up", "-d", "--wait", "postgres")
        eventually(lambda: status("/health/ready") == 200)
        eventually(lambda: healthy("scheduler"))
        print("Database outage/recovery passed", flush=True)

        compose("stop", "scheduler")
        assert "shutdown complete" in compose("logs", "scheduler")
        compose("up", "-d", "--wait", "scheduler")

        eventually(lambda: sql("SELECT count(*) FROM task_runs WHERE status IN "
                               "('RUNNING','RETRY_WAIT')") == "0")
        compose("stop", "api", "scheduler", "publisher", "worker", "frontend")
        compose("run", "--rm", "api", "python", "integration/migration_acceptance.py")
        compose("run", "--rm", "migrate", "alembic", "downgrade", "base")
        compose("run", "--rm", "migrate", "alembic", "upgrade", "0001_identity")
        sql("INSERT INTO users(id,email,password_hash) VALUES "
            "('00000000-0000-0000-0000-000000000001','Old@EXAMPLE.test','fixture')")
        sql("INSERT INTO projects(id,owner_id,name) VALUES "
            "('00000000-0000-0000-0000-000000000002',"
            "'00000000-0000-0000-0000-000000000001','Migrated project')")
        sql("INSERT INTO project_members(project_id,user_id,role) VALUES "
            "('00000000-0000-0000-0000-000000000002',"
            "'00000000-0000-0000-0000-000000000001','OWNER')")
        # Reject dirty existing data without altering or discarding the account.
        compose("run", "--rm", "migrate", "alembic", "upgrade", "head", expected=1)
        assert sql("SELECT version_num FROM alembic_version") == "0001_identity"
        assert sql("SELECT email FROM users") == "Old@EXAMPLE.test"
        sql("UPDATE users SET email = lower(email)")  # Disposable fixture only.
        compose("run", "--rm", "migrate", "alembic", "upgrade", "head")
        assert sql("SELECT count(*) FROM users") == "1"
        assert sql("SELECT count(*) FROM projects") == "1"
        assert sql("SELECT count(*) FROM project_members") == "1"
        compose("run", "--rm", "migrate", "alembic", "downgrade", "0002_identity_guards")
        assert sql("SELECT count(*) FROM users") == "1"
        assert sql("SELECT count(*) FROM projects") == "1"
        compose("run", "--rm", "migrate", "alembic", "upgrade", "head")
        compose("run", "--rm", "migrate", "alembic", "upgrade", "head")
        compose("up", "-d", "--wait", "--wait-timeout", "120")
        assert status("/health/ready") == 200
        print("Migration base/old/dirty-data/head/repeat upgrade passed", flush=True)
        observability()
    finally:
        try:
            (reports / "compose.log").write_text(
                compose("logs", "--no-color", telemetry=True), encoding="utf-8",
            )
        finally:
            # Cleanup must still be attempted if collecting logs fails.
            # The name is generated above, never accepted from env or user input.
            compose("down", "--volumes", "--remove-orphans", telemetry=True)

    (reports / "result.txt").write_text(
        "PASS: build, integration, browser, proxy, outages, SIGTERM, "
        "migrations, telemetry, cleanup\n",
        encoding="utf-8",
    )
    print(f"PASS. Evidence: {reports}", flush=True)

if __name__ == "__main__":
    main()
