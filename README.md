# FlowForge

Distributed workflow orchestration built with FastAPI, PostgreSQL and RabbitMQ.

**Current status:** infrastructure scaffold plus validated DAG/message contracts. Authentication, DAG execution, retry and cancellation are planned, not implemented. See [document authority](docs/source-documents.md).

Local foundation acceptance passed on 2026-11-15: full checks, clean local clone/install and
isolated Compose migration/outage recovery. GitLab project/runner and remote pipeline/clone are
pending setup. See [verification evidence](docs/development/verification.md).

## Quick start (PowerShell)

Prerequisites: Git, Docker Desktop with Linux containers and Docker Compose.

```powershell
Copy-Item .env.example .env
docker compose up -d --build --wait
docker compose ps
```

Open http://localhost:5173 for the connection screen and http://localhost:8000/docs for implemented API documentation.
Local credentials in the example are development-only. Database/broker ports are not published.

```powershell
docker compose exec api python -m flowforge.process worker --probe
docker compose logs scheduler publisher worker
docker compose down
```

Stopping preserves volumes. Do not use `down -v` on data you want to retain.

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

## Trade-offs

One backend codebase and image, separate API/scheduler/publisher/worker processes.
PostgreSQL owns state; RabbitMQ transports notifications. Planned execution semantics are at-least-once with fenced commits, not exactly-once external effects.
AWS, arbitrary user code, visual editing and Kubernetes are outside the MVP.

## Verification helpers

Run `./scripts/check.ps1` for lint, types, unit/contract coverage, dependency audits and diagrams.
Run `./scripts/smoke.ps1` for isolated Compose validation with temporary free ports. It creates
its own project, tests migrations and DB/broker restart recovery, saves reports, and removes only
that project's test containers/volumes. See [recorded verification](docs/development/verification.md).
Daily progress and next actions live in [plan v2](tasks/plan.md).
