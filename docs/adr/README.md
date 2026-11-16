# Architecture decisions

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
