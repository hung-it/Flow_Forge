# Architecture decisions

## ADR 005 Revocable login sessions and project authorization

Accepted 2026-11-16 after user selection. Access JWT lasts 30 minutes; every protected request checks
the DB session. Opaque refresh cookie rotates within a fixed 7-day session; only hashes/history are stored.
Known-token replay revokes the session, including strict concurrent-refresh races; no grace window.
Logout is session-scoped and invalidates access through DB checks. HttpOnly cookie routes require a custom
CSRF header/JSON and checked Origin; no wildcard CORS. This adds DB reads and requires future clients to
serialize refresh, in exchange for immediate revocation and replay detection.
Project writes lock session then project and re-read membership after obtaining the lock. Ownership changes
are atomic and cannot pass through a zero-owner or two-owner committed state. No generic policy framework.

## ADR 004 Contracts v1 and failure semantics

Accepted 2026-11-15. Repository documents and tasks/plan.md v2 supersede the original
9-week external proposals. DAG v1 uses key/handler/edges; schema_version is independent
of immutable workflow version. Continue independent branches after failure; skip descendants.
Cancel is immediate logical CANCELED with invalidated leases, without physical-stop guarantees.
Scheduler includes recovery and run aggregation; no separate recovery deployment needed.
Queue wait alone never advances generation: redrive same event/generation, preserving backlog progress.
See ../architecture/execution-contracts.md for transaction and delivery matrices.
Trade-off: polling and possible duplicate delivery, bounded by idempotent claims and fenced commits.

## ADR 001 Một codebase và nhiều process
Accepted. Một image backend cho API, scheduler, publisher, worker. Chia sẻ service/domain, deployment độc lập bằng command. Không tách microservice vì chưa có nhu cầu ownership/scaling riêng; tránh API background jobs nhân đôi khi tăng uvicorn workers.

## ADR 002 PostgreSQL và transactional outbox
Accepted. PostgreSQL quyết định trạng thái; READY→QUEUED và outbox ghi cùng transaction. Publisher confirms không thay transaction DB; duplicate delivery vẫn có thể xảy ra. Không dùng publish trực tiếp trong request API. Đổi lại phải vận hành outbox polling và recovery.

## ADR 003 Lease fencing và idempotency
Accepted. Unique constraints xử lý concurrent create; conditional update với token/generation chặn stale attempt. DB clock quyết định timeout, heartbeat chỉ gia hạn token hiện hành. Handler không giữ transaction. Không đảm bảo exactly-once external side effects; handler tương tác bên ngoài phải có idempotency riêng.
