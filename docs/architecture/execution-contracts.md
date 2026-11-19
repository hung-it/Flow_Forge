# Execution contracts v1

DAG/message validation, demo handler execution and durable messaging are implemented.
This document and ADR 004 replace conflicting examples in the original external documents.

## DAG and handlers

Canonical DAG: `schema_version: 1`, `nodes[{key,handler,inputs,timeout_seconds,retry}]`,
`edges[[predecessor,successor]]`. See `examples/document-workflow.json`.
Reject unknown fields/handlers, cycles, duplicate nodes/edges, self/missing dependencies and
bindings to non-ancestors. Disconnected branches allowed; empty DAG forbidden.
Schema version is distinct from the server-assigned immutable workflow version.
Publish expands defaults, stores checksum and handler contract version. All processes use the same release/catalog.

Bindings: `{source: run_input, pointer: /document}` or
`{source: task_output, node: extract, pointer: /metadata}`. `pointer` is JSON Pointer:
empty selects the whole object, `~0` means `~`, `~1` means `/`. No eval, expressions or URLs.
Publish validates syntax/references; execution rejects missing keys, invalid array indexes and
type mismatch as permanent `INPUT_BINDING_INVALID`. No silent null fallback.

Planned handler signature: `async execute(inputs: dict, context: TaskContext) -> dict`.
Context: run/task IDs, attempt number, cancellation signal and stable side-effect key
`task:{task_run_id}`; no open DB session. Registry supplies concrete input/output Pydantic schemas.
Validate before execution and before commit. The five allowed handler names reserve the demo
catalog. Worker validates the published input and output schemas during execution. No arbitrary config,
output schema, Python/shell code, generic HTTP task or file upload in v1.
Input/output JSON are objects without secrets. Invalid input/output is permanent; network errors,
timeouts and unexpected handler exceptions retry within max_attempts with sanitized error codes.
Hard deadline = attempt.started_at + timeout_seconds, independent of heartbeat renewal.
Cancel/timeout is cooperative; no promise of physical termination or reversal of external effects.
CPU-bound handlers must run in another process to avoid blocking heartbeat.

## State and ownership

The scheduler implements dependency reconciliation, initial dispatch, queued redrive and run
completion, expired leases and due retries. It polls every second in batches of at most 50.
Process commands own the loops.
Multiple schedulers use short transactions and conditional updates; lock run first, tasks in UUID
order, then outbox. Recheck state under lock; SKIP LOCKED for batch claims. No I/O under DB locks.
Publisher owns outbox claims; workers own task claims, heartbeat and result commit.

Failure policy: continue independent branches; recursively SKIP not-started descendants of FAILED
tasks. Run SUCCEEDED iff all tasks succeeded; FAILED once all terminal and any FAILED/SKIPPED.
PENDING run becomes RUNNING at first dispatch. Terminal states never transition.
Cancel locks run/tasks, sets CANCELED, cancels nonterminal tasks/attempts and invalidates leases
atomically. No CANCELING state. Repeated cancel returns existing terminal run, including prior
SUCCEEDED/FAILED. Racing completion uses the same run lock. Logical cancel is immediate;
physical handler execution may continue. No compensation of external effects.

## Transaction matrix

| Owner / operation | Atomic writes and guards |
|---|---|
| API / create run | Recheck session and membership under locks, then validate version and run-input pointers. Insert a PENDING run, tasks and direct dependencies in one transaction. The project/key index arbitrates duplicates. On conflict, read the committed row in a separate statement in the current Read Committed transaction. Same fingerprint returns the run, different fingerprint returns 409. |
| Scheduler / dispatch | Active run + READY task; increment generation, QUEUED and one outbox event in same transaction. |
| Publisher / claim | PENDING/due or expired CLAIMED event; assign claim token/deadline, commit before network. |
| Publisher / finish | Mandatory routing + confirm; mark PUBLISHED only with current claim token. Nack/return/timeout keeps event recoverable with backoff. |
| Worker / claim | Active run, QUEUED, matching event/generation, attempts remaining. New token/lease, RUNNING, increment count and insert attempt together. |
| Worker / heartbeat | Active run, RUNNING, current token, unexpired lease/deadline; renew to min(DB time+30s, hard deadline). Never resurrect expired lease. |
| Worker / finish | Same guards; atomically finalize attempt and result or RETRY_WAIT/FAILED, clear lease; ACK after commit. |
| Scheduler / recover | Recheck expiry/deadline under locks. Mark expired lease LOST or elapsed deadline FAILED/TASK_TIMEOUT, then clear lease and choose RETRY_WAIT/FAILED within budget. |
| Scheduler / retry | Due RETRY_WAIT -> READY; only claim increments attempt count. New dispatch increments generation. |
| API / cancel | Run lock, tasks in UUID order; cancel nonterminal attempts/tasks, clear tokens, finalize run. |
| Scheduler / complete | Run lock; aggregate terminal tasks and set run status/finished_at. Poll DB; completion messages unnecessary for correctness. |

## Recovery without queue starvation

Queue wait consumes no attempt and never expires generation. At 60s without claim, redrive may
reset the SAME PUBLISHED event to PENDING under locks; keep event_id and generation, record
last_redriven_at, rate-limit once per 60s, never reset an active publisher claim. Do not create new
generation just because workers are busy. Original/redriven deliveries both contend for one claim.
Only a new attempt after retry/recovery changes generation. Before publish, recheck active run
and current task/generation; mark obsolete events OBSOLETE. Crash after this check can still
publish late, so consumer must revalidate. Broker outage retains DB events.
Required future tests: backlog >60s; repeated redrive; races with cancel/claim; stale heartbeat/commit;
max attempts after crash; fork/join never runs early. These engine tests are not implemented yet.

## Broker and delivery policy

Single-node durable classic queue, no HA claim. Durable direct exchange `flowforge.tasks.v1`,
queue `flowforge.tasks.v1`, key `task.execute`; DLX/queue `flowforge.dead.v1`, key `task.invalid`.
The publisher declares topology on startup/reconnect and sends persistent JSON with mandatory
routing and publisher confirms. Worker reuses that topology, applies manual ACK and sets
prefetch to concurrency (default 1).
DispatchMessage envelope max 16 KiB checked before JSON parsing; no input/config/secrets in it.
Unknown schema version is poison; optional traceparent is W3C v00.

| Delivery | Decision |
|---|---|
| Terminal/canceled, stale generation, duplicate with another valid RUNNING lease | ACK after successful DB check; expired-lease recovery protects crashed owner. |
| QUEUED current generation + active run | Atomic claim, execute, fenced commit, ACK. |
| DB unavailable or uncertain commit | No ACK; pause/reconnect with backoff. Consult DB on redelivery; no tight nack/requeue loop. |
| Malformed/oversized/unknown schema, unknown task after successful DB lookup | Reject without requeue to DLQ; sanitized logs. |
| Handler failure | Commit DB retry/terminal policy then ACK; broker is not retry timer. |

DLQ cap 1000 messages, TTL 7 days; manual replay revalidates DB identity. Diagnostic only.
Publisher retry: exponential backoff 1s capped 30s with jitter; no deletion of unpublished work.
Shutdown stops new claims, drains within grace, leaves unfinished deliveries unacknowledged.

## Limits and enforcement staging

| Limit | MVP value | Enforcement |
|---|---|---|
| Nodes / edges / bindings per node | 100 / 500 / 32 | Implemented Pydantic contract |
| Timeout / attempts | 1–300s default 60 / 1–5 default 3 | Contract implemented; runtime later |
| Retry | initial 1s, multiplier 2, cap 30s; allowed cap <=300s | Contract ranges implemented; scheduling later |
| DAG body / run creation body / task output | 256 / 64 / 64 KiB | HTTP limits are enforced before parsing. Task output limit belongs to the worker. |
| Message | 16 KiB | Add in consumer before parsing |
| Lease / heartbeat | 30s / 10s | Worker implementation |
| Worker concurrency / DB pool | 1 / pool_size 2, max_overflow 0 | Implemented |
| Backend process memory / PIDs / shutdown | 512 MiB / 128 / 20s | Compose implemented |

Current coverage gate >=80% measures auth, projects, contracts, workflows, runs, scheduler,
outbox and worker separately.
Add future domain/engine modules to the measured set; do not lower thresholds or omit them.

Sources: [RabbitMQ confirms](https://www.rabbitmq.com/docs/confirms),
[Pydantic validators](https://docs.pydantic.dev/latest/concepts/validators/),
[PostgreSQL locks](https://www.postgresql.org/docs/current/explicit-locking.html).
