# Data dictionary

UUID cho mọi ID; TIMESTAMPTZ UTC cho timestamps; JSONB cho payload. PK id trừ project_members dùng composite PK. Cột không ghi nullable là NOT NULL.
Identity migrate ở 0001_identity, canonical email/one-owner index bổ sung ở 0002_identity_guards.
Các bảng workflow/execution/outbox/audit vẫn là thiết kế đích.
Migration 0003_auth_sessions bổ sung auth_sessions, refresh_tokens và login_counters (đã triển khai).

| Bảng | Cột và constraints |
|---|---|
| users | id UUID PK; email TEXT UNIQUE; password_hash TEXT; created_at timestamp default now |
| projects | id UUID PK; owner_id UUID FK users; name TEXT; created_at timestamp |
| project_members | project_id FK projects + user_id FK users composite PK; role TEXT CHECK OWNER/DEVELOPER/VIEWER |
| auth_sessions | id UUID PK; user_id FK users; created_at/expires_at TIMESTAMPTZ; revoked_at nullable; expires_at > created_at; index(user_id) |
| refresh_tokens | token_hash TEXT PK (SHA-256, không lưu raw token); session_id FK auth_sessions; created_at; consumed_at nullable; index(session_id) |
| login_counters | composite PK(ip,window_start); attempts INTEGER CHECK >0; index(window_start); shared fixed-minute login limit |
| workflow_definitions | id UUID PK; project_id FK projects; name TEXT; created_at timestamp; UNIQUE(project_id,id) |
| workflow_versions | id UUID PK; workflow_id FK definitions; version INTEGER CHECK >0; dag_json JSONB; checksum TEXT; created_at timestamp; UNIQUE(workflow_id,version); UNIQUE(workflow_id,id) |
| workflow_runs | id UUID PK; workflow_id UUID; workflow_version_id UUID; project_id FK projects; composite FKs(project_id,workflow_id)->definitions(project_id,id), (workflow_id,workflow_version_id)->versions(workflow_id,id); status TEXT CHECK run states; input_json JSONB; input_hash TEXT; idempotency_key TEXT nullable; created_at timestamp; started_at/finished_at timestamp nullable; unique index(project_id,idempotency_key) WHERE idempotency_key IS NOT NULL |
| task_runs | id UUID PK; run_id FK runs; node_key TEXT; status TEXT CHECK task states; attempt_count INTEGER default 0 CHECK >=0; dispatch_generation INTEGER default 0; lease_token UUID nullable; lease_until timestamp nullable; next_run_at timestamp nullable; result_json JSONB nullable; UNIQUE(run_id,node_key); UNIQUE(run_id,id) |
| task_dependencies | id UUID PK; run_id FK runs; predecessor_task_id UUID; successor_task_id UUID; composite FK(run_id, predecessor_task_id) and FK(run_id, successor_task_id) reference task_runs(run_id,id); UNIQUE(run_id,predecessor_task_id,successor_task_id); CHECK predecessor != successor |
| task_attempts | id UUID PK; task_run_id FK task_runs; attempt_no INTEGER CHECK >0; worker_id TEXT; lease_token UUID; status TEXT CHECK attempt states; error JSONB nullable; started_at timestamp; finished_at timestamp nullable; UNIQUE(task_run_id,attempt_no) |
| outbox_events | id UUID PK (event_id); task_run_id FK task_runs; dispatch_generation INTEGER; event_type TEXT; payload JSONB; status TEXT CHECK PENDING/CLAIMED/PUBLISHED/OBSOLETE; available_at/created_at timestamp; claim_token UUID nullable; claim_until/published_at/last_redriven_at timestamp nullable; publish_attempts INTEGER default 0 CHECK >=0; UNIQUE(task_run_id,dispatch_generation,event_type) |
| audit_events | id UUID PK; project_id FK projects; actor_id FK users nullable for system; action TEXT; metadata_json JSONB; created_at timestamp |

## Invariant ownership and retention

- Implemented DB: email is canonical lower(trim(email)), length 3–254 and UNIQUE.
  Auth/bootstrap must normalize before insert/login. This is an identifier policy, not complete email syntax validation.
  Migration rejects existing noncanonical/duplicate data; never merges/deletes accounts automatically.
- Implemented DB: at most one OWNER membership per project (partial unique index).
  Service must create project + matching OWNER membership atomically. Exactly one OWNER and
  owner_id matching that membership are service invariants; DB does not yet enforce existence/match.
  Ownership transfer locks project, demotes prior owner and sets new owner/member in one transaction.
  Tests for these service invariants are required with project service; do not claim them implemented now.
- Target DB: add UNIQUE(project_id,id) on definitions; include workflow_id on runs and composite FKs
  (project_id,workflow_id)->definitions(project_id,id) and
  (workflow_id,workflow_version_id)->versions(workflow_id,id), with UNIQUE(workflow_id,id) on versions.
  Thus a run cannot reference a version from another project even if service validation fails.
- Target CHECKs: RUNNING task has nonnull token/lease; other states clear them. RETRY_WAIT requires
  next_run_at; attempt count never negative. RUNNING attempts have null finished_at; terminal attempts
  and terminal runs require finished_at. Outbox CLAIMED requires token/deadline, PUBLISHED requires
  published_at. Constraints are added together with each feature migration, not on nonexistent tables.
- Target outbox: status also includes OBSOLETE; add created_at, last_redriven_at nullable and publish_attempts.
  Redrive reuses current event/generation; PUBLISHED->PENDING is a controlled redrive transition.
- MVP retention: no automatic deletion of runs, attempts, versions, audit or unpublished outbox.
  Idempotency keys live with runs; no key TTL. Published/obsolete outbox also retained until a measured
  cleanup policy is introduced. Local capacity must be monitored; no unlimited-retention production claim.
  DLQ alone has 7-day TTL/1000-message cap; it is never the source of execution state.

Index bắt buộc: task_runs(status,next_run_at); task_runs(status,lease_until); outbox_events(status,available_at); outbox_events(status,claim_until); workflow_runs(project_id,started_at DESC).
Run/project/version consistency được kiểm tra trong transaction tạo run; membership OWNER được duy trì atomically bởi service.
Không xóa version đã có run; không hỗ trợ hard-delete project trong MVP. Cột JSONB không chứa secrets.
Migration tiếp theo bổ sung theo feature, kèm upgrade/downgrade test trên DB disposable.
