# Metrics and distributed traces

Add a private `FLOWFORGE_GRAFANA_ADMIN_PASSWORD` value to your local `.env`, then run:

```powershell
docker compose -f compose.yaml -f compose.observability.yaml --profile observability up -d --build --wait
```

Open Grafana at <http://localhost:3000>, sign in as `admin`, and open **FlowForge Operations**. Open Jaeger at <http://localhost:16686>. The Compose overlay supports `GRAFANA_PORT` and `JAEGER_PORT` for local port conflicts. The metrics listeners, Prometheus and the OTLP receiver stay on the Compose network.

## Follow a workflow run

1. Publish and start the [document workflow](../../examples/document-workflow.json) from the FlowForge dashboard.
2. In Grafana, check API request rate and p95 latency first. Compare pending outbox events and their oldest age with RabbitMQ ready and unacknowledged messages. Growing outbox lag points to dispatch or publish progress. A ready queue that keeps growing while consumers are online points to execution throughput.
3. Copy the run UUID from the dashboard URL. Find its `run.created` JSON log in `docker compose logs api` and copy the `trace_id`. In Jaeger, open `http://localhost:16686/trace/<trace_id>`. Inspect `run.create`, `scheduler.dispatch`, `publisher.publish`, and the Worker spans for claim, execute, commit and ACK.
4. For a retry, compare the attempt number and dispatch generation in spans and logs with the retry and lease recovery series in Grafana. For cancellation, inspect the `run.cancel` span and its link to the original run creation.

Prometheus scrapes every five seconds. Rate panels use one-minute windows. Expect a short delay while the first samples arrive. Dashboard startup config is versioned in the repository.

## Signal boundaries

- The base Compose stack leaves export and metrics disabled. The optional overlay uses 100% trace sampling for demos and CI. Jaeger stores traces in memory. Prometheus keeps up to one day or 256 MB of data in the local container.
- Counters track committed outcomes within each process. A crash can interrupt metric recording after commit. Use PostgreSQL to reconcile exact outcomes. The outbox gauges include pending and claimed work. Grafana hides the snapshot when it is older than 15 seconds.
- Run, task, event and request identifiers appear in logs or spans. Metric labels use route templates and bounded classifications. The telemetry carries neither workflow input/output nor authentication data.
- To return to the base stack, stop Grafana, Jaeger and Prometheus with the overlay command, then run `docker compose up -d --build --wait` with the base configuration.

## Verification

`./scripts/smoke.ps1` starts a disposable Compose project and verifies Grafana provisioning, outbox and RabbitMQ backlog, discovery of two workers, idempotent run creation, cancellation and the trace parent chain. The same test runs in the GitLab `smoke` job. It also stops Jaeger and Prometheus and checks that a new workflow still completes.
