# FlowForge — Plan v2 và nhật ký tiến trình

Cập nhật **2026-11-15** (Asia/Ho_Chi_Minh). File theo dõi chính sau mỗi buổi làm việc.
FlowForge **10 tuần**, tổng hai project **19 tuần**, 12–15 giờ/tuần.
Tài liệu trong repo là nguồn duy trì; hai tài liệu ngoài repo là đề xuất v1.
Xem [document authority](../docs/source-documents.md).

## Trạng thái hiện tại

Foundation có code và tests; DAG/message validation dùng được trong Python.
Auth, API publish/run, scheduler và worker thực thi chưa triển khai.
Nghiệm thu local Compose/migration/outage đã pass sau restart. Remote CI và remote clone còn chờ
thiết lập GitLab; phần designed của engine không được coi là đã chạy.

## Nghiệm thu tiếp ngày 2026-11-15 — sau restart

- Đã kiểm tra và cleanup đúng stack test cũ `ff-foundation-2f7b5e9d4d6b`, gồm containers/network/volumes.
- Lượt `ff-foundation-e6531c65bd69`: các bài runtime pass nhưng harness lỗi decode log UTF-8 bằng cp1258.
  Đã sửa subprocess decode UTF-8, giữ log có ký tự không giải mã được bằng replacement;
  chỉ ghi result PASS sau khi thu log và cleanup thành công.
- Lượt `ff-foundation-620f6ea29f00`: **smoke.ps1 exit 0**, build API/frontend từ source,
  migrations trên volumes mới, 7 PostgreSQL tests, frontend health proxy, broker và DB outage/recovery,
  graceful SIGTERM, downgrade base → 0001 → từ chối dirty data/rollback → head → repeat upgrade.
  API live=200 và ready=503 khi DB tắt; ready vẫn 200 khi chỉ broker tắt theo policy.
- Đã xác nhận containers/volumes của lượt smoke sạch không còn; evidence ở
  `reports/ff-foundation-620f6ea29f00/{result.txt,compose.log}` (gitignored).
- Ruff đã khai báo flowforge là first-party để kết quả import checks ổn định từ root/backend.
- **Clean local clone/install pass**: export source hiện tại qua index tạm, tạo repo fixture riêng và git clone
  vào thư mục mới; không có .env/.venv/node_modules. Cài uv --frozen và npm ci từ lockfiles, rồi Ruff,
  mypy, 21 tests/coverage gate, ESLint, TypeScript và frontend build đều pass.
  Evidence: `reports/clean-clone.log`, source tree `02e2596809b615b342dbc2a786e4285366267137`.
  Đây là local snapshot clone, không phải clone nhánh remote chưa push.
- Người dùng xác nhận chưa có GitLab project/runner. Đã kiểm tra GitHub metadata: chưa có variables/secrets
  đồng bộ. Remote CI/remote clone là bước thiết lập tiếp theo; hướng dẫn có trong operations.md.
- Chưa commit/push project chính, chưa đổi remote settings; Git index thật giữ nguyên.
- **check.ps1 exit 0** sau restart: Ruff/mypy, 21 tests (contracts coverage 100%), pip-audit,
  npm ci/ESLint/typecheck/build/audit và render 9 Mermaid charts đều pass theo gate hiện tại.
  Backend và tooling audit không thấy lỗ hổng đã biết; frontend production dependencies audit = 0.
  Frontend dev tooling còn 7 moderate qua minimatch/typescript-eslint, 0 high/critical; theo dõi bản vá
  upstream, không force downgrade/bỏ advisory. Đã giảm output export requirements để log CI dễ đọc.

**Kết luận mốc:** local foundation được nghiệm thu. Có thể bắt đầu auth/projects và workflow API theo
roadmap; remote CI/remote clone vẫn là checklist mở cho tới khi có GitLab project/runner và source được push.

Nhật ký bên dưới ghi kết quả trước restart; các trạng thái runtime còn chờ ở đó đã được lượt trên giải quyết.

## Nhật ký 2026-11-15 — trước restart

| Hạng mục | Đã làm | Bằng chứng / giới hạn |
|---|---|---|
| Alembic | Template tạo revision, offline SQL, dispose khi lỗi | Regression tạo revision fail trước sửa và pass sau sửa |
| DAG v1 | key/handler/edges, schema_version; defaults/limits; validate cycle, missing/duplicate/self dependency, upstream bindings | Example đồng bộ và unit tests |
| Message v1 | Version, UUID, generation, traceparent; cấm field/payload lạ | Unit tests |
| Execution design | Handler/input/output contract, transaction/ACK matrix, broker topology, quotas | execution-contracts.md; runtime còn ở các mốc sau |
| Failure/cancel/recovery | Independent branches, logical CANCELED; redrive giữ event/generation để không starvation | ADR 004; chưa claim runtime engine đã thực hiện |
| Identity | 0002_identity_guards: canonical email, tối đa một OWNER membership | Không sửa 0001; 7 PostgreSQL integration tests pass |
| DB đích | Composite project/workflow/version FKs, state CHECKs, ownership DB/service và retention | Migration bảng engine theo feature sau |
| Process/Compose | Health marker của vòng lặp, healthchecks, backend limits, isolated image tags | Unit health test; API/scheduler/publisher/worker healthy trong Compose |
| Frontend | Proxy /api và /health; native override; ESLint/Hooks tách TypeScript | Lint, typecheck, build pass |
| Quality | Contract coverage >=80%, pip-audit, npm high-severity gate, CI JUnit/Cobertura | 21 unit tests pass; contracts coverage 100%; backend audit không thấy lỗ hổng đã biết |
| Test tooling | Stack disposable, ports/volumes riêng; migration/outage/SIGTERM drill; cleanup dù lỗi thu logs | Script đã có; full drill còn cần kết quả sạch |
| Docs | Architecture/contracts/ADR/catalog; Markdown v1 có banner; Word giữ nguyên | Plan này theo dõi kết quả và bước tiếp theo |

### Kiểm chứng và vấn đề còn mở hôm nay

- Ruff và mypy strict pass; **21 unit/contract tests pass**, coverage contracts **100%** (gate 80%).
- Export source bằng temporary Git index, không có .env/.venv, chạy lại **21 tests pass** với Python dependencies
  hiện có. Index thật không đổi. Đây là source-export rehearsal, chưa phải fresh dependency install hoặc remote clone.
- Frontend ESLint, TypeScript và production build pass.
- pip-audit: không có lỗ hổng đã biết; frontend audit: **0 high/critical**, **7 moderate** trong
  dev tooling minimatch/typescript-eslint. Không force downgrade hoặc ignore advisory; theo dõi upstream.
- Mermaid render thành công **9 charts**; GitLab YAML parse local pass, chưa server-lint/run.
- Compose đã build và chạy trên volumes mới; **7 PostgreSQL integration tests pass**, health proxy pass.
- Full outage/migration drill chưa hoàn tất: Docker BuildKit EOF, sau đó Windows hết RAM/pagefile,
  Docker CLI/Compose và PowerShell không khởi động ổn định. Không coi lỗi môi trường là test pass.
- Lần thử bằng image đã build trong phiên cũng chưa có kết quả hoàn tất; Docker Desktop Linux engine
  sau đó không còn truy cập được. Stack test `ff-foundation-2f7b5e9d4d6b` cần kiểm tra/cleanup khi Docker hoạt động lại.
- Đã sửa một startup race tìm được trong smoke: frontend nay có healthcheck HTTP/proxy, nên Compose --wait
  không kết thúc chỉ dựa vào container frontend đang chạy. Chưa có clean full smoke pass sau thay đổi này.
- Chưa commit/push, cấu hình GitLab hoặc fresh remote clone; không thay đổi remote settings.
- Auth/handler payload schemas/engine/runtime limits vẫn chưa triển khai. Không mở rộng AWS/Kubernetes.

## Các bước tiếp theo theo thứ tự

1. Review và commit source/lockfiles;
   tạo GitLab project/runner, cấu hình URL/deploy key/known_hosts, pipeline đúng SHA và fresh remote clone.
   Smoke local đã pass; cấu hình CI tồn tại không đồng nghĩa pipeline remote đã pass.
2. Auth/projects: bootstrap user, Argon2, JWT expiry, canonical email; transaction project + OWNER,
   role/transfer-owner/cross-project tests.
3. Workflow/version API: dùng WorkflowSpec, limits trước parse, defaults snapshot/checksum,
   handler payload schemas và immutable version tests.
4. Runs/idempotency: bảng runs/tasks/dependencies và composite FK/CHECKs; binding resolution;
   100 requests cùng key tạo một run, payload khác trả 409.
5. Scheduler/outbox: atomic dispatch, DB polling, confirms/mandatory routing; same-generation redrive,
   backlog >60s và broker outage không mất committed work.
6. Worker/reliability: registry, claims/attempts, leases/heartbeat/hard deadlines, retries, fenced commits,
   cancel races và real multi-worker/crash tests.
7. Dashboard → observability → benchmark/release theo lịch bên dưới.

## Roadmap v2

| Tuần | Deliverable | Acceptance |
|---|---|---|
| 1 | Boundaries, ERD, contracts, state machine, ADR | Tám nhóm sơ đồ, consistency review |
| 2 | Compose, migration identity, process probes, frontend health, CI | Fresh clone và local checks pass; remote CI cần secrets |
| 3 | Login, bootstrap user, project membership, DAG validator | JWT expiry, RBAC, cycle/missing/duplicate node tests |
| 4 | Immutable versions, run/tasks/dependencies, idempotency | 100 concurrent requests một run; khác payload 409 |
| 5 | Scheduler, outbox, publisher | Atomic QUEUED/outbox; broker outage không mất event |
| 6 | Worker registry, claim, lease và fenced commit | Linear + fork/join, 4 workers, commit trước ACK |
| 7 | Retry/backoff, heartbeat, recovery, cancel | Crash, stale token, duplicate delivery, cancel tests |
| 8 | Dashboard và permissions | Create workflow/version/run; task và attempt views |
| 9 | Fault injection, OTel, Prometheus/Grafana | Time-bounded recovery tests, low-cardinality metrics |
| 10 | Benchmark, docs, video, release | 1000 tasks, real environment/results; v1.0.0 |

## Thứ tự mốc đầu
1. Scope/glossary → boundaries → ERD → state machines.
2. Contracts và recovery sequences → ADR.
3. Package/lockfiles → migrations → Compose/process lifecycle.
4. Frontend health → CI/mirror → fresh-clone rehearsal.

Mỗi buổi 2–3 giờ. Unit tests cho behavior, integration trên PostgreSQL thật; không thay PostgreSQL bằng SQLite cho constraints/concurrency.
Coverage domain/orchestration >=80% khi domain được triển khai. Không hạ gate hoặc skip test để xanh.
Demo: validate document → extract metadata + analyze content → aggregate → generate report. JSON input, trusted handlers; chưa upload file.
Hoãn AWS/Kubernetes, cron, compensation, visual editor, arbitrary user code.
Roadmap tổng hai project tăng từ 18 lên 19 tuần; Guardian bắt đầu sau FlowForge.

## Quy tắc cập nhật tiến trình

Sau mỗi buổi thêm nhật ký có ngày, thay đổi, command/kết quả, phần chưa xong và bước tiếp theo.
Giữ nhật ký cũ; không ghi đè lịch sử. Phân biệt designed / implemented / verified.
Không skip test/hạ gate; thêm domain/engine mới vào coverage khi triển khai.
Chạy `./scripts/check.ps1` và `./scripts/smoke.ps1` lần lượt trên máy ít RAM.
Evidence chi tiết: [verification.md](../docs/development/verification.md);
checklist ngắn: [todo.md](todo.md). Reports local ở reports/ (gitignored).
