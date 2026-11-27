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

## Measured results — 2026-11-20

These measurements use the clean source at [cdf3293](https://github.com/hung-it/Flow_Forge/commit/cdf32939225a773ed16e5c75117ecbb385860a92). Docker Desktop ran Linux containers on Windows 11 with 8 logical CPUs and 15.84 GiB of host memory. The engine had 8 CPUs and 7.67 GiB of memory available. Docker Engine 29.8.1, Compose 5.5.1 and k6 2.4.0 were recorded by the harness.

| Trial | Tasks | Attempts | Wall time | Tasks/s | Run p95 | Task p95 | Outbox to claim p95 | Submit HTTP p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline 1 | 1,000 | 1,000 | 40.94 s | 24.43 | 40.76 s | 27.70 ms | 9.20 s | 159.89 ms |
| Baseline 2 | 1,000 | 1,000 | 48.21 s | 20.74 | 47.74 s | 38.58 ms | 17.16 s | 412.91 ms |
| Baseline 3 | 1,000 | 1,000 | 37.17 s | 26.90 | 36.94 s | 18.93 ms | 6.16 s | 205.91 ms |
| Worker fault | 1,000 | 1,001 | 39.61 s | 25.24 | 39.29 s | 22.36 ms | 8.76 s | 280.32 ms |

The baseline median is **24.43 tasks/s**. The fault run took **29.90 s** from observing the attempt immediately before killing the Worker to committing the recovered task. Reconciliation found one LOST attempt and one successful attempt for that logical task. Each trial ended with zero pending outbox events and zero ready or unacknowledged messages in both queues.

Run and submit percentiles use ten samples per trial. Task execution and outbox-to-claim latencies describe separate intervals. The figures apply to this machine and the synthetic workload above. [Download the full benchmark evidence](https://github.com/hung-it/Flow_Forge/releases/download/v1.0.0/flowforge-benchmark.zip)
