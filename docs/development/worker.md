# Worker execution

Worker consumes persistent DispatchMessage v1 from the same RabbitMQ topology as the publisher. Each process has one slot and uses manual ACK and prefetch 1. Messages over 16 KiB, invalid envelopes and unknown dispatch identities are rejected to the DLQ. A valid delivery can arrive before its publisher updates the outbox status. The worker uses the outbox identity without depending on that status.

Claim locks run then task. Only a current QUEUED task with attempts remaining can move to RUNNING. Task state, the attempt row and its fresh lease token commit together. The worker resolves input bindings against the immutable workflow version and successful predecessor outputs, runs the five local demo handlers outside a transaction, and validates the result against the published catalog. Output is limited to 64 KiB and is persisted internally; the existing API returns run/task status.

The lease lasts at most 30 seconds and heartbeat runs every 10 seconds using PostgreSQL time. Both are bounded by the task's hard timeout. Result commit also checks ownership, generation and the deadline. The worker only ACKs a completed attempt after commit. If it loses ACK or the commit outcome is uncertain, RabbitMQ may redeliver. Redelivery rechecks durable state rather than re-executing a committed attempt. On database failure the connection is closed with a bounded reconnect backoff so in-flight deliveries can return to the queue.

Scheduler records an expired lease as LOST and a reached hard deadline as TASK_TIMEOUT. A retryable failure with attempts remaining transitions to RETRY_WAIT. Delay is the version snapshot's initial delay multiplied by the backoff factor for each prior attempt, capped at max delay. Scheduler promotes a due retry to READY and dispatches a fresh generation. Invalid bindings and invalid outputs are permanent. Exhausted attempts fail the task, and Scheduler continues eligible independent branches.

The [cancellation endpoint](runs.md#cancellation) invalidates active leases. A running handler receives a cooperative stop signal when heartbeat detects the lost lease. After a canceled or already completed delivery is requeued, the next consumer checks durable state and acknowledges it without running the handler again.

Migration 0007 preserves queued tasks and outbox events. Downgrade drops worker metadata and refuses while RUNNING or RETRY_WAIT tasks exist. Use the downgrade rehearsal only in a disposable stack. The task-attempt read endpoint belongs to the dashboard milestone.
