# Bằng chứng nghiệm thu local

## Auth + Projects API — 2026-11-16

Nhánh feat/auth-projects từ commit 305f2c4; thay đổi mới chưa commit/push.
`check.ps1` exit 0 và clean local snapshot clone/fresh uv/npm install pass (reports/clean-clone.log,
source tree bf12d8a341a6b126e4612b42db2153ccde55ba29).

Full `smoke.ps1` **exit 0**, project **ff-foundation-7d4f489fe514**:

- **63 tests pass**: 35 unit + 28 PostgreSQL integration. Không skip/xfail/hạ assertion.
- Domain coverage: **auth 98.0%, projects 86.8%, contracts 100%**; gate >=80% cho từng nhóm.
  Đây không phải claim 100% coverage toàn bộ backend.
- Real TCP HTTP: login/me/refresh/logout, hai user, session isolation và OWNER/VIEWER project RBAC pass.
- Replay revokes committed session; concurrent refresh/logout/login/ownership/member requests covered.
  Test permission race xác nhận request chờ DB project lock rồi đọc lại role sau khi được cấp khóa.
- Cookie flags, CSRF, Origin, request limits, JWT rejection, secret redaction, duplicate bootstrap,
  ownership guard và rollback khi membership insert bị lỗi có test.
- Broker/DB stop-start, API live/ready semantics và recovery; SIGTERM pass.
- Migration base/old-schema/dirty-data rejection/rollback/head, downgrade 0003 về 0002 và upgrade/repeat pass;
  user/project/OWNER membership fixture được giữ. Chỉ chạy trên disposable volumes.
- Log capture và cleanup pass; query lại không còn containers/volumes của project này.
- init-env đã thử trong thư mục disposable: tạo key ngẫu nhiên, không overwrite .env khi gọi lại.
- Ruff/mypy strict pass (17 source files); frontend lint/typecheck/build và 9 Mermaid renders pass.
  Backend/tooling audit: no known vulnerabilities; frontend còn 7 moderate dev-tooling findings cũ,
  0 high/critical. Chưa có login UI hoặc workflow execution để nghiệm thu.

Các lỗi tìm và sửa trong phiên: migration asyncpg không chấp nhận nhiều DDL trong một prepared statement;
OSError kết nối DB chưa thành 503; container non-root cần coverage data trong /tmp.
Các lượt Docker Desktop 500/502 được ghi là lỗi môi trường, không là pass. Harness giảm memory overlap
bằng cách dừng idle scaffold roles lúc test auth rồi khởi động lại trước full outage drills.

GitLab project đã tạo; GitHub Actions bị billing lock trước khi start job, đang chờ Support ticket.
Chưa xác nhận SSH sync hoặc GitLab pipeline mới. Evidence cuối nằm trong
reports/ff-foundation-7d4f489fe514/{result.txt,coverage.json,compose.log} (gitignored).

## Nghiệm thu 2026-11-15 — sau restart

Full `scripts/smoke.ps1` **exit 0**, project `ff-foundation-620f6ea29f00`:

- API/frontend build từ source, PostgreSQL/RabbitMQ volumes mới; tất cả healthchecks pass.
- 7 PostgreSQL integration tests pass; frontend proxy trả status ready.
- Stop/start broker: worker probe fails khi broker tắt; API ready/scheduler probe vẫn pass;
  worker/publisher loop health phục hồi trong deadline.
- Stop/start DB: API live 200, ready 503, scheduler probe fails; ready/loop health phục hồi.
- SIGTERM scheduler: shutdown complete và khởi động lại pass.
- Downgrade base, upgrade 0001, seed noncanonical email; upgrade head bị từ chối và transaction rollback
  giữ schema 0001/account. Sửa fixture có chủ đích trong disposable DB, upgrade head/repeat pass,
  account được giữ và stack lên lại healthy.
- Log capture UTF-8 và cleanup pass; query lại không còn containers/volumes của project này.
- `reports/ff-foundation-620f6ea29f00/result.txt` và `compose.log` giữ evidence local.

Lượt trước `e6531c65bd69` phát hiện lỗi harness cp1258 ở bước lưu log dù runtime checks pass.
Đã sửa decode UTF-8 và chuyển ghi PASS xuống sau cleanup; không coi lượt lỗi là full pass.
Stack cũ `2f7b5e9d4d6b` đã cleanup sau restart. GitLab project/runner chưa có (người dùng xác nhận);
đã đọc metadata GitHub và xác nhận chưa có biến/secrets sync, không đọc/in giá trị credential.

Clean local clone/install **pass**, evidence `reports/clean-clone.log`:
export source tree `02e2596809b615b342dbc2a786e4285366267137` qua index tạm; git clone từ một repo
fixture riêng vào thư mục mới. Không mang .env/.venv/node_modules sang clone; uv sync --frozen và
npm ci tạo lại dependencies. Ruff, mypy, 21 tests/coverage gate, ESLint, typecheck và build pass.
Repo fixture dùng Git identity hiện có và bị xóa cùng thư mục disposable; history/index/remote của
project thật giữ nguyên. Đây không phải nghiệm thu remote clone hay remote CI.

`scripts/check.ps1` **exit 0** sau restart: Ruff, mypy strict, 21 tests với contracts coverage 100%,
pip-audit, frontend fresh npm ci/lint/typecheck/build/high-severity audit, tooling npm ci/audit và
9 Mermaid diagrams. Backend/tooling audit: no known vulnerabilities; frontend `npm audit --omit=dev`
trả 0 vulnerabilities. Full frontend tree vẫn có 7 moderate trong dev tooling minimatch/typescript-eslint;
không suppress advisory hoặc ép downgrade. Contract coverage không phải coverage toàn bộ engine.

## Phiên kiểm chứng 2026-11-15 — trước restart

- Ruff, mypy strict: pass (7 source files).
- Unit/contract cuối phiên: 21 pass; contracts coverage 100%, gate >=80%.
- Source-export rehearsal: 21 tests pass từ source được Git archive bằng index tạm, không có .env/.venv;
  dùng Python dependencies hiện có. Index thật không đổi; chưa chứng minh fresh install/remote clone.
- Migration generator regression: failed before template fix, passed afterwards.
- PostgreSQL integration: 7 pass on new ff-foundation-60c17651abc4 volumes;
  canonical email/unique OWNER, email uniqueness, owner FK and role CHECK.
- Frontend ESLint/Hooks, separate typecheck and build: pass.
- pip-audit pinned export: no known vulnerabilities. Frontend npm audit: 0 high/critical,
  7 moderate dev-tooling findings through minimatch/typescript-eslint. No advisory ignored.
- Mermaid: 9 charts rendered successfully. GitLab YAML parsed locally; remote pipeline not run.
- Compose built API/frontend and healthy API/scheduler/publisher/worker, frontend health proxy passed.
- Full dependency outage/migration rehearsal not yet clean: earlier BuildKit EOF and later Windows
  RAM/pagefile exhaustion prevented CLI startup. 60c17651abc4 test containers/volumes were explicitly
  removed after failed automatic log collection. No other project's stop/down/migration was requested.
- New script builds images sequentially, uses isolated tags and attempts cleanup even when log collection fails.
  It limits its Docker CLI Go runtime memory/concurrency to reduce host pressure.
- Frontend HTTP/proxy healthcheck added after detecting a Vite startup race. A later diagnostic using
  already-built images did not complete before the Docker engine became unavailable; no clean full smoke pass.
  Check/cleanup ff-foundation-2f7b5e9d4d6b when the engine is available again.
- Auth and engine behavior remain planned. Remote CI and fresh remote clone remain unverified.

See tasks/plan.md for maintained daily progress. Historical results below are not current guarantees.

## Phiên trước 2026-11-14

Ngày 2026-11-14, nhánh feat/architecture-scaffolding. Không commit/push hoặc thay đổi remote settings.
Mốc hiện tại là Architecture & Scaffolding, không phải toàn bộ engine 10 tuần.

| Kiểm tra | Kết quả |
|---|---|
| Ruff | Pass |
| mypy strict | Pass, 6 source files |
| pytest unit | 2 pass: readiness thành công và lỗi DB |
| PostgreSQL integration | 3 pass: email uniqueness, owner FK, role CHECK; transaction rollback sau mỗi test |
| Frontend typecheck/build | Pass |
| npm audit frontend/tooling | 0 vulnerabilities |
| Mermaid CLI 12.2.0 | Render 9 charts sang SVG và PNG |
| Compose | 7 services chạy + migration one-shot exit 0, isolated project ff-scaffold-check |
| Migration | Empty DB upgrade, downgrade base, upgrade head và repeat upgrade pass |
| Browser | Desktop và 390px mobile: API ready; mobile không overflow ngang |
| Worker connection failure | Container riêng với broker localhost:1: probe exit 1 đúng mong đợi |
| SIGTERM | Throwaway scheduler logs shutdown complete khi nhận TERM qua timeout |
| GitLab YAML | Parse local; chưa được GitLab server lint/run |

UI test hiện chạy http://localhost:15173; API http://localhost:18000. Port 5173 trên máy đang bận nên test dùng alternate ports.
Dữ liệu test nằm trong volumes ff-scaffold-check; không sửa DB của dự án khác. Stack được giữ chạy để xem demo.

## Hạn chế còn lại
- GitLab project URL, write deploy key và verified known_hosts chưa có; chưa chạy remote pipeline hoặc mirror.
- Automatic approval review từ chối lệnh dừng PostgreSQL/RabbitMQ cho outage drill, không cung cấp lý do cụ thể. Không thực hiện shutdown/restart test đó; dùng unit failure và container probe với URL lỗi để kiểm chứng phần error path.
- Chưa chứng minh fresh git clone từ remote vì thay đổi chưa được commit/push. Compose đã build từ source và chạy trên volumes mới.
- pytest integration trong container non-root có cảnh báo không ghi được cache; tests vẫn pass. Unit có warning upstream Starlette khuyến nghị httpx2; không ảnh hưởng hai assertions.
- Observability hiện có JSON logs; metrics/traces, auth và engine được theo dõi ở roadmap sau.

## Chạy lại
PowerShell: ./scripts/check.ps1 và ./scripts/smoke.ps1.
Diagram outputs ở reports/ (gitignored), mở architecture.md hoặc các SVG/PNG.
Remote CI chỉ được nghiệm thu sau khi pipeline GitLab chạy thành công trên đúng commit SHA.


## Workflow/draft/publication ? 2026-11-17

AUTH/Projects baseline: https://gitlab.com/hung-it/flow_forge/-/pipelines/3077706299 at c1a7b44, all four jobs including smoke succeeded.

Final Linux run: 83 tests passed; Ruff/mypy passed. Coverage: auth 99.0%, projects 100%, contracts 100%, workflows 99.5%. Greenlet concurrency is configured to measure SQLAlchemy async continuations correctly; thresholds are unchanged. Real TCP HTTP acceptance and workflow migration downgrade/reapply preserving identity/session rows passed. Evidence: reports/workflows-linux.log, workflows-http.log, workflows-coverage.json, workflows-junit.xml.

Full isolated Compose smoke succeeded: reports/ff-foundation-c60b9cc014f1/result.txt and compose.log (77 tests before additional race/streaming/JSONB cases). Remote acceptance completed at 7acd91f: [pipeline 3077760956](https://gitlab.com/hung-it/flow_forge/-/pipelines/3077760956) and [pipeline 3077766257](https://gitlab.com/hung-it/flow_forge/-/pipelines/3077766257) each succeeded in all four jobs, including smoke with 83 passing tests, per-domain coverage gates, real HTTP acceptance and migration preservation/base-to-head drills. Reports are available in GitLab artifacts; API evidence is saved locally to reports/workflows-gitlab-acceptance.json. Source progress: tasks/plan.md. This final evidence entry was updated locally after confirming CI; tested code was pushed at 7acd91f.


Final local checks: check.ps1 succeeded (frontend lint/typecheck/build, 9 diagrams, backend lint/typecheck/unit tests and audits). Existing 7 moderate frontend development-tool findings remain; no high/critical findings. Fresh GitHub clone of 55a43d5 with frozen uv install: 46 unit tests passed (reports/workflows-remote-check.log). Full Compose rerun with the workflow migration preservation drill: reports/ff-foundation-d98b21ece35b/result.txt (80 tests before the final JSONB boundary cases); final Linux 83-test run includes those cases.

## Run/Task persistence and idempotency — 2026-11-18

The PostgreSQL suite and isolated Compose smoke passed locally with 100 tests. Run-domain coverage was 98.6% against an 80% gate. Coverage remained above the gate for every other domain. One hundred concurrent POSTs through two API instances reused one run, five tasks and five dependencies. HTTP replay, conflict, authorization, rollback, broker and database recovery, SIGTERM, and migration drills passed. Local evidence is in `reports/ff-foundation-7b170516741f/`.

At `197d78f`, both GitLab smoke jobs passed with 100 tests each, including real HTTP, coverage gates and the migration round trip preserving identity, project, draft and version data. Both uploaded JUnit, coverage and Compose logs. [Feature branch smoke](https://gitlab.com/hung-it/flow_forge/-/jobs/18143413296) · [Independent verification smoke](https://gitlab.com/hung-it/flow_forge/-/jobs/18143413189).

The overall GitLab pipelines and local `check.ps1` still fail on the mandatory full frontend audit. Frontend lint, typecheck and build passed and a separate production dependency audit reported zero findings. The new high-severity finding is in the development dependency tree, and the advisory lists no patched picomatch release at verification time. [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf).

## Scheduler/Outbox — 2026-11-18

Local PostgreSQL and RabbitMQ checks passed all 107 tests. Coverage was 100% for Scheduler and 95.6% for Publisher/Outbox. All domains passed their 80% gates. Ruff and mypy passed. Isolated Compose smoke passed HTTP dispatch during broker outage, confirmed delivery of the same event after recovery, database recovery, SIGTERM and migration round trips. Local evidence is in `reports/ff-foundation-7c8eb1b489e3/`.

Both GitLab smoke jobs passed at `d0b3d3c` with 107 tests each, broker stop/start and delivery checks, and migration preservation of run, task and dependency rows. Both pipelines passed backend and diagrams. The frontend audit remains a failing gate. [Feature smoke](https://gitlab.com/hung-it/flow_forge/-/jobs/18144601641) · [Independent smoke](https://gitlab.com/hung-it/flow_forge/-/jobs/18144603776).

## Worker execution — 2026-11-18

Local PostgreSQL and RabbitMQ checks passed all 118 tests. Worker coverage was 88.7%, and every domain passed its 80% gate. Ruff and mypy passed. Four RabbitMQ consumers executed the linear and fork/join fixtures. Tests cover duplicate claims, lease fencing, recovery and retry, rollback, database/ACK failure boundaries and dead-letter routing. Full isolated Compose smoke passed, including broker/database outage recovery and execution of a backlog held across migration 0007. Local evidence is in `reports/ff-foundation-b85bfb360daa/`.

Both GitLab smoke jobs passed at `564237e` with 118 tests each. Both preserved the queued task and pending outbox during the migration round trip and executed the same dispatch through the final report. Backend and diagrams passed. The full frontend audit failed in those runs. [Feature smoke](https://gitlab.com/hung-it/flow_forge/-/jobs/18145575135) · [Independent smoke](https://gitlab.com/hung-it/flow_forge/-/jobs/18145578919).

## Frontend audit — 2026-11-19

After upgrading TypeScript ESLint to `8.73.0` and the withdrawal of the picomatch advisory, `npm audit --audit-level=high` reported zero vulnerabilities locally. The full audit remains a required CI check. [Withdrawn advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf).

## Workflow, run and attempt dashboard — 2026-11-19

`scripts/check.ps1` passed 57 unit tests, lint, type checks, the frontend build, all dependency audits and nine diagram renders. Two focused dashboard tests passed on PostgreSQL with project isolation, search and filters, and attempt history pagination.

Chrome passed both dashboard smoke phases against Compose. The first phase covers owner membership management, serialized refresh across two tabs, concurrent draft recovery, an immutable published DAG, a lost create-run response with replay of the same idempotency key, a real Worker run, attempt history, viewer access, and keyboard and mobile navigation. The second phase pauses the execution services to check canceling a pending run and reading its empty attempt history.

The full Compose runner and GitLab `smoke` job include both browser phases. The run artifacts include `dashboard-smoke.log`, the PostgreSQL integration results, and coverage for each domain. [Dashboard pipelines](https://gitlab.com/hung-it/flow_forge/-/pipelines?ref=feat%2Fdashboard).
