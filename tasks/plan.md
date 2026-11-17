# FlowForge — Plan v2 và nhật ký tiến trình

Cập nhật **2026-11-17** (Asia/Ho_Chi_Minh). File theo dõi chính sau mỗi buổi làm việc.
FlowForge **10 tuần**, tổng hai project **19 tuần**, 12–15 giờ/tuần.
Tài liệu trong repo là nguồn duy trì; hai tài liệu ngoài repo là đề xuất v1.
Xem [document authority](../docs/source-documents.md).

## Trạng thái hiện tại

Foundation, Auth/Projects và Workflow/draft/version API đã có code, migration và tests.
Remote nền AUTH/Projects tại c1a7b44 đã xanh cả smoke (pipeline 3077706299).
Workflow đang nghiệm thu remote; chi tiết phiên hiện tại ở nhật ký 2026-11-17 cuối file.
Run/task persistence, scheduler/worker thực thi và login UI là các bước sau.

## Nhật ký 2026-11-17 — Workflow/draft/version API

- Quyết định: một draft lưu được DAG chưa hợp lệ; OWNER/DEVELOPER cùng biên tập; mỗi publish tạo version mới, không deduplicate checksum. Revision chống stale save/publish; version bất biến.
- AUTH/Projects đã commit/push. Pipeline nền 3077706299 tại c1a7b44 xanh đủ bốn job, gồm smoke. Root cause healthcheck cũ: import DB/broker vượt timeout 5s. Đã chuyển import sang nhánh cần dùng, giữ nguyên timeout và tiêu chí health.
- Migration 0004, workflow metadata, GET/PUT draft, publish/list/read version, schema năm handler và body cap 256 KiB đã triển khai. Hợp đồng: docs/development/workflows.md.
- Linux: **79 tests pass**, Ruff/mypy pass; coverage auth 99.0%, projects 100%, contracts 100%, workflows 99.5%. Cấu hình coverage hỗ trợ greenlet; không hạ gate 80%. Evidence: reports/workflows-linux.log, workflows-coverage.json, workflows-junit.xml.
- HTTP acceptance thật và downgrade 0004 về 0003 rồi upgrade lại bảo toàn user/project/member/session đều pass.
- Full isolated Compose smoke pass: reports/ff-foundation-c60b9cc014f1/result.txt, compose.log; gồm outage/recovery và migration base/old/dirty-data/head. Lượt này có 77 tests trước khi bổ sung hai case tranh chấp/chunked UTF-8; hai case mới đã có trong lượt Linux 79 tests.
- Commit Workflow API 448866d đã push. Chưa đánh dấu nghiệm thu remote workflow: cần hai pipeline hoàn chỉnh xanh trên cùng SHA bàn giao sau cập nhật tài liệu.
- Tiếp theo sau nghiệm thu: Run/Task persistence và Idempotency-Key theo project; chưa triển khai scheduler/worker execution.

## Nhật ký 2026-11-16 — Auth + Projects API

Nhánh `feat/auth-projects` từ foundation commit `305f2c4`. Không commit/push thay đổi này tự động.

### Phạm vi và quyết định đã triển khai

- Bootstrap CLI nhập ẩn mật khẩu, Argon2, email canonical; không public registration hoặc reset password.
- Login/me, access JWT 30 phút và refresh cookie HttpOnly trong session tuyệt đối 7 ngày.
  Logout thu hồi phiên hiện tại; mọi request bảo vệ kiểm tra DB session. Refresh rotation và replay policy
  strict: token cũ dùng lại thu hồi session, kể cả trường hợp refresh đồng thời; không grace window.
- JSON/custom CSRF header, Origin allowlist, body cap 8 KiB; login limit 10/phút/IP dùng DB counter atomic,
  không tin proxy headers. Giới hạn hai job Argon2 đồng thời để kiểm soát RAM.
- Project create/list/detail/rename, member list/add/change/remove và ownership transfer. Project+OWNER
  ghi cùng transaction; khóa session rồi project, đọc lại quyền sau khi lấy khóa. Không xóa project.
- Migration 0003 thêm session/refresh hash history/login counters; không sửa 0001/0002 hoặc tự đổi dữ liệu cũ.
- `scripts/init-env.ps1` tạo private JWT key và từ chối ghi đè .env; smoke có key riêng. Backend image dùng
  root build context để test canonical examples; coverage/cache ghi /tmp khi chạy non-root.
- Smoke/CI đo auth, projects và contracts riêng với gate >=80%; thêm HTTP acceptance hai user qua TCP thật.
  Hướng dẫn sử dụng/giới hạn: docs/development/auth.md. ADR 005 ghi quyết định phiên và RBAC.

### Kiểm chứng hiện có

- RED: unit security fail trước khi có module; HTTP login 404 trước khi nối router; DB outage test tái hiện
  OSError chưa được xử lý. Đã sửa để DB mất kết nối trả 503 và giữ nguyên auth session.
- Full suite **63 pass** trong Compose; coverage auth **98.0%**, projects **86.8%**, contracts **100%**.
  HTTP login/me/refresh/logout, session isolation và project RBAC qua server thật pass.
- Tests gồm bad/missing/expired/forged JWT; replay và refresh/logout races; concurrent login limit;
  duplicate bootstrap/member; cookie flags/CSRF/secret redaction; rollback khi insert member lỗi;
  transfer-owner race và quyền bị thu hồi trong lúc chờ project lock.
- `check.ps1` exit 0: 35 unit tests, Ruff/mypy strict (17 source files), dependency audit,
  frontend lint/typecheck/build và 9 Mermaid charts. Backend/tooling audit không có known vulnerabilities;
  7 moderate frontend dev-tooling findings cũ vẫn được theo dõi, không suppress/downgrade ép.
- Clean local snapshot clone với fresh uv/npm installs pass: source tree
  `bf12d8a341a6b126e4612b42db2153ccde55ba29`, evidence reports/clean-clone.log.
  Đây không phải remote CI/remote clone.
- init-env đã thử trên thư mục disposable: tạo key ngẫu nhiên và giữ file nguyên vẹn khi chạy lần hai.
- **Full smoke exit 0**, project `ff-foundation-7d4f489fe514`: 63 tests, coverage gate từng nhóm,
  HTTP acceptance thật, broker/DB outage/recovery, SIGTERM và migration drill đều pass.
  Drill gồm base → 0001 → dirty-data rejection/rollback → head → downgrade 0002 → head/repeat;
  user/project/OWNER membership cũ được giữ. Log capture và cleanup pass, không còn container test.
  Evidence: reports/ff-foundation-7d4f489fe514/{result.txt,coverage.json,compose.log}.
  Một lượt trước pass với 55 tests; các lượt Docker 500/502 không được coi là full pass.
  Tạm dừng idle roles trong auth tests và khởi động lại trước outage drills giúp giảm tải; không bỏ test.

**Kết luận:** mốc Auth + Projects API được nghiệm thu local. Không phát hiện lỗi nghiệp vụ còn mở trong
các luồng đã kiểm chứng; không coi test xanh là chứng minh tuyệt đối không còn lỗi. Login UI và workflow
execution nằm ngoài mốc này. Remote CI vẫn chờ billing lock/runner và source mới chưa được push.

### Remote CI và bước tiếp theo

- Foundation đã được người dùng commit/push lên GitHub (`305f2c4`); GitLab project đã tạo:
  https://gitlab.com/hung-it/flow_forge.git. GitHub Actions sync đã kích hoạt nhưng job chưa start do
  billing lock; đang chờ Support ticket. SSH/deploy-key và GitLab pipeline chưa được xác nhận chạy đúng.
- Nghiệm thu local của auth đã xong; review diff rồi commit/push khi người dùng yêu cầu.
- Mốc tiếp theo: workflow/version API dùng DAG schema hiện có, immutable publication, handler payload
  schemas và request limits; sau đó mới làm run/task persistence/idempotency. Chưa triển khai engine trong mốc auth.

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

1. Review/commit/push nhánh feat/auth-projects khi được yêu cầu.
   Chờ billing lock được gỡ, rerun GitHub sync và kiểm chứng GitLab pipeline đúng SHA/remote clone.
2. Workflow/version API: dùng WorkflowSpec, limits trước parse, defaults snapshot/checksum,
   handler payload schemas và immutable version tests.
3. Runs/idempotency: bảng runs/tasks/dependencies và composite FK/CHECKs; binding resolution;
   100 requests cùng key tạo một run, payload khác trả 409.
4. Scheduler/outbox: atomic dispatch, DB polling, confirms/mandatory routing; same-generation redrive,
   backlog >60s và broker outage không mất committed work.
5. Worker/reliability: registry, claims/attempts, leases/heartbeat/hard deadlines, retries, fenced commits,
   cancel races và real multi-worker/crash tests.
6. Dashboard → observability → benchmark/release theo lịch bên dưới.

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

### 2026-11-17 — Chẩn đoán và sửa GitLab CI

- Pipeline `3077518973`, commit `305f2c43`: frontend pass; backend không pull được image (`manifest unknown`); diagrams thiếu package index cho bước Puppeteer `--install-deps`; smoke bị skip vì stage check thất bại.
- Sửa `.gitlab-ci.yml`: đổi backend sang tag đã kiểm tra tồn tại `ghcr.io/astral-sh/uv:0.11.0-python3.12-trixie-slim`, giữ uv 0.11.0/Python 3.12; thêm `apt-get update` trước bước cài dependency Chrome.
- Đã kiểm tra trong container Linux sạch trên source local hiện tại: ruff pass, mypy pass (17 files), 35 unit tests pass, coverage contracts 100%, pip-audit không có lỗ hổng đã biết. Diagrams render đủ 9 SVG, npm audit 0 vulnerabilities. Log: `reports/ci-backend-20261117.log`, `reports/ci-diagrams-20261117.log`.
- Đã commit riêng hai chỗ sửa CI thành `af9d51d` và push nhánh `feat/auth-projects` lên GitHub. GitHub Actions sync run `44033620654` đang chạy tại thời điểm kiểm tra. Các thay đổi AUTH/Projects còn lại và nhật ký này vẫn ở local, chưa commit/push.
- Tiếp theo: kiểm tra pipeline GitLab tại đúng SHA `af9d51d` (bao gồm smoke), chưa xác nhận pipeline xanh. Retry pipeline SHA cũ sẽ vẫn dùng cấu hình cũ. Sau đó đưa AUTH/Projects lên remote CI để nghiệm thu trước khi chuyển sang workflow/DAG.

Sau mỗi buổi thêm nhật ký có ngày, thay đổi, command/kết quả, phần chưa xong và bước tiếp theo.
Giữ nhật ký cũ; không ghi đè lịch sử. Phân biệt designed / implemented / verified.
Không skip test/hạ gate; thêm domain/engine mới vào coverage khi triển khai.
Chạy `./scripts/check.ps1` và `./scripts/smoke.ps1` lần lượt trên máy ít RAM.
Evidence chi tiết: [verification.md](../docs/development/verification.md);
checklist ngắn: [todo.md](todo.md). Reports local ở reports/ (gitignored).
