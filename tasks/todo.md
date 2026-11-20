# Acceptance checklist

## Architecture & scaffold
- [x] Repo branch, package và dependency locks
- [x] Architecture, tám nhóm sơ đồ Mermaid, ERD và contracts
- [x] Năm ADR và roadmap 10 tuần
- [x] Health API, identity migration, lifecycle/probes
- [x] Frontend health và Compose
- [x] GitHub sync / GitLab pipeline configuration
- [x] Local verification evidence (xem docs/development/verification.md)
- [x] Remote GitLab project, deploy key và known hosts configured
- [x] GitLab nền AUTH/Projects xanh tại c1a7b44 (pipeline 3077706299)
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
- [x] Workflow/draft API, DAG validation, handler contracts, immutable versions (local verified)
- [x] Hai pipeline GitLab workflow xanh tại SHA 7acd91f, gồm smoke: 3077760956 và 3077766257 (83 tests/lần)
- [x] Run/Task persistence, Idempotency-Key và 100 request đồng thời trên PostgreSQL local
- [x] Run/Task full Compose smoke local, gồm migration và HTTP acceptance
- [x] Hai lượt GitLab smoke tại SHA 197d78f, pipeline 3083978303 và 3083978289, 100 tests mỗi lần
- [x] Pipeline GitLab xanh toàn bộ trên `main` tại `a5d1331` sau khi GHSA-fc5q-39xc-vjpf được rút (3087574383)
- [x] Scheduler/Outbox, durable messaging, claim fencing và redrive trên PostgreSQL/RabbitMQ local
- [x] Full Compose smoke và hai lượt GitLab smoke cho Scheduler/Outbox tại d0b3d3c (3084131219, 3084131467, 107 tests/lần)
- [x] Worker execution, lease/fencing, retry và recovery trong giới hạn snapshot đã qua test local
- [x] Full Compose smoke và hai lượt GitLab smoke cho Worker tại 564237e (3084257493, 3084257871, 118 tests/lần)
- [x] Cancellation API, race tests và hai ca Worker crash/redelivery
- [x] Full Compose smoke local và hai lượt GitLab smoke tại `05d7e7c` trên nhánh cancellation, 139 tests mỗi lượt (3087655516, 3087682148)
- [x] Dashboard workflow/run/attempt, DAG chỉ đọc, bảng task và quản lý thành viên
- [ ] Full Compose smoke dashboard local và hai lượt GitLab smoke cùng SHA
- [ ] Metrics/traces, benchmark, release video
