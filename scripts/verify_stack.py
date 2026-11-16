"""Build and exercise a new disposable Compose project; never touches other stacks."""
import json
import os
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
    base = ["docker", "compose", "--env-file", ".env.example", "-p", project]

    def compose(*args, expected=0):
        result = subprocess.run(base + list(args), check=False, cwd=ROOT, env=env,
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
    def healthy(role):
        try:
            compose("exec", "-T", role, "python", "-m", "flowforge.process", role, "--health")
            return True
        except RuntimeError:
            return False

    reports.mkdir(parents=True)
    print(f"Disposable project: {project}", flush=True)
    try:
        compose("build", "api")
        compose("build", "frontend")
        compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
        assert status("/health/ready") == 200
        with urllib.request.urlopen(
            f"http://127.0.0.1:{env['WEB_PORT']}/health/ready", timeout=8
        ) as response:
            assert json.load(response)["status"] == "ready"
        print(compose("exec", "-T", "api", "pytest", "integration", "-q"), flush=True)

        compose("stop", "rabbitmq")
        compose("exec", "-T", "api", "python", "-m", "flowforge.process",
                "worker", "--probe", expected=1)
        assert status("/health/ready") == 200
        compose("exec", "-T", "api", "python", "-m", "flowforge.process",
                "scheduler", "--probe")
        compose("up", "-d", "--wait", "rabbitmq")
        eventually(lambda: healthy("worker"))
        eventually(lambda: healthy("publisher"))
        print("Broker outage/recovery passed", flush=True)

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

        compose("stop", "api", "scheduler", "publisher", "worker", "frontend")
        compose("run", "--rm", "migrate", "alembic", "downgrade", "base")
        compose("run", "--rm", "migrate", "alembic", "upgrade", "0001_identity")
        sql("INSERT INTO users(id,email,password_hash) VALUES "
            "('00000000-0000-0000-0000-000000000001','Old@EXAMPLE.test','fixture')")
        # Reject dirty existing data without altering or discarding the account.
        compose("run", "--rm", "migrate", "alembic", "upgrade", "head", expected=1)
        assert sql("SELECT version_num FROM alembic_version") == "0001_identity"
        assert sql("SELECT email FROM users") == "Old@EXAMPLE.test"
        sql("UPDATE users SET email = lower(email)")  # Disposable fixture only.
        compose("run", "--rm", "migrate", "alembic", "upgrade", "head")
        assert sql("SELECT count(*) FROM users") == "1"
        compose("run", "--rm", "migrate", "alembic", "upgrade", "head")
        compose("up", "-d", "--wait", "--wait-timeout", "120")
        assert status("/health/ready") == 200
        print("Migration base/old/dirty-data/head/repeat upgrade passed", flush=True)
    finally:
        try:
            (reports / "compose.log").write_text(compose("logs", "--no-color"), encoding="utf-8")
        finally:
            # Cleanup must still be attempted if collecting logs fails.
            # The name is generated above, never accepted from env or user input.
            compose("down", "--volumes", "--remove-orphans")

    (reports / "result.txt").write_text(
        "PASS: build, integration, proxy, outages, SIGTERM, migrations, log capture, cleanup\n",
        encoding="utf-8",
    )
    print(f"PASS. Evidence: {reports}", flush=True)

if __name__ == "__main__":
    main()
