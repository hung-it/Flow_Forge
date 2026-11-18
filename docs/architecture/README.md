# Kiến trúc FlowForge

Trạng thái: API health, Auth/Projects/membership, migrations identity/sessions, DAG/message validation,
process lifecycle và giao diện kết nối đã có code. Workflow engine và login UI vẫn chưa triển khai.
Nguồn chính: tài liệu trong repo và [progress plan v2](../../tasks/plan.md).
Xem [execution contracts v1](execution-contracts.md) cho DAG, handler, transaction, broker và limits.

## 1 System context

```mermaid
flowchart LR
  User[Người dùng] --> FF[FlowForge]
  Dev[Developer] --> GH[GitHub]
  GH --> GL[GitLab CI]
  GL --> FF
```

## 2 Container và deployment

```mermaid
flowchart LR
  Browser --> Web[React :5173]
  Web --> API[FastAPI :8000]
  API --> DB[(PostgreSQL)]
  Scheduler --> DB
  Publisher --> DB
  Publisher --> MQ[RabbitMQ]
  MQ --> Workers
  Workers --> DB
  API -.-> O[Logs / metrics / traces]
  Scheduler -.-> O
  Publisher -.-> O
  Workers -.-> O
```

Compose là một host local, DB/broker chỉ trong network nội bộ, named volumes riêng. Migration là one-shot trước các process. Observability hiện có JSON logs; metrics/traces bổ sung tuần 9.
Một image backend, entrypoint API bằng uvicorn, các role còn lại qua python -m flowforge.process ROLE. Role scaffold chỉ probe dependency, chưa consume/schedule.

## 3 Module boundaries

```mermaid
flowchart TD
  API --> Services[Auth / workflows / execution services]
  Scheduler --> Services
  Workers --> Services
  Services --> Domain[DAG validation / state transitions]
  Services --> Persistence[SQLAlchemy / transactions]
  Publisher --> Messaging[Outbox / RabbitMQ]
  Messaging --> Persistence
```

Domain không import FastAPI, SQLAlchemy hoặc RabbitMQ. Service sở hữu transaction; không giữ lock trong lúc handler chạy.
Scaffold hiện dùng api.py, persistence.py và process.py; tách thành package khi tính năng tương ứng xuất hiện, không tạo module rỗng.

## 4 ERD

```mermaid
erDiagram
 USERS ||--o{ PROJECTS : owns
 USERS ||--o{ PROJECT_MEMBERS : joins
 PROJECTS ||--o{ PROJECT_MEMBERS : includes
 PROJECTS ||--o{ WORKFLOW_DEFINITIONS : contains
 WORKFLOW_DEFINITIONS ||--o{ WORKFLOW_VERSIONS : versions
 WORKFLOW_VERSIONS ||--o{ WORKFLOW_RUNS : executes
 PROJECTS ||--o{ WORKFLOW_RUNS : scopes
 WORKFLOW_RUNS ||--o{ TASK_RUNS : contains
 WORKFLOW_RUNS ||--o{ TASK_DEPENDENCIES : contains
 TASK_RUNS ||--o{ TASK_DEPENDENCIES : predecessor
 TASK_RUNS ||--o{ TASK_DEPENDENCIES : successor
 TASK_RUNS ||--o{ TASK_ATTEMPTS : records
 TASK_RUNS ||--o{ OUTBOX_EVENTS : dispatches
 PROJECTS ||--o{ AUDIT_EVENTS : records
 USERS o|--o{ AUDIT_EVENTS : acts
```

Xem data-model.md cho cột, constraint và migration staging.

## 5 State machines

```mermaid
stateDiagram-v2
 [*] --> PENDING
 PENDING --> READY: dependencies succeeded
 PENDING --> SKIPPED: dependency failed or skipped
 READY --> QUEUED: transaction with outbox
 QUEUED --> RUNNING: atomic claim
 RUNNING --> SUCCEEDED: fenced commit
 RUNNING --> RETRY_WAIT: retryable error or expired lease
 RETRY_WAIT --> READY: backoff elapsed
 RUNNING --> FAILED: permanent error or attempts exhausted
```

Mọi task chưa terminal có thể chuyển CANCELED khi hủy run. Terminal không chuyển trạng thái.
Independent branches tiếp tục nếu một nhánh lỗi; descendants bị SKIPPED. Run hoàn tất khi tất cả task terminal.
```mermaid
stateDiagram-v2
 [*] --> PENDING
 PENDING --> RUNNING: dispatch begins
 RUNNING --> SUCCEEDED: all tasks succeeded
 RUNNING --> FAILED: all terminal and any failure
 PENDING --> CANCELED: cancel
 RUNNING --> CANCELED: cancel and invalidate leases
```
Attempt: RUNNING → SUCCEEDED / FAILED / LOST / CANCELED. Lease timeout ghi LOST; retry mới tạo attempt_no mới.
Hủy run khóa row run trước task, vô hiệu token và hủy task chưa terminal trong một transaction. Worker commit cũng kiểm tra run và token theo cùng lock order. Không hoàn tác external side effects.

## 6 Dispatch thành công

```mermaid
sequenceDiagram
 participant API
 participant DB as PostgreSQL
 participant S as Scheduler
 participant P as Publisher
 participant Q as RabbitMQ
 participant W as Worker
 API->>DB: Run + tasks + dependencies, commit
 S->>DB: READY to QUEUED + outbox in transaction
 P->>DB: Claim outbox batch with bounded lease
 P->>Q: Persistent message, mandatory routing
 Q-->>P: Confirm
 P->>DB: Mark published
 Q->>W: Deliver
 W->>DB: Conditional claim, token, attempt
 W->>W: Execute trusted handler
 W->>DB: Fenced result commit
 W->>Q: ACK
```

Publisher claim transaction kết thúc trước network I/O. Claim hết hạn được lấy lại. Durable exchange/queue, persistent messages, publisher confirms và manual ACK.
Message cũ/trùng được ACK khi DB chứng minh không còn hợp lệ; lỗi DB không ACK. Invalid envelope chuyển dead-letter queue để tránh vòng lặp poison message.

## 7 Recovery

```mermaid
sequenceDiagram
 participant P as Publisher
 participant Q as RabbitMQ
 participant DB
 participant W as Worker
 participant S as Recovery
 P->>Q: Publish
 Note over P: Crash before mark published
 P->>Q: Republish after claim expiry
 Q->>W: Duplicate delivery
 W->>DB: Claim only current dispatch identity
 Note over W: Crash while executing
 S->>DB: Expired lease to LOST and retry or FAILED
 S->>DB: New dispatch generation
 W->>DB: Old worker commit rejected
 Note over Q: Broker outage
 P->>DB: Leave event pending for retry
 Note over Q: Broker recovers
 P->>Q: Drain pending outbox
```

DB time quyết định lease deadline; heartbeat chỉ gia hạn token hiện hành và không vượt hard deadline. Lease 30s/heartbeat 10s là deployment policy; timeout/retry snapshot trong immutable version. Permanent error không retry.
QUEUED chờ quá 60s chỉ redrive cùng event/generation, không vô hiệu message đang chờ. Chỉ retry sau một attempt mới thay generation; xem execution-contracts.md.
Handler chạy ngoài transaction; không cho người dùng nộp mã Python/shell.

## 8 CI flow

```mermaid
flowchart LR
 Push[GitHub branch push] --> Sync[Actions sync current ref]
 Sync --> Mirror[GitLab repository]
 Mirror --> Checks[Lint / typecheck / unit / frontend build]
 Checks --> Smoke[Compose build / migration / probes]
 Smoke --> SHA[Manually compare successful SHA]
 SHA --> Merge[Merge PR on GitHub]
```

## Contracts

API prefix /api/v1; snake_case; UUID string; timestamps UTC; limit/offset default 20 max 100.
Planned endpoints: POST auth/login; GET auth/me; GET/POST projects; GET/POST projects/{id}/workflows; POST workflows/{id}/versions; GET workflows/{id}/versions/{version}; POST workflows/{id}/runs; GET runs; GET runs/{id}; GET runs/{id}/tasks; GET tasks/{id}/attempts; POST runs/{id}/cancel.
Run input: version (positive integer), input (JSON object). POST run returns 202 with run_id and status. Optional Idempotency-Key scoped to project: canonical hash includes workflow ID, explicit version and input; equal key/hash returns original run, different hash returns 409. Keys retained with run.
Errors: code, message, details, request_id. Invalid input 422, unauthenticated 401, cross-project 404, insufficient role within visible project 403.
Auth MVP: local bootstrap-user command, no public registration, Argon2 passwords; JWT access tokens expire in 30 minutes. OWNER manages membership/workflows/runs; DEVELOPER publishes and runs; VIEWER reads. Owner membership maintained atomically.
Message v1: schema_version, event_id, run_id, task_run_id, dispatch_generation, traceparent (optional). Task data stays in DB; consumer checks schema and current generation.
Health, Auth, Projects/membership, Workflow/draft/version and Run/Task persistence routes are implemented. Run execution remains planned. See [workflow API](../development/workflows.md) and [run API](../development/runs.md).
Auth now includes rotating HttpOnly refresh cookies and server-side session revocation per the accepted
2026-11-16 plan. See [auth contracts and usage](../development/auth.md).

## Sources

- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [RabbitMQ reliability](https://www.rabbitmq.com/docs/reliability)
- [PostgreSQL row locks](https://www.postgresql.org/docs/current/explicit-locking.html)
