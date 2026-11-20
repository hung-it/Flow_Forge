# FlowForge

FlowForge is a distributed workflow engine built with FastAPI, PostgreSQL and RabbitMQ. Define a workflow as a JSON DAG, publish an immutable version, then run it with workers that retry eligible failures and recover expired leases.

[v1.0.0 release](https://github.com/hung-it/Flow_Forge/releases/tag/v1.0.0) · [Demo video](https://github.com/hung-it/Flow_Forge/releases/download/v1.0.0/flowforge-demo.mp4) · [Benchmark results](docs/development/benchmark.md)

## What it demonstrates

- **Versioned workflows and scoped access.** Project roles control who can publish and run workflows. Each run uses an explicit, immutable version. Repeating a create-run request with the same idempotency key and input returns the original run. [Workflow API](docs/development/workflows.md) · [Run API](docs/development/runs.md)
- **Durable dispatch.** The scheduler records ready work and its outbox event in one PostgreSQL transaction. A separate publisher sends persistent RabbitMQ messages and waits for broker confirmation. [Scheduler and outbox](docs/development/dispatch.md)
- **Recovery and cancellation.** Workers commit results with a valid lease before acknowledging messages. The scheduler recovers expired leases, and canceling a run fences active attempts and stops further dispatch. [Worker execution](docs/development/worker.md)
- **Operations dashboard.** Sign in, manage project access, publish JSON workflows and inspect run graphs and task attempts.
- **Metrics and distributed traces.** Use the Grafana dashboard to inspect queue pressure and task latency. Follow one run in Jaeger across the API, durable dispatch and Worker. [Observability demo](docs/development/observability.md)

## Verification

The integrated engine passed **146 smoke tests** in GitLab with real PostgreSQL and RabbitMQ. The smoke checks cover HTTP and browser flows, migration compatibility, database and broker recovery, Prometheus queries and end-to-end trace relationships in Jaeger. [Integrated pipeline](https://gitlab.com/hung-it/flow_forge/-/pipelines/3092800202) · [Release pipeline](https://gitlab.com/hung-it/flow_forge/-/pipelines?ref=v1.0.0) · [Verification notes](docs/development/verification.md)

A Windows Docker Desktop benchmark completed **1,000 tasks per trial** with four Workers. Median baseline throughput was **24.43 tasks/s**. In the fault trial, a task whose Worker was killed before commit recovered in **29.90 s**. [Method and measurements](docs/development/benchmark.md) · [Raw benchmark evidence](https://github.com/hung-it/Flow_Forge/releases/download/v1.0.0/flowforge-benchmark.zip)

Run `./scripts/smoke.ps1` to reproduce the end-to-end checks in a temporary Compose project. The [sample document workflow](examples/document-workflow.json) shows parallel metadata extraction and content analysis joined into a single report. Run `./scripts/check.ps1` for lint, type checks, unit tests, dependency audits and diagrams.

## Quick start (PowerShell)

Prerequisites: Git, Docker Desktop with Linux containers and Docker Compose.

```powershell
.\scripts\init-env.ps1
docker compose up -d --build --wait
docker compose ps
```

Open the [dashboard](http://localhost:5173) after creating an account with the bootstrap command below. [Swagger UI](http://localhost:8000/docs) documents the API.
Local credentials in the example are development-only. Database/broker ports are not published.
init-env.ps1 generates a private signing key and refuses to overwrite an existing .env.
For an existing .env, add FLOWFORGE_JWT_SECRET with at least 32 random bytes; keep it across restarts.
Changing the signing key invalidates existing access JWTs. Use localhost or HTTPS in a browser with Web Locks support to allow safe session refresh across tabs.

```powershell
docker compose exec api python -m flowforge.process worker --probe
docker compose exec api python -m flowforge.bootstrap --email owner@example.com
docker compose logs scheduler publisher worker
docker compose down
```

Stopping preserves volumes. Do not use `down -v` on data you want to retain.

### Try a workflow

1. Sign in and create a project. Add existing accounts from **Members**.
2. Create a workflow and paste the [document workflow JSON](examples/document-workflow.json) into **Workflow JSON**. Save the draft and publish it.
3. Enter `{"document":{"title":"Example","content":"A workflow run."}}` as the run input and start a run from the displayed version.
4. Watch the execution graph update. Select a task’s attempt count to inspect its worker, timing and result status.

## Development checks

```powershell
cd backend
uv sync --frozen
uv run ruff check .
uv run mypy
uv run pytest
cd ../frontend
npm ci
npm run lint
npm run typecheck
npm run build
```

Backend dependency management requires uv; frontend requires Node 24.11+.
Background processes run in Linux containers; native Windows signal handling is not supported.

## Architecture and roadmap

- [Architecture, diagrams and contracts](docs/architecture/README.md)
- [Data dictionary](docs/architecture/data-model.md)
- [10-week implementation roadmap](tasks/plan.md)
- [Acceptance checklist](tasks/todo.md)
- [CI setup, operation and recovery](docs/development/operations.md)
- [Architecture decisions](docs/adr/README.md)

## Project scope

One backend image runs four roles: API, scheduler, publisher and worker. PostgreSQL stores execution state, and RabbitMQ provides at-least-once delivery. Lease fencing protects database writes. External side effects require idempotency at the destination.

The MVP runs built-in demo handlers through the dashboard and API. AWS, arbitrary user code and Kubernetes are outside the MVP.
