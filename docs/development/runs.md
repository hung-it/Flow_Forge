# Run and task persistence

OWNER and DEVELOPER can create runs from an explicit published workflow version. VIEWER can read them. The API checks current session and membership on every write, including a replay. Missing or inaccessible resources return `404`.

| Endpoint | Contract |
|---|---|
| `POST /api/v1/workflows/{workflow_id}/runs` | Accepts `{"version": 1, "input": {"document": {"title": "Example", "content": "Text"}}}`. Returns `202`, `{run_id, status}` and `Location: /api/v1/runs/{run_id}`. |
| `GET /api/v1/runs` | Lists metadata in visible projects. Optional filters are `project_id`, `workflow_id`, `status`, `created_from`, and `created_to`. Orders by newest creation time, then descending ID. |
| `GET /api/v1/runs/{run_id}` | Returns run, project, workflow and published version IDs, version number, creator, status and timestamps. |
| `POST /api/v1/runs/{run_id}/cancel` | Returns `200` with the run metadata. The request needs no body or idempotency key. A terminal run is returned with its original status and timestamps. |
| `GET /api/v1/runs/{run_id}/tasks` | Returns task ID, node key, status, attempt count and direct predecessor IDs, ordered by node key. |
| `GET /api/v1/tasks/{task_id}/attempts` | Returns attempt number, generation, worker ID, status, error code and timestamps, newest attempt first. Project members can read it. Tasks without attempts and empty pages return `[]`. |

Lists accept `limit` (default 20, max 100) and `offset` (default 0). Input, its fingerprint and idempotency keys stay in storage.

Creation-time filters require ISO 8601 timestamps with an offset or UTC marker. The lower bound is inclusive and the upper bound exclusive. An invalid or reversed range returns `422 INVALID_TIME_RANGE`. A workflow outside the selected or visible project returns `404`. Attempt responses omit runtime leases, tokens and task payloads.

Run creation accepts an application/json body up to 64 KiB, including the surrounding version and input fields. Version is a positive PostgreSQL integer. Input must be a JSON object. Oversize requests return `413`, incorrect media types return `415` and invalid JSON or input returns `422`. All `run_input` pointers in the selected snapshot must resolve before persistence. A missing value yields `422 INPUT_BINDING_INVALID`. JSON null is a value. The worker checks handler payload types and resolves `task_output` pointers when the handler runs.

## Safe retry

Supply one optional `Idempotency-Key` header with 1–128 case-sensitive characters from `[A-Za-z0-9._~-]`. An empty, duplicated or malformed header returns `422 INVALID_IDEMPOTENCY_KEY`.

- A key is scoped to its project and retained with the run. An authorized replay with the same workflow, version and input returns the original run ID with its current status.
- Reuse of the key with a different valid request returns `409 IDEMPOTENCY_KEY_CONFLICT`. The SHA-256 fingerprint covers workflow ID, version and parsed input in compact UTF-8 JSON with sorted object keys and original array order. Integer and floating-point values such as `1` and `1.0` produce different fingerprints.
- Validation and authorization precede replay. Invalid requests and rolled-back inserts leave the key available. A request without the header creates a fresh run. Reuse the same key after an uncertain transport outcome to recover the committed result.

## Cancellation

An OWNER can cancel any run in the project. A DEVELOPER can cancel runs they created. A VIEWER or a DEVELOPER targeting another member's run receives `403`. An unknown run or a run outside the caller's visible projects returns `404`.

Cancellation sets the run, unfinished tasks, and running attempts to `CANCELED` in one transaction. It clears task leases and retry deadlines, marks pending and claimed outbox events `OBSOLETE`, and preserves completed tasks, attempt history, and published events. A canceled run that never started keeps a null `started_at`. If the scheduler finishes the run first, cancel returns the recorded terminal status. Replaying a create-run request returns the canceled run with the same ID.

A worker that loses its lease cannot commit a result. RabbitMQ may still deliver an already published message, which the worker settles after checking the database. Cancellation takes effect when the database commits. Handler termination is cooperative and external effects require their own idempotency.

## Storage

Migration `0005_runs` adds `workflow_runs`, `task_runs` and `task_dependencies`. Composite foreign keys enforce consistency across project, workflow, version and run boundaries. The API writes one run, one task per snapshot node and one dependency per direct edge in a single PostgreSQL transaction. A project-scoped partial unique index arbitrates keyed inserts. The replay reads the committed row with a separate statement in the current Read Committed transaction after authorization under the project lock.

Run and task status start at `PENDING`. The run has null start and finish times and each task starts with zero attempts.
