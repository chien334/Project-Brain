# Hướng Dẫn Toàn Diện: Kết Nối MCP, Quản Lý Branch / Version Tag, Tài Liệu (GPU/CPU OCR & Local AI) & Hệ Sinh Thái 26 Plugin Project-Brain v3.0

> **Đối tượng sử dụng:** Kỹ sư Phần mềm, Kỹ sư Nhúng / Firmware, QA / Test Automation Engineer, Tech Lead và DevOps / CI-CD Engineer.  
> **Phạm vi tài liệu:** Cấu hình MCP Client (`Cursor`, `Claude Desktop`, `Antigravity`), kiến trúc Knowledge Base 3 chiều (`project_id` + `branch` + `version_tag`), đặc tả chi tiết toàn bộ công cụ FastMCP (`src/projectbrain/ai/mcp.py` & `extensions_mcp/`), cấu hình Local OCR GPU/CPU & Local AI Document Parsing, hệ sinh thái 26 Plugin Microkernel v3.0, CLI (`src/projectbrain/main.py`) và Webhook CI/CD (`POST /v3/webhooks/{source}`).

---

## Mục Lục

- [Phần 1 — Cấu hình kết nối MCP Client (Cursor, Claude Desktop, Antigravity `mcp_config.json`)](#phần-1--cấu-hình-kết-nối-mcp-client-cursor-claude-desktop-antigravity-mcp_configjson)
  - [1.1. Hai chế độ vận hành: Local Stdio vs Remote Server (SSE / Streamable HTTP)](#11-hai-chế-độ-vận-hành-local-stdio-vs-remote-server-sse--streamable-http)
  - [1.2. Chế độ Local Stdio (`python3 -m projectbrain.main mcp`)](#12-chế-độ-local-stdio-python3--m-projectbrainmain-mcp)
  - [1.3. Chế độ Remote Server qua SSE (`/mcp`) và Streamable HTTP (`/mcp-http`)](#13-chế-độ-remote-server-qua-sse-mcp-và-streamable-http-mcp-http)
  - [1.4. Phân quyền Bearer Token (`PB_API_KEY`) & RBAC (`reader` vs `collaborator` vs `admin`)](#14-phân-quyền-bearer-token-pb_api_key--rbac-reader-vs-collaborator-vs-admin)
  - [1.5. Mẫu cấu hình chuẩn cho Cursor, Claude Desktop và Antigravity](#15-mẫu-cấu-hình-chuẩn-cho-cursor-claude-desktop-và-antigravity)
- [Phần 2 — Chiến lược gọi đúng Knowledge Base theo 3 chiều (`project_id` + `branch` + `version_tag`)](#phần-2--chiến-lược-gọi-đúng-knowledge-base-theo-3-chiều-project_id--branch--version_tag)
  - [2.1. Chiều 1 & 2: Root Branch (`pb_root`) vs Feature Branch Delta Overlay (`pb_br_...`)](#21-chiều-1--2-root-branch-pb_root-vs-feature-branch-delta-overlay-pb_br_)
  - [2.2. Kiến trúc 3-Tier Hybrid Knowledge Base (`Local Partial` → `Server Branch` → `Server Root`)](#22-kiến-trúc-3-tier-hybrid-knowledge-base-local-partial--server-branch--server-root)
  - [2.3. Chiều 3: `version_tag` cho Firmware / App Build & Tìm kiếm liên nhánh `project:%`](#23-chiều-3-version_tag-cho-firmware--app-build--tìm-kiếm-liên-nhánh-project)
  - [2.4. Bảng quyết định nhanh: Khi nào dùng tham số nào?](#24-bảng-quyết-định-nhanh-khi-nào-dùng-tham-số-nào)
  - [2.5. Multi-Source Project Hub (`project_id@source_id:branch`) & Zero-Touch Auto-Bootstrap](#25-multi-source-project-hub-project_idsource_idbranch--zero-touch-auto-bootstrap)
- [Phần 3 — Hướng dẫn chi tiết từng nhóm công cụ MCP (kèm JSON arguments mẫu)](#phần-3--hướng-dẫn-chi-tiết-từng-nhóm-công-cụ-mcp-kèm-json-arguments-mẫu)
  - [3.1. Nhóm A — Graphify, Diff một phần giữa các Branch & Hybrid Knowledge Base](#31-nhóm-a--graphify-diff-một-phần-giữa-các-branch--hybrid-knowledge-base)
  - [3.2. Nhóm B — Upload / Xử lý Tài liệu đa phương thức, Local OCR GPU/CPU & Local AI Document Parsing](#32-nhóm-b--upload--xử-lý-tài-liệu-đa-phương-thức-local-ocr-gpucpu--local-ai-document-parsing)
  - [3.3. Nhóm C — Hệ sinh thái 26 Plugin & Kiểm thử Đa thiết bị / Nhúng v3.0 theo `branch` + `version_tag`](#33-nhóm-c--hệ-sinh-thái-26-plugin--kiểm-thử-đa-thiết-bị--nhúng-v30-theo-branch--version_tag)
- [Phần 4 — Bảng tra cứu nhanh các lệnh CLI & Webhook CI/CD](#phần-4--bảng-tra-cứu-nhanh-các-lệnh-cli--webhook-cicd)
  - [4.1. Bảng tra cứu nhanh toàn bộ lệnh CLI (`python3 -m projectbrain.main ...`)](#41-bảng-tra-cứu-nhanh-toàn-bộ-lệnh-cli-python3--m-projectbrainmain-)
  - [4.2. Tích hợp Webhook CI/CD (`POST /v3/webhooks/{gitlab,github,testrail,jira,notion}`)](#42-tích-hợp-webhook-cicd-post-v3webhooksgitlabgithubtestrailjiranotion)

---

## Phần 1 — Cấu hình kết nối MCP Client (Cursor, Claude Desktop, Antigravity `mcp_config.json`)

Project-Brain triển khai máy chủ FastMCP (`mcp_server = FastMCP("projectbrain-mcp")`) trong `src/projectbrain/ai/mcp.py`, tự động nạp cả các công cụ cốt lõi (HSG Memory, Graphify, Unified KB, v3.0 Testing Plugins) lẫn các extension MCP chuyển đổi tài liệu (`extensions_mcp/loader.py`: PDF, DOCX, Excel, PPTX, Local PaddleOCR, Migration Helper).

### 1.1. Hai chế độ vận hành: Local Stdio vs Remote Server (SSE / Streamable HTTP)

```mermaid
flowchart LR
    subgraph Clients["MCP Clients"]
        C1["Cursor (.cursor/mcp.json)"]
        C2["Claude Desktop (claude_desktop_config.json)"]
        C3["Antigravity (mcp_config.json)"]
    end

    subgraph LocalMode["Chế độ 1: Local Stdio"]
        STDIO["python3 -m projectbrain.main mcp"]
        SQLITE["SQLite (OM_DB_PATH)"]
        PG1["PostgreSQL (OM_METADATA_BACKEND=postgres)"]
        STDIO --> SQLITE
        STDIO --> PG1
    end

    subgraph RemoteMode["Chế độ 2: Remote Server / Proxy"]
        PROXY["Local Stdio Proxy (PB_MODE=remote)"]
        SSE["SSE Endpoint: /mcp (hoặc /mcp/sse)"]
        HTTP["Streamable HTTP: /mcp-http"]
        AUTH["MCP Auth & RBAC Middleware<br/>(Bearer Token -> reader / collaborator / admin)"]
        PROXY -->|PB_URL + PB_API_KEY| AUTH
        SSE --> AUTH
        HTTP --> AUTH
    end

    C1 -->|stdio| STDIO
    C2 -->|stdio| PROXY
    C3 -->|SSE / HTTP| SSE
    C3 -->|HTTP| HTTP
```

### 1.2. Chế độ Local Stdio (`python3 -m projectbrain.main mcp`)

Trong chế độ **Local Stdio**, MCP Client trực tiếp spawn tiến trình Python chạy `run_mcp_server()` (`await mcp_server.run_stdio_async()`). Dữ liệu được đọc/ghi trực tiếp vào cơ sở dữ liệu cấu hình qua biến môi trường:

| Biến môi trường | Giá trị mẫu | Ý nghĩa & Lưu ý từ `src/projectbrain/core/config.py` |
| :--- | :--- | :--- |
| `PB_MODE` (hoặc `OM_MODE`) | `local` | Chạy trực tiếp trên DB cục bộ (mặc định nếu không set `remote`). |
| `OM_DB_PATH` (hoặc `PB_DB_PATH`) | `/path/to/projectbrain.sqlite` | Đường dẫn file SQLite dùng chung cho Memory, GraphStore overlay và bảng v3.0 (`udcp_script_cache`, `telemetry_artifacts`, `v3_rca_incidents`). |
| `PB_GRAPH_DB_PATH` | `/path/to/graph.sqlite` | Tùy chọn tách riêng file SQLite cho GraphStore (nếu bỏ trống sẽ fallback về `OM_DB_PATH` / `PB_DB_PATH`). |
| `OM_METADATA_BACKEND` | `sqlite` \| `postgres` | Chọn backend metadata/lưu trữ. Khi dùng `postgres`, cấu hình thêm `DATABASE_URL` (ví dụ `postgresql://user:pass@host:5432/projectbrain`). |
| `PB_DEFAULT_USER_ID` | `my-project` | Project ID mặc định nếu tool call không truyền `project_id` / `user_id`. |
| `PB_READ_ONLY` | `false` \| `true` | Khi `true`, mọi tool ghi (`sync`, `store`, `upload`, `ingest`, `promote`, `toggle`) đều bị chặn tại nguồn. |
| `PB_WORKSPACE_ROOT` | `/Users/dev/my-repo` | Khóa phạm vi đọc file của `projectbrain_v3_ingest_etl` vào đúng thư mục repo, chống path traversal. |

> [!TIP]
> **Khi dùng Local Stdio kết nối tới PostgreSQL dùng chung cho cả team:** Chỉ cần đặt `OM_METADATA_BACKEND=postgres` và `DATABASE_URL=postgresql://...` trong block `env` của `mcp_config.json`. Khi đó máy dev vẫn chạy tool cục bộ (quét AST, OCR PDF không tốn băng thông upload raw file) nhưng đồng bộ delta trực tiếp vào schema `pb_root` / `pb_br_*` trên PostgreSQL trung tâm.

### 1.3. Chế độ Remote Server qua SSE (`/mcp`) và Streamable HTTP (`/mcp-http`)

Khi chạy Project-Brain Server (`python3 -m projectbrain.main serve`, mặc định cổng `PB_PORT=8080`), `src/projectbrain/server/api.py` mount đồng thời hai endpoint MCP chuẩn:

1. **SSE Transport (`/mcp`):**
   - `app.mount("/mcp", mcp_server.sse_app())`
   - Endpoint kết nối SSE stream: `http://<SERVER_HOST>:8080/mcp/sse` (hoặc `/mcp` — middleware `mcp_auth_middleware` tự động xử lý cả trường hợp client POST nhầm vào `/mcp/sse` hoặc `/mcp` để rewrite sang `/mcp/messages/`, đồng thời tự động chèn `&token=<token>` vào URL `session_id=...` trong SSE stream và thêm header `X-Accel-Buffering: no`).
   - Hỗ trợ truyền `project_id` qua query string (`?token=<TOKEN>&project_id=<PROJECT_ID>`) để tự động gán vào `mcp_request_project_id`.
2. **Streamable HTTP Transport (`/mcp-http`):**
   - `app.mount("/mcp-http", mcp_server.streamable_http_app())`
   - Phù hợp với các MCP client thế hệ mới hỗ trợ chuẩn **Streamable HTTP** (gửi JSON-RPC POST kèm header `Authorization: Bearer <PB_API_KEY>`, nhận `Mcp-Session-Id` trong CORS `expose_headers`).
3. **Chế độ Remote Proxy qua Stdio (`PB_MODE=remote`):**
   - Dành cho client chỉ hỗ trợ `stdio` (như Claude Desktop bản cũ) hoặc khi cần chạy **Local AST extraction / Local OCR** ở máy client rồi đẩy JSON delta gọn nhẹ lên server qua `PB_URL` + `PB_API_KEY`.
   - Trong `mcp.py`, mọi tool Graphify, Unified KB, Document Pipeline và v3.0 Plugin đều kiểm tra `is_pb_remote_mode()`: nếu `PB_MODE=remote`, client sẽ gọi REST API tương ứng tới `PB_URL` kèm `Authorization: Bearer <PB_API_KEY>`.

### 1.4. Phân quyền Bearer Token (`PB_API_KEY`) & RBAC (`reader` vs `collaborator` vs `admin`)

Hệ thống xác thực MCP (`src/projectbrain/server/routes/auth.py` và `mcp_auth_middleware` trong `src/projectbrain/server/api.py`) kiểm tra token từ header `Authorization: Bearer <token>` hoặc query param `?token=` / `?api_key=` đối chiếu với bảng `mcp_accounts`:

| Vai trò (`role`) | Công cụ được phép thấy (`tools/list`) & gọi (`tools/call`) | Công cụ bị chặn (JSON-RPC `-32003` / HTTP `403`) | Đối tượng khuyên dùng |
| :--- | :--- | :--- | :--- |
| **`reader`** | Chỉ các tool **chỉ đọc** (Read-Only): `projectbrain_query`, `projectbrain_get`, `projectbrain_list`, `projectbrain_stats`, `projectbrain_hardware_status`, `projectbrain_query_codegraph`, `projectbrain_diff_project_versions`, `projectbrain_query_graphify`, `projectbrain_local_graphify_diff`, `projectbrain_hybrid_query`, `projectbrain_list_document_versions`, `projectbrain_diff_document_versions`, `projectbrain_trace_code_and_docs`, `projectbrain_kb_coverage_report`, `projectbrain_list_plugins`, `projectbrain_v3_script_cache_lookup`, `projectbrain_v3_query_evidence_window`, `projectbrain_v3_plan_test_dag`, `projectbrain_v3_oracle_context`, các tool đọc PDF/DOCX/Excel/PPTX (`pdf_convert_to_markdown`, `docx_extract_text`, `excel_convert_to_markdown`, `pptx_to_markdown`...). | Mọi tool chứa từ khóa ghi (`_WRITE_TOOL_KEYWORDS`: `store`, `delete`, `sync`, `ingest`, `register`, `reinforce`, `push`, `promote`, `upload`, `build`, `generate`, `export`, `create`, `write`, `enable`, `disable`, `reload`, `invalidate`, `purge`, `index`, `triage`, `toggle`...) và danh sách `_EXPLICIT_WRITE_TOOLS` (kể cả `ocr_image_to_markdown`, `pdf_extract_images`, `docx_create_document`...). | QA Reviewer, AI Assistant chỉ đọc tri thức Production, External Read-only Agent. |
| **`collaborator`** | Toàn bộ tool của `reader` **cộng thêm** các tool ghi tri thức, đồng bộ branch, upload tài liệu, build KB, nạp ETL v3.0, lưu Script Cache, index Telemetry Artifacts, chạy RCA Triage. | Nhóm `_ADMIN_MCP_TOOLS` (`projectbrain_toggle_plugin`) và các REST route admin (`/auth/*`, `/memory/mcp/*`, `POST/PUT /plugins/*`). | Lập trình viên, Kỹ sư Firmware, QA Automation Agent, CI/CD Runner. |
| **`admin`** | **Toàn quyền** mọi MCP tool (bao gồm `projectbrain_toggle_plugin`), đồng thời có thể dùng REST API quản trị tài khoản `/auth/accounts` và plugin control plane `/plugins/*`. | Không bị chặn (trừ khi `allowed_tools` của tài khoản bị giới hạn danh sách cụ thể thay vì `"*"`). | Tech Lead, Platform Admin, DevOps quản lý Server. |

> [!IMPORTANT]
> Ngoài `role`, mỗi tài khoản trong `mcp_accounts` còn có trường `allowed_tools` (mặc định `"*"` hoặc chuỗi phân tách bằng dấu phẩy). Middleware `SSEFilter` sẽ tự động **lọc ẩn** các tool không có quyền ngay trong phản hồi `tools/list` (cả trên SSE stream lẫn HTTP JSON-RPC) và chặn cứng tại `tools/call`.

### 1.5. Mẫu cấu hình chuẩn cho Cursor, Claude Desktop và Antigravity

#### A. Cursor (`.cursor/mcp.json` hoặc `.mcp.json` tại root repo)

```json
{
  "mcpServers": {
    "projectbrain-remote-http": {
      "url": "http://127.0.0.1:8080/mcp-http",
      "headers": {
        "Authorization": "Bearer pb_collab_your_token_here"
      }
    },
    "projectbrain-local-proxy": {
      "command": "python3",
      "args": ["-m", "projectbrain.main", "mcp"],
      "cwd": "/Users/dev/workspace/Project-Brain",
      "env": {
        "PYTHONPATH": "/Users/dev/workspace/Project-Brain/src",
        "PB_MODE": "remote",
        "PB_URL": "http://127.0.0.1:8080",
        "PB_API_KEY": "pb_collab_your_token_here",
        "PB_DEFAULT_USER_ID": "my-firmware-project",
        "PB_WORKSPACE_ROOT": "/Users/dev/workspace/my-firmware-project"
      }
    }
  }
}
```

#### B. Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json` trên macOS)

Ví dụ 1 — Chạy **Local Stdio thuần túy** với SQLite (`OM_DB_PATH`) hoặc PostgreSQL (`OM_METADATA_BACKEND=postgres`):

```json
{
  "mcpServers": {
    "projectbrain-local": {
      "command": "/Users/dev/workspace/Project-Brain/.venv/bin/python",
      "args": ["-m", "projectbrain.main", "mcp"],
      "env": {
        "PYTHONPATH": "/Users/dev/workspace/Project-Brain/src",
        "PB_MODE": "local",
        "OM_METADATA_BACKEND": "sqlite",
        "OM_DB_PATH": "/Users/dev/.projectbrain/projectbrain.db",
        "PB_DEFAULT_USER_ID": "my-project"
      }
    },
    "projectbrain-local-postgres": {
      "command": "/Users/dev/workspace/Project-Brain/.venv/bin/python",
      "args": ["-m", "projectbrain.main", "mcp"],
      "env": {
        "PYTHONPATH": "/Users/dev/workspace/Project-Brain/src",
        "PB_MODE": "local",
        "OM_METADATA_BACKEND": "postgres",
        "DATABASE_URL": "postgresql://pb_user:pb_pass@127.0.0.1:5432/projectbrain",
        "PB_DEFAULT_USER_ID": "my-project"
      }
    }
  }
}
```

> [!NOTE]
> Bạn cũng có thể tự động đăng ký vào cấu hình Claude Desktop thông qua công cụ MCP `projectbrain_register_mcp` hoặc Web UI tab MCP.

#### C. Antigravity (`~/.gemini/antigravity/mcp_config.json`)

Antigravity hỗ trợ cả **Local Stdio**, **Remote SSE (`/mcp/sse`)** và **Streamable HTTP (`/mcp-http`)**:

```json
{
  "mcpServers": {
    "projectbrain-sse": {
      "serverUrl": "http://127.0.0.1:8080/mcp/sse?token=pb_admin_your_token&project_id=smart-lock-fw"
    },
    "projectbrain-http": {
      "serverUrl": "http://127.0.0.1:8080/mcp-http",
      "headers": {
        "Authorization": "Bearer pb_collab_your_token"
      }
    },
    "projectbrain-stdio-remote": {
      "command": "python3",
      "args": ["-m", "projectbrain.main", "mcp"],
      "env": {
        "PYTHONPATH": "/Users/dev/workspace/Project-Brain/src",
        "PB_MODE": "remote",
        "PB_URL": "http://127.0.0.1:8080",
        "PB_API_KEY": "pb_collab_your_token",
        "PB_MCP_ROLE": "collaborator",
        "OM_DB_PATH": "/Users/dev/.projectbrain/local_cache.db"
      }
    }
  }
}
```

---

## Phần 2 — Chiến lược gọi đúng Knowledge Base theo 3 chiều (`project_id` + `branch` + `version_tag`)

Để tránh "ô nhiễm ngữ cảnh" (context pollution) giữa các dự án, các nhánh Git đang phát triển song song, và các bản build firmware/app khác nhau, Project-Brain tổ chức tri thức theo tọa độ **3 chiều**:

$$\text{Context Coordinate} = (\texttt{project\_id},\; \texttt{branch},\; \texttt{version\_tag})$$

### 2.1. Chiều 1 & 2: Root Branch (`pb_root`) vs Feature Branch Delta Overlay (`pb_br_...`)

Trong `src/projectbrain/graphify_engine/store.py`, Project-Brain không nhân bản toàn bộ đồ thị cho mỗi nhánh Git mới. Thay vào đó, engine sử dụng **kiến trúc Overlay Delta $O(\Delta)$**:

1. **Root Branch (`branch="main"`):**
   - Dữ liệu chuẩn (canonical) được lưu tại PostgreSQL schema **`pb_root`** (gồm các bảng `pb_root.nodes`, `pb_root.edges`, `pb_root.hyperedges`, `pb_root.graph_branches`).
   - Trên SQLite (`SqliteOverlayGraphStore`), dữ liệu root dùng bộ bảng `root_nodes`, `root_edges`, `root_hyperedges`.
2. **Feature Branch (`branch="feature/ble-v2"`):**
   - Engine tạo một **overlay delta schema** riêng biệt theo hàm `sanitize_branch_schema_name(project_id, branch_name)` đảm bảo an toàn định danh PostgreSQL $\le 60$ bytes:
     $$\texttt{pb\_br\_\{clean\_proj[:16]\}\_\{clean\_branch[:28]\}\_\{sha256\_8\}}$$
     Ví dụ: `project_id="smartlock"`, `branch="feature/ble-v2"` $\rightarrow$ `pb_br_smartlock_feature_ble_v2_a1b2c3d4`.
   - Schema nhánh chỉ chứa **phần sai khác (delta)**: `node_upserts`, `edge_upserts`, `hyperedge_upserts` (node/cạnh thêm hoặc sửa) và `node_tombstones`, `edge_tombstones`, `hyperedge_tombstones` (node/cạnh bị xóa trên nhánh).
   - **Tự động hợp nhất qua SQL Views (`v_resolved_*`):** Khi truy vấn nhánh `feature/ble-v2`, server đọc từ 3 View ảo trong schema nhánh:
     - `v_resolved_nodes` = `node_upserts` $\cup$ (`pb_root.nodes` $\setminus$ `node_tombstones` $\setminus$ `node_upserts`)
     - `v_resolved_edges` = `edge_upserts` $\cup$ (`pb_root.edges` $\setminus$ `edge_tombstones` $\setminus$ `edge_upserts`)
     - `v_resolved_hyperedges` = `hyperedge_upserts` $\cup$ (`pb_root.hyperedges` $\setminus$ `hyperedge_tombstones` $\setminus$ `hyperedge_upserts`)
   - Nếu nhánh con kế thừa từ một nhánh cha (`base_branch="release/v2"`, hỗ trợ độ sâu tối đa `max_depth=8`), View tự động nối theo chuỗi lineage `[main -> release/v2 -> feature/ble-v2]`.
3. **Quy tắc phân giải `project_id` và `branch` trong MCP (`resolve_mcp_user_id`):**
   - Bạn có thể truyền tách rời `project_id="smartlock", branch="feature/ble-v2"` **hoặc** dạng ghép `project_id="smartlock:feature/ble-v2"`.
   - Nếu bỏ trống `project_id`, MCP tự lấy từ `PB_DEFAULT_USER_ID`, `env.active_project`, hoặc tự đọc `pyproject.toml`/`package.json`/`Cargo.toml` và chạy `git rev-parse --abbrev-ref HEAD` để tự ghép `<project>:<git_branch>`.

### 2.2. Kiến trúc 3-Tier Hybrid Knowledge Base (`Local Partial` → `Server Branch` → `Server Root`)

Khi lập trình viên đang sửa code ở local (chưa commit hoặc chưa push lên server), `HybridResolver` (`projectbrain_hybrid_query`, `projectbrain_query_graphify`, `projectbrain_local_graphify_diff`) hợp nhất tri thức theo **3 tầng (3-Tier)** theo thứ tự ưu tiên từ cao xuống thấp:

```mermaid
flowchart TB
    Q["Agent / MCP Tool Call<br/>(project_id, branch, local_path)"] --> T1
    subgraph T1["Tier 1: Local Partial Graphify (Ưu tiên cao nhất)"]
        L["File: .projectbrain/local_graph/partial_graph.json<br/>Chứa AST delta của các file đang sửa / git dirty"]
    end
    subgraph T2["Tier 2: Server Branch Overlay Schema"]
        B["Schema: pb_br_&lt;proj&gt;_&lt;branch&gt;_&lt;hash8&gt;<br/>node_upserts / edge_upserts / tombstones"]
    end
    subgraph T3["Tier 3: Server Root Canonical Schema"]
        R["Schema: pb_root<br/>Code AST + Docs + Tests của nhánh main"]
    end

    T1 -->|Ghi đè & áp dụng tombstones| T2
    T2 -->|Hợp nhất qua v_resolved_*| T3
    T3 --> OUT["Resolved Subgraph + HSG Memories<br/>(Context chính xác 100% với code đang mở)"]
```

- **Lợi ích:** AI Agent không bị đọc nhầm cấu trúc code cũ của `main` khi bạn vừa đổi tên hàm hoặc thêm endpoint ở local; chỉ cần chạy `projectbrain_sync_graphify(..., git_dirty_only=true, local_only=true)` trong vài trăm mili-giây là Tier 1 đã cập nhật.

### 2.3. Chiều 3: `version_tag` cho Firmware / App Build & Tìm kiếm liên nhánh `project:%`

Trong hệ thống nhúng, IoT và kiểm thử đa thiết bị (Mobile + Firmware + Cloud API), cùng một nhánh `main` hoặc `release/v1` có thể tồn tại nhiều bản dựng (build) có bảng mã lỗi UART, sơ đồ chuyển màn hình, hoặc lệnh UDCP khác nhau. Do đó, v3.0 bổ sung chiều **`version_tag`** (ví dụ `"fw-v1.2.0"`, `"app-v3.0.1"`):

1. **Phân tách Đồ thị Luồng Trạng thái (`Flow Graph`):**
   - Plugin `linker.state_flow` gắn `metadata.version_tag` vào các `STATE_NODE`, `SCREEN_NODE` và `TRANSITION_EDGE`.
   - Khi gọi `GET /v3/flow-graph?project_id=...&branch=...&version_tag=fw-v1.2.0` hoặc `projectbrain_v3_oracle_context`, engine chỉ lọc các chuyển trạng thái thuộc đúng bản build đó, tránh báo pass/fail sai theo UI hoặc FSM của bản build cũ.
2. **Phân tách Bảng Mã lỗi UART / AT / HardFault / HTTP API:**
   - `etl.hardware_datasheet` và `etl.openapi31` gắn `version_tag` cho `ErrorCodeRule` (ví dụ `AT+BLECONN` trả mã lỗi mới trên `fw-v1.2.0`, hoặc HTTP `422` trên OpenAPI `v3.1`).
3. **Phân tách `UDCP Script Cache` (`build_version`) & `Test DAG`:**
   - `projectbrain_v3_script_cache_lookup` / `store` định danh script đã xác minh bằng bộ khóa `(project_id, intent_id, platform, build_version, screen_hash)`. Khi nâng cấp app từ `app-v3.0.0` lên `app-v3.0.1`, cache cũ không bị trộn lẫn; nếu `ui_fingerprint` thay đổi vượt ngưỡng `fingerprint_tolerance` (`0.5`), script được đánh dấu cần tái tổng hợp.
   - `projectbrain_v3_plan_test_dag` nhận `version_tag` (làm fallback cho `build_version` nếu không truyền riêng) để chọn đúng danh sách testcase bị ảnh hưởng và tự động chèn precondition (`flash_firmware`, `install_app`) phù hợp bản build.
4. **Tìm kiếm liên nhánh (`Cross-Branch Search`) bằng `project:%`:**
   - Trong hệ thống **HSG Cognitive Memory**, mỗi nhánh được lưu dưới `user_id` dạng `"<project_id>:<branch>"` (ví dụ `"smartlock:main"`, `"smartlock:feature/ble-v2"`).
   - Khi cần tìm kiếm ký ức/lịch sử quyết định kỹ thuật **xuyên suốt mọi nhánh** của dự án, truyền wildcard SQL `user_id="smartlock:%"` vào `projectbrain_query`: engine `hsg_query` (`src/projectbrain/memory/hsg.py`) hỗ trợ mẫu `%` để khớp tất cả các nhánh thuộc dự án `smartlock`.

### 2.4. Bảng quyết định nhanh: Khi nào dùng tham số nào?

| Tình huống sử dụng | `project_id` | `branch` | `version_tag` / `build_version` | Chiến lược khuyến nghị |
| :--- | :--- | :--- | :--- | :--- |
| Tra cứu kiến trúc chuẩn đang chạy Production | `"smartlock"` | `"main"` | *(tùy chọn)* | Đọc trực tiếp Tier 3 (`pb_root`) qua `projectbrain_hybrid_query`. |
| Đang code tính năng mới trên nhánh Git | `"smartlock"` | `"feature/ble-v2"` | *(không cần)* | Sync `git_dirty_only=true, local_only=true` rồi dùng `projectbrain_hybrid_query` (Tier 1+2+3). |
| So sánh nhánh feature trước khi tạo Merge Request | `"smartlock"` | `"feature/ble-v2"` | *(không cần)* | Dùng `projectbrain_local_graphify_diff` (local vs server) hoặc `projectbrain_diff_project_versions` (nhánh vs nhánh). |
| Tìm kiếm ghi chú/lỗi đã từng gặp ở *mọi* nhánh | `"smartlock:%"` | *(trong `user_id`)* | *(không cần)* | Dùng `projectbrain_query` với `user_id="smartlock:%"`. |
| Nạp Datasheet UART / OpenAPI / Kiểm thử bản build mới | `"smartlock"` | `"release/1.2"` | `"fw-v1.2.0"` | Dùng `projectbrain_v3_ingest_etl`, `projectbrain_v3_plan_test_dag`, `projectbrain_v3_oracle_context`. |
| Tra cứu/lưu kịch bản điều khiển thiết bị UDCP | `"smartlock"` | *(ngầm định)* | `build_version="app-v3.0.1"` | Dùng `projectbrain_v3_script_cache_lookup` & `store` kèm `platform` và `screen_hash`. |

### 2.5. Multi-Source Project Hub (`project_id@source_id:branch`) & Zero-Touch Auto-Bootstrap

Khi dự án gồm nhiều repo/module con (`backend-api`, `web-frontend`, `mobile-app`, `infra`) dùng chung một bộ tài liệu kiến trúc/API (`__shared__`), Project-Brain mở rộng định danh thành **3 phần**:

- `project_id@source_id:branch` — định tuyến trực tiếp vào schema nhánh riêng của từng sub-source (`pb_src_<slug>_<h6>_root` / `_br_*`).
- `project_id@__shared__` — lưu tài liệu dùng chung tại `pb_root` (`Shared Hub`) cùng 2 bảng hợp đồng liên source (`cross_source_relations`, `cross_source_contract_artifacts`).
- **Auto-Bootstrap không cần thao tác UI:** AI Agent chỉ cần kết nối MCP và gọi `projectbrain_get_agent_setup_playbook` (hoặc resource `projectbrain://guides/agent-multi-source-setup`) → `projectbrain_discover_workspace_sources` → `projectbrain_bootstrap_project_hub` (hoặc REST `POST /master/hub/discover` và `POST /master/hub/bootstrap`), sau đó nâng cấp từng phần bằng `projectbrain_sync_graphify(..., source_id=..., upgrade_mode="delta", auto_link_sources=True)` và kiểm tra cảnh báo `drifted` qua `projectbrain_cross_source_impact`.

> [!TIP]
> Xem hướng dẫn đầy đủ, sơ đồ kiến trúc, bảng tham chiếu 9 MCP tools / REST API mới, Web UI `/app/workspace/sources` và các template copy-paste cho `AGENTS.md` / `CLAUDE.md` / `.cursorrules` tại **[Hướng Dẫn Multi-Source Project Hub & Agent Auto-Setup](HUONG_DAN_MULTI_SOURCE_VA_AGENT_AUTO_SETUP.md)**.

---

## Phần 3 — Hướng dẫn chi tiết từng nhóm công cụ MCP (kèm JSON arguments mẫu)

### 3.1. Nhóm A — Graphify, Diff một phần giữa các Branch & Hybrid Knowledge Base

#### 1. `projectbrain_sync_graphify`
Trích xuất đồ thị AST (hàm, class, route, module) và tài liệu Markdown trong workspace, lưu bản cache cục bộ tại `.projectbrain/local_graph/partial_graph.json` và đồng bộ lên GraphStore (local hoặc remote `/graphify/sync`).

- **Tham số chính xác (`src/projectbrain/ai/mcp.py`):**
  - `workspace_path` (`str`, mặc định `"."`)
  - `project_id` (`Optional[str]`)
  - `branch` (`str`, mặc định `"main"`)
  - `scoped_paths` (`Optional[List[str]]`): Chỉ quét một số thư mục/file cụ thể (kích hoạt chế độ `partial=True`).
  - `git_dirty_only` (`bool`, mặc định `False`): Chỉ quét các file có thay đổi theo `git status`/`git diff` (kích hoạt `partial=True`).
  - `local_only` (`bool`, mặc định `False`): Chỉ ghi ra `.projectbrain/local_graph/partial_graph.json` (Tier 1), không đẩy lên Server.
  - `cluster` (`bool`, mặc định `True`): Phân cụm community trên đồ thị.
  - `include_docs` (`bool`, mặc định `True`): Trích xuất thêm thực thể từ file tài liệu.
  - `base_branch` (`Optional[str]`): Khai báo nhánh cha để tạo overlay lineage và đăng ký memory branch.
  - `device` (`Optional[str]`): Thiết bị tăng tốc Graphify (`auto` | `gpu` | `cuda:0` | `mps` | `cpu`).
  - `workers` (`Optional[int]`): Số luồng xử lý song song AST & tài liệu.
  - `semantic_engine` (`Optional[str]`): Động cơ embedding ngữ nghĩa (`auto` | `st` | `ollama` | `openai` | `tfidf`).

- **JSON Arguments mẫu (4 chế độ phổ biến):**

```json
// Chế độ 1: Full sync nhánh main lên Server
{
  "workspace_path": "/Users/dev/workspace/smartlock-fw",
  "project_id": "smartlock",
  "branch": "main",
  "cluster": true,
  "include_docs": true
}
```

```json
// Chế độ 2: Scoped sync theo thư mục đang sửa trên Feature Branch (kế thừa từ main)
{
  "workspace_path": "/Users/dev/workspace/smartlock-fw",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "base_branch": "main",
  "scoped_paths": ["src/ble/", "tests/test_ble_pairing.py"],
  "cluster": true
}
```

```json
// Chế độ 3: Chỉ quét các file Git dirty & giữ ở Tier 1 Local (siêu nhanh, không ghi lên Server)
{
  "workspace_path": "/Users/dev/workspace/smartlock-fw",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "git_dirty_only": true,
  "local_only": true
}
```

#### 2. `projectbrain_local_graphify_diff`
Tính toán khác biệt cấu trúc AST (node thêm/sửa/xóa, edge mới/mất) giữa đồ thị cục bộ chưa commit (`.projectbrain/local_graph/partial_graph.json`) và trạng thái hiện có trên Server (Tier 2/3).

- **Tham số chính xác:** `workspace_path` (`"."`), `project_id`, `branch` (`"main"`), `scoped_paths`, `git_dirty_only` (`False`), `refresh_local` (`True` — tự quét lại workspace trước khi diff).

```json
{
  "workspace_path": "/Users/dev/workspace/smartlock-fw",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "git_dirty_only": true,
  "refresh_local": true
}
```

#### 3. `projectbrain_hybrid_query`
Truy vấn **3-Tier Hybrid Knowledge Base** kết hợp cả duyệt đồ thị cấu trúc Graphify (Tier 1 → Tier 2 → Tier 3) lẫn tìm kiếm ngữ nghĩa HSG Cognitive Memories (`include_memories=True`), trả về `graph_summary`, `matched_nodes`, `linked_doc_chunks`, `traversal_stats` (`tier_breakdown`) và `cognitive_memories`.

- **Tham số chính xác:** `query` (bắt buộc), `project_id`, `branch` (`"main"`), `local_path` (đường dẫn tới `partial_graph.json` nếu muốn chỉ định), `include_memories` (`True`), `mode` (`"bfs"`), `budget` (`2500`).

```json
{
  "query": "BLE pairing handshake timeout và xử lý lỗi AT+BLECONN",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "local_path": "/Users/dev/workspace/smartlock-fw/.projectbrain/local_graph/partial_graph.json",
  "include_memories": true,
  "mode": "bfs",
  "budget": 3000
}
```

#### 4. `projectbrain_query_graphify`
Công cụ phân tích đồ thị chuyên sâu với **8 hành động (`action`)**: `query`, `trace`, `impact`, `callers`, `callees`, `node`, `god_nodes`, `community`.

- **Tham số chính xác:** `query` (bắt buộc — từ khóa tìm kiếm, `node_id`, hoặc số thứ tự community), `project_id`, `branch` (`"main"`), `action` (`"query"`), `target_symbol` (dùng cho `action="trace"` làm đích), `mode` (`"bfs"`), `max_hops` (`3`, cũng dùng làm `top_n` khi `action="god_nodes"`), `budget` (`2500`).

```json
// Ví dụ 1: Tìm bán kính ảnh hưởng (impact radius) trong 3 bước nhảy khi sửa hàm ble_pair_start
{
  "query": "src/ble/pairing.c::ble_pair_start",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "action": "impact",
  "max_hops": 3
}
```

```json
// Ví dụ 2: Tìm đường đi (trace) từ API route tới hàm ghi Flash
{
  "query": "POST /api/v1/lock/provision",
  "target_symbol": "flash_write_key_slot",
  "project_id": "smartlock",
  "branch": "main",
  "action": "trace"
}
```

```json
// Ví dụ 3: Lấy danh sách callers / callees hoặc god_nodes (top 10 nút trung tâm)
{
  "query": "top_architectural_hubs",
  "project_id": "smartlock",
  "branch": "main",
  "action": "god_nodes",
  "max_hops": 10
}
```

#### 5. `projectbrain_diff_project_versions`
So sánh chi tiết symbol codegraph (`added`, `deleted`, `modified`) và ký ức ngữ cảnh (`memories`) giữa hai nhánh hoặc hai phiên bản dự án. Lưu ý định dạng truyền vào là chuỗi ghép `<project_id>:<branch>`.

- **Tham số chính xác:** `base_project_id` (bắt buộc), `target_project_id` (bắt buộc).

```json
{
  "base_project_id": "smartlock:main",
  "target_project_id": "smartlock:feature/ble-v2"
}
```

#### 6. `projectbrain_promote_branch_graph`
Hợp nhất nguyên tử (atomic promote) toàn bộ mutation (`upserts` và `tombstones`) từ schema overlay của nhánh feature lên schema canonical `pb_root` (hoặc lên `target_branch` nếu merge vào nhánh trung gian như `release/v2`). Tự động xóa bỏ delta của nhánh nguồn sau khi promote.

- **Tham số chính xác:** `project_id` (bắt buộc), `branch` (bắt buộc), `target_branch` (`Optional[str]`).

```json
{
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "target_branch": "main"
}
```

---

### 3.2. Nhóm B — Upload / Xử lý Tài liệu đa phương thức, Local OCR GPU/CPU & Local AI Document Parsing

#### 1. `projectbrain_upload_document`
Tự động đọc, tách đơn vị (unit-level chunking theo trang PDF / section DOCX / sheet Excel / slide PPTX), tái sử dụng các đơn vị không đổi để tiết kiệm chi phí OCR (**pre-OCR delta reuse**), lưu phiên bản tài liệu (`doc_key`, `doc_version`), nạp vào HSG Memory và trích xuất `GraphDelta` đồng bộ vào nhánh chỉ định.

- **Tham số chính xác:** `file_path` (bắt buộc), `project_id`, `branch` (`"main"`), `author`, `tags` (`List[str]`), `push_to_server` (`Optional[bool]` — mặc định theo `PB_MODE`), `doc_key`, `doc_version`, `base_version`, `base_branch`, `ocr_engine`, `ocr_device`, `local_ai_model`, `local_ai_url`.

```json
{
  "file_path": "/Users/dev/specs/SmartLock_SRS_v2.1.pdf",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "author": "nguyenvana",
  "tags": ["srs", "ble", "spec"],
  "push_to_server": true,
  "doc_key": "smartlock-srs",
  "doc_version": "v2.1",
  "base_version": "v2.0",
  "base_branch": "main"
}
```

#### 2. Quy trình Client-Side Parse + `projectbrain_push_processed_document` (Khuyên dùng cho File lớn / Bảo mật / Tối ưu CPU Server)
Thay vì upload file nhị phân nặng lên server, bạn có thể dùng các MCP tool chuyển đổi cục bộ thuộc `extensions_mcp/` trên máy trạm để xuất ra Markdown sạch, kiểm tra nội dung rồi đẩy trực tiếp Markdown và `GraphDelta` lên Server qua `projectbrain_push_processed_document` (gọi `POST /sources/push-processed`).

**Bước 1 — Chuyển đổi tài liệu cục bộ bằng đúng chữ ký tham số của `extensions_mcp`:**

```json
// PDF -> Markdown (extensions_mcp/convert_pdf/server_stdio.py)
// Tool: pdf_convert_to_markdown
{
  "file_path": "/Users/dev/specs/SmartLock_SRS_v2.1.pdf",
  "include_tables": true,
  "include_images": false
}
```

```json
// DOCX -> Text (extensions_mcp/convert_docx/server_stdio.py)
// Tool: docx_extract_text (lưu ý bọc trong object "params")
{
  "params": {
    "file_path": "/Users/dev/specs/API_Contract.docx"
  }
}
```

```json
// Excel / CSV -> Markdown (extensions_mcp/convert_excel/server_stdio.py)
// Tool: excel_convert_to_markdown
{
  "file_path": "/Users/dev/specs/UART_Error_Matrix.xlsx",
  "sheets": ["UART_Codes", "BLE_Status"]
}
```

```json
// PowerPoint -> Markdown (extensions_mcp/convert_pptx/server_stdio.py)
// Tool: pptx_to_markdown (lưu ý bọc trong object "params")
{
  "params": {
    "file_path": "/Users/dev/specs/Architecture_Review.pptx"
  }
}
```

```json
// Ảnh sơ đồ / bảng mạch -> Markdown bằng PaddleOCR cục bộ (extensions_mcp/ocr_local/server.py)
// Tool: ocr_image_to_markdown
{
  "image_path": "/Users/dev/specs/ble_state_machine_diagram.png",
  "output_path": "/Users/dev/specs/ble_state_machine_diagram.md"
}
```

**Bước 2 — Đẩy Markdown đã xử lý lên Server theo `branch` & `doc_version`:**

- **Tham số chính xác của `projectbrain_push_processed_document`:** `filename` (bắt buộc), `markdown_content` (bắt buộc), `project_id`, `branch` (`"main"`), `author`, `tags`, `server_url`, `doc_key`, `doc_version`, `base_version`, `base_branch`.

```json
{
  "filename": "SmartLock_SRS_v2.1.md",
  "markdown_content": "# SmartLock SRS v2.1\n\n## BLE Pairing Flow\n- Timeout: 30000ms\n- Error Code: `ERR_BLE_TIMEOUT (0xE041)`...",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "author": "nguyenvana",
  "tags": ["srs", "ble", "processed"],
  "doc_key": "smartlock-srs",
  "doc_version": "v2.1",
  "base_version": "v2.0",
  "base_branch": "main"
}
```

**Quản lý & So sánh phiên bản tài liệu (`projectbrain_list_document_versions` & `projectbrain_diff_document_versions`):**

```json
// Xem lịch sử các phiên bản của một doc_key
{
  "doc_key": "smartlock-srs",
  "project_id": "smartlock",
  "branch": "feature/ble-v2"
}
```

```json
// Diff chi tiết cấp độ từng trang/section (unchanged, modified, added, removed)
{
  "doc_key": "smartlock-srs",
  "from_version": "v2.0",
  "to_version": "v2.1",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "from_branch": "main",
  "to_branch": "feature/ble-v2"
}
```

#### 3. `projectbrain_build_complete_kb`
Chạy pipeline tổng hợp Knowledge Base đầu cuối (end-to-end): quét AST, cắt chunk tài liệu, liên kết đa tín hiệu Code ↔ Docs ↔ Tests (`linker.code_doc`), nạp vào HSG Memory (`persist_memory=True`) và xuất Wiki + bộ Office (`.docx`, `.xlsx`, `.pptx`) khi `generate_office_exports=True`.

- **Tham số chính xác:** `workspace_path` (`"."`), `project_id`, `branch` (`"main"`), `persist_memory` (`True`), `output_dir` (`Optional[str]`), `generate_office_exports` (`True`), `device`, `workers`, `semantic_engine`, `ocr_engine`, `ocr_device`.

```json
{
  "workspace_path": "/Users/dev/workspace/smartlock-fw",
  "project_id": "smartlock",
  "branch": "main",
  "persist_memory": true,
  "output_dir": "docs/kb",
  "generate_office_exports": true
}
```

#### 4. `projectbrain_trace_code_and_docs`
Truy vết liên tầng (cross-layer traceability) cho bất kỳ symbol, API route, hoặc đường dẫn file nào: trả về node AST, các chunk tài liệu giải thích nó (`documents_symbol`/`documented_in`), các ca kiểm thử liên quan (`verifies_symbol`), callers/callees và trạng thái **drift detection** (tài liệu bị lệch so với code).

- **Tham số chính xác:** `target` (bắt buộc), `project_id`, `branch` (`"main"`), `workspace_path` (`"."`).

```json
{
  "target": "ble_pair_start",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "workspace_path": "/Users/dev/workspace/smartlock-fw"
}
```

#### 5. `projectbrain_kb_coverage_report`
Thống kê độ phủ tri thức toàn repository: tỷ lệ symbol đã có tài liệu (`documented`), suy luận (`inferred`), chưa có tài liệu (`undocumented`), tỷ lệ phủ API & MCP tool, và danh sách cảnh báo lệch code-tài liệu (`drift alerts`).

- **Tham số chính xác:** `project_id`, `branch` (`"main"`), `workspace_path` (`"."`).

```json
{
  "project_id": "smartlock",
  "branch": "main",
  "workspace_path": "/Users/dev/workspace/smartlock-fw"
}
```

#### 6. `projectbrain_generate_project_docs`
Sinh lại ngay lập tức (on-demand) Markdown Knowledge Wiki và các tài liệu Office từ dữ liệu GraphStore hiện tại của nhánh mà không cần chạy lại toàn bộ quá trình ingest.

- **Tham số chính xác:** `workspace_path` (`"."`), `project_id`, `branch` (`"main"`), `output_dir` (`"docs/kb"`), `formats` (mặc định `["wiki", "docx", "xlsx", "pptx"]`).

```json
{
  "workspace_path": "/Users/dev/workspace/smartlock-fw",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "output_dir": "docs/kb_feature_ble",
  "formats": ["wiki", "xlsx"]
}
```

#### 7. Tăng tốc GPU/MPS/CUDA cho Graphify, Local OCR (GPU/CPU) & Local AI Document Parsing (`GraphGPUAccelerator`, `UnifiedOCREngine` & `etl.local_ai_ocr`)

Project-Brain hỗ trợ tăng tốc phần cứng đồng bộ cho cả **Graphify AST / Clustering / Semantic Linking** (`projectbrain.graphify_engine.gpu_accelerator.GraphGPUAccelerator`) lẫn **Local OCR & AI Document Parsing** (`projectbrain.ocr_runtime.UnifiedOCREngine`, `etl.local_ai_ocr`, `etl.multimodal_doc`).

**A. Cài đặt gói mở rộng tùy chọn (Optional Extras):**

```bash
pip install -e ".[ocr-cpu]"       # PaddleOCR chạy CPU + PyMuPDF
pip install -e ".[ocr-gpu]"       # PaddleOCR tăng tốc GPU/CUDA + PyMuPDF
pip install -e ".[ocr-onnx]"      # RapidOCR qua ONNX Runtime CPU (gọn nhẹ, tốc độ cao)
pip install -e ".[ocr-onnx-gpu]"  # RapidOCR qua ONNX Runtime GPU/CUDA
pip install -e ".[graphify-gpu]"  # PyTorch (CUDA/Apple MPS) + sentence-transformers + igraph + scipy
pip install -e ".[graphify-cuda]" # Đầy đủ graphify-gpu + NVIDIA RAPIDS cuGraph/cuDF (Linux x86_64)
```

**B. Biến môi trường cấu hình GPU/MPS/CUDA, Multi-Worker & Embedding:**

| Biến môi trường | Giá trị / Mặc định | Ý nghĩa |
| :--- | :--- | :--- |
| `PB_GRAPHIFY_DEVICE` | `auto` \| `gpu` \| `cuda:0` \| `mps` \| `cpu` (`auto`) | Chọn backend cho Graphify (`cugraph_cuda` → `torch_cuda` → `torch_mps` → `numpy_blas_cpu`). |
| `PB_GRAPHIFY_WORKERS` | Số nguyên (`min(8, cpu_count)`) | Số worker song song cho Tree-sitter AST & trích xuất tài liệu. |
| `PB_GRAPHIFY_SEMANTIC_ENGINE` | `auto` \| `st` \| `ollama` \| `openai` \| `tfidf` (`auto`) | Chọn engine batch embedding cho liên kết ngữ nghĩa và độ tương đồng. |
| `PB_EMBED_DEVICE` | `auto` \| `gpu` \| `cuda:0` \| `mps` \| `cpu` (`auto`) | Chọn thiết bị chạy local embedding (sentence-transformers / HSG). |
| `PB_OCR_DEVICE` | `auto` \| `gpu` \| `cuda:0` \| `mps` \| `cpu` (`auto`) | Chọn thiết bị chạy OCR; `auto` tự nhận diện GPU/CUDA/MPS và rơi về CPU khi cần. |
| `PB_OCR_GPU_MEM_MB` | Số nguyên MB (`2000`, ví dụ `4096`) | Giới hạn dung lượng VRAM GPU cho PaddleOCR / ONNX Runtime. |
| `PB_OCR_USE_TENSORRT` | `true` \| `false` (`false`) | Bật tối ưu TensorRT trên GPU NVIDIA hỗ trợ. |
| `PB_OCR_PRECISION` | `fp32` \| `fp16` (`fp32`) | Độ chính xác suy luận trên GPU hỗ trợ. |
| `PB_OCR_CPU_THREADS` | Số nguyên (mặc định `min(8, cpu_count)`) | Số luồng CPU cho suy luận OCR trên CPU. |
| `PB_OCR_ENABLE_MKLDNN` | `auto` \| `true` \| `false` (`auto`) | Bật tăng tốc oneDNN/MKL-DNN trên CPU x86 (`auto` tự bật trên x86_64/AMD64). |
| `PB_OCR_FALLBACK_TO_CPU` | `true` \| `false` (`true`) | Tự động chuyển sang CPU nếu khởi tạo GPU lỗi hoặc hết VRAM (OOM). |
| `PB_OCR_BATCH_SIZE` | Số nguyên (`4`) | Kích thước lô trang/ảnh xử lý đồng thời. |

> [!TIP]
> Kiểm tra trạng thái phần cứng hợp nhất (Graphify GPU/MPS/CUDA + OCR + Local AI) bất cứ lúc nào qua REST **`GET /graphify/hardware`**, MCP tool chỉ đọc **`projectbrain_hardware_status`** (mở cho cả `reader`), hoặc CLI **`python3 -m projectbrain.main hardware-status`**.

**C. Các backend Local AI Document Parsing (`PB_OCR_ENGINE`):**

| Giá trị `PB_OCR_ENGINE` | Mô tả & Biến môi trường liên quan |
| :--- | :--- |
| `auto` | Chuỗi tự động: `paddle` → `rapidocr` → Local VLM khả dụng → cloud vision. |
| `paddle` | PaddleOCR cục bộ (`pip install -e ".[ocr-cpu]"` hoặc `".[ocr-gpu]"`). |
| `rapidocr` | RapidOCR nền ONNX Runtime (`pip install -e ".[ocr-onnx]"` hoặc `".[ocr-onnx-gpu]"`). |
| `tesseract` | Tesseract OCR cục bộ (`pytesseract` + binary `tesseract`). |
| `ollama` | Vision LLM chạy cục bộ qua Ollama: `OLLAMA_URL` (mặc định `http://localhost:11434`), `PB_OLLAMA_VISION_MODEL=qwen2.5vl:7b`. |
| `vllm` | Máy chủ vLLM tương thích OpenAI API: `PB_LOCAL_AI_URL` (ví dụ `http://localhost:8000/v1`), `PB_LOCAL_AI_MODEL`. |
| `lmstudio` | LM Studio local server: `PB_LOCAL_AI_URL` (ví dụ `http://localhost:1234/v1`), `PB_LOCAL_AI_MODEL`. |
| `llamacpp` | `llama.cpp` (`llama-server`): `PB_LOCAL_AI_URL` (ví dụ `http://localhost:8081/v1`), `PB_LOCAL_AI_MODEL`. |
| `hybrid_local` | Kết hợp OCR cổ điển (`paddle`/`rapidocr`) cho trang văn bản thuần và Local VLM (`ollama`/`vllm`/`lmstudio`/`llamacpp`) cho trang có bảng phức tạp, sơ đồ mạch hoặc công thức. |

**D. Ví dụ gọi MCP với GPU/CPU OCR & Local AI:**

```json
// 1. Kiểm tra trạng thái thiết bị GPU/MPS/CUDA, OCR, Graphify backend và Local AI (REST: GET /graphify/hardware)
// Tool: projectbrain_hardware_status (chỉ đọc, khả dụng cho mọi role kể cả reader)
{}
```

```json
// 1b. Kiểm tra riêng trạng thái OCR engine & Local AI endpoints
// Tool: ocr_engine_status
{}
```

```json
// 2. Chuyển đổi PDF scan sang Markdown bằng GPU + Local AI (hybrid_local / ollama / vllm)
// Tool: ocr_pdf_to_markdown
{
  "pdf_path": "/Users/dev/specs/STM32WB55_Datasheet_Scanned.pdf",
  "dpi": 200,
  "force_ocr": false,
  "engine": "hybrid_local",
  "device": "gpu",
  "local_ai_model": "qwen2.5vl:7b",
  "local_ai_url": "http://localhost:11434",
  "batch_size": 4,
  "output_path": "/Users/dev/specs/STM32WB55_Datasheet_Scanned.md"
}
```

```json
// 3. Trích xuất Markdown từ một ảnh sơ đồ / ảnh chụp màn hình bằng GPU/CPU OCR hoặc Local AI Vision
// Tool: img2md_extract_image
{
  "image_path": "/Users/dev/specs/ble_state_diagram.png",
  "engine": "ollama",
  "device": "cuda:0",
  "local_ai_model": "qwen2.5vl:7b",
  "output_path": "/Users/dev/specs/ble_state_diagram.md"
}
```

```json
// 4. Upload & nạp tài liệu PDF scan / ảnh trực tiếp vào KB (tự đọc PB_OCR_ENGINE, PB_OCR_DEVICE, PB_LOCAL_AI_*)
// Tool: projectbrain_upload_document
{
  "file_path": "/Users/dev/specs/STM32WB55_Datasheet_Scanned.pdf",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "doc_key": "stm32wb55-datasheet",
  "doc_version": "v1.2",
  "tags": ["datasheet", "ocr", "stm32"]
}
```

---

### 3.3. Nhóm C — Hệ sinh thái 26 Plugin & Kiểm thử Đa thiết bị / Nhúng v3.0 theo `branch` + `version_tag`

#### Tổng quan 26 Built-in Plugins thuộc 9 Category (`src/projectbrain/plugins/builtins/`)

| Nhóm (`PluginCategory`) | Số lượng | Danh sách `plugin_id` & Vai trò chính |
| :--- | :---: | :--- |
| **`graph_backend`** | 3 | `graph.sqlite_overlay` (priority 100), `graph.postgres_schema` (90), `graph.neo4j` (50) — lưu trữ Graphify overlay & Flow Graph theo `branch`/`version_tag`. |
| **`vector_backend`** | 2 | `vector.hsg_memory` (100), `vector.qdrant` (60) — tìm kiếm vector ngữ nghĩa đa sector. |
| **`etl_parser`** | 7 | `etl.graphify_ast` (100), `etl.legacy_testcase` (95), `etl.hardware_datasheet` (95), `etl.openapi31` (95), `etl.cpp_embedded` (95), `etl.multimodal_doc` (90), `etl.local_ai_ocr` (85 — GPU/CPU OCR & Local AI Vision parser qua `UnifiedOCREngine`). |
| **`knowledge_linker`** | 2 | `linker.code_doc` (100), `linker.state_flow` (100) — liên kết Code-Doc-Test và xây đồ thị trạng thái `STATE_NODE`/`SCREEN_NODE`/`TRANSITION_EDGE`. |
| **`doc_exporter`** | 4 | `exporter.wiki` (100), `exporter.docx` (90), `exporter.xlsx` (90), `exporter.pptx` (90). |
| **`script_cache`** | 2 | `cache.sqlite_pg` (100), `cache.redis` (60) — bộ nhớ đệm UDCP Script Cache có auto-invalidation (`fallback_rate > 0.25`, `min_samples=4`). |
| **`artifact_store`** | 2 | `artifacts.local_sqlite` (100), `artifacts.minio_clickhouse` (60) — lưu & truy vấn 8 loại telemetry đồng bộ NTP (`video_offset_ms`, TTL 30–90 ngày). |
| **`brain_consumer`** | 3 | `brain.planner` (100 — Test DAG), `brain.oracle` (100 — AI Oracle Judge), `brain.rca` (100 — RCA Triage & Dedup). |
| **`webhook`** | 1 | `webhook.event_ingest` (100) — chuẩn hóa & xác thực HMAC-SHA256 cho GitLab, GitHub, TestRail, Jira, Notion. |

---

#### 1. `projectbrain_list_plugins`
Liệt kê tất cả plugin đã đăng ký (kèm category, priority, trạng thái bật/tắt theo project và báo cáo sức khỏe `health`). Các khóa nhạy cảm trong `config` (`password`, `token`, `secret`, `api_key`, DSN) được tự động che (`***`).

- **Tham số chính xác:** `category` (`Optional[str]`), `project_id` (`Optional[str]`).

```json
{
  "category": "etl_parser",
  "project_id": "smartlock"
}
```

#### 2. `projectbrain_toggle_plugin` *(Chỉ dành cho `admin`)*
Bật, tắt hoặc cập nhật cấu hình (`config`) cho một plugin ở cấp toàn cục hoặc riêng theo `project_id`. Chặn tự động nếu `PB_READ_ONLY=true` hoặc vai trò không phải `admin`.

- **Tham số chính xác:** `plugin_id` (bắt buộc), `enabled` (`True`), `project_id`, `config` (`Optional[Dict[str, Any]]`).

```json
{
  "plugin_id": "cache.redis",
  "enabled": true,
  "project_id": "smartlock",
  "config": {
    "redis_url": "redis://127.0.0.1:6379/0",
    "key_prefix": "pb:smartlock",
    "ttl_seconds": 2592000,
    "fallback_threshold": 0.25,
    "min_samples": 4
  }
}
```

#### 3. `projectbrain_v3_ingest_etl`
Nạp dữ liệu kiểm thử, đặc tả phần cứng, OpenAPI 3.1 hoặc mã nguồn nhúng C/C++/Qt/Linker thông qua các ETL parser chuyên biệt và tự động đồng bộ `GraphDelta` vào đúng `(project_id, branch, version_tag)`.

- **Tham số chính xác:** `file_path` (`Optional[str]`), `content` (`Optional[str]` — bắt buộc có ít nhất `file_path` hoặc `content`), `source_name` (`"input.md"`), `format_hint` (`Optional[str]`), `project_id`, `branch` (`"main"`), `version_tag` (`Optional[str]`).

**4 JSON Arguments mẫu cho từng loại nguồn dữ liệu:**

```json
// Mẫu 1: Testcase Legacy (CSV / Excel / TestRail JSON-XML) -> CanonicalTestIntent
{
  "file_path": "tests/suites/ble_pairing_cases.xlsx",
  "source_name": "ble_pairing_cases.xlsx",
  "format_hint": "xlsx",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "version_tag": "app-v3.0.1"
}
```

```json
// Mẫu 2: Datasheet phần cứng / Bảng mã lỗi UART & AT Commands (inline content hoặc file .md)
{
  "content": "# STM32WB55 BLE Modem UART Spec\n| Code | Symbol | Meaning | Severity |\n|---|---|---|---|\n| 0xE041 | ERR_BLE_TIMEOUT | Pairing handshake timeout 30s | CRITICAL |\n| +CME ERROR: 100 | AT_ERR_SIM_MISSING | eSIM not detected | ERROR |",
  "source_name": "uart_ble_errata_v1.2.md",
  "format_hint": "uart_errors",
  "project_id": "smartlock",
  "branch": "main",
  "version_tag": "fw-v1.2.0"
}
```

```json
// Mẫu 3: Đặc tả API OpenAPI 3.1 / Swagger (.yaml / .json)
{
  "file_path": "docs/api/smartlock_cloud_openapi31.yaml",
  "source_name": "smartlock_cloud_openapi31.yaml",
  "format_hint": "openapi31",
  "project_id": "smartlock",
  "branch": "main",
  "version_tag": "cloud-v2.4.0"
}
```

```json
// Mẫu 4: Mã nguồn C/C++ Firmware, Qt UI (.ui), hoặc Linker Script (.ld / .map)
{
  "file_path": "firmware/src/ble_gatt_handler.c",
  "source_name": "ble_gatt_handler.c",
  "format_hint": "c",
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "version_tag": "fw-v1.2.0"
}
```

#### 4. `projectbrain_v3_script_cache_store` & `projectbrain_v3_script_cache_lookup`
Quản lý **Verified UDCP Script Cache** theo từng cặp `(intent_id, platform, build_version, screen_hash)` để Layer 2 tái sử dụng kịch bản kiểm thử đã chạy thành công thay vì phải tổng hợp lại từ LLM.

- **Tham số `projectbrain_v3_script_cache_store`:** `intent_id` (bắt buộc), `platform` (bắt buộc — ví dụ `"android"`, `"ios"`, `"qt_linux"`, `"mcu_uart"`), `build_version` (bắt buộc), `udcp_commands` (`List[Dict[str, Any]]`, bắt buộc), `project_id`, `screen_hash` (`""`), `ui_fingerprint` (`Optional[Dict[str, Any]]`).

```json
// Store UDCP Script Cache
{
  "project_id": "smartlock",
  "intent_id": "TC_BLE_PAIR_001",
  "platform": "android",
  "build_version": "app-v3.0.1",
  "screen_hash": "sha256:9f84b2c1d0e7",
  "ui_fingerprint": {
    "activity": "BlePairingActivity",
    "primary_cta_id": "btn_start_pair",
    "widgets_count": 12
  },
  "udcp_commands": [
    { "step": 1, "action": "tap", "target": "id/btn_start_pair", "timeout_ms": 5000 },
    { "step": 2, "action": "wait_for_text", "text": "Connected", "timeout_ms": 15000 }
  ]
}
```

- **Tham số `projectbrain_v3_script_cache_lookup`:** `intent_id`, `platform`, `build_version`, `project_id`, `screen_hash`.

```json
// Lookup UDCP Script Cache
{
  "project_id": "smartlock",
  "intent_id": "TC_BLE_PAIR_001",
  "platform": "android",
  "build_version": "app-v3.0.1",
  "screen_hash": "sha256:9f84b2c1d0e7"
}
```

#### 5. `projectbrain_v3_index_artifact` & `projectbrain_v3_query_evidence_window`
Đánh chỉ mục và truy vấn đồng bộ 8 loại telemetry đa phương thức (`video_h264`, `har`, `pcap`, `crashpad_dmp`, `gdb_hardfault`, `uart_log`, `power_trace_ma`, `screenshot_png`) theo mốc thời gian **NTP (`ntp_timestamp_ms`)** kèm căn chỉnh `video_offset_ms`.

- **Tham số `projectbrain_v3_index_artifact`:** `run_id`, `step_id`, `artifact_type`, `uri`, `ntp_timestamp_ms` (`int`, bắt buộc), `project_id`, `device_id` (`""`), `build_version` (`""`), `retention_days` (`30`, tự clamp 30–90 ngày), `summary_metrics` (`Optional[Dict[str, Any]]`).

```json
// Index một UART log hoặc Crash dump vào kho Telemetry
{
  "project_id": "smartlock",
  "run_id": "run_20261121_ci_8842",
  "step_id": "step_04_ble_pair",
  "artifact_type": "uart_log",
  "uri": "s3://pb-artifacts/smartlock/run_8842/uart_dut01.log",
  "ntp_timestamp_ms": 1795264700120,
  "device_id": "stm32wb55-rig-03",
  "build_version": "fw-v1.2.0",
  "retention_days": 45,
  "summary_metrics": {
    "peak_current_ma": 42.6,
    "error_count": 1,
    "last_at_code": "0xE041"
  }
}
```

- **Tham số `projectbrain_v3_query_evidence_window`:** `run_id`, `center_ts_ms` (`int`), `window_ms` (`5000`), `project_id`.

```json
// Truy vấn toàn bộ video, log UART, biểu đồ dòng điện, pcap trong cửa sổ +/- 5000ms quanh thời điểm lỗi
{
  "run_id": "run_20261121_ci_8842",
  "center_ts_ms": 1795264700120,
  "window_ms": 5000,
  "project_id": "smartlock"
}
```

#### 6. `projectbrain_v3_plan_test_dag`
Lập kế hoạch **Impact-Driven Test Execution DAG** bằng thuật toán Kahn's Topological Sort: tính bán kính ảnh hưởng (`blast_radius`) từ code diff / SRS diff, tự động chèn các bước tiền điều kiện (`flash_firmware`, `install_app`), và gắn sẵn UDCP Script Cache theo `version_tag`.

- **Tham số chính xác:** `project_id`, `branch` (`"main"`), `version_tag`, `srs_diff` (`""`), `code_diff_files` (`List[str]`), `artifacts` (`List[str]`), `target_devices` (`List[str]`).

```json
{
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "version_tag": "fw-v1.2.0",
  "srs_diff": "Thay đổi timeout bắt tay BLE từ 15s lên 30s và thêm mã lỗi 0xE041",
  "code_diff_files": [
    "firmware/src/ble_gatt_handler.c",
    "mobile/android/BlePairingActivity.kt"
  ],
  "artifacts": ["fw-v1.2.0.bin", "app-v3.0.1.apk"],
  "target_devices": ["stm32wb55-rig-03", "pixel-8-android15"]
}
```

#### 7. `projectbrain_v3_oracle_context`
Đánh giá dữ liệu telemetry thu được sau bước chạy test so với tiêu chí SRS, đồ thị chuyển trạng thái (`Flow Graph`), bảng mã lỗi UART/AT/HTTP, ngưỡng HardFault và ngân sách công suất (`power_budget_ma`) thuộc đúng `(project_id, branch, version_tag)`.

- **Tham số chính xác:** `project_id`, `branch` (`"main"`), `version_tag`, `test_intent_id`, `screen_id`, `telemetry_summary` (`Dict[str, Any]`).

```json
{
  "project_id": "smartlock",
  "branch": "feature/ble-v2",
  "version_tag": "fw-v1.2.0",
  "test_intent_id": "TC_BLE_PAIR_001",
  "screen_id": "SCREEN_BLE_PAIRING",
  "telemetry_summary": {
    "from_state": "STATE_IDLE",
    "to_state": "STATE_PAIRED",
    "uart_log": "[12:01:02] BLE_INIT OK\n[12:01:05] ERR_BLE_TIMEOUT 0xE041",
    "http_status": 200,
    "avg_current_ma": 38.4,
    "hardfault_detected": false
  }
}
```

#### 8. `projectbrain_v3_rca_triage`
Chuẩn hóa stacktrace/log lỗi, khử trùng lặp sự cố (deduplication vào bảng `v3_rca_incidents`), phân loại miền lỗi (`Firmware/HardFault`, `Network/API`, `UI/Timing`, `FlakyScript`), trích xuất mã ticket (Jira/Linear/GitLab) và kích hoạt tự chữa lành (`self_heal` / `invalidate`) ngược lại `UDCP Script Cache` nếu có `cache_id`.

- **Tham số chính xác:** `stacktrace_or_log` (bắt buộc), `project_id`, `run_id`, `test_intent_id`, `cache_id`.

```json
{
  "project_id": "smartlock",
  "run_id": "run_20261121_ci_8842",
  "test_intent_id": "TC_BLE_PAIR_001",
  "cache_id": "sc_9921_android_tc001",
  "stacktrace_or_log": "HardFault_Handler: CFSR=0x00008200 BFAR=0x2000FFF4 PC=0x08014A2C in ble_gatt_handler.c:142 (Ref: JIRA-4512)"
}
```

---

## Phần 4 — Bảng tra cứu nhanh các lệnh CLI & Webhook CI/CD

### 4.1. Bảng tra cứu nhanh toàn bộ lệnh CLI (`python3 -m projectbrain.main ...`)

| Nhóm | Lệnh CLI & Cú pháp chính xác (`src/projectbrain/main.py`) | Mô tả & Cờ tùy chọn quan trọng |
| :--- | :--- | :--- |
| **Server & MCP** | `python3 -m projectbrain.main serve` | Khởi động FastAPI REST API, Dashboard Web UI, `/mcp` (SSE) & `/mcp-http` (cổng `PB_PORT`, mặc định `8080`). |
| | `python3 -m projectbrain.main mcp` | Khởi động FastMCP server qua `stdio` (hỗ trợ `PB_MODE=local` hoặc `remote`). |
| **Graphify & Branch** | `python3 -m projectbrain.main hardware-status [--device auto\|gpu\|cuda\|mps\|cpu] [--ocr-device D] [--semantic-engine E]` | Xuất JSON kiểm tra phần cứng GPU/MPS/CUDA/CPU cho cả OCR, Graphify & Local AI (tương ứng `GET /graphify/hardware`). |
| | `python3 -m projectbrain.main graphify-sync <project_id> [path] [--branch B] [--base-branch BASE] [--url URL] [--scoped P1,P2] [--dirty] [--local-only] [--device D] [--workers N] [--semantic-engine E]` | Quét AST + Docs, lưu `.projectbrain/local_graph/partial_graph.json`, đồng bộ lên `/graphify/sync`. Dùng `--dirty` (`-d`) cho file sửa đổi, `--local-only` (`-l`) chỉ cache local. |
| | `python3 -m projectbrain.main graphify-diff <project_id> [--base main] [--target branch] [--local] [--path .] [--url URL]` | So sánh đồ thị giữa hai nhánh trên server (`/graphify/diff`) hoặc giữa local uncommitted và remote khi kèm `--local` (`-l`). |
| **Tài liệu & Legacy** | `python3 -m projectbrain.main upload-doc <project_id> <file_path> [--branch main] [--base-branch BASE] [--doc-key KEY] [--doc-version VER] [--base-version BASE_VER] [--ocr-engine E] [--ocr-device D] [--local-ai-model M] [--local-ai-url U] [--url URL] [--author A]` | Parse tài liệu cục bộ bằng `DocumentPipeline` (pre-OCR delta) và đẩy Markdown + `GraphDelta` lên `/sources/push-processed`. |
| | `python3 -m projectbrain.main upload-file <project_id> <file_path> [url] [tags] [author] [--branch main] [--base-branch BASE] [--doc-key KEY] [--doc-version VER] [--base-version BASE_VER]` | Upload trực tiếp file gốc qua multipart form lên `/sources/upload`. |
| | `python3 -m projectbrain.main codegraph-sync <project_id> [url] [path] [branch] [-m] [-u]` | Đồng bộ `.codegraph/codegraph.db` (`-m` sync thêm memories, `-u` upload nguyên file SQLite DB; fallback sang Graphify nếu chưa có `.codegraph`). |
| | `python3 -m projectbrain.main ingest-files <project_id> <dir_path>` | Đọc và nạp toàn bộ file text trong thư mục vào HSG Memories. |
| **Unified KB** | `python3 -m projectbrain.main build-kb [project_id] [path] [--branch B] [--no-memory] [--no-office] [--output DIR] [--db-path DB] [--device D] [--workers N] [--semantic-engine E] [--ocr-engine OE] [--ocr-device OD]` | Tổng hợp trọn bộ Unified KB (AST + Docs + Cross-link + HSG Memory + Wiki/Office exports). |
| | `python3 -m projectbrain.main kb-trace <project_id> <target> [--branch B] [--path PATH] [--db-path DB]` | Truy vết liên tầng Code-Doc-Test cho một symbol, route hoặc file. |
| | `python3 -m projectbrain.main kb-coverage <project_id> [--branch B] [--path PATH] [--db-path DB]` | Xuất báo cáo JSON độ phủ tài liệu, test và cảnh báo drift. |
| **v3.0 Plugins & Testing** | `python3 -m projectbrain.main plugins [list\|enable\|disable\|reload] [target] [--project P] [--category C]` | Liệt kê, bật/tắt (toàn cục hoặc theo `--project`), hoặc hot-reload thư mục plugin (`PB_PLUGINS_DIR`). |
| | `python3 -m projectbrain.main v3-etl <project_id> <file_path> [--branch B] [--version-tag V] [--format F] [--db-path DB]` | Chạy ETL parser v3.0 và StateFlow linker cho testcase, datasheet, OpenAPI, C/C++ theo `branch` + `version_tag`. |
| | `python3 -m projectbrain.main v3-cache <lookup\|store> <project_id> --intent-id I --platform P --build-version V [--screen-hash H] [--commands JSON] [--db-path DB]` | Tra cứu hoặc lưu UDCP Script Cache (`--commands` nhận chuỗi JSON hoặc đường dẫn file `.json`). |
| | `python3 -m projectbrain.main v3-artifacts <window\|purge> [--run-id R] [--center-ts T] [--window-ms W] [--db-path DB]` | Truy vấn cửa sổ bằng chứng đồng bộ NTP (`window`) hoặc dọn artifact hết hạn TTL (`purge`). |
| | `python3 -m projectbrain.main v3-plan <project_id> [--branch B] [--version-tag V] [--files f1,f2] [--artifacts a1,a2] [--srs-diff TEXT] [--db-path DB]` | Lập Test Execution DAG (`brain.planner`) từ danh sách file thay đổi và artifact build. |
| | `python3 -m projectbrain.main v3-oracle <project_id> [--intent-id I] [--version-tag V] [--uart-log L] [--branch B] [--screen-id S] [--db-path DB]` | Đánh giá pass/fail (`brain.oracle`) từ file hoặc chuỗi UART log theo đúng `version_tag`. |
| | `python3 -m projectbrain.main v3-rca <project_id> <log_text_or_file> [--run-id R] [--cache-id C] [--db-path DB]` | Phân tích & khử trùng lặp RCA (`brain.rca`) từ log/stacktrace và phản hồi tự chữa lành cho `cache_id`. |

### 4.2. Tích hợp Webhook CI/CD (`POST /v3/webhooks/{gitlab,github,testrail,jira,notion}`)

Endpoint v3.0 `POST /v3/webhooks/{source}` (triển khai tại `src/projectbrain/server/routes/plugins_v3.py` qua plugin `webhook.event_ingest`) hỗ trợ chuẩn hóa sự kiện từ 5 nền tảng: **`gitlab`**, **`github`**, **`testrail`**, **`jira`**, **`notion`** (và `generic` cho hệ thống nội bộ).

> [!NOTE]
> Phân biệt với 2 webhook legacy đơn giản hơn ở `/sources/webhook/github` và `/sources/webhook/notion` (chỉ ingest text thô vào HSG Memory). Trong quy trình CI/CD & kiểm thử v3.0, luôn dùng **`/v3/webhooks/{source}`** để có xác thực chữ ký HMAC-SHA256 và trích xuất `project_id` + `version_tag`.

#### Quy tắc bảo mật & xác định `project_id` của `/v3/webhooks/{source}`:
1. **Xác định `project_id`:** Ưu tiên lần lượt query param `?project_id=...`, header `X-Project-Id`, header `X-PB-Project-Id`, rồi mới đến trường `project_id` trong JSON body.
2. **Xác thực chữ ký (Signature Verification):**
   - Server kiểm tra secret theo thứ tự: cấu hình plugin toàn cục (`webhook_secret`/`secret`), biến môi trường riêng `PB_<SOURCE>_WEBHOOK_SECRET` (ví dụ `PB_GITLAB_WEBHOOK_SECRET`, `PB_GITHUB_WEBHOOK_SECRET`) hoặc `PB_WEBHOOK_SECRET`, và cấu hình theo project (không cho phép payload chưa xác thực ghi đè hạ cấp secret toàn cục).
   - Mặc định `allow_unsigned=False` (hoặc ép buộc qua `PB_REQUIRE_WEBHOOK_SIGNATURE=true`): mọi request thiếu/sai chữ ký sẽ bị từ chối với HTTP `401 Invalid or missing webhook signature`.
   - Nếu `PB_REQUIRE_REST_AUTH=true`, endpoint `/v3/*` cũng yêu cầu thêm Bearer Token hợp lệ (`collaborator` hoặc `admin`).
3. **Tự động trích xuất `version_tag`:** `WebhookEventIngestPlugin` tự đọc `version_tag`, `tag` (GitLab Tag/Release), hoặc `milestone` (TestRail/Jira) để chuyển tiếp cho Planner/KB.

#### Bảng tóm tắt cấu hình Webhook theo từng nền tảng:

| Nền tảng (`{source}`) | Endpoint | Biến môi trường Secret | Header chữ ký / Token | Sự kiện tiêu biểu |
| :--- | :--- | :--- | :--- | :--- |
| **GitLab** | `POST /v3/webhooks/gitlab` | `PB_GITLAB_WEBHOOK_SECRET` | `X-Gitlab-Token` (hoặc HMAC) | Push, Merge Request, Tag Push (`version_tag`) |
| **GitHub** | `POST /v3/webhooks/github` | `PB_GITHUB_WEBHOOK_SECRET` | `X-Hub-Signature-256` (`sha256=...`) + `X-GitHub-Event` | `push`, `pull_request`, `release` |
| **TestRail** | `POST /v3/webhooks/testrail` | `PB_TESTRAIL_WEBHOOK_SECRET` | `X-PB-Signature` / HMAC-SHA256 | Test Run / Case Update, Milestone (`version_tag`) |
| **Jira** | `POST /v3/webhooks/jira` | `PB_JIRA_WEBHOOK_SECRET` | `X-Hub-Signature` / HMAC-SHA256 | Issue created/updated, FixVersion (`version_tag`) |
| **Notion** | `POST /v3/webhooks/notion` | `PB_NOTION_WEBHOOK_SECRET` | `X-Notion-Signature` / HMAC-SHA256 | PRD / SRS page updated |

#### Ví dụ `curl` mô phỏng bước CI/CD Gate (GitLab / GitHub MR gửi Webhook + Gọi Planner Test DAG):

```bash
# 1. Thiết lập secret và gửi sự kiện GitHub Merge/Pull Request đã ký HMAC-SHA256 tới /v3/webhooks/github
PAYLOAD='{"action":"synchronize","pull_request":{"number":142,"head":{"ref":"feature/ble-v2"}},"version_tag":"fw-v1.2.0","changed_files":["firmware/src/ble_gatt_handler.c"]}'
SIG="sha256=$(printf '%s' "$PAYLOAD" | openssl dgst -sha256 -hmac "$PB_GITHUB_WEBHOOK_SECRET" | sed 's/^.* //')"

curl -X POST "http://127.0.0.1:8080/v3/webhooks/github?project_id=smartlock" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PB_API_KEY" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$PAYLOAD"

# 2. Trong job CI/CD tiếp theo, yêu cầu Project-Brain lập Test Execution DAG theo đúng branch + version_tag
curl -X POST "http://127.0.0.1:8080/v3/planner/test-dag" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $PB_API_KEY" \
  -d '{
    "project_id": "smartlock",
    "branch": "feature/ble-v2",
    "base_branch": "main",
    "version_tag": "fw-v1.2.0",
    "code_diff_files": ["firmware/src/ble_gatt_handler.c"],
    "artifacts": ["fw-v1.2.0.bin"],
    "target_devices": ["stm32wb55-rig-03"]
  }'
```
