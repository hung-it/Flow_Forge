# FlowForge — Plan v2 và nhật ký tiến trình

Cập nhật **2026-11-20** (Asia/Ho_Chi_Minh). File theo dõi chính sau mỗi buổi làm việc.
FlowForge **10 tuần**, tổng hai project **19 tuần**, 12–15 giờ/tuần.
Tài liệu trong repo là nguồn duy trì; hai tài liệu ngoài repo là đề xuất v1.
Xem [document authority](../docs/source-documents.md).

## Trạng thái hiện tại

Foundation, Auth/Projects và Workflow/draft/version API đã nghiệm thu. GitLab smoke của
commit tài liệu 7d5ef78 cũng xanh ở pipeline 3077804397.
Run/Task persistence và Idempotency-Key đã pass test PostgreSQL, full Compose smoke local
và hai job smoke GitLab tại `197d78f`. Pipeline tổng tại commit đó đỏ vì audit frontend.
Scheduler/Outbox đã pass full Compose smoke local và hai job smoke GitLab tại `d0b3d3c`.
Worker execution, lease fencing và retry đã pass full Compose smoke local và hai GitLab smoke
tại `564237e`. Bản tích hợp `main` tại `a5d1331` xanh cả bốn job [GitLab](https://gitlab.com/hung-it/flow_forge/-/pipelines/3087574383).
Run cancellation và các drill Worker đã nghiệm thu trên `feat/run-cancellation` tại `05d7e7c`.
Cancellation, dashboard và observability đã tích hợp vào `main` theo thứ tự. [Pipeline tích hợp observability](https://gitlab.com/hung-it/flow_forge/-/pipelines/3092800202).
Benchmark 1.000 task và video demo đã nghiệm thu. Evidence và các pipeline của bản phát hành được liên kết tại [v1.0.0](https://github.com/hung-it/Flow_Forge/releases/tag/v1.0.0).

## Nhật ký 2026-11-20 — Nghiệm thu MVP và benchmark

- Đã kiểm tra `main` sau mỗi lần merge. [Cancellation](https://gitlab.com/hung-it/flow_forge/-/pipelines/3092714507) · [Dashboard](https://gitlab.com/hung-it/flow_forge/-/pipelines/3092777547) · [Observability](https://gitlab.com/hung-it/flow_forge/-/pipelines/3092800202).
- Dashboard đạt 141 smoke tests ở hai pipeline tại `7d1f8d7`. [Pipeline push](https://gitlab.com/hung-it/flow_forge/-/pipelines/3087808351) · [Pipeline thủ công](https://gitlab.com/hung-it/flow_forge/-/pipelines/3092756699).
- Observability đạt 146 smoke tests ở hai pipeline tại `83c2a96`. Fresh clone từ GitHub đã pass `check.ps1` và Full Compose smoke trên Windows. Sửa launcher smoke để Python xuất UTF-8 khi terminal dùng CP1258. [Pipeline push](https://gitlab.com/hung-it/flow_forge/-/pipelines/3089037648) · [Pipeline thủ công](https://gitlab.com/hung-it/flow_forge/-/pipelines/3092757680).
- Ba lượt baseline và một lượt dừng Worker đều hoàn tất đủ 1.000 task với bốn Worker. Median baseline 24,43 task/giây. Task mất Worker phục hồi sau 29,90 giây, với một attempt LOST và một attempt thành công. [Môi trường và phép đo](../docs/development/benchmark.md).
- Video demo ghép từ các cảnh của stack chạy thật, cho thấy DAG, kết quả run, lịch sử phục hồi, Grafana và Jaeger. [Video demo](https://github.com/hung-it/Flow_Forge/releases/download/v1.0.0/flowforge-demo.mp4) · [Evidence benchmark](https://github.com/hung-it/Flow_Forge/releases/download/v1.0.0/flowforge-benchmark.zip).

## Nhật ký 2026-11-19 — Metrics và distributed tracing

- Bật tùy chọn Prometheus, Grafana và Jaeger qua Compose. Dashboard vận hành hiển thị API latency, kết quả run, task attempt, retry và lease recovery, outbox lag và RabbitMQ backlog.
- Migration `0008_run_trace_context` lưu W3C context cùng transaction tạo run. Scheduler nối span qua outbox, Publisher truyền context qua AMQP header và Worker ghi claim, execute, commit, ACK. Replay giữ context gốc và span hủy liên kết về run.
- Đếm outcome sau commit. Thêm nghiệm thu trace, backlog, hai Worker, replay, cancellation và hoạt động khi hệ thống telemetry ngừng. [Hướng dẫn demo](../docs/development/observability.md) · [GitLab pipelines](https://gitlab.com/hung-it/flow_forge/-/pipelines?ref=feat%2Fobservability)
- `check.ps1` pass 60 unit tests, lint, typecheck, build, audit và 9 sơ đồ. Hai test PostgreSQL và smoke observability riêng đã pass, gồm kiểm tra trace trước và sau khi Jaeger được khởi động lại.
- Bước tiếp theo sau khi nghiệm thu và tích hợp là benchmark 1.000 task, ghi lại môi trường, throughput, latency và các ca recovery để chuẩn bị release.

## Nhật ký 2026-11-19 — Dashboard workflow/run/attempt

- Giao diện tiếng Anh có đăng nhập, chọn project, danh sách workflow và run. OWNER quản lý thành viên. OWNER và DEVELOPER chỉnh draft JSON, publish phiên bản và bắt đầu run. VIEWER có quyền đọc.
- Giữ nội dung draft khi trùng revision. Khi phản hồi tạo run bị mất, nút retry gửi lại cùng `Idempotency-Key` và payload. Phiên đăng nhập dùng access token trong bộ nhớ và tuần tự hóa refresh giữa các tab.
- Run có bộ lọc workflow, trạng thái, thời gian tạo. DAG chỉ đọc và bảng task lấy phiên bản gắn với run. Xem lịch sử attempt, theo dõi run khi tab hiện và hủy theo quyền của API.
- Bổ sung test PostgreSQL cho cô lập project, tìm workflow, lọc run và lịch sử attempt. Chrome smoke kiểm tra luồng từ publish đến Worker thực thi, retry sau mất phản hồi, hủy run và quyền thành viên. Kết quả CI tra theo SHA trong [GitLab pipelines của dashboard](https://gitlab.com/hung-it/flow_forge/-/pipelines?ref=feat%2Fdashboard).
- Sau khi dashboard qua nghiệm thu và được tích hợp, bước kế tiếp là metrics/traces cho API, Scheduler, Publisher và Worker. Tiếp theo đo benchmark, ghi kết quả và chuẩn bị demo release.

## Nhật ký 2026-11-19 — Run cancellation và fault injection

- `POST /api/v1/runs/{run_id}/cancel` trả metadata. OWNER hủy mọi run trong project, DEVELOPER hủy run mình tạo. Hủy lại trả trạng thái terminal đã lưu.
- Transaction hủy dùng schema hiện có, giữ kết quả đã commit, vô hiệu lease và retry, đánh dấu outbox chưa ghi publish là `OBSOLETE`.
- Thêm 21 test nghiệp vụ và crash drill trên PostgreSQL/RabbitMQ. Worker chết trước commit phải đến attempt mới. Worker chết sau commit trước ACK nhận lại cùng event và giữ đúng một attempt. Có acceptance qua HTTP trong Compose smoke.
- `scripts/check.ps1` pass 57 unit tests, đủ audit và 9 sơ đồ. Phần triển khai ở `eb2ed11`. Kết quả remote theo SHA được ghi trong PR và [GitLab pipelines của nhánh](https://gitlab.com/hung-it/flow_forge/-/pipelines?ref=feat%2Frun-cancellation).
- Full Compose smoke local và hai pipeline GitLab của `05d7e7c` pass với 139 tests mỗi lượt. [Pipeline push](https://gitlab.com/hung-it/flow_forge/-/pipelines/3087655516) · [Pipeline thủ công](https://gitlab.com/hung-it/flow_forge/-/pipelines/3087682148).

## Nhật ký 2026-11-19 — Chuẩn bị tích hợp vào `main`

- GitHub rút advisory [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) ngày 2026-11-18. Chạy lại `npm audit --audit-level=high` trên lockfile đã nâng TypeScript ESLint, kết quả 0 cảnh báo. Gate CI tiếp tục chạy lệnh audit đầy đủ.
- README tiếng Anh tóm tắt kiến trúc, đưa các bằng chứng kiểm thử và hướng dẫn chạy demo lên đầu. Trang kiến trúc được cập nhật theo trạng thái đã triển khai.
- `scripts/check.ps1` pass với 57 unit tests, coverage contract 100%, audit dependency và 9 sơ đồ Mermaid. Bước nghiệm thu tiếp theo là xác nhận pipeline GitLab xanh cùng commit trước khi merge vào `main`.

## Nhật ký 2026-11-18 — Audit frontend

- Nâng TypeScript ESLint lên `8.73.0`. Lockfile dùng `minimatch@11.0.8` và audit đầy đủ còn 0 cảnh báo moderate. `npm ci`, lint, typecheck và build pass. Audit dependency production báo 0 cảnh báo.
- `npm audit --audit-level=high` vẫn exit 1. Cả 10 mục high đều quy về [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) của `picomatch@4.0.7`, do Vite và TypeScript ESLint kéo vào. Advisory chưa có bản vá phát hành. Tiếp tục theo dõi bản vá và chạy lại gate đầy đủ trước khi xác nhận CI xanh.
- Full Compose smoke local pass với 118 tests, đủ các ngưỡng coverage, frontend proxy, HTTP acceptance, phục hồi DB/broker và migration. Bằng chứng ở `reports/ff-foundation-f05b0c360b2c/result.txt`.

## Nhật ký 2026-11-18 — Worker execution và lease fencing

- Migration `0007_worker_execution` bổ sung task lease/deadline, retry, result và attempt. Worker claim theo run/task/event/generation. Mỗi attempt có token mới, lease 30 giây và heartbeat 10 giây giới hạn bởi hard deadline.
- Worker chạy năm handler demo từ snapshot đã publish, validate binding và output, commit có fencing rồi ACK. Scheduler thu hồi lease, phân loại mất worker/timeout và retry theo snapshot. API cancel thuộc mốc sau.
- PostgreSQL và RabbitMQ thật chạy **118 tests pass**, Worker **88,7% coverage**. Ruff, mypy và tất cả gate 80% pass. Bốn consumer dùng RabbitMQ thật kiểm tra linear/fork-join, duplicate và DLQ. Các ca commit/ACK/DB fault và lease hết hạn đã được kiểm chứng.
- Full Compose smoke local pass. Migration 0007 round trip giữ task QUEUED và outbox PENDING. Worker xử lý từ event gốc đến report, mỗi task của fixture có đúng một attempt. Broker/DB outage, SIGTERM, migration base/old/head và cleanup pass. Evidence `reports/ff-foundation-b85bfb360daa/`.
- **Hai GitLab smoke pass tại `564237e`**. Pipeline [3084257493](https://gitlab.com/hung-it/flow_forge/-/pipelines/3084257493) và [3084257871](https://gitlab.com/hung-it/flow_forge/-/pipelines/3084257871) đều có 118 tests pass, coverage gate, HTTP acceptance, broker recovery, migration giữ backlog và worker thực thi event gốc đến report. Backend và diagrams pass.
- Pipeline tổng vẫn đỏ ở gate audit frontend bắt buộc do picomatch. Audit dependency production báo 0. Tiếp tục theo dõi bản vá [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf).
- Trong các ca đã kiểm thử, không còn lỗi Worker đã biết. Bước tiếp theo: cancellation nguyên tử, requeue/worker crash drill và các race với heartbeat, commit hoặc scheduler completion.

## Nhật ký 2026-11-18 — Scheduler/Outbox

- Migration `0006_scheduler_outbox` thêm generation và outbox. Scheduler mở task theo predecessor, lan SKIPPED, chốt run và ghi QUEUED cùng outbox event trong một transaction.
- Publisher dùng claim lease 30 giây, token fencing, persistent message, mandatory routing và confirm. Redrive sau 60 giây giữ event ID, generation và attempt count. Worker thực thi handler là mốc tiếp theo.
- Local PostgreSQL + RabbitMQ: **107 tests pass**. Ruff và mypy pass. Scheduler **100%**, Outbox/Publisher **95,6%**, mọi domain vượt gate 80%. Đã xác nhận fork/join, propagation, rollback, lô 50, claim đồng thời, token hết hạn, redrive và mandatory return trên RabbitMQ thật.
- Full Compose smoke local **pass**. Tạo run qua HTTP khi RabbitMQ tắt, quan sát QUEUED và outbox PENDING trong DB, rồi nhận cùng persistent event sau khi RabbitMQ phục hồi. DB outage/recovery, SIGTERM, migration round trip và cleanup đều pass. Evidence `reports/ff-foundation-7c8eb1b489e3/`.
- **Hai GitLab smoke pass tại `d0b3d3c`**. Pipeline [3084131219](https://gitlab.com/hung-it/flow_forge/-/pipelines/3084131219) trên nhánh tính năng và [3084131467](https://gitlab.com/hung-it/flow_forge/-/pipelines/3084131467) trên nhánh xác minh đều có 107 tests pass, coverage gate, HTTP acceptance, broker recovery và migration bảo toàn run/task/dependency. Backend và diagrams pass.
- Pipeline tổng vẫn đỏ ở `npm audit --audit-level=high` của frontend. Advisory [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) trên picomatch còn mở. Audit dependency production chạy riêng báo 0 findings. Giữ gate và theo dõi bản vá.
- Trong các ca đã kiểm thử, không còn lỗi Scheduler/Outbox đã biết. Mốc tiếp theo là Worker claim, nhận dạng event/generation và fenced commit trước ACK.

## Nhật ký 2026-11-18 — Run/Task persistence và Idempotency-Key

- Migration 0005 tạo run, task và dependency từ version đã publish. Cả ba bảng được ghi trong một transaction, trạng thái ban đầu PENDING. Composite FK giữ đúng project/workflow/version và ngăn dependency nối hai run.
- Create run nhận version rõ ràng và JSON object, giới hạn body 64 KiB. Kiểm tra pointer run_input trước khi ghi, kể cả binding ở task hạ nguồn. Kiểm tra kiểu payload và resolve task_output sẽ theo worker.
- Key tùy chọn, phạm vi project. Replay hợp lệ trả cùng run ID và status hiện tại. Thay đổi request hợp lệ trả 409. Lỗi validation hoặc rollback cho phép dùng lại key. Kiểm tra lại session/membership cả khi replay.
- Nghiệm thu PostgreSQL local: **100 tests pass**, Ruff và mypy pass. Coverage runs **98,6%**, auth 99,0%, projects 100%, contracts 100%, workflows 99,5%. Test 100 POST đồng thời qua hai instance xác nhận một run, 5 task và 5 dependency. Các ca thu hồi quyền khi chờ khóa, rollback khi insert dependency lỗi và giới hạn UTF-8 chia chunk đều pass.
- Full Compose smoke local **pass**, gồm HTTP publish/tạo/replay/xung đột/đọc task, DB/broker recovery, SIGTERM, migration 0005 round trip bảo toàn dữ liệu cũ và base → head. Bằng chứng trong `reports/ff-foundation-7b170516741f/result.txt`, `coverage.json` và `compose.log`. Chín sơ đồ render và tooling audit pass khi chạy riêng.
- Hai pipeline đầu trên bb04ad5 gặp audit frontend mức high do advisory mới GHSA-fc5q-39xc-vjpf của picomatch. Lint, typecheck và build frontend pass. Npm và advisory chưa có bản vá tương thích đã phát hành.
- Smoke tự build Compose nên chỉ chờ job backend qua needs. Frontend audit vẫn bắt buộc. Các lượt đầu gặp timeout khi tải image GHCR/Docker Hub. Commit `197d78f` kéo PostgreSQL và RabbitMQ nối tiếp, thử lại tối đa ba lần cho mỗi lượt kéo và dùng image đã tải khi khởi động.
- **Hai smoke GitLab pass tại cùng SHA `197d78f`**. Pipeline [3083978303](https://gitlab.com/hung-it/flow_forge/-/pipelines/3083978303) trên nhánh tính năng và [3083978289](https://gitlab.com/hung-it/flow_forge/-/pipelines/3083978289) trên nhánh xác minh CI đều có backend, diagrams và smoke thành công. Mỗi smoke có 100 tests pass, HTTP acceptance, coverage gate, migration bảo toàn dữ liệu cũ và artifact JUnit/coverage/Compose log.
- `check.ps1` và trạng thái tổng của hai pipeline vẫn đỏ tại audit frontend. Advisory [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) chưa có bản vá phát hành tại thời điểm kiểm tra. Audit dependency production frontend trả 0 findings khi chạy riêng. Theo dõi bản vá rồi chạy lại gate đầy đủ.
- Trong phạm vi các ca đã kiểm thử, không còn lỗi nghiệp vụ Run/Task đã biết. Mốc chức năng tiếp theo là Scheduler/Outbox: chuyển task đủ điều kiện sang READY, ghi dispatch và outbox cùng transaction.

## Nhật ký 2026-11-17 — Workflow/draft/version API

- Quyết định: một draft lưu được DAG chưa hợp lệ; OWNER/DEVELOPER cùng biên tập; mỗi publish tạo version mới, không deduplicate checksum. Revision chống stale save/publish; version bất biến.
- AUTH/Projects đã commit/push. Pipeline nền 3077706299 tại c1a7b44 xanh đủ bốn job, gồm smoke. Root cause healthcheck cũ: import DB/broker vượt timeout 5s. Đã chuyển import sang nhánh cần dùng, giữ nguyên timeout và tiêu chí health.
- Migration 0004, workflow metadata, GET/PUT draft, publish/list/read version, schema năm handler và body cap 256 KiB đã triển khai. Hợp đồng: docs/development/workflows.md.
- Linux cuối: **83 tests pass**, Ruff/mypy pass; coverage auth 99.0%, projects 100%, contracts 100%, workflows 99.5%. Cấu hình coverage hỗ trợ greenlet; không hạ gate 80%. Evidence: reports/workflows-linux.log, workflows-coverage.json, workflows-junit.xml.
- Review thêm: chặn số JSON tràn thành infinity, NUL và Unicode surrogate trước khi ghi JSONB; trả 422 và giữ nguyên revision. Có regression test cho từng trường hợp và cho healthcheck không import DB/broker.
- HTTP acceptance thật và downgrade 0004 về 0003 rồi upgrade lại bảo toàn user/project/member/session đều pass.
- Full isolated Compose smoke pass: reports/ff-foundation-c60b9cc014f1/result.txt, compose.log; gồm outage/recovery và migration base/old/dirty-data/head. Lượt này có 77 tests trước khi bổ sung hai case tranh chấp/chunked UTF-8; hai case mới đã có trong lượt Linux 79 tests.
- **Nghiệm thu remote hoàn tất tại SHA 7acd91f**: pipeline [3077760956](https://gitlab.com/hung-it/flow_forge/-/pipelines/3077760956) và [3077766257](https://gitlab.com/hung-it/flow_forge/-/pipelines/3077766257) đều SUCCESS đủ backend/frontend/diagrams/smoke. Lần xác nhận thứ hai dùng nhánh ci/verify-workflows với cùng SHA, checkout và stack riêng.
- Mỗi smoke chạy **83 tests pass**, coverage từng nhóm >=80%, HTTP acceptance thật và migration preservation/base→head đều pass. Evidence API: reports/workflows-gitlab-acceptance.json; JUnit/coverage/Compose/health diagnostics có trong artifact GitLab.
- `check.ps1` pass: lint/mypy, unit tests, frontend lint/typecheck/build, 9 sơ đồ và audit. Bản clone mới từ GitHub tại 55a43d5, frozen uv install: 46 unit tests pass. Bảy moderate findings cũ trong frontend dev tooling vẫn được theo dõi, không có high/critical.
- Mốc Workflow/draft/version API đã nghiệm thu trong các luồng kiểm thử; không có lỗi nghiệp vụ đã biết còn mở trong phạm vi này. Nhật ký kết quả cuối này cập nhật local sau khi xác minh CI; code đã push tại 7acd91f.
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

1. Đối chiếu SHA của `v1.0.0` với `main` và xem kết quả GitLab của tag. [Pipeline của tag](https://gitlab.com/hung-it/flow_forge/-/pipelines?ref=v1.0.0).
2. Dùng [release](https://github.com/hung-it/Flow_Forge/releases/tag/v1.0.0) và [báo cáo benchmark](../docs/development/benchmark.md) làm evidence portfolio.
3. Bắt đầu API Contract Guardian theo roadmap tổng sau khi hoàn tất FlowForge MVP.

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
