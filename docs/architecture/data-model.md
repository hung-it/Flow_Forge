# Data dictionary

UUID cho mọi ID; TIMESTAMPTZ UTC cho timestamps; JSONB cho payload. Khóa chính theo từng bảng. Cột không ghi nullable là NOT NULL.
Identity migrate ở 0001_identity, canonical email/one-owner index bổ sung ở 0002_identity_guards.
Migration 0004 tạo workflow/draft/version. Migration 0005 tạo run/task/dependencies. Migration 0006 thêm generation và outbox. Các bảng attempt/audit vẫn là thiết kế đích.
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
| workflow_runs | id UUID PK; composite FKs(project_id,workflow_id)->definitions(project_id,id), (workflow_id,workflow_version_id)->versions(workflow_id,id); created_by UUID FK users; status default PENDING; input_json object JSONB; input_hash SHA-256; idempotency_key nullable; created_at, started_at/finished_at nullable; partial unique index(project_id,idempotency_key) |
| task_runs | id UUID PK; run_id FK runs; node_key TEXT; status default PENDING; attempt_count INTEGER default 0 CHECK >=0; UNIQUE(run_id,node_key); UNIQUE(run_id,id). Dispatch, lease, retry and result fields are added with execution. |
| task_dependencies | composite PK(run_id,predecessor_task_id,successor_task_id); composite FKs for both endpoints reference task_runs(run_id,id); CHECK predecessor != successor |
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
- Outbox 0006 includes OBSOLETE, created_at, last_redriven_at and publish_attempts.
  Redrive reuses current event/generation; PUBLISHED->PENDING is a controlled redrive transition.
- MVP retention: no automatic deletion of runs, attempts, versions, audit or unpublished outbox.
  Idempotency keys live with runs; no key TTL. Published/obsolete outbox also retained until a measured
  cleanup policy is introduced. Local capacity must be monitored; no unlimited-retention production claim.
  DLQ alone has 7-day TTL/1000-message cap; it is never the source of execution state.

Migration 0005 thêm index run theo (project_id,created_at DESC,id DESC) và dependency theo (run_id,successor_task_id). Index lease/retry và outbox sẽ theo migration thực thi.
Run/project/version consistency được kiểm tra trong transaction tạo run; membership OWNER được duy trì atomically bởi service.
Không xóa version đã có run; không hỗ trợ hard-delete project trong MVP. Cột JSONB không chứa secrets.
Migration tiếp theo bổ sung theo feature, kèm upgrade/downgrade test trên DB disposable.


## Workflow publication milestone

Migration 0004 adds workflow_definitions, workflow_drafts and workflow_versions. Drafts have one row/workflow, JSON object content and positive revision. Published versions additionally contain source_draft_revision, created_by, handler_contracts JSONB and SHA-256 checksum. UNIQUE(project_id,id) on definitions and UNIQUE(workflow_id,id) on versions prepare the run composite FKs. Version numbers are unique per workflow; checksums are deliberately not unique. A trigger rejects UPDATE/DELETE of version rows. Execution/outbox tables remain planned. See ../development/workflows.md for the API and locking contract.
