# Acceptance checklist

## Architecture & scaffold
- [x] Repo branch, package và dependency locks
- [x] Architecture, tám nhóm sơ đồ Mermaid, ERD và contracts
- [x] Năm ADR và roadmap 10 tuần
- [x] Health API, identity migration, lifecycle/probes
- [x] Frontend health và Compose
- [x] GitHub sync / GitLab pipeline configuration
- [x] Local verification evidence (xem docs/development/verification.md)
- [ ] Remote GitLab project, deploy key và known hosts configured
- [ ] GitLab pipeline thành công trên cùng SHA
- [x] Mermaid render verification
- [x] Alembic revision template + offline SQL support
- [x] DAG/message v1 contracts, invalid graph/binding tests
- [x] Failure/cancel/redrive policy and transaction/delivery matrices (design)
- [x] Identity guards migration and PostgreSQL constraint tests
- [x] ESLint, typecheck, contract coverage gate and backend dependency audit
- [x] Background loop freshness healthchecks
- [x] Real broker/DB shutdown-restart outage drill (2026-11-15, sau restart)
- [x] Full isolated Compose smoke, migration rollback/upgrade, SIGTERM, logs and cleanup
- [x] Clean local snapshot clone + fresh dependency install rehearsal (không phải remote clone)
- [ ] Fresh remote clone rehearsal after commit/push

## Core engine tiếp theo
- [x] Auth, bootstrap-user, project membership, cross-project tests (nghiệm thu local 2026-11-16)
- [x] Refresh rotation/replay, session logout, CSRF/cookie và concurrency tests
- [x] Auth/projects/contracts coverage gates >=80% trên combined PostgreSQL suite
- [ ] DAG validation và immutable versions
- [ ] Run persistence, idempotency, concurrent requests
- [ ] Scheduler/outbox, durable messaging
- [ ] Worker execution, lease/fencing
- [ ] Retry, recovery, cancellation, failure injection
- [ ] Dashboard workflow/run/attempt views
- [ ] Metrics/traces, benchmark, release video
