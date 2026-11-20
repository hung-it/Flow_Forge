"""Run the 1,000-task workload in fresh, locally isolated Compose stacks."""

import argparse
import json
import math
import os
import platform
import secrets
import statistics
import subprocess
import time
from datetime import UTC, datetime

from verify_stack import ROOT, free_port

K6_IMAGE = "grafana/k6:2.4.0"
DOCKER = ["docker", "--context", "desktop-linux"]
OVERLAY = ["-f", "compose.yaml", "-f", "compose.observability.yaml",
           "--profile", "observability"]


def invoke(args, *, env=None, input_text=None, timeout=180):
    result = subprocess.run(args, cwd=ROOT, env=env, input=input_text, encoding="utf-8",
                            errors="replace", capture_output=True,
                            check=False, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}: {result.stderr[-3000:]}")
    return result.stdout


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def percentiles(values):
    ordered = sorted(values)
    return {f"p{q}": ordered[math.ceil(len(ordered) * q / 100) - 1] for q in (50, 95, 99)}


def wait_for_fault(container):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        logs = invoke([*DOCKER, "logs", container], timeout=5)
        if "fault-boundary" in logs.splitlines():
            return
        time.sleep(.4)
    raise AssertionError("Worker fixture never reached the pre-commit boundary")


def trial(output, nodes, runs, inject_fault, provenance):
    project = "ff-benchmark-" + secrets.token_hex(6)
    args = [*DOCKER, "compose", "--env-file", ".env.example", "-p", project, *OVERLAY]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FLOWFORGE_", "COMPOSE_"))}
    env.pop("DOCKER_HOST", None)
    env.pop("DOCKER_CONTEXT", None)
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value
    env.update(
        FLOWFORGE_JWT_SECRET=secrets.token_urlsafe(48),
        FLOWFORGE_GRAFANA_ADMIN_PASSWORD=secrets.token_urlsafe(32),
        API_PORT=free_port(), WEB_PORT=free_port(), GRAFANA_PORT=free_port(),
        JAEGER_PORT=free_port(), COMPOSE_PARALLEL_LIMIT="1", GOMEMLIMIT="64MiB", GOMAXPROCS="2",
    )

    def compose(*parts, **kwargs):
        return invoke([*args, *parts], env=env, **kwargs)

    def fixture(*parts, input_text=None):
        raw = compose("exec", "-T", "api", "python", "integration/benchmark_acceptance.py",
                      *map(str, parts), input_text=input_text, timeout=330)
        return json.loads(raw.strip().splitlines()[-1])

    output.mkdir()
    print(f"Benchmark {output.name} on {project}", flush=True)
    try:
        compose("up", "-d", "--build", "--wait", "--wait-timeout", "150",
                "--scale", "worker=4", "api", "scheduler", "publisher", "worker",
                "prometheus", "jaeger", timeout=240)
        ids = compose("ps", "-q", "api", "scheduler", "publisher", "worker",
                      "postgres", "rabbitmq", "prometheus", "jaeger").splitlines()
        entries = invoke([*DOCKER, "inspect", "--format", "{{.Name}} {{.Image}}", *ids], env=env)
        images = dict(line.split() for line in entries.splitlines())
        write_json(output / "environment.json", {
            **provenance, "compose_project": project, "images": images,
            "nodes_per_run": nodes, "runs": runs, "worker_slots": 4, "fault": inject_fault,
            "vus": min(5, runs), "warm_up_runs": 1,
        })
        account = fixture("seed", nodes)
        container = project + "-fault"
        if inject_fault:
            compose("up", "-d", "--no-build", "--wait", "--scale", "worker=3", "worker")
            compose("run", "-d", "--no-deps", "--name", container, "worker", "python",
                    "integration/worker_crash_fixture.py", "flowforge.tasks.v1", "before_commit")

        load_env = {**env, "BENCHMARK_WORKFLOW": account["workflow_id"],
                    "BENCHMARK_RUNS": str(runs), "BENCHMARK_TOKEN": account.pop("token")}
        load_container = project + "-k6"
        mount = f"type=bind,source={ROOT / 'scripts/benchmark-runs.js'},target=/benchmark.js,readonly"
        command = [*DOCKER, "run", "--rm", "--name", load_container,
                   "--label", f"com.docker.compose.project={project}",
                   "--network", project + "_default",
                   "--env", "BENCHMARK_WORKFLOW", "--env", "BENCHMARK_RUNS",
                   "--env", "BENCHMARK_TOKEN", "--mount", mount,
                   K6_IMAGE, "run", "--quiet", "/benchmark.js"]
        deadline = time.monotonic() + 300
        fault = None
        with subprocess.Popen(command, cwd=ROOT, env=load_env, encoding="utf-8",
                              errors="replace", stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as load:
            try:
                if inject_fault:
                    wait_for_fault(container)
                    label = invoke([*DOCKER, "inspect", "--format",
                                    '{{ index .Config.Labels "com.docker.compose.project" }}',
                                    container], env=env).strip()
                    if label != project:
                        raise RuntimeError("Fault fixture is outside the benchmark project")
                    fault = fixture("fault", account["workflow_id"])
                    invoke([*DOCKER, "kill", "--signal", "KILL", container], env=env, timeout=10)
                    write_json(output / "fault.json", fault)
                    compose("up", "-d", "--no-build", "--wait", "--scale", "worker=4", "worker")
                raw, diagnostics = load.communicate(timeout=45)
                (output / "k6.log").write_text(diagnostics, encoding="utf-8")
                if load.returncode:
                    raise RuntimeError(f"k6 exited {load.returncode}, see {output / 'k6.log'}")
            finally:
                if load.poll() is None:
                    load.kill()
                    load.wait(timeout=5)

        k6 = json.loads(raw.strip().splitlines()[-1])
        write_json(output / "k6.json", k6)
        assert k6["metrics"]["iterations"]["values"]["count"] == runs
        observed = fixture("collect", account["workflow_id"], runs, nodes,
                           max(.1, deadline - time.monotonic()), input_text=json.dumps(fault))
        queues = json.loads(compose("exec", "-T", "rabbitmq", "rabbitmqctl", "list_queues",
                                    "-q", "--formatter", "json", "name", "messages_ready",
                                    "messages_unacknowledged", timeout=20))
        assert {"flowforge.tasks.v1", "flowforge.dead.v1"} <= {queue["name"] for queue in queues}
        assert all(q["messages_ready"] == q["messages_unacknowledged"] == 0 for q in queues)
        (output / "trace.ndjson").write_text(observed.pop("trace"), encoding="utf-8")
        write_json(output / "prometheus.json", observed.pop("prometheus"))
        submitted_at = k6["metrics"]["benchmark_submitted_at_ms"]["values"]["min"] / 1000
        elapsed = observed["finished_at"] - submitted_at
        assert 0 < elapsed <= 300, f"Workload exceeded its deadline: {elapsed:.3f}s"
        report = {
            "runs": observed["run_count"], "tasks": observed["task_count"],
            "attempts": observed["attempt_count"], "seconds": elapsed,
            "tasks_per_second": observed["task_count"] / elapsed,
            "run_seconds": percentiles(observed["run_seconds"]),
            "task_seconds": percentiles(observed["task_seconds"]),
            "outbox_to_claim_seconds": percentiles(observed["dispatch_seconds"]),
            "recovery_seconds": observed["recovery_seconds"],
            "http_ms": k6["metrics"]["http_req_duration"]["values"],
            "trace_id": observed["trace_id"], "pending_outbox": observed["pending_outbox"],
            "queues": queues,
        }
        write_json(output / "reconciliation.json", observed)
        write_json(output / "summary.json", report)
        print(f"PASS: {report['tasks']} tasks in {elapsed:.2f}s "
              f"({report['tasks_per_second']:.2f} tasks/s)", flush=True)
        return report
    finally:
        try:
            (output / "compose.log").write_text(compose("logs", "--no-color"), encoding="utf-8")
        finally:
            compose("down", "--volumes", "--remove-orphans")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Run one five-task readiness trial")
    options = parser.parse_args()
    endpoint = invoke([*DOCKER, "context", "inspect", "desktop-linux",
                       "--format", "{{.Endpoints.docker.Host}}"]).strip()
    if endpoint != "npipe:////./pipe/dockerDesktopLinuxEngine":
        raise RuntimeError("The benchmark requires the local Docker Desktop Linux engine")
    output = ROOT / "reports" / ("benchmark-" + secrets.token_hex(6))
    output.mkdir(parents=True)
    provenance = {
        "sha": invoke(["git", "rev-parse", "HEAD"]).strip(),
        "clean_tree": not invoke(["git", "status", "--porcelain"]).strip(),
        "host_os": platform.platform(), "host_cpu": platform.processor(),
        "host_logical_cpus": os.cpu_count(), "started_utc": datetime.now(UTC).isoformat(),
        "host_ram_bytes": int(invoke(["powershell", "-NoProfile", "-Command",
                                     "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])),
        "docker": json.loads(invoke([*DOCKER, "info", "--format",
                                     ('{"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}},'
                                      '"version":{{json .ServerVersion}}}')])),
        "compose": invoke([*DOCKER, "compose", "version", "--short"]).strip(),
        "k6": K6_IMAGE,
    }
    print(f"Evidence: {output}", flush=True)
    invoke([*DOCKER, "pull", K6_IMAGE])
    provenance["k6_image"] = invoke(
        [*DOCKER, "image", "inspect", "--format", "{{.Id}}", K6_IMAGE],
    ).strip()
    if options.quick:
        trial(output / "quick", 5, 1, False, provenance)
        return
    baseline = [trial(output / f"baseline-{n}", 100, 10, False, provenance) for n in range(1, 4)]
    fault = trial(output / "fault", 100, 10, True, provenance)
    write_json(output / "summary.json", {
        "baseline": baseline, "fault": fault,
        "median_tasks_per_second": statistics.median(row["tasks_per_second"] for row in baseline),
    })


if __name__ == "__main__":
    main()
