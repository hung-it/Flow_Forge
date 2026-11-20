# Reproduce the FlowForge benchmark

From the repository root on Windows with Docker Desktop in Linux container mode, run:

```powershell
.\scripts\benchmark.ps1 -Quick
.\scripts\benchmark.ps1
```

The quick run checks the harness with five tasks. The full command runs three baseline trials and one Worker fault trial sequentially. Each trial starts a dedicated Compose project, four Worker slots, Prometheus and Jaeger. It publishes a DAG with 100 independent `validate_document` nodes, waits for one warm-up run to finish and uses k6 2.4.0 to submit ten measured runs with five VUs.

The Worker fault trial pauses a designated fixture after its real handler runs, kills it before its commit, and restores the fourth normal Worker. Reconciliation requires 1,000 successful logical tasks, one successful attempt per task and one additional `LOST` attempt in the fault trial. That task must recover within 90 seconds. All ten measured runs must complete within five minutes. The script also verifies the outbox and broker queues have drained. A normal trial expects exactly 1,000 attempts.

## Read the results

- `environment.json` records the source SHA, host and Docker resources, images and workload inputs.
- `k6.json` measures submission latencies. Each trial has ten timed HTTP requests.
- `summary.json` includes throughput, latency percentiles, recovery time and queue reconciliation. Throughput uses the wall time from the first measured submission to the last measured run's terminal timestamp.
- `reconciliation.json` contains the run latency from PostgreSQL timestamps and the successful task attempts' duration and latency from outbox creation to claim. Jaeger and Prometheus evidence is stored separately alongside the Compose log.

Results are written under `reports/benchmark-*`. The top-level report gives the median throughput of the three baseline runs. Warm-up is excluded. Treat the figures as measurements from the recorded machine and this synthetic workload. Keep the reports when comparing changes to scheduling or Worker throughput.
