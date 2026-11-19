# Run and task persistence

OWNER and DEVELOPER can create runs from an explicit published workflow version. VIEWER can read them. The API checks current session and membership on every write, including a replay. Missing or inaccessible resources return `404`.

| Endpoint | Contract |
|---|---|
| `POST /api/v1/workflows/{workflow_id}/runs` | Accepts `{"version": 1, "input": {"document": {"title": "Example", "content": "Text"}}}`. Returns `202`, `{run_id, status}` and `Location: /api/v1/runs/{run_id}`. |
| `GET /api/v1/runs` | Lists metadata in projects where the caller is a member. Optional `project_id` restricts the list to one visible project. Orders by newest creation time, then descending ID. |
| `GET /api/v1/runs/{run_id}` | Returns run, project, workflow and published version IDs, version number, creator, status and timestamps. |
| `GET /api/v1/runs/{run_id}/tasks` | Returns task ID, node key, status, attempt count and direct predecessor IDs, ordered by node key. |

Lists accept `limit` (default 20, max 100) and `offset` (default 0). Input, its fingerprint and idempotency keys stay in storage.

Run creation accepts an application/json body up to 64 KiB, including the surrounding version and input fields. Version is a positive PostgreSQL integer. Input must be a JSON object. Oversize requests return `413`, incorrect media types return `415` and invalid JSON or input returns `422`. All `run_input` pointers in the selected snapshot must resolve before persistence. A missing value yields `422 INPUT_BINDING_INVALID`. JSON null is a value. The worker will check handler payload types and resolve `task_output` pointers when the handler runs.

## Safe retry

Supply one optional `Idempotency-Key` header with 1–128 case-sensitive characters from `[A-Za-z0-9._~-]`. An empty, duplicated or malformed header returns `422 INVALID_IDEMPOTENCY_KEY`.

- A key is scoped to its project and retained with the run. An authorized replay with the same workflow, version and input returns the original run ID with its current status.
- Reuse of the key with a different valid request returns `409 IDEMPOTENCY_KEY_CONFLICT`. The SHA-256 fingerprint covers workflow ID, version and parsed input in compact UTF-8 JSON with sorted object keys and original array order. Integer and floating-point values such as `1` and `1.0` produce different fingerprints.
- Validation and authorization precede replay. Invalid requests and rolled-back inserts leave the key available. A request without the header creates a fresh run. Reuse the same key after an uncertain transport outcome to recover the committed result.

## Storage

Migration `0005_runs` adds `workflow_runs`, `task_runs` and `task_dependencies`. Composite foreign keys enforce consistency across project, workflow, version and run boundaries. The API writes one run, one task per snapshot node and one dependency per direct edge in a single PostgreSQL transaction. A project-scoped partial unique index arbitrates keyed inserts. The replay reads the committed row with a separate statement in the current Read Committed transaction after authorization under the project lock.

Run and task status start at `PENDING`. The run has null start and finish times and each task starts with zero attempts. Scheduler promotion, broker dispatch, execution and cancellation are later milestones.
