# Hướng dẫn sử dụng Web UI, Điều phối Telemetry & Hệ thống Phân quyền RBAC (ProjectBrain)

> **Phạm vi tài liệu:** Tài liệu này hướng dẫn chi tiết cho người vận hành, kỹ sư phát triển, QA/PM và DevOps cách sử dụng **Web Dashboard (`http://localhost:8080/dashboard/`)**, **Orchestrator Telemetry UI (`http://localhost:8484`)**, phân biệt rạch ròi giữa các phân hệ có giao diện đồ họa (Web UI) với các phân hệ chạy **Headless (MCP / REST API / CLI)**, đồng thời trình bày đầy đủ cơ chế **Phân quyền (RBAC), Token & Bảo mật** trong ProjectBrain.
>
> *Mọi tên tab, nút bấm, biến môi trường, đường dẫn API và quy tắc phân quyền trong tài liệu được đối chiếu trực tiếp từ mã nguồn thực tế (`index.html`, `app.js`, `api.py`, `auth.py`, `plugins_v3.py`, `sources.py`, `graphify.py`, `kb.py`, `codegraph.py`, `memory.py`, `mcp_registry.py`, `006_mcp_auth.sql`, `007_admin_seed.sql`).*

---

## Mục 1 — Ma trận tổng quan: Phân hệ có Web UI vs Phân hệ chạy Headless (MCP / REST API / CLI)

ProjectBrain được thiết kế theo kiến trúc **Hybrid Control Plane**: các tác vụ trực quan (quản lý bộ nhớ nhận thức, tìm kiếm RAG, duyệt đồ thị code Vis.js, so sánh nhánh, sinh tài liệu PM, cấp phát token MCP) được đưa lên **Web Dashboard**; trong khi các năng lực chuyên sâu v3.0 (Plugin Microkernel, PostgreSQL Multi-Schema Overlay, phân tích `version_tag`, UDCP Script Cache, Telemetry Evidence Store, Planner/Oracle/RCA, đồng bộ AST/Graphify hàng loạt) vận hành ở chế độ **Headless** qua FastMCP, REST API và CLI để tích hợp trực tiếp với IDE/AI Agent và CI/CD.

| Phân hệ / Tính năng | Web UI? | Đường dẫn / Tab trên UI | Giao diện Headless (REST API / MCP / CLI) | Ghi chú thực tế |
| :--- | :---: | :--- | :--- | :--- |
| **Bộ nhớ nhận thức HSG** (Lưu, xem feed, reinforce, xóa, thống kê sector/tag) | **Có** | `Dashboard` & `Memory Feed` | `POST /memory/add`, `GET /memory/history`, `GET /memory/stats`, `POST /memory/{id}/reinforce`, `DELETE /memory/{id}`, `POST /memory/delete_all` · MCP: `projectbrain_store`, `projectbrain_list`, `projectbrain_get`, `projectbrain_stats`, `projectbrain_reinforce`, `projectbrain_delete`, `projectbrain_delete_all` | UI tự ẩn nút thêm/reinforce/xóa và `Danger Zone` nếu đăng nhập bằng role `reader`. |
| **Tìm kiếm ngữ nghĩa & RAG** | **Có** | `Smart Search` | `POST /pm/search` (Gemini RAG), `POST /memory/search` (Raw HSG) · MCP: `projectbrain_query` | Hỗ trợ chuyển đổi `Gemini RAG Search` và `Raw Context Retrieval`. |
| **Đồ thị Codegraph / Graphify (Vis.js)** | **Có** | `Code Graph` | `GET /codegraph/data`, `POST /codegraph/query`, `GET /graphify/subgraph`, `POST /graphify/query`, `POST /graphify/hybrid-query` · MCP: `projectbrain_query_codegraph`, `projectbrain_query_graphify`, `projectbrain_hybrid_query` | Backend `/codegraph/data` ưu tiên gọi `GraphStore.to_legacy_visjs()` và fallback về bảng `project_nodes`/`project_edges`. Lọc sâu theo cụm **Leiden community** (`community=<int>`) chạy qua `/graphify/diff` hoặc Graphify API/MCP. |
| **So sánh phiên bản / nhánh (Version Diff)** | **Có** | `Version Compare` | `GET /codegraph/diff`, `GET /memory/diff`, `GET /graphify/diff`, `POST /graphify/local-diff` · MCP: `projectbrain_diff_project_versions`, `projectbrain_local_graphify_diff` · CLI: `graphify-diff` | Trên UI so sánh song song cả **Codebase Symbols** (`Added`/`Deleted`/`Modified`) lẫn **Memories & Rules**. |
| **Upload & bóc tách tài liệu có phiên bản** | **Có** | `Memory Feed` → `Upload Document` | `POST /sources/upload`, `POST /sources/push-processed`, `GET /sources/documents`, `GET /sources/documents/{doc_key}/versions`, `GET /sources/documents/{doc_key}/diff` · MCP: `projectbrain_upload_document`, `projectbrain_push_processed_document`, `projectbrain_list_document_versions`, `projectbrain_diff_document_versions` · CLI: `upload-doc`, `upload-file` | UI dropzone nhận `.pdf`, `.docx`, `.xlsx`, `.xls`, `.txt`, `.md`; backend `/sources/upload` hỗ trợ thêm `.pptx` và `.csv`. Lịch sử phiên bản & diff từng unit tài liệu xem qua API/MCP. |
| **PM Spec Generator (`PM Copilot`)** | **Có** | `PM Copilot` | `POST /pm/generate-doc` | Sinh `status_report`, `prd`, `user_story`, `roadmap` bằng Gemini dựa trên bộ nhớ dự án. |
| **Unified KB Builder & Office Export** (Wiki, Architecture Spec `.docx`, Traceability `.xlsx`, `.pptx`) | **Không** (Headless) | — | `POST /kb/build`, `POST /kb/export`, `GET /kb/trace`, `GET /kb/coverage` · MCP: `projectbrain_build_complete_kb`, `projectbrain_trace_code_and_docs`, `projectbrain_kb_coverage_report`, `projectbrain_generate_project_docs` · CLI: `build-kb`, `kb-trace`, `kb-coverage` | Sinh Architecture Spec `.docx` và Wiki tự động từ CodeGraph/GraphStore qua API/MCP/CLI (khác với tab `PM Copilot` trên Web UI). |
| **Quản lý Project & đồng bộ AST/Graphify** | **Một phần** | `Project Registry` (đăng ký, xem metadata, lấy lệnh sync, xóa) | `GET/POST /codegraph/projects`, `DELETE /codegraph/projects/{id}`, `POST /codegraph/sync`, `POST /codegraph/upload-db`, `POST /graphify/sync`, `POST /graphify/promote`, `GET/DELETE /graphify/branches` · MCP: `projectbrain_sync_codegraph`, `projectbrain_sync_graphify`, `projectbrain_promote_branch_graph` · CLI: `codegraph-sync`, `graphify-sync`, `ingest-files` | Tạo/xóa project thực hiện được trên UI; tác vụ scan AST/Graphify và promote branch chạy qua CLI/MCP/API. |
| **Cấu hình MCP Client & External MCP** | **Có** | `MCP Integration` | `GET /memory/mcp/status`, `POST /memory/mcp/register`, `POST /memory/mcp/unregister`, `GET/POST /memory/mcp/external`, `DELETE /memory/mcp/external/{name}` · MCP: `projectbrain_register_mcp`, `projectbrain_register_external_mcp` | Sinh sẵn snippet `.vscode/mcp.json` và `.mcp.json` theo Active Project & token đang đăng nhập. |
| **Quản lý Tài khoản MCP & RBAC** | **Có** | `Accounts & Auth` (chỉ Admin) | `GET /auth/verify`, `GET /auth/accounts`, `POST /auth/accounts`, `DELETE /auth/accounts/{username}`, `GET /auth/tools` | Tab `Accounts & Auth` tự ẩn với `collaborator` và `reader`. |
| **Orchestrator Telemetry UI (Giám sát Multi-Agent)** | **Có (UI riêng)** | `http://localhost:8484/` | Server riêng tại `~/.gemini/tools/orchestrator/server.py` (`/events`, `/api/tasks`, `/api/tasks/transcript`, `/api/tasks/kill`) · CLI: `client.py` | UI tách biệt hoàn toàn với ProjectBrain `:8080`, dùng để theo dõi tiến độ task, SSE stream, DAG và chi phí token/USD của đội ngũ agent. |
| **Plugin Microkernel v3.0** (Quản lý, hot-reload, enable/disable, config) | **Không** (Headless) | — | `GET /plugins`, `POST /plugins/reload`, `POST /plugins/{id}/enable`, `POST /plugins/{id}/disable`, `PUT /plugins/{id}/config` · MCP: `projectbrain_list_plugins`, `projectbrain_toggle_plugin` · CLI: `plugins [list\|enable\|disable\|reload]` | Toàn bộ vòng đời plugin quản trị qua REST API / MCP / CLI. |
| **PostgreSQL Multi-Schema Branch Overlay** (`pb_br_*` virtual view) | **Không** (Headless) | — | Điều khiển tự động qua `PostgresSchemaGraphStore` & `GET/POST /graphify/*` | Cô lập nhánh bằng schema `pb_br_{proj}_{branch}_{hash8}` khi dùng PostgreSQL backend. |
| **v3.0 ETL, StateFlow & `version_tag`** | **Không** (Headless) | — | `POST /v3/etl/ingest`, `GET /v3/flow-graph?version_tag=...` · MCP: `projectbrain_v3_ingest_etl` · CLI: `v3-etl` | Chuẩn hóa SRS/OpenAPI/log và lọc đồ thị trạng thái `STATE_NODE`/`SCREEN_NODE`/`TRANSITION_EDGE` theo `version_tag`. |
| **UDCP Script Cache v3.0** | **Không** (Headless) | — | `POST /v3/script-cache/lookup`, `POST /v3/script-cache/store`, `POST /v3/script-cache/feedback`, `POST /v3/script-cache/invalidate` · MCP: `projectbrain_v3_script_cache_lookup`, `projectbrain_v3_script_cache_store` · CLI: `v3-cache` | Lưu và tái sử dụng kịch bản kiểm thử tự động theo `intent_id`, `platform`, `build_version`, `screen_hash`. |
| **Telemetry Evidence Store v3.0** | **Không** (Headless) | — | `POST /v3/artifacts/index`, `GET /v3/artifacts/window`, `POST /v3/artifacts/purge` · MCP: `projectbrain_v3_index_artifact`, `projectbrain_v3_query_evidence_window` · CLI: `v3-artifacts` | Đánh chỉ mục và truy vấn cửa sổ thời gian bằng chứng log/screenshot/video theo `run_id`, `center_ts_ms`, `window_ms`. |
| **v3.0 Brain Consumers (Planner / Oracle / RCA)** | **Không** (Headless) | — | `POST /v3/planner/test-dag`, `POST /v3/oracle/context`, `POST /v3/rca/triage` · MCP: `projectbrain_v3_plan_test_dag`, `projectbrain_v3_oracle_context`, `projectbrain_v3_rca_triage` · CLI: `v3-plan`, `v3-oracle`, `v3-rca` | Tính blast radius & test DAG, đối chiếu Oracle với SRS/`version_tag`, phân tích nguyên nhân gốc (RCA). |
| **v3.0 Webhooks & Legacy Connectors** | **Không** (Headless) | — | `POST /v3/webhooks/{source}`, `GET /sources`, `POST /sources/{source}/ingest`, `POST /sources/webhook/github`, `POST /sources/webhook/notion` · MCP: `projectbrain_ingest` | Webhook v3 hỗ trợ xác thực HMAC bằng `PB_WEBHOOK_SECRET` / `PB_{SOURCE}_WEBHOOK_SECRET`. |

---

## Mục 2 — Hướng dẫn khởi chạy & sử dụng chi tiết từng màn hình trên Web Dashboard (`http://localhost:8080/dashboard/`)

### 2.1. Khởi chạy Server (`SQLite` local vs `PostgreSQL` multi-schema)

ProjectBrain đọc cấu hình từ file `projectbrain.toml` (nếu có tại thư mục hiện hành) và các biến môi trường tiền tố `PB_*` (hoặc `OM_*` tương thích ngược). Cổng mặc định là `8080` (tùy chỉnh qua `PB_PORT` hoặc `OM_PORT`).

#### Cách A: Chạy với SQLite Local (phù hợp phát triển cá nhân / thử nghiệm nhanh)

Nếu không cấu hình PostgreSQL, ProjectBrain mặc định sử dụng SQLite (`sqlite:///<tên_project>.db` hoặc đường dẫn chỉ định qua `PB_DB_PATH` / `OM_DB_PATH`):

```bash
# Chạy mặc định với SQLite tự động đặt tên theo thư mục hiện hành
python3 -m projectbrain.main serve

# Hoặc chỉ định rõ file SQLite và cổng chạy
PB_DB_PATH=./data/projectbrain.sqlite PB_PORT=8080 python3 -m projectbrain.main serve
```

#### Cách B: Chạy với PostgreSQL Multi-Schema (phù hợp team, CI/CD & Branch Overlay cô lập)

Khi dùng PostgreSQL, `PostgresSchemaGraphStore` sẽ tự động tạo các schema cô lập theo nhánh (`pb_br_<proj>_<branch>_<hash8>`) và hợp nhất qua virtual view. Bạn có thể cấu hình qua URL tổng `PB_DB_URL` (hoặc `OM_DB_URL`), hoặc qua từng biến `OM_PG_*`:

```bash
# Cách 1: Truyền trực tiếp connection URL (khuyên dùng)
PB_DB_URL="postgresql://postgres:your_password@127.0.0.1:5432/projectbrain" \
PB_PORT=8080 \
python3 -m projectbrain.main serve

# Cách 2: Cấu hình qua backend + thông số host/port/db/user/pass
OM_METADATA_BACKEND=postgres \
OM_PG_HOST=127.0.0.1 \
OM_PG_PORT=5432 \
OM_PG_DB=projectbrain \
OM_PG_USER=postgres \
OM_PG_PASSWORD=your_password \
python3 -m projectbrain.main serve
```

> **Lưu ý khi khởi chạy lần đầu:** Khi server kết nối DB, hệ thống tự chạy các file migration SQL (`001` → `010`), tạo bảng `mcp_accounts` (`006_mcp_auth.sql`) và tự động seed tài khoản quản trị mặc định `admin` với token `pb_tok_admin` (`007_admin_seed.sql` và `init_default_admin()`).

---

### 2.2. Đăng nhập & Cấu hình Active Project (`project:branch`)

1. Mở trình duyệt truy cập **`http://localhost:8080/dashboard/`**.
2. **Màn hình Login Gate (`Authorization Token`):**
   - Nhập token vào ô **Authorization Token** (placeholder `pb_tok_...`), mặc định lần đầu là `pb_tok_admin`.
   - Nhấn **Log In to Dashboard**. Frontend gửi `GET /auth/verify` với header `Authorization: Bearer <token>`.
   - Nếu hợp lệ, token được lưu vào `localStorage` (`pb_auth_token`), widget góc phải trên thanh Header hiển thị tên tài khoản, badge vai trò (`admin` / `collaborator` / `reader`) và nút **Logout**.
   - Từ thời điểm này, mọi `fetch()` từ dashboard đều tự động đính kèm header `Authorization: Bearer <token>`.
3. **Thanh cấu hình ngữ cảnh trên Header (áp dụng cho toàn bộ các tab):**
   - **Project (`#currentProject`):** Chọn dự án đang làm việc (ví dụ `chien334/Project-Brain`, hoặc `default`).
   - **Branch (`#currentBranch`):** Khi dự án có nhiều nhánh (lưu dưới dạng `project_id:branch`), dropdown Branch tự hiện. Bạn có thể chọn một nhánh cụ thể (ví dụ `main`, `feature/auth`) hoặc **`All Branches`** (`%`).
     - Hệ thống tính toán `activeUser` theo quy tắc: `default` (nếu chọn project `default`), `<project>:%` (nếu chọn `All Branches`), hoặc `<project>:<branch>` (nếu chọn nhánh cụ thể), đồng thời đồng bộ lên server qua `POST /memory/active-project`.
     - **Quan trọng:** Khi chọn **`All Branches`** (`%`), bạn chỉ được phép **xem/tìm kiếm** tổng hợp trên mọi nhánh; thao tác **Store Memory** và **Upload Document** sẽ cảnh báo yêu cầu chọn một nhánh cụ thể.
   - **Nút `+` (`Add New Project`):** Mở hộp thoại tạo nhanh Project ID (ví dụ `my-org/my-app:main`) và Display Name thông qua `POST /codegraph/projects`.
   - **Your Name (`#currentUser`):** Tự động điền theo `username` của token đang đăng nhập và khóa chỉnh sửa (`disabled`) để đảm bảo tính truy vết tác giả (`author`) khi lưu memory hoặc upload tài liệu.

---

### 2.3. Hướng dẫn thao tác chi tiết từng tab trên Web Dashboard

#### Tab 1: `Dashboard` (Tổng quan hệ thống)

Mục đích: Theo dõi bức tranh tổng thể về khối lượng tri thức đã lưu trong Active Project (`project:branch` hoặc `project:%`).

- **System Statistics:** Hiển thị tổng số **Memories** và **Temporal Facts** (số liệu lấy từ `GET /memory/stats?user_id=<activeUser>`).
- **Memory Sectors:** Thống kê phân bổ bộ nhớ theo các sector nhận thức HSG (như `semantic`, `episodic`, `procedural`, `reflective`, `emotional`).
- **Active Tags:** Hiển thị toàn bộ tag đang có trong dự án. **Mẹo thao tác:** Click trực tiếp vào một tag bất kỳ, UI sẽ tự chuyển sang tab `Smart Search`, điền sẵn truy vấn `tag:<tên_tag>` và thực thi tìm kiếm ngay lập tức.
- **Danger Zone (`Clear All Memories`):** Nút xóa toàn bộ memory và temporal facts thuộc `activeUser` hiện tại (`POST /memory/delete_all`). Khu vực này **tự động ẩn** đối với tài khoản `reader`.

#### Tab 2: `Memory Feed` — (a) Nạp & quản lý Memory HSG và (d) Upload & bóc tách tài liệu

Tab này chia làm 2 khối nhập liệu song song ở phía trên và danh sách **Recent Cognitive Nodes** ở phía dưới:

- **(a) Khối `Store New Memory` (ẩn với `reader`):**
  1. Đảm bảo đang chọn một nhánh cụ thể trên Header (không phải `All Branches`).
  2. Nhập nội dung tri thức vào ô textarea (ví dụ quy tắc kiến trúc, quyết định thiết kế, luồng nghiệp vụ).
  3. Nhập **Tags (comma separated)** (ví dụ `architecture, auth, rule`).
  4. Nhấn **Store Memory** → gọi `POST /memory/add`.
- **(d) Khối `Upload Document` (ẩn với `reader`):**
  1. Kéo-thả file vào vùng dropzone hoặc click chọn tệp.
     - Trên giao diện Web UI, thẻ `<input type="file">` lọc sẵn các định dạng: **`.pdf`, `.docx`, `.xlsx`, `.xls`, `.txt`, `.md`**.
     - *Mở rộng:* Endpoint backend `POST /sources/upload` còn hỗ trợ bóc tách slide **`.pptx`** và **`.csv`** (có thể gọi qua API/CLI `upload-doc` nếu cần đẩy file `.pptx`).
     - Giới hạn dung lượng mặc định là **50 MB** (cấu hình qua biến môi trường `PB_MAX_UPLOAD_MB`).
  2. Nhập **Tags (comma separated)** (hệ thống tự gắn thêm tag `document`).
  3. Nhấn **Upload** → gọi `POST /sources/upload` (multipart form kèm `file`, `project_id`, `tags`, `author`).
  4. **Cơ chế xử lý ở backend:** File được bóc tách cục bộ sang Markdown theo từng đơn vị (`page` với PDF, `sheet` với Excel, `slide` với PPTX, section với DOCX/MD) bằng **Versioned Pre-OCR Delta Ingestion** (tái sử dụng các unit có `unit_sha256` không đổi để tránh chạy lại OCR/embedding thừa), lưu vào bộ nhớ HSG và đồng bộ tiếp thành các node/edge vào `GraphStore` branch overlay.
- **Danh sách `Recent Cognitive Nodes`:**
  - Hiển thị 30 node gần nhất (`GET /memory/history?user_id=<activeUser>&limit=30`), kèm nhãn `primary_sector`, thời gian tạo, ID rút gọn, tags và thanh **Salience** (độ nổi bật/tầm quan trọng của ký ức).
  - Nhấn **Refresh** để tải lại; nhấn **Reinforce** (`POST /memory/{id}/reinforce`) để tăng điểm salience của ký ức quan trọng; nhấn **Delete** (`DELETE /memory/{id}`) để xóa node (hai nút này ẩn với `reader`).

#### Tab 3: `Smart Search` — Tìm kiếm ngữ nghĩa & RAG

- Nhập câu hỏi tự nhiên hoặc từ khóa (hoặc `tag:<tên_tag>`) vào ô tìm kiếm (`Ask a question or search keywords...`) và nhấn **Search** (hoặc `Enter`).
- Chọn 1 trong 2 chế độ:
  1. **`Gemini RAG Search`** (mặc định): Gọi `POST /pm/search`. Hệ thống truy xuất các memory liên quan từ HSG, đưa vào ngữ cảnh cho Gemini tổng hợp thành câu trả lời mạch lạc trong thẻ **Gemini AI Answer**, đồng thời liệt kê bên dưới phần **Supporting Memories** để bạn đối chứng nguồn gốc.
  2. **`Raw Context Retrieval`**: Gọi `POST /memory/search` (`limit: 10`), trả về trực tiếp danh sách các memory node thô khớp nhất kèm điểm **Score** mà không qua LLM tổng hợp (nhanh và không tốn token LLM).

#### Tab 4: `PM Copilot` — (e) Sinh tài liệu PM / PRD / Roadmap

Tab này dùng cho Product Manager / Tech Lead tổng hợp tri thức dự án thành tài liệu chuẩn Markdown bằng Google Gemini (`POST /pm/generate-doc`).

1. Chọn **Document Type (`#docType`)**:
   - `Project Status Update (Báo cáo tiến độ)` (`status_report`)
   - `Product Requirement Document (PRD)` (`prd`)
   - `User Stories & Acceptance Criteria` (`user_story`)
   - `Product Development Roadmap (Lộ trình)` (`roadmap`)
2. Nhập hướng dẫn chi tiết vào ô **Prompt / Guidance** (nhấn `Ctrl + Enter` để chạy nhanh) và bấm **Generate Document**.
3. Kết quả hiển thị gồm 2 cột:
   - **Generated Document:** Tài liệu Markdown đã định dạng, có nút **Copy Markdown** ở góc phải.
   - **Utilized Source Memories:** Danh sách tối đa 20 memory nguồn mà RAG đã sử dụng để biên soạn tài liệu.

> **Phân biệt quan trọng về Architecture Spec (`.docx`) & Traceability Matrix (`.xlsx`):**
> Tab `PM Copilot` trên Web UI sinh tài liệu quản lý sản phẩm từ bộ nhớ ngữ nghĩa. Nếu bạn cần sinh **bộ tài liệu kỹ thuật/kiến trúc toàn diện** từ đồ thị mã nguồn AST (`ProjectBrain_Architecture_Spec.docx`, `ProjectBrain_Traceability_Matrix.xlsx`, Markdown Wiki hoặc `.pptx`), hãy sử dụng phân hệ **Unified KB Builder / Synthesizer** (Headless) qua REST API `POST /kb/build` hoặc `POST /kb/export`, MCP tool `projectbrain_build_complete_kb` / `projectbrain_generate_project_docs`, hoặc CLI `python3 -m projectbrain.main build-kb`.

#### Tab 5: `Code Graph` — (b) Khám phá đồ thị tương tác Vis.js

Mục đích: Trực quan hóa cấu trúc mã nguồn và tri thức tài liệu (class, function, method, file, server/client PLC) dưới dạng đồ thị mạng Vis.js (`vis.Network`).

1. **Active Project (`#cgProjectSelect`):** Chọn `Local Codegraph (Default)` (dữ liệu `.codegraph/codegraph.db` cục bộ trên server) hoặc một project:branch đã đồng bộ trên server (hoặc `project:%` để xem tất cả nhánh).
2. **Search Symbols (`#cgSearchInput`):** Nhập tên class, hàm hoặc đường dẫn file cần tìm (nhấn `Enter` hoặc **Load Graph**).
3. **Filter by Kind (`#cgKindSelect`):** Chọn bộ lọc loại node:
   - `Standard View (Clean)` → `class,function,method,file` (mặc định, gọn gàng)
   - `PLC/LASAL View (with Servers & Clients)` → `class,function,method,file,variable,server,client` (dành cho codebase tự động hóa/PLC)
   - `All Kinds (with Imports & Variables)` → `all`
   - `Classes Only` → `class`
   - `Functions & Methods` → `function,method`
   - `Files Only` → `file`
4. Nhấn **Load Graph** → gọi `GET /codegraph/data?project_id=...&query=...&kinds=...` (tối đa 300 node mặc định, giới hạn API tối đa 1000).
5. **Tương tác đồ thị:** Kéo-thả, cuộn chuột zoom, rê chuột xem tooltip `qualified_name (kind)`, **click vào một node** để mở bảng **Symbol Details** bên phải (`Name`, `Kind`, `File Path`, `Lines`, `Signature`, `Docstring`).
   - *Lưu ý về cụm Leiden community:* Khi dữ liệu được sync bằng Graphify (`graphify-sync`), trường `community` (mã cụm Leiden) được lưu kèm trên từng node trong payload `/codegraph/data`. Tuy nhiên bộ lọc chuyên sâu theo mã cụm (`community=<id>`) hiện được cung cấp qua API/MCP Graphify (`GET /graphify/diff?community=...`) chứ chưa có ô dropdown riêng trên thanh filter của tab `Code Graph`.

#### Tab 6: `Version Compare` — (c) So sánh Diff giữa 2 nhánh / phiên bản

Mục đích: Đối chiếu thay đổi cấu trúc mã nguồn và thay đổi bộ nhớ/quy tắc giữa hai nhánh hoặc hai phiên bản của cùng dự án.

1. Chọn **Base Version (e.g. main)** (`#diffBaseProject`, ví dụ `my-app:main`).
2. Chọn **Target Version (e.g. branch)** (`#diffTargetProject`, ví dụ `my-app:feature-v2`).
3. Nhấn **Run Comparison**. UI gọi song song 2 endpoint:
   - `GET /codegraph/diff?base_project_id=...&target_project_id=...`
   - `GET /memory/diff?base_project_id=...&target_project_id=...`
4. **Đọc kết quả:**
   - Banner tổng hợp hiển thị số lượng **Added** (xanh lá), **Deleted** (đỏ), **Modified** (vàng) cho cả **Codebase Symbols** và **Memories & Rules**.
   - Cột trái (**Codebase changes**): Liệt kê từng symbol thêm/xóa/sửa kèm loại, đường dẫn file và danh sách trường thay đổi (`Changed: ...`).
   - Cột phải (**Memories & rules changes**): Hiển thị nội dung memory thêm/xóa; với memory bị sửa (`Modified`), hiển thị trực quan phần nội dung cũ (màu đỏ gạch ngang) ở trên và phần nội dung mới (màu xanh lá) ở dưới.

#### Tab 7: `MCP Integration` — (f) Đăng ký & xuất cấu hình nhanh cho Claude / Cursor / VS Code

Tab này gồm 3 phần:

1. **Integration Settings & Active System Status (cho Claude Desktop trên máy chạy server):**
   - Chọn **Connection Method (Transport Type)**:
     - `Stdio (Local Process)`: Điền **Default User ID** (mặc định `default`) và **Default Tags** (mặc định `source:mcp`). Mode này đăng ký lệnh chạy trực tiếp `python -m projectbrain.main mcp`.
     - `HTTP/SSE (Server Stream)`: Điền **SSE Endpoint URL** (mặc định `http://localhost:8080/mcp/sse`). Mode này đăng ký qua proxy `mcp-remote`.
   - Nhấn **Install & Apply Configuration** (`POST /memory/mcp/register`) để ghi trực tiếp vào file `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows: `%APPDATA%\Claude\claude_desktop_config.json`, Linux: `~/.config/Claude/claude_desktop_config.json`), hoặc **Remove Integration** (`POST /memory/mcp/unregister`). Sau khi áp dụng cần khởi động lại Claude Desktop.
   - Panel **Active System Status** (`GET /memory/mcp/status`) hiển thị trạng thái file config (`Found`/`Not Found`), trạng thái đã đăng ký (`Registered`/`Not Registered`), đường dẫn file và nội dung JSON hiện hành (mọi giá trị nhạy cảm như token/key được tự động che `********` khi trả về).
2. **Local Project Configuration (`mcp.json`):**
   - Tự động sinh sẵn 4 khối JSON tương ứng với **Active Project Context** (`project:branch`) và **Auth Token** bạn đang đăng nhập; **chỉ cần click vào khung code để copy**:
     - `VS Code Copilot (.vscode/mcp.json - HTTP/SSE)` → cấu hình `"type": "http"` trỏ tới `${origin}/mcp/sse?token=<token>&project_id=<activeUser>`.
     - `Claude Code / Cursor (.mcp.json - HTTP/SSE)` → dùng `npx -y mcp-remote <sseUrl>`.
     - `Claude Code / Cursor (.mcp.json - Stdio Local)` → chạy `python3 -m projectbrain.main mcp` với `PB_DEFAULT_USER_ID` và `PB_AUTH_TOKEN`.
     - `Claude Code / Cursor (.mcp.json - Stdio Remote DB)` → bổ sung `PB_DB_PATH` trỏ tới PostgreSQL từ xa.
3. **External MCP Servers Manager:**
   - Cho phép thêm các MCP server phụ trợ khác (ví dụ `postgres-mcp`) vào `claude_desktop_config.json` với `Server Name`, `Command`, `Arguments (JSON Array)` và `Environment Variables (JSON Object)` qua nút **Save MCP Server** (`POST /memory/mcp/external`).

#### Tab 8: `Project Registry` — Quản lý không gian dự án

- **Register Project:** Nhập `Project ID` (khuyến nghị định dạng `username/project-name` hoặc `project:branch`), `Display Name`, `Description` và nhấn **Register Project** (`POST /codegraph/projects`).
- **Registered Project Spaces:** Liệt kê toàn bộ project đã đăng ký và các project được merge từ `GraphStore`, hiển thị `Project Path`, `Last Sync IP`, `Last Sync Author`, `Last Synced At`, kèm sẵn dòng lệnh CLI mẫu để copy:
  `python -m projectbrain.main codegraph-sync <project_id> <serverOrigin> . main -u -m`
  và nút **Delete** (`DELETE /codegraph/projects/{project_id}`).

#### Tab 9: `Accounts & Auth` — Quản lý tài khoản & Whitelist công cụ (Chỉ hiển thị với `admin`)

- **Create or Update Account:**
  - `Username`: Tên định danh tài khoản (ví dụ `ai_reader_bot`, `ci_pipeline`). Nếu username đã tồn tại, thao tác sẽ cập nhật đè (`ON CONFLICT DO UPDATE`) token, role và `allowed_tools`.
  - `Custom Token`: Có thể nhập thủ công (ví dụ `pb_tok_...`) hoặc để trống để hệ thống tự sinh ngẫu nhiên `pb_tok_<24_hex_chars>`.
  - `Role`: Chọn 1 trong 3 vai trò (`admin`, `collaborator` — mặc định, `reader`).
  - `Allowed Tools`: Mặc định `*` (tất cả công cụ được phép theo role), hoặc danh sách tên tool phân tách dấu phẩy. Khung **Select/toggle tools to authorize** (`GET /auth/tools`) cho phép click chọn/bỏ chọn từng tool MCP trực quan.
  - Nhấn **Save Account** → `POST /auth/accounts`.
- **Registered Accounts:** Hiển thị danh sách tài khoản (`GET /auth/accounts`), badge vai trò, token (Admin thấy raw token và có nút copy; non-admin chỉ thấy dạng masked), `Allowed Tools`, thời gian tạo và nút **Delete** (`DELETE /auth/accounts/{username}`, vô hiệu hóa đối với tài khoản `admin`).

---

### 2.4. Hướng dẫn ngắn: Orchestrator Telemetry UI (`http://localhost:8484`)

Ngoài dashboard của ProjectBrain (`:8080`), hệ thống Multi-Agent Orchestrator đi kèm một giao diện giám sát thời gian thực độc lập (**Hospital Suite - Multi-Agent Orchestrator**) chạy tại **`http://localhost:8484`** (cấu hình cổng qua `ORCHESTRATOR_PORT`, mã nguồn tại `~/.gemini/tools/orchestrator/server.py` và `dashboard.py`).

- **Kết nối thời gian thực:** Dashboard kết nối tới stream SSE `/events` (có chỉ báo trạng thái kết nối ở góc trên phải) và đồng bộ dự phòng mỗi 4 giây.
- **4 tab chính trên Telemetry UI:**
  1. **`⚡ Live Dashboard`:** Hiển thị Pipeline Stage Visualizer, Banner **Concurrent Parallel Workers Active** khi nhiều subagent chạy song song, các thẻ chỉ số (`Total Tasks`, `In Progress`, `Parallel Workers`, ...), sơ đồ DAG phụ thuộc giữa các task, tiến độ `%`, nút xem transcript chi tiết (`/api/tasks/transcript`) và nút dừng tác vụ (`/api/tasks/kill`).
  2. **`📊 Model Analytics`:** Thống kê hiệu năng và phân bổ tầng model theo kiến trúc **5-Tier Fleet** (Periodic Table).
  3. **`📜 Usage History & Cost Audit`:** Theo dõi lượng `input_tokens`, `output_tokens`, `cache_tokens`, chi phí ước tính (`USD` / `VND`) và số tiền tiết kiệm được nhờ prompt caching.
  4. **`🖥️ System Logs`:** Luồng log điều phối tập trung theo thời gian thực.
- **Công cụ nhanh trên Header:**
  - **`🧠 Router Sandbox`:** Thử nghiệm logic chọn model & `reasoning_effort` theo độ phức tạp tác vụ.
  - **`📥 Audit (.md)` / `JSON`:** Xuất báo cáo kiểm toán toàn bộ phiên làm việc ra Markdown hoặc JSON.

---

## Mục 3 — Hệ thống Phân quyền (RBAC), Token & Bảo mật cho các phần không có UI riêng

Khi vận hành ở môi trường nhóm, CI/CD hoặc tích hợp nhiều AI Agent qua MCP/REST API (đặc biệt các endpoint Headless `/plugins/*`, `/v3/*`, `/graphify/*`, `/kb/*`), bạn cần nắm rõ cơ chế xác thực và phân quyền 3 lớp của ProjectBrain.

### 3.1. Cơ chế 3 vai trò, Seed Account & `allowed_tools`

Bảng `mcp_accounts` (`src/projectbrain/migrations/006_mcp_auth.sql`) lưu trữ danh sách tài khoản với 5 cột: `username` (PK), `token` (UNIQUE), `role`, `allowed_tools`, `created_at`.

1. **Ba vai trò (Roles):**
   - **`admin` (Quản trị viên):**
     - Toàn quyền trên mọi REST API và mọi FastMCP tool (bỏ qua kiểm tra `allowed_tools` trong MCP middleware và `SSEFilter`).
     - Có quyền duy nhất đối với nhóm tài nguyên quản trị: quản lý tài khoản (`/auth/*`), quản lý cấu hình Claude Desktop (`/memory/mcp/*`), quản trị vòng đời Plugin (`/plugins/reload`, `/plugins/{id}/enable`, `/plugins/{id}/disable`, `/plugins/{id}/config`, MCP tool `projectbrain_toggle_plugin`), và purge telemetry toàn cục (`POST /v3/artifacts/purge` khi không truyền `project_id`).
   - **`collaborator` (Cộng tác viên / CI/CD Pipeline / Dev Agent):**
     - Được phép đọc và **ghi/đồng bộ dữ liệu** trên Memory, Codegraph, Graphify, Sources/Documents, Unified KB và v3.0 ETL/Script Cache/Artifacts/RCA.
     - Trên MCP, chịu ràng buộc bởi danh sách `allowed_tools` của chính tài khoản đó và bị **chặn** đối với các tool trong `_ADMIN_MCP_TOOLS` (`projectbrain_toggle_plugin`).
     - Trên REST API (khi bật `PB_REQUIRE_REST_AUTH=true`), bị **chặn (HTTP `403`)** tại mọi route Admin (`/auth/*`, `/memory/mcp/*`, và `POST/PUT/PATCH/DELETE /plugins/*`).
   - **`reader` (Chỉ đọc / AI Agent tra cứu KB):**
     - Chỉ được phép thực hiện các thao tác **đọc và truy vấn**.
     - Trên MCP: Bị chặn cứng mọi công cụ ghi (xem cơ chế nhận diện tool ghi ở mục 3.2), **bất kể `allowed_tools` có đặt là `*` hay liệt kê tên tool ghi đó**. Đồng thời `SSEFilter` và `tools/list` tự động ẩn toàn bộ tool ghi và tool admin khỏi danh sách tool mà client nhìn thấy.
     - Trên REST API (khi bật `PB_REQUIRE_REST_AUTH=true`): Mọi `GET` hợp lệ (trừ route admin) đều được phép; mọi `POST`/`PUT`/`PATCH`/`DELETE` đều trả về `403`, **ngoại trừ** 9 endpoint truy vấn chỉ đọc trong `_READ_ONLY_POST_ROUTES`.

2. **Tài khoản Seed mặc định & Cảnh báo Production:**
   - Migration `007_admin_seed.sql` và hàm `init_default_admin()` trong `auth.py` tự động tạo tài khoản mặc định khi bảng `mcp_accounts` rỗng:
     - `username`: `admin`
     - `token`: `pb_tok_admin`
     - `role`: `admin`
     - `allowed_tools`: `*`
   - **CẢNH BÁO BẢO MẬT KHI LÊN PRODUCTION:** Token `pb_tok_admin` được công khai trong mã nguồn và tài liệu. Bạn **BẮT BUỘC** phải đổi token cho user `admin` (qua `POST /auth/accounts` với `username: "admin"`, `token: "<token_ngau_nhien_manh>"`, `role: "admin"`) ngay sau khi khởi tạo server và bật `PB_REQUIRE_REST_AUTH=true`.

3. **Giới hạn phạm vi công cụ qua `allowed_tools`:**
   - Giá trị `*`: Cho phép tất cả công cụ hợp lệ với `role` của tài khoản.
   - Giá trị danh sách phân tách dấu phẩy (ví dụ: `projectbrain_query, projectbrain_query_graphify, projectbrain_v3_oracle_context`):
     - `mcp_auth_middleware` và `SSEFilter` đối chiếu cả tên đầy đủ (`tool_name`) lẫn phần tên rút gọn sau dấu gạch ngang đầu tiên (`short_tool_name = tool_name.split("-", 1)[1]`).
     - Với `role = "admin"`: bỏ qua kiểm tra `allowed_tools` khi lọc SSE, nhưng **nếu** một tài khoản `admin` có `allowed_tools != "*"`, lưu ý `tools/call` vẫn kiểm tra `allowed_tools`! Vì vậy tài khoản `admin` nên giữ `allowed_tools = "*"`.
     - Với `role = "reader"`: `allowed_tools` chỉ có thể **thu hẹp thêm** tập tool đọc, không bao giờ mở khóa được tool ghi hoặc tool admin.

4. **Cách truyền Token khi gọi API hoặc MCP:**
   - Header HTTP: `Authorization: Bearer <token>` (khuyên dùng cho REST API và Streamable HTTP `/mcp-http`).
   - Query param (hữu ích cho SSE client không hỗ trợ custom header): `?token=<token>` hoặc `?api_key=<token>` (ví dụ `http://localhost:8080/mcp/sse?token=pb_tok_...&project_id=my-app:main`). Khi kết nối SSE `/mcp/sse` thành công, `mcp_auth_middleware` tự động chèn `&token=<token>` vào URL `session_id=...` để các thông điệp JSON-RPC tiếp theo giữ nguyên phiên xác thực.

---

### 3.2. Ma trận quyền hạn chi tiết: `reader` vs `collaborator` vs `admin`

#### A. Ma trận trên từng nhóm REST API

> **Lưu ý quan trọng:** Các quy tắc kiểm tra token/role trên REST API chỉ được cưỡng chế khi đặt biến môi trường **`PB_REQUIRE_REST_AUTH=true`** (hoặc `1`, `yes`). Nếu `PB_REQUIRE_REST_AUTH=false` (mặc định khi chạy dev local), `rest_auth_middleware` sẽ cho qua REST API, ngoại trừ Web Login (`/auth/verify`), mask token ở `GET /auth/accounts`, và chế độ khóa cứng `PB_READ_ONLY=true`.
> Các endpoint công khai luôn miễn kiểm tra tại `rest_auth_middleware`: `/health`, `/auth/verify`, `/docs*`, `/redoc*`, `/openapi.json`, `/dashboard*`, và `/mcp*` (riêng `/mcp*` được bảo vệ bởi `mcp_auth_middleware` riêng, luôn yêu cầu token bất kể `PB_REQUIRE_REST_AUTH`).

| Nhóm Endpoint | Method & Đường dẫn tiêu biểu | `reader` | `collaborator` | `admin` | Ghi chú |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **Health & Docs** | `GET /health`, `GET /auth/verify`, `GET /docs`, `GET /dashboard/*` | ✅ | ✅ | ✅ | Miễn xác thực middleware (`_verify` cần token hợp lệ để trả thông tin account). |
| **Auth & Accounts** | `GET /auth/accounts`, `POST /auth/accounts`, `DELETE /auth/accounts/{u}`, `GET /auth/tools` | ❌ (`403`) | ❌ (`403`) | ✅ | Route Admin. Nếu `PB_REQUIRE_REST_AUTH=false`, `GET /auth/accounts` vẫn che token với non-admin. |
| **MCP Desktop Registry** | `GET /memory/mcp/status`, `POST /memory/mcp/register`, `POST /memory/mcp/unregister`, `GET/POST/DELETE /memory/mcp/external*` | ❌ (`403`) | ❌ (`403`) | ✅ | Route Admin (can thiệp cấu hình hệ thống `claude_desktop_config.json`). |
| **Plugins (Đọc)** | `GET /plugins`, `GET /plugins?project_id=...` | ✅ | ✅ | ✅ | Giá trị config nhạy cảm (`secret`/`token`/`key`/`password`) luôn được redact tự động. |
| **Plugins (Quản trị)** | `POST /plugins/reload`, `POST /plugins/{id}/enable`, `POST /plugins/{id}/disable`, `PUT /plugins/{id}/config` | ❌ (`403`) | ❌ (`403`) | ✅ | Mọi method ghi trên `/plugins*` là **Admin-only**. |
| **Memory (Đọc & Tìm)** | `GET /memory/history`, `GET /memory/stats`, `GET /memory/diff`, `GET /memory/{id}`, `GET /memory/active-project`, **`POST /memory/search`** | ✅ | ✅ | ✅ | `POST /memory/search` nằm trong `_READ_ONLY_POST_ROUTES`. |
| **Memory (Ghi & Xóa)** | `POST /memory/add`, `POST /memory/{id}/reinforce`, `DELETE /memory/{id}`, `POST /memory/delete_all`, `POST /memory/active-project` | ❌ (`403`) | ✅ | ✅ | Bị chặn nếu `PB_READ_ONLY=true`. |
| **Codegraph (Đọc & Tìm)** | `GET /codegraph/projects`, `GET /codegraph/diff`, `GET /codegraph/status`, `GET /codegraph/data`, **`POST /codegraph/query`** | ✅ | ✅ | ✅ | `POST /codegraph/query` nằm trong `_READ_ONLY_POST_ROUTES`. |
| **Codegraph (Ghi & Sync)** | `POST /codegraph/projects`, `DELETE /codegraph/projects/{id}`, `POST /codegraph/sync`, `POST /codegraph/upload-db` | ❌ (`403`) | ✅ | ✅ | Bị chặn nếu `PB_READ_ONLY=true`. |
| **Graphify (Đọc & Diff)** | `GET /graphify/subgraph`, `GET /graphify/diff`, `GET /graphify/branches`, `GET /graphify/stats`, **`POST /graphify/query`**, **`POST /graphify/hybrid-query`**, **`POST /graphify/local-diff`** | ✅ | ✅ | ✅ | 3 route `POST` truy vấn/diff nằm trong `_READ_ONLY_POST_ROUTES`. |
| **Graphify (Sync & Promote)** | `POST /graphify/sync`, `POST /graphify/promote`, `DELETE /graphify/branches` | ❌ (`403`) | ✅ | ✅ | Bị chặn nếu `PB_READ_ONLY=true`. |
| **KB (Truy vết & Coverage)** | `GET /kb/trace`, `GET /kb/coverage` | ✅ | ✅ | ✅ | — |
| **KB (Build & Export)** | `POST /kb/build`, `POST /kb/export` | ❌ (`403`) | ✅ | ✅ | Ghi file wiki/office & ingest memory. |
| **Sources / Tài liệu (Đọc)** | `GET /sources`, `GET /sources/documents`, `GET /sources/documents/{k}/versions`, `GET /sources/documents/{k}/diff` | ✅ | ✅ | ✅ | — |
| **Sources / Tài liệu (Upload & Ingest)** | `POST /sources/upload`, `POST /sources/push-processed`, `POST /sources/{s}/ingest`, `POST /sources/webhook/github`, `POST /sources/webhook/notion` | ❌ (`403`) | ✅ | ✅ | Upload chịu giới hạn `PB_MAX_UPLOAD_MB`. |
| **PM Copilot** | **`POST /pm/search`** | ✅ | ✅ | ✅ | Nằm trong `_READ_ONLY_POST_ROUTES`. |
| **PM Copilot (Sinh Doc)** | `POST /pm/generate-doc` | ❌ (`403`) | ✅ | ✅ | Không nằm trong `_READ_ONLY_POST_ROUTES` nên `reader` bị chặn khi bật `PB_REQUIRE_REST_AUTH=true`. |
| **v3.0 Đọc & Suy luận Read-Only** | `GET /v3/flow-graph`, `GET /v3/artifacts/window`, **`POST /v3/script-cache/lookup`**, **`POST /v3/planner/test-dag`**, **`POST /v3/oracle/context`** | ✅ | ✅ | ✅ | 3 route `POST` này nằm trong `_READ_ONLY_POST_ROUTES`. |
| **v3.0 Ghi (ETL, Cache, Evidence, RCA, Webhook)** | `POST /v3/etl/ingest`, `POST /v3/script-cache/store`, `POST /v3/script-cache/feedback`, `POST /v3/script-cache/invalidate`, `POST /v3/artifacts/index`, `POST /v3/rca/triage`, `POST /v3/webhooks/{source}` | ❌ (`403`) | ✅ | ✅ | Webhook `/v3/webhooks/{source}` còn kiểm tra chữ ký HMAC nếu có secret hoặc `PB_REQUIRE_WEBHOOK_SIGNATURE=true`. |
| **v3.0 Artifact Purge** | `POST /v3/artifacts/purge` (kèm `project_id`) | ❌ (`403`) | ✅ | ✅ | — |
| **v3.0 Artifact Purge (Toàn cục)** | `POST /v3/artifacts/purge` (KHÔNG truyền `project_id`) | ❌ (`403`) | ❌ (`403`) | ✅ | Khi `PB_REQUIRE_REST_AUTH=true`, purge toàn bộ hệ thống không chỉ định `project_id` bắt buộc quyền `admin`. |

#### B. Ma trận trên các nhóm FastMCP Tool (`/mcp` SSE và `/mcp-http` Streamable HTTP)

Khác với REST API, `mcp_auth_middleware` **luôn bật xác thực token** đối với mọi kết nối `/mcp` và `/mcp-http`. Quy tắc xác định quyền trong `src/projectbrain/server/api.py`:

1. **Nhóm Tool Admin (`_ADMIN_MCP_TOOLS` — chỉ `admin` được gọi và nhìn thấy):**
   - `projectbrain_toggle_plugin` (bật/tắt plugin global hoặc per-project).
2. **Nhóm Tool Ghi (`_is_write_tool` — `collaborator` & `admin` được phép; `reader` bị chặn & ẩn hoàn toàn):**
   Một tool bị xếp vào nhóm ghi nếu thỏa mãn bất kỳ điều kiện nào sau đây:
   - Có annotation `readOnlyHint: False`.
   - Thuộc `_EXPLICIT_WRITE_TOOLS`: `docx_create_document`, `markdown_convert_to_excel`, `pptx_create_presentation`, `pdf_extract_images`, `pptx_extract_images`, `ocr_image_to_markdown`, `ocr_pdf_to_markdown`, `img2md_extract_image`, `img2md_extract_folder`, `migration_batch_scan_logic`, `migration_plan_execution_phases`.
   - Tên tool chứa bất kỳ từ khóa nào trong `_WRITE_TOOL_KEYWORDS`: `store`, `delete`, `sync`, `ingest`, `register`, `reinforce`, `push`, `promote`, `upload`, `build`, `generate`, `export`, `create`, `write`, `extract_images`, `batch_scan`, `plan_execution`, `ocr_image`, `ocr_pdf`, `img2md_extract`, `enable`, `disable`, `reload`, `invalidate`, `purge`, `index`, `triage`, `toggle`.
   - *Ví dụ cụ thể bị chặn với `reader`:* `projectbrain_store`, `projectbrain_delete`, `projectbrain_delete_all`, `projectbrain_reinforce`, `projectbrain_ingest`, `projectbrain_sync_codegraph`, `projectbrain_sync_graphify`, `projectbrain_upload_document`, `projectbrain_push_processed_document`, `projectbrain_promote_branch_graph`, `projectbrain_build_complete_kb`, `projectbrain_generate_project_docs`, `projectbrain_register_mcp`, `projectbrain_register_external_mcp`, `projectbrain_v3_ingest_etl`, `projectbrain_v3_script_cache_store`, `projectbrain_v3_index_artifact`, `projectbrain_v3_rca_triage`.
3. **Nhóm Tool Chỉ Đọc (cả `reader`, `collaborator`, `admin` đều gọi được nếu nằm trong `allowed_tools`):**
   - **Memory & Knowledge:** `projectbrain_query`, `projectbrain_get`, `projectbrain_list`, `projectbrain_stats`.
   - **Codegraph / Graphify / Versioning / KB:** `projectbrain_query_codegraph`, `projectbrain_diff_project_versions`, `projectbrain_query_graphify`, `projectbrain_local_graphify_diff`, `projectbrain_hybrid_query`, `projectbrain_list_document_versions`, `projectbrain_diff_document_versions`, `projectbrain_trace_code_and_docs`, `projectbrain_kb_coverage_report`.
   - **Plugins & v3.0 Read-only:** `projectbrain_list_plugins`, `projectbrain_v3_script_cache_lookup`, `projectbrain_v3_query_evidence_window`, `projectbrain_v3_plan_test_dag`, `projectbrain_v3_oracle_context`.
   - **Extension Tools chỉ đọc:** `pdf_extract_text`, `pdf_extract_tables`, `pdf_convert_to_markdown`, `pdf_get_page_count`, `pdf_extract_page_text`, `docx_extract_text`, `docx_extract_tables`, `excel_list_sheets`, `excel_convert_to_markdown`, `excel_convert_sheet_to_markdown`, `pptx_extract_text`, `pptx_to_markdown`, `ocr_engine_status`, `img2md_list_images`, `migration_translate_code_comments`, `migration_recommend_refactor`.

---

### 3.3. Biến môi trường bảo mật & Thực hành bằng `curl`

#### A. Bảng tổng hợp các biến môi trường bảo mật trọng yếu

| Biến môi trường | Giá trị mặc định | Tác dụng thực tế |
| :--- | :--- | :--- |
| **`PB_REQUIRE_REST_AUTH`** | `false` | Đặt `true` (hoặc `1`, `yes`) để kích hoạt `rest_auth_middleware` và `_enforce_rest_auth_if_configured`: bắt buộc Bearer token trên mọi REST API thuộc `/memory`, `/codegraph`, `/graphify`, `/kb`, `/sources`, `/pm`, `/plugins`, `/v3`, `/auth` (trừ `/auth/verify`). **Bắt buộc bật khi triển khai mạng nội bộ / production.** |
| **`PB_READ_ONLY`** (hoặc `OM_READ_ONLY`) | `false` | Đặt `true` để đưa toàn bộ server vào trạng thái chỉ đọc cứng: mọi endpoint ghi (`add`, `upload`, `sync`, `promote`, `build`, `export`, `plugins/reload`, `v3/etl/ingest`, v.v.) trả `403` ngay cả với tài khoản `admin`. Hữu ích khi chạy bản replica phục vụ tra cứu. |
| **`PB_WEBHOOK_SECRET`** / **`PB_{SOURCE}_WEBHOOK_SECRET`** | `""` (rỗng) | Secret HMAC-SHA256 cho webhook `/v3/webhooks/{source}` (ví dụ `PB_GITHUB_WEBHOOK_SECRET`, `PB_GITLAB_WEBHOOK_SECRET`). Lưu ý bảo mật: `query_project_id` không thể ghi đè để làm suy yếu global secret đang active. |
| **`PB_REQUIRE_WEBHOOK_SIGNATURE`** | `false` | Đặt `true` để bắt buộc mọi webhook gửi tới `/v3/webhooks/{source}` phải có chữ ký hợp lệ (trả `401` nếu thiếu secret hoặc sai chữ ký, ngay cả khi plugin cấu hình `allow_unsigned=true`). |
| **`PB_MAX_UPLOAD_MB`** | `50` | Giới hạn dung lượng file tối đa (MB) khi upload qua `POST /sources/upload` và `POST /codegraph/upload-db`. Vượt quá trả `413 Payload Too Large`. |
| **`PB_PLUGINS_DIR`** | `./plugins` | Thư mục chứa các plugin v3.0. `POST /plugins/reload` chỉ cho phép reload các thư mục nằm trong `cwd` hoặc danh sách đã đăng ký hợp lệ. |

#### B. Khởi động server ở chế độ Bảo mật Production

```bash
export PB_DB_URL="postgresql://postgres:strong_pass@127.0.0.1:5432/projectbrain"
export PB_REQUIRE_REST_AUTH=true
export PB_WEBHOOK_SECRET="whsec_9f8a7b6c5d4e3f2a1b0c"
export PB_REQUIRE_WEBHOOK_SIGNATURE=true
export PB_MAX_UPLOAD_MB=100
export PB_PORT=8080

python3 -m projectbrain.main serve
```

#### C. Kịch bản thực hành đầy đủ bằng `curl`

Giả sử server chạy tại `http://localhost:8080`.

##### Bước 1: Kiểm tra kết nối & Đổi token cho tài khoản `admin` mặc định

```bash
# 1. Kiểm tra server sống
curl -s http://localhost:8080/health

# 2. Xác thực token seed ban đầu
curl -s -H "Authorization: Bearer pb_tok_admin" \
  http://localhost:8080/auth/verify

# 3. ĐỔI NGAY token admin sang giá trị bí mật mới
export ADMIN_TOKEN="pb_tok_prod_admin_998877665544"
curl -s -X POST http://localhost:8080/auth/accounts \
  -H "Authorization: Bearer pb_tok_admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"username\": \"admin\",
    \"token\": \"${ADMIN_TOKEN}\",
    \"role\": \"admin\",
    \"allowed_tools\": \"*\"
  }"
```

*(Từ bước 2 trở đi, `pb_tok_admin` không còn hiệu lực, phải dùng `$ADMIN_TOKEN`.)*

##### Bước 2: Tạo tài khoản `reader` cho AI Agent chỉ tra cứu KB

Ví dụ: tạo tài khoản `ai_kb_reader` chỉ đọc, và chỉ cho phép sử dụng 4 MCP tool tra cứu cốt lõi:

```bash
curl -s -X POST http://localhost:8080/auth/accounts \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "ai_kb_reader",
    "role": "reader",
    "allowed_tools": "projectbrain_query, projectbrain_query_graphify, projectbrain_hybrid_query, projectbrain_v3_oracle_context"
  }'
```

Server sẽ trả về token mới sinh, ví dụ:
`{"status":"success","username":"ai_kb_reader","token":"pb_tok_a1b2c3d4e5f6..."}`

Kiểm chứng quyền của `ai_kb_reader`:

```bash
export READER_TOKEN="pb_tok_a1b2c3d4e5f6..."

# ĐƯỢC PHÉP: Gọi REST API truy vấn chỉ đọc (/memory/search nằm trong _READ_ONLY_POST_ROUTES)
curl -s -X POST http://localhost:8080/memory/search \
  -H "Authorization: Bearer ${READER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query": "auth middleware", "user_id": "chien334/Project-Brain:main", "limit": 5}'

# ĐƯỢC PHÉP: Xem danh sách plugin (GET)
curl -s -H "Authorization: Bearer ${READER_TOKEN}" \
  http://localhost:8080/plugins

# BỊ CHẶN (403 Forbidden): Thử ghi memory mới bằng tài khoản reader
curl -i -X POST http://localhost:8080/memory/add \
  -H "Authorization: Bearer ${READER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"content": "unauthorized note", "user_id": "chien334/Project-Brain:main"}'
# Kết quả: HTTP/1.1 403 Forbidden
# {"detail":"Permission denied: Read-only accounts cannot perform mutating operations."}
```

Cấu hình mẫu `.mcp.json` cho Cursor / Claude Code khi dùng tài khoản `reader` qua SSE:

```json
{
  "mcpServers": {
    "projectbrain-readonly": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://localhost:8080/mcp/sse?token=pb_tok_a1b2c3d4e5f6...&project_id=chien334/Project-Brain:main"
      ]
    }
  }
}
```

##### Bước 3: Tạo tài khoản `collaborator` cho CI/CD Pipeline đẩy Build / ETL / Tài liệu

Tài khoản `ci_cd_pipeline` cần quyền đồng bộ mã nguồn, đẩy tài liệu versioned, nạp ETL và lưu UDCP Script Cache, nhưng **không** được phép chỉnh cấu hình plugin hay xóa tài khoản:

```bash
curl -s -X POST http://localhost:8080/auth/accounts \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "ci_cd_pipeline",
    "role": "collaborator",
    "allowed_tools": "*"
  }'
```

Thực hành các tác vụ CI/CD bằng `COLLAB_TOKEN`:

```bash
export COLLAB_TOKEN="pb_tok_..."

# 1. ĐƯỢC PHÉP: Upload tài liệu SRS/Spec vào nhánh main
curl -s -X POST http://localhost:8080/sources/upload \
  -H "Authorization: Bearer ${COLLAB_TOKEN}" \
  -F "file=@./docs/SRS_v3.pdf" \
  -F "project_id=chien334/Project-Brain" \
  -F "branch=main" \
  -F "tags=srs,release-3.0" \
  -F "author=ci_cd_pipeline"

# 2. ĐƯỢC PHÉP: Nạp dữ liệu v3.0 ETL kèm version_tag
curl -s -X POST http://localhost:8080/v3/etl/ingest \
  -H "Authorization: Bearer ${COLLAB_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "chien334/Project-Brain",
    "branch": "main",
    "version_tag": "v3.0.1",
    "file_path": "./docs/openapi.yaml"
  }'

# 3. ĐƯỢC PHÉP: Build Unified KB (Wiki + Office exports)
curl -s -X POST http://localhost:8080/kb/build \
  -H "Authorization: Bearer ${COLLAB_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "chien334/Project-Brain",
    "branch": "main",
    "persist_memory": true,
    "generate_office_exports": true
  }'

# 4. BỊ CHẶN (403 Forbidden): Thử reload plugin hoặc tạo tài khoản mới bằng collaborator
curl -i -X POST http://localhost:8080/plugins/reload \
  -H "Authorization: Bearer ${COLLAB_TOKEN}"
# Kết quả: HTTP/1.1 403 Forbidden
# {"detail":"Permission denied: Admin role required."}
```

##### Bước 4: Dùng tài khoản `admin` quản trị Plugin Microkernel & Kiểm tra Audit tài khoản

```bash
# 1. Liệt kê tất cả tài khoản (chỉ admin mới thấy raw token thay vì dạng masked)
curl -s -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  http://localhost:8080/auth/accounts

# 2. Hot-reload thư mục plugins
curl -s -X POST http://localhost:8080/plugins/reload \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"

# 3. Bật / Tắt một plugin riêng cho một dự án cụ thể
curl -s -X POST "http://localhost:8080/plugins/brain.oracle/enable?project_id=chien334/Project-Brain" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"

# 4. Cập nhật cấu hình plugin và độ ưu tiên (priority)
curl -s -X PUT http://localhost:8080/plugins/artifact.sqlite/config \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "chien334/Project-Brain",
    "priority": 100,
    "config": {
      "retention_days": 30
    }
  }'

# 5. Purge telemetry artifacts toàn cục (không truyền project_id -> bắt buộc admin)
curl -s -X POST http://localhost:8080/v3/artifacts/purge \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{}'
```
