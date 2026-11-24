# Hướng Dẫn Sử Dụng Toàn Diện ProjectBrain v3.0 (End-to-End User Manual)

> **Tài liệu chuẩn v3.0 Studio Workbench:** Cẩm nang toàn diện từ **cài đặt Server & Client Kit 1 chạm**, **đăng nhập & điều hướng 16 route đa phân hệ**, **hướng dẫn thao tác từng màn hình kèm ảnh chụp thực tế**, cho tới **quy trình vận hành AI Agents (`SKILL.md` + `sub/*.md` + `/pb-*`)**.
>
> *Mọi đường dẫn route (`/login`, `/app/*`), tên nút bấm, lệnh CLI (`projectbrain` / `python3 -m projectbrain.main`), công cụ FastMCP và danh sách 24 skills / 11 lệnh `/pb-*` trong tài liệu này được đối chiếu trực tiếp từ mã nguồn thực tế của ProjectBrain v3.0.*

---

## Mục Lục

1. [Phần 1 — Giới thiệu & Cài đặt Server + Client 1 chạm](#phần-1--giới-thiệu--cài-đặt-server--client-1-chạm)
   - [1.1. Tổng quan kiến trúc ProjectBrain v3.0](#11-tổng-quan-kiến-trúc-projectbrain-v30)
   - [1.2. Cài đặt Server từ con số 0 & Khởi chạy `projectbrain serve`](#12-cài-đặt-server-từ-con-số-0--khởi-chạy-projectbrain-serve)
   - [1.3. Cài đặt Client Kit 1 chạm lên máy Developer](#13-cài-đặt-client-kit-1-chạm-lên-máy-developer)
2. [Phần 2 — Đăng nhập, Phân quyền & Điều hướng Đa Route (`/login`, `/app/*`)](#phần-2--đăng-nhập-phân-quyền--điều-hướng-đa-route-login-app)
   - [2.1. Cổng xác thực 3 chế độ (`/login`) & Vai trò RBAC](#21-cổng-xác-thực-3-chế-độ-login--vai-trò-rbac)
   - [2.2. Bản đồ 16 Routes trên Workbench v3.0](#22-bản-đồ-16-routes-trên-workbench-v30)
   - [2.3. Thanh ngữ cảnh `Project @ Source : Branch`, Breadcrumbs, `⌘K` & Theme](#23-thanh-ngữ-cảnh-project--source--branch-breadcrumbs-k--theme)
3. [Phần 3 — Hướng dẫn Từng Màn Hình Chi Tiết (Full Screenshots)](#phần-3--hướng-dẫn-từng-màn-hình-chi-tiết-full-screenshots)
   - [3.1. Overview (`/app/overview`)](#31-overview-appoverview)
   - [3.2. Projects & Databases (`/app/workspace/projects`)](#32-projects--databases-appworkspaceprojects)
   - [3.3. Sources & Relations Hub (`/app/workspace/sources`)](#33-sources--relations-hub-appworkspacesources)
   - [3.4. Memories & Docs (`/app/workspace/memories`)](#34-memories--docs-appworkspacememories)
   - [3.5. Semantic Search (`/app/workspace/search`)](#35-semantic-search-appworkspacesearch)
   - [3.6. Graph Explorer (`/app/graph/explorer`)](#36-graph-explorer-appgraphexplorer)
   - [3.7. Branch Diff (`/app/graph/diff`)](#37-branch-diff-appgraphdiff)
   - [3.8. Unified KB & Trace (`/app/kb`) & PM Copilot (`/app/copilot`)](#38-unified-kb--trace-appkb--pm-copilot-appcopilot)
   - [3.9. Plugins & Hardware (`/app/platform/plugins`), v3.0 QA Layer 1 (`/app/platform/qa`) & MCP Clients (`/app/platform/mcp`)](#39-plugins--hardware-appplatformplugins-v30-qa-layer-1-appplatformqa--mcp-clients-appplatformmcp)
   - [3.10. AI & LLM Settings (`/app/settings/ai`), My Profile & Keys (`/app/settings/profile`) & Identity & RBAC (`/app/settings/iam`)](#310-ai--llm-settings-appsettingsai-my-profile--keys-appsettingsprofile--identity--rbac-appsettingsiam)
4. [Phần 4 — Dành cho AI Agents: Bộ Skills (`addyosmani/agent-skills`), Custom `sub/*.md` & Commands (`/pb-*`)](#phần-4--dành-cho-ai-agents-bộ-skills-addyosmaniagent-skills-custom-submd--commands-pb-)
   - [4.1. Kiến trúc 2 lớp `SKILL.md` + `sub/*.md` & Quy tắc ưu tiên](#41-kiến-trúc-2-lớp-skillmd--submd--quy-tắc-ưu-tiên)
   - [4.2. Danh mục 24 Skills tích hợp sẵn](#42-danh-mục-24-skills-tích-hợp-sẵn)
   - [4.3. Cách tạo và bảo toàn quy tắc dự án trong `sub/*.md`](#43-cách-tạo-và-bảo-toàn-quy-tắc-dự-án-trong-submd)
   - [4.4. Bộ 11 Workflow Commands `/pb-*` cho Điều phối Đa Nguồn](#44-bộ-11-workflow-commands-pb--cho-điều-phối-đa-nguồn)

---

## Phần 1 — Giới thiệu & Cài đặt Server + Client 1 chạm

### 1.1. Tổng quan kiến trúc ProjectBrain v3.0

**ProjectBrain v3.0** là nền tảng **Hybrid Graphify Cognitive Engine**, **Multi-Schema Knowledge Base** và **v3.0 Testing Layer 1** dành cho cả kỹ sư phần mềm lẫn đội ngũ AI Agents (Antigravity, Claude Code, Cursor, Codex):

- **Cách ly vật lý 3 tầng (`Master DB -> Project DB -> Branch Schema`)**:
  - **Tier 1 (Master Control-Plane DB)**: Quản lý định danh IAM/RBAC, bảng danh mục `master_projects` và `master_branches`.
  - **Tier 2 (Project DB — `1 Project = 1 Database`)**: Mỗi dự án có một database vật lý riêng (`pb_proj_<slug>_<hash8>` trên PostgreSQL hoặc thư mục `pb_projects/pb_proj_<slug>_<hash8>/` trên SQLite).
  - **Tier 3 (Branch & Sub-Source Schema — `pb_root` / `pb_br_*` / `pb_src_*`)**: Cô lập từng nhánh và từng sub-source (`project_id@source_id:branch`) cho cả đồ thị AST Graphify lẫn HSG Memories/Documents.
- **Multi-Source Project Hub (`__shared__` + Sub-Sources)**: Quản lý nhiều repo/module con (`backend-api`, `web-ui`, `worker`, `mobile`, ...) trong cùng một Project DB, liên kết tài liệu dùng chung (`__shared__`) và phát hiện trôi dạt hợp đồng liên nguồn (`Cross-Source Drift`).
- **Tăng tốc phần cứng & OCR/Local AI ngoại tuyến**: Hỗ trợ Tree-sitter AST song song, GPU/MPS/CUDA (`cuGraph`, PyTorch) và bóc tách PDF/Office/ảnh qua PaddleOCR, RapidOCR ONNX, Tesseract, Ollama Vision hoặc vLLM/LM Studio.
- **Microkernel 26 Plugins / 9 Categories & FastMCP**: Cho phép IDE kết nối qua **Stdio**, **HTTP/SSE** (`/mcp/sse`) hoặc **Streamable HTTP** (`/mcp-http`).

---

### 1.2. Cài đặt Server từ con số 0 & Khởi chạy `projectbrain serve`

#### Yêu cầu tối thiểu
- **Python `>= 3.10`** (`3.10`, `3.11`, `3.12`) kèm `venv`.
- **Git CLI** (khuyến nghị `>= 2.30`).
- **Cơ sở dữ liệu**: SQLite tích hợp sẵn (`OM_METADATA_BACKEND=sqlite`) hoặc **PostgreSQL 15+** cho môi trường team nhiều nhánh.

#### Bước 1: Chạy script cài đặt Server tự động

Bộ script tự kiểm tra Python `>= 3.10`, tạo `.venv`, cài gói theo profile bạn chọn, tạo `.env` từ `.env.example` (nếu chưa có), và chạy kiểm tra `hardware-status` + `plugins list`.

**Trên macOS / Linux (`scripts/install_from_scratch.sh`):**

```bash
git clone https://github.com/chien334/Project-Brain.git
cd Project-Brain

# Chọn 1 trong 4 profile cài đặt:
./scripts/install_from_scratch.sh --profile standard    # Mặc định: Graphify AST, FastAPI, FastMCP, Office parsers
./scripts/install_from_scratch.sh --profile cpu-ocr     # Standard + RapidOCR ONNX Runtime (OCR offline nhẹ trên CPU)
./scripts/install_from_scratch.sh --profile gpu         # Standard + PaddleOCR GPU + PyTorch/Sentence-Transformers (CUDA/MPS)
./scripts/install_from_scratch.sh --profile cuda-full   # Standard + PaddleOCR GPU + PyTorch + NVIDIA RAPIDS cuGraph/cuDF (Linux x86_64)
```

> **Mẹo:** Chạy `./scripts/install_from_scratch.sh` không kèm `--profile` trong terminal tương tác để chọn trực tiếp từ menu `[1-4]`. Các cờ hỗ trợ thêm: `--venv-dir <path>`, `--python <bin>`, `--non-interactive`, `--skip-checks`.

**Trên Windows PowerShell (`scripts/install_from_scratch.ps1`):**

```powershell
git clone https://github.com/chien334/Project-Brain.git
cd Project-Brain

# Chọn 1 trong 4 profile cài đặt:
.\scripts\install_from_scratch.ps1 -Profile standard
.\scripts\install_from_scratch.ps1 -Profile cpu-ocr
.\scripts\install_from_scratch.ps1 -Profile gpu
.\scripts\install_from_scratch.ps1 -Profile cuda-full
```

#### Bước 2: Khởi chạy Server (`projectbrain serve`)

Sau khi kích hoạt `.venv`:

```bash
# macOS / Linux
source .venv/bin/activate
projectbrain serve
# Hoặc chạy trực tiếp qua module Python:
python3 -m projectbrain.main serve
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
projectbrain serve
```

> **Chạy với PostgreSQL Multi-Schema (tùy chọn):** Thiết lập biến môi trường trước khi chạy `projectbrain serve`:
> ```bash
> PB_DB_URL="postgresql://postgres:your_password@127.0.0.1:5432/projectbrain" \
> PB_PORT=8080 \
> projectbrain serve
> ```
> Mặc định server mở cổng **`8080`**. Mở trình duyệt tại **`http://localhost:8080/dashboard/`** (tự chuyển hướng tới `/login` nếu chưa đăng nhập).

---

### 1.3. Cài đặt Client Kit 1 chạm lên máy Developer

Khi ProjectBrain Server đã được triển khai (local hoặc trên máy chủ team), developer không cần clone toàn bộ source Server mà chỉ cần cài **Client Kit** vào thư mục dự án để nhận trọn bộ:
- Thư mục `.agents/skills/*` (`SKILL.md`), `.agents/workflows/pb-*.md`, `.claude/commands/`, `.cursor/commands/`, và `AGENTS.md` / `CLAUDE.md` / `.cursorrules`.
- Tệp cấu hình `.mcp.json` (tự động merge giữ nguyên các MCP server hiện có, hỗ trợ cả **HTTP/SSE** và **Streamable HTTP** `/mcp-http`).
- **Bảo toàn tuyệt đối** mọi file quy tắc tùy biến của dự án trong `.agents/skills/*/sub/*` (không bao giờ bị ghi đè khi nâng cấp kit).

#### Cách A — Cài từ Remote Server (1 dòng lệnh)

Server đã triển khai cung cấp sẵn endpoint `/client-kit/install.sh` và `/client-kit/install.ps1`:

**macOS / Linux (`bash`):**

```bash
curl -fsSL https://<server>/client-kit/install.sh | bash -s -- \
  --server-url https://<server> \
  --token <pb_pat_...> \
  --project-id <org/app> \
  --ide all
```

**Windows PowerShell:**

```powershell
irm https://<server>/client-kit/install.ps1 | iex
```

> **Gợi ý truyền tham số trên Windows:** Nếu muốn truyền cờ trực tiếp khi chạy remote trên PowerShell, có thể tải script về tạm hoặc truyền qua biến môi trường (`$env:PB_SERVER_URL="https://<server>"`, `$env:PB_TOKEN="<pb_pat_...>"`, `$env:PB_PROJECT_ID="<org/app>"`) trước khi chạy `irm https://<server>/client-kit/install.ps1 | iex`, hoặc tải về file rồi gọi `.\install_client.ps1 -ServerUrl https://<server> -Token <pb_pat_...> -ProjectId <org/app> -Ide all`.

#### Cách B — Cài từ Local Clone hoặc qua CLI `projectbrain client-install`

Khi bạn đã có bản clone ProjectBrain tại máy (ví dụ đang đứng trong workspace đích hoặc truyền đường dẫn kit):

```bash
# 1. Dùng script install_client.sh với --local-kit
./scripts/install_client.sh \
  --local-kit . \
  --target-dir /path/to/your-project \
  --server-url http://localhost:8080 \
  --token <pb_pat_...> \
  --project-id <org/app> \
  --ide all

# 2. Hoặc dùng lệnh CLI tích hợp sẵn trong package ProjectBrain
projectbrain client-install \
  --target-dir /path/to/your-project \
  --server-url http://localhost:8080 \
  --token <pb_pat_...> \
  --project-id <org/app> \
  --ide all \
  --bootstrap
```

Các tùy chọn quan trọng của bộ cài Client Kit:
- `--ide <name>`: `all` (mặc định: cài cho `claude`, `cursor`, `antigravity`, `codex`), hoặc chọn riêng / phân tách dấu phẩy (vd. `--ide cursor,claude`).
- `--source-id <id>`: Gắn sẵn `source_id` mặc định vào `.mcp.json` khi workspace là một sub-source con (vd. `backend-api`).
- `--bootstrap`: Tự động gọi `POST <server-url>/master/hub/bootstrap` ngay sau khi cài để Server nhận diện hub và các sub-source.

---

## Phần 2 — Đăng nhập, Phân quyền & Điều hướng Đa Route (`/login`, `/app/*`)

### 2.1. Cổng xác thực 3 chế độ (`/login`) & Vai trò RBAC

Truy cập `http://<server>:8080/login` (hoặc `/dashboard/`), hệ thống hiển thị cổng xác thực **Authenticate Session** với tối đa 3 tab đăng nhập:

![Trang đăng nhập ProjectBrain /login](assets/manual_v3/manual_01_login.png)

1. **Password** *(mặc định)*: Đăng nhập bằng `Username or Email` và `Password` (hỗ trợ nút hiện/ẩn mật khẩu và tùy chọn **Keep me signed in for 30 days** để cấp cookie session `pb_session` an toàn).
2. **Token / API Key**: Dán trực tiếp token `pb_tok_...` hoặc Personal Access Token `pb_pat_...`.
   - *Thiết lập cục bộ lần đầu:* Seed token mặc định của tài khoản `admin` là `pb_tok_admin`. Sau khi đăng nhập lần đầu, thanh cảnh báo màu vàng ở `Overview` sẽ nhắc bạn bấm **Set Password & Rotate Seed Token** để đổi mật khẩu và xoay token ngay.
3. **Create Account**: Tab tự đăng ký tài khoản mới (tự động hiện khi Admin bật chính sách `Allow self-registration` trong `Identity & RBAC`).

**Ma trận 3 vai trò RBAC:**

| Vai trò | Quyền thao tác Web UI & REST | Quyền gọi công cụ MCP | Phạm vi dữ liệu |
| :--- | :--- | :--- | :--- |
| **`admin`** | Toàn quyền đọc/ghi, quản lý User/Policy/Audit Log, xoay token, xóa dữ liệu (`Danger Zone`). | Bỏ qua giới hạn `Allowed Tools`; gọi toàn bộ tool. | Toàn bộ dự án (hoặc theo `Allowed Projects`). |
| **`collaborator`** | Tạo/sửa memory, upload tài liệu, build KB, chạy QA Layer 1, quản lý `pb_pat_...` cá nhân. | Đọc và ghi trong phạm vi `Allowed Tools` được cấp. | Giới hạn theo `Allowed Projects` (`*` hoặc danh sách cụ thể). |
| **`reader`** | Chỉ xem dashboard, tìm kiếm ngữ nghĩa, xem đồ thị, diff nhánh, đổi mật khẩu/API key cá nhân (nút ghi/xóa tự ẩn). | Chỉ gọi được các tool đọc (`read-only`); các tool ghi bị `SSEFilter` chặn. | Giới hạn theo `Allowed Projects`. |

---

### 2.2. Bản đồ 16 Routes trên Workbench v3.0

Giao diện v3.0 sử dụng định tuyến SPA sạch theo 4 nhóm Domain trên thanh Sidebar (cộng với trang `/login` tạo thành **16 route chính thức**):

| # | Route | Tên màn hình | Nhóm Domain | Quyền & Chức năng chính |
| :---: | :--- | :--- | :--- | :--- |
| 1 | `/login` | **Authenticate Session** | Auth Gate | Đăng nhập Password / Token (`pb_tok_...`, `pb_pat_...`) / Create Account. |
| 2 | `/app/overview` | **Overview** | Workspace | KPI bộ nhớ, Hardware Acceleration, Memory Sectors, Active Tags, Quick Actions, Danger Zone. |
| 3 | `/app/workspace/projects` | **Projects & Databases** | Workspace | Topology 3 tầng (`Master DB` → `Project DB` → `Branch Schema`), cấp phát Project DB mới. |
| 4 | `/app/workspace/sources` | **Sources & Relations** | Workspace | Multi-Source Hub: đăng ký Sub-Source, nâng cấp version độc lập, Shared Docs bindings, Auto-Linker & Drift. |
| 5 | `/app/workspace/memories` | **Memories & Docs** | Workspace | Lưu HSG Cognitive Memory, Upload tài liệu (`project_shared` / `source`), chọn OCR Engine & OCR Device, xem lịch sử phiên bản. |
| 6 | `/app/workspace/search` | **Semantic Search** | Workspace | Tìm kiếm AI RAG Synthesis vs Raw Context Retrieval, bật/tắt `__shared__` và chọn `Cross-Source Hops` (`0/1/2`). |
| 7 | `/app/graph/explorer` | **Graph Explorer** | Code & Knowledge | Đồ thị Vis.js tương tác (Class/Function/File/Doc/Cross-Source), Split-pane Symbol Details & Fullscreen (`F`). |
| 8 | `/app/graph/diff` | **Branch Diff** | Code & Knowledge | So sánh song song thay đổi Codebase Symbols và Memories & Rules giữa hai nhánh/phiên bản. |
| 9 | `/app/kb` | **Unified KB & Trace** | Code & Knowledge | Chạy `Build Complete KB`, kiểm tra `KB Coverage & Drift`, truy vết hai chiều `Code <-> Doc`. |
| 10 | `/app/copilot` | **PM Copilot** | Code & Knowledge | Tổng hợp memories thành tài liệu PM (`Status Update`, `PRD`, `User Stories`, `Roadmap`). |
| 11 | `/app/platform/plugins` | **Plugins & Hardware** | Platform & QA | Chẩn đoán phần cứng GPU/MPS/CPU/Local AI và bật/tắt/reload 26 plugins thuộc 9 categories. |
| 12 | `/app/platform/qa` | **v3.0 QA Layer 1** | Platform & QA | 4 sub-tabs: `Impact Test DAG Planner`, `UDCP Script Cache`, `Telemetry Evidence Window`, `AI Oracle & RCA Triage`. |
| 13 | `/app/platform/mcp` | **MCP Clients** | Platform & QA | Cấu hình Stdio / HTTP SSE / Streamable HTTP (`/mcp-http`), snippet `.mcp.json` & External MCP Servers Manager. |
| 14 | `/app/settings/ai` | **AI & LLM Settings** | Settings & IAM | Cấu hình chuỗi fallback `Auto -> Cloud / Local -> Built-in Offline Local RAG`, Gemini, OpenAI/vLLM, Ollama. |
| 15 | `/app/settings/profile` | **My Profile & Keys** | Settings & IAM | Cập nhật hồ sơ, đổi mật khẩu, tạo scoped `pb_pat_...`, quản lý & thu hồi Active Sessions. |
| 16 | `/app/settings/iam` | **Identity & RBAC** | Settings & IAM | *(Chỉ Admin)* Policy bảo mật, tạo/sửa tài khoản, whitelist MCP tools, xem Security & Auth Audit Log. |

> **Route rút gọn (Aliases):** Các đường dẫn ngắn như `/`, `/app`, `/app/projects`, `/app/sources`, `/app/memories`, `/app/search`, `/app/graph`, `/app/diff`, `/app/plugins`, `/app/qa`, `/app/mcp` sẽ tự động chuyển hướng sang route đầy đủ tương ứng.

---

### 2.3. Thanh ngữ cảnh `Project @ Source : Branch`, Breadcrumbs, `⌘K` & Theme

Trên thanh Header cố định ở mọi màn hình `/app/*`, bạn luôn thấy 4 công cụ điều hướng cốt lõi:

1. **Breadcrumbs & Route URL Chip**: Hiển thị vị trí hiện tại (`Workbench / <Domain> / <Page>`) cùng badge route (ví dụ `/app/overview`) và nút **Copy shareable route URL** để chia sẻ link chính xác màn hình + ngữ cảnh đang xem cho đồng đội.
2. **Bộ chọn ngữ cảnh `Project @ Source : Branch` (`Workspace context`)**:
   - **Project**: Chọn Project ID đang làm việc (ví dụ `chien334/Project-Brain`). Bấm **+ New Project** để tạo nhanh dự án mới.
   - **Source**: Chọn `All Sources (*)` để xem tổng hợp, `Shared Docs Hub (__shared__)` cho tài liệu dùng chung toàn dự án, hoặc một sub-source cụ thể (ví dụ `backend-api`, `web-ui`).
   - **Branch**: Chọn nhánh cụ thể (`main`, `feature/...`) hoặc `All Branches` (`%`). *Lưu ý: Khi ghi Memory hoặc Upload tài liệu mới, hãy chọn một nhánh cụ thể (không để `All Branches`).*
3. **Command Palette (`⌘K` / `Ctrl+K`)**: Bấm nút **Search `⌘K`** trên Header hoặc nhấn phím tắt `⌘K` để mở hộp lệnh nhanh — cho phép nhảy tới bất kỳ route nào, chuyển project, chạy Build KB hoặc xoay API key chỉ bằng bàn phím:

![Command Palette Cmd+K](assets/ui_v3/04_command_palette.png)

4. **Light / Dark Theme Toggle**: Nút chuyển đổi giao diện Sáng / Tối / Theo hệ thống nằm cạnh badge trạng thái server (`Connected`) và thông tin phần cứng (`CPU / MPS Ready`):

![Giao diện Light Theme trên Overview](assets/ui_v3/14_overview_light_theme.png)

---

## Phần 3 — Hướng dẫn Từng Màn Hình Chi Tiết (Full Screenshots)

### 3.1. Overview (`/app/overview`)

Màn hình trang chủ cung cấp ảnh chụp telemetry tức thời của không gian làm việc đang chọn trên `Project @ Source : Branch`:

![Màn hình Overview /app/overview](assets/manual_v3/manual_02_overview.png)

- **Dải KPI trên cùng**: Hiển thị **Cognitive Memories** (số vector tri thức), **Temporal Facts** (số cạnh/quan hệ), **Active Plugins** (26 microkernel capabilities) và **Compute Backend** (runtime đang dùng cho OCR & Graphify).
- **Live Hardware & Local AI Acceleration**: Báo cáo trực tiếp trạng thái `OCR Device`, `Graphify Backend`, `CPU / Accelerators` và `Local AI Endpoints`.
- **Memory Sectors & Active Tags**: Thống kê phân bổ bộ nhớ theo sector (`semantic`, `episodic`, `procedural`, `reflective`, `emotional`). *Mẹo: Click trực tiếp vào một tag trong Active Tags để chuyển ngay sang `Semantic Search` với bộ lọc `tag:<tên_tag>`.*
- **Quick Workbench Actions**: 6 nút truy cập nhanh tới `Projects & Databases`, `Ingest Document`, `Build Unified KB`, `Inspect Plugins`, `v3.0 QA Layer 1` và `AI & LLM Settings`.
- **Danger Zone**: Nút `Clear All Memories` dùng để xóa sạch memory trong phạm vi active context *(tự động ẩn với tài khoản `reader`)*.

---

### 3.2. Projects & Databases (`/app/workspace/projects`)

Màn hình quản trị **Kiến trúc Cách ly Vật lý 3 Tầng** (`Master DB -> Project DB -> Branch Schema`):

![Màn hình Projects & Databases](assets/ui_v3/15_projects_databases_schemas.png)

- **Topology Summary Strip**: Hiển thị tên **MASTER CONTROL DATABASE**, tổng số **PROJECT DATABASES (`1 DB / PROJECT`)** và **BRANCH SCHEMAS (`1 SCHEMA / BRANCH`)**.
- **Provision New Project Database**: Nhập `Project ID` theo chuẩn `org/project-name` (ví dụ `acme/core-api`), hệ thống tự động preview tên DB vật lý (`pb_proj_<slug>_<hash8>`) và schema gốc (`pb_root`), nhập `Display Name`, `Description` rồi nhấn **Provision Project DB**.
- **Project Databases & Branch Schemas**: Liệt kê toàn bộ dự án đã cấp phát kèm danh sách branch schema (`pb_root`, `pb_br_*`), trạng thái đồng bộ và lệnh sync gợi ý.

---

### 3.3. Sources & Relations Hub (`/app/workspace/sources`)

Trung tâm điều phối kiến trúc **Multi-Source Hub** cho dự án nhiều repository/dịch vụ:

![Màn hình Sources & Relations Hub](assets/manual_v3/manual_03_sources_hub.png)

Màn hình gồm 3 thẻ công việc chính:

1. **Child Sub-Sources & Independent Lifecycle**:
   - Đăng ký từng sub-source độc lập với `Source ID` (vd. `backend-api`, `web-ui`), `Display Name`, `Source Type` (`service`, `backend`, `frontend`, `library`, `mobile`, `infra`, `docs`, `worker`) và `Initial Version` (vd. `1.0.0`).
   - Mỗi sub-source có version, thế hệ sync và schema `pb_src_*` riêng. Bạn có thể bấm nút **Upgrade** trên từng thẻ sub-source để nâng cấp phiên bản độc lập (`delta` hoặc `full`) mà không ảnh hưởng các sub-source khác.
2. **Shared Project Documents (`__shared__`) & Bindings**:
   - Gắn kết tài liệu cấp dự án từ kho `__shared__` (như `PRD`, `ADR`, `API Contract`, `Architecture`, `Runbook`, `Policy`) tới toàn bộ sub-source (`*`) hoặc một sub-source đích cụ thể qua form **Bind Document**.
   - Bấm **Upload Shared Doc** để chuyển nhanh sang màn hình `Memories & Docs` ở chế độ `project_shared`.
3. **Cross-Source Relationships & Drift Monitor**:
   - Khai báo cạnh liên kết hợp đồng giữa hai sub-source (`Origin Source` → `Relation Type` → `Target Source` với `Contract Key` như `POST /api/v1/orders`). Các loại quan hệ hỗ trợ: `calls_api`, `shares_schema`, `consumes_events`, `publishes_events`, `implements_spec`, `shares_auth`, `deploys`, `documented_by`.
   - Bấm **Run Auto-Linker** để hệ thống tự quét endpoint/schema và sinh cạnh liên kết tự động.
   - Bộ lọc `Status` (`active`, `drifted`, `stale`, `pinned`, `rejected`), `Type` và ô tìm kiếm `Filter Contract or Source` giúp rà soát nhanh các cạnh bị **drifted** (lệch hợp đồng khi một bên đổi API/DTO mà bên kia chưa cập nhật).

---

### 3.4. Memories & Docs (`/app/workspace/memories`)

Màn hình nạp tri thức nhận thức và bóc tách tài liệu đa phương thức có kiểm soát phiên bản:

![Màn hình Memories & Docs upload project_shared và source](assets/manual_v3/manual_04_memories_shared_upload.png)

- **Store Cognitive Memory (Cột trái)**: Nhập nội dung quy tắc/quyết định kiến trúc, gắn `Tags (comma separated)` và nhấn **Store Memory**.
- **Upload & Parse Document (Cột phải)**:
  - Banner xanh hiển thị rõ **Target Storage** (`DB pb_proj_* -> Schema pb_root / pb_br_*`) theo ngữ cảnh đang chọn.
  - Kéo-thả hoặc chọn file định dạng `.pdf`, `.docx`, `.xlsx`, `.xls`, `.pptx`, `.csv`, `.txt`, `.md`, `.png`, `.jpg`, `.jpeg`.
  - **Document Scope**: Chọn **`Sub-Source Scoped (source)`** để tài liệu chỉ thuộc sub-source đang chọn, hoặc **`Shared Project Hub (project_shared)`** để đẩy vào `__shared__` dùng chung toàn dự án.
  - **Document Role**: Gắn vai trò ngữ nghĩa (`general`, `architecture`, `api_contract`, `prd`, `adr`, `runbook`).
  - **OCR Engine & OCR Device**: Chọn engine (`Auto`, `PaddleOCR`, `RapidOCR`, `Tesseract`, `Ollama Vision`, `vLLM Vision`, `Hybrid Local Pipeline`) và phần cứng (`Auto`, `CUDA GPU`, `Apple MPS`, `CPU`).
- **Memory Feed & Ingested Document Versions**: Phía dưới cho phép lọc nhanh các node bộ nhớ hiện có và theo dõi danh sách phiên bản tài liệu đã bóc tách (`doc_key`, version, trạng thái delta).

---

### 3.5. Semantic Search (`/app/workspace/search`)

Công cụ truy vấn kết hợp Vector Memory, Code Graph và Tài liệu liên nguồn:

![Màn hình Semantic Search](assets/ui_v3/06_semantic_search.png)

- **Hai chế độ tìm kiếm**:
  - **AI RAG Synthesis**: Tổng hợp câu trả lời có dẫn chứng dựa trên AI Engine đang kích hoạt (hiển thị qua badge `Engine: ...`, có nút tắt sang **AI Settings**).
  - **Raw Context Retrieval**: Trả về trực tiếp các đoạn context, memory và symbol khớp điểm cao nhất mà không qua bước LLM synthesis.
- **Mở rộng ngữ cảnh liên nguồn (`Cross-Source Scope`)**:
  - Checkbox **Include Shared Project Docs (`__shared__`)**: Bật để đưa các tài liệu PRD/ADR/OpenAPI dùng chung vào kết quả truy vấn.
  - Dropdown **Cross-Source Hops**: Chọn độ sâu mở rộng qua các cạnh liên kết giữa các sub-source:
    - `0 (Target Only)`: Chỉ tìm trong sub-source đích.
    - `1 (Direct Relations)` *(mặc định)*: Mở rộng sang các sub-source liên kết trực tiếp 1 bước.
    - `2 (2-Hop Relations)`: Truy vết sâu qua 2 bước liên kết (ví dụ Frontend → BFF → Core Service).

---

### 3.6. Graph Explorer (`/app/graph/explorer`)

Trình khám phá đồ thị cấu trúc code & tài liệu tương tác bằng Vis.js:

![Graph Explorer Split-Pane](assets/ui_v3/16_graph_explorer_splitpane.png)

- **Thanh công cụ lọc**:
  - Chọn `Active Project`, tìm theo tên symbol (`Search Symbols`), lọc theo loại node (`Standard View`, `PLC/LASAL View`, `All Kinds`, `Classes Only`, `Functions & Methods`, `Files Only`) và chọn **Cross-Source Expansion (`0` / `+1-Hop` / `+2-Hop`)**.
- **Canvas HUD & Điều khiển**:
  - Góc trên trái hiển thị thống kê `X nodes · Y edges` và bảng chú giải màu sắc (`Class`, `Function`, `File`, `Doc`, cùng **đường nét đứt `Cross-Source`**).
  - Góc trên phải có cụm nút: **Zoom in (`+`)**, **Zoom out (`-`)**, **Fit graph to view**, **Toggle physics layout**, và **Fullscreen (`F`)**.
  - Khi bấm vào một node bất kỳ, panel **Symbol Details** bên phải hiển thị `Name`, `Kind`, `Source`, `File Path`, `Lines`, `Signature` và `Docstring`.
- **Chế độ Toàn màn hình (`Fullscreen`, phím tắt `F`)**: Mở rộng canvas chiếm toàn bộ vùng nhìn để kiểm tra các đồ thị lớn và các cạnh liên kết nét đứt `CROSS_SOURCE` / `drifted`:

![Graph Explorer Fullscreen](assets/ui_v3/17_graph_explorer_fullscreen.png)

---

### 3.7. Branch Diff (`/app/graph/diff`)

So sánh thay đổi cấu trúc giữa hai nhánh hoặc hai phiên bản dự án (`O(Delta)`):

![Màn hình Branch Diff](assets/ui_v3/08_branch_diff.png)

- Chọn **Base Version** (vd. `main`) và **Target Version** (vd. nhánh feature), rồi nhấn **Run Comparison**.
- Hiển thị dải tổng hợp số lượng **Added / Deleted / Modified** cho cả **Codebase Symbols** (cột trái) lẫn **Memories & Rules** (cột phải), giúp review tác động kiến trúc trước khi merge.

---

### 3.8. Unified KB & Trace (`/app/kb`) & PM Copilot (`/app/copilot`)

#### Unified KB & Trace (`/app/kb`)

![Màn hình Unified KB & Trace](assets/manual_v3/manual_05_kb_trace.png)

- **Build Complete KB**: Nhập `Workspace Path`, `Branch`, chọn `Compute Device` (`Auto`, `CUDA GPU`, `Apple MPS`, `CPU`) và số luồng `Workers`, rồi nhấn **Build Complete KB** để biên dịch chỉ mục Graphify + CodeGraph, phân đoạn tài liệu `H1 > H2 > H3` vào HSG memory và liên kết chéo `Code <-> Doc <-> Test`.
- **KB Coverage & Drift**: Bấm **Refresh Coverage** để kiểm tra tỷ lệ tài liệu phủ code/API/MCP và phát hiện các điểm lệch phiên bản (stale drift warnings).
- **Bidirectional Code <-> Doc Trace**: Nhập tên symbol, đường dẫn file hoặc tiêu đề tài liệu (vd. `AuthService.verify_token` hoặc `docs/architecture.md`) và nhấn **Trace Links** để truy vết hai chiều từ code sang tài liệu/test và ngược lại.

#### PM Copilot (`/app/copilot`)

Tại route `/app/copilot`, Product Manager và Tech Lead có thể chọn loại tài liệu (`Project Status Update`, `Product Requirement Document (PRD)`, `User Stories & Acceptance Criteria`, `Product Development Roadmap`), nhập hướng dẫn bổ sung và bấm **Generate Document** để sinh tài liệu Markdown kèm danh sách **Utilized Source Memories** trích xuất trực tiếp từ bộ nhớ dự án.

---

### 3.9. Plugins & Hardware (`/app/platform/plugins`), v3.0 QA Layer 1 (`/app/platform/qa`) & MCP Clients (`/app/platform/mcp`)

#### Plugins & Hardware (`/app/platform/plugins`)

![Màn hình Plugins & Hardware](assets/ui_v3/10_plugins_and_hardware.png)

- **GPU / CPU & Local AI Hardware Diagnostics**: Kiểm tra sức khỏe bộ tăng tốc đồ thị, OCR device, ONNX/CUDA/MPS và các endpoint Ollama/vLLM.
- **Microkernel Plugins Directory**: Duyệt toàn bộ **26 plugins thuộc 9 nhóm** (`graph_backend`, `vector_backend`, `etl_parser`, `knowledge_linker`, `doc_exporter`, `script_cache`, `artifact_store`, `brain_consumer`, `webhook`), tìm kiếm theo tên/năng lực, bật/tắt nhanh hoặc bấm **Reload Plugins** để hot-reload không cần khởi động lại server.

#### v3.0 QA Layer 1 (`/app/platform/qa`)

![Màn hình v3.0 Testing Layer 1](assets/ui_v3/11_v3_testing_layer1.png)

Gồm 4 sub-tabs phục vụ kiểm thử đa thiết bị & hệ thống nhúng:
1. **Impact Test DAG Planner**: Nhập danh sách file thay đổi + `Version Tag` để sinh đồ thị thứ tự test tối ưu theo bán kính ảnh hưởng (`blast-radius`).
2. **UDCP Script Cache**: Tra cứu script kiểm thử đã được kiểm chứng theo `Test Intent`, `Target Platform` (`Playwright`, `REST/MCP API`, `Appium`, `Desktop UIA`) và `App Version`.
3. **Telemetry Evidence Window**: Cắt lát toàn bộ log/tín hiệu đồng bộ NTP quanh một `Center Timestamp` (`±Window ms`) theo `Execution Run ID`.
4. **AI Oracle & RCA Triage**: Dán traceback lỗi / HTTP 500 / assertion diff để phân tích nguyên nhân gốc rễ và đánh dấu script flaky nếu cần.

#### MCP Clients (`/app/platform/mcp`)

![Màn hình MCP Clients](assets/manual_v3/manual_06_mcp_clients.png)

- **Integration Settings (Stdio vs HTTP/SSE)**: Đăng ký hoặc gỡ cài đặt ProjectBrain vào cấu hình Claude Desktop cục bộ theo mode **Stdio (Local Process)** hoặc **HTTP/SSE (`/mcp/sse`)**.
- **Active System Status**: Kiểm tra đường dẫn file cấu hình và JSON đang kích hoạt trên máy.
- **Local Project Configuration (`mcp.json`)**: Sinh sẵn 4 đoạn cấu hình sao chép nhanh theo đúng Active Project & Token đang đăng nhập:
  - VS Code Copilot (`.vscode/mcp.json` — HTTP/SSE)
  - Claude Code / Cursor (`.mcp.json` — HTTP/SSE qua `/mcp/sse?token=...&project_id=...`)
  - Claude Code / Cursor (`.mcp.json` — Stdio Local)
  - Claude Code / Cursor (`.mcp.json` — Stdio Remote DB)
  - *(Lưu ý: Khi dùng `client-install`, file `.mcp.json` được tạo sẵn đồng thời cả entry `"projectbrain"` dùng **HTTP/SSE** lẫn `"projectbrain-http"` dùng **Streamable HTTP** tại `/mcp-http` với header `Authorization`, `X-PB-Project-Id`, `X-PB-Source-Id`.)*
- **External MCP Servers Manager**: Cho phép đăng ký thêm các MCP server bên ngoài (như `postgres-mcp`, `playwright-mcp`, ...) bằng `Server Name`, `Command`, `Arguments (JSON Array)` và `Environment Variables (JSON Object)`.

---

### 3.10. AI & LLM Settings (`/app/settings/ai`), My Profile & Keys (`/app/settings/profile`) & Identity & RBAC (`/app/settings/iam`)

#### AI & LLM Settings (`/app/settings/ai`)

![Màn hình AI & LLM Settings](assets/manual_v3/manual_07_settings_ai.png)

- **Resolved Engine & Runtimes + Deterministic Fallback Chain**: Luôn hiển thị engine đang được chọn thực tế qua live probe và chuỗi fallback 3 tầng:
  1. **Cloud API Provider** (`Google Gemini / AI Studio / OpenRouter`, `OpenAI-Compatible`, `MiniMax`).
  2. **Local Inference Server** (`Ollama` tại `http://localhost:11434`, `vLLM` / `LM Studio` tại `http://localhost:8000/v1` hoặc `1234/v1`).
  3. **Built-in Offline Local RAG**: Bộ tổng hợp từ khóa + vector + đồ thị chạy nội bộ, **không cần API key hay GPU server**.
- **Provider & Credential Configuration**: Chọn `Active AI Chat / RAG Provider` (`Auto`, `Built-in Offline Local RAG`, `Google Gemini`, `OpenAI/vLLM/LM Studio`, `Ollama`, `MiniMax`), `Vector Embedding Provider` (`Synthetic Hash`, `Gemini`, `OpenAI`, `Ollama nomic-embed-text`, `MiniMax`), nhập key/model/URL, bấm **Test Connection** rồi **Save AI Settings**.

#### My Profile & Keys (`/app/settings/profile`)

![Màn hình My Profile & API Keys](assets/ui_v3/12_my_profile_api_keys.png)

- Dành cho **mọi vai trò**: cập nhật `Display Name` / `Email`, đổi mật khẩu (PBKDF2) kèm tùy chọn **Revoke all other active sessions**, phát hành Personal API & MCP Keys (`pb_pat_...`) giới hạn theo `Expiry (Days)`, `Role Scope`, `Allowed Projects`, `Allowed Tools`, và quản lý/thu hồi **Active Login Sessions**.

#### Identity & RBAC (`/app/settings/iam`)

![Màn hình Identity & RBAC Admin](assets/ui_v3/13_identity_rbac_admin.png)

- Dành riêng cho **Admin**:
  - **Global Auth & Security Policy**: Bật/tắt `Allow self-registration`, chọn `Default Role` (`reader` / `collaborator`), thiết lập `Session TTL (hrs)` và `Require REST auth`.
  - **Create or Update Account & Registered Accounts**: Tạo mới hoặc chỉnh sửa tài khoản người dùng / service account, gán vai trò, phân quyền danh sách `Allowed Projects` và checkbox từng MCP tool trong `Toggle Authorized MCP Tools`, xoay token hoặc khóa tài khoản.
  - **Security & Auth Audit Log**: Tra cứu lịch sử sự kiện bảo mật (đăng nhập thất bại, khóa brute-force, đổi mật khẩu, phát hành/thu hồi key) lọc theo `Event` và `Username`.

---

## Phần 4 — Dành cho AI Agents: Bộ Skills (`addyosmani/agent-skills`), Custom `sub/*.md` & Commands (`/pb-*`)

### 4.1. Kiến trúc 2 lớp `SKILL.md` + `sub/*.md` & Quy tắc ưu tiên

Client Kit cài đặt sẵn bộ kỹ năng kỹ nghệ phần mềm chuẩn `addyosmani/agent-skills` tại `.agents/skills/<skill>/`, kết hợp chặt chẽ với các cổng kiểm chứng ProjectBrain MCP/CLI. Để tùy biến cho từng dự án mà không mất thay đổi khi nâng cấp Client Kit, hệ thống áp dụng **quy tắc 2 lớp**:

1. **Lớp 1 — Base Skill (`.agents/skills/<skill>/SKILL.md`)**: Quy trình chuẩn (mẫu spec, checklist test, review 5 trục, cổng gọi `projectbrain_*`). File này sẽ được cập nhật khi nâng cấp Client Kit — **tuyệt đối không sửa trực tiếp `SKILL.md` cho quy tắc riêng của repo**.
2. **Lớp 2 — Project Overrides (`.agents/skills/<skill>/sub/*.md`)**: Nơi lưu stack công nghệ, lệnh build/test thực tế, quy ước API, thiết kế UI và ranh giới kiến trúc của riêng dự án (ví dụ: `.agents/skills/spec-driven-development/sub/spec.md`, `.agents/skills/api-and-interface-design/sub/api-conventions.md`).
   - **Quy tắc ưu tiên (Precedence):** Khi thực thi một skill, Agent **bắt buộc** đọc cả `SKILL.md` lẫn toàn bộ các file `.md` trong `sub/` (theo thứ tự chữ cái, **bỏ qua `sub/README.md`**). Nội dung trong `sub/*.md` **ghi đè hoặc mở rộng** mặc định của `SKILL.md` (nhưng không được phép vô hiệu hóa các bước kiểm chứng bảo mật hoặc đồng bộ ProjectBrain).
   - **An toàn nâng cấp:** Script `install_client.sh` / `install_client.ps1` luôn giữ nguyên hoàn toàn thư mục `sub/`.

---

### 4.2. Danh mục 24 Skills tích hợp sẵn

| Nhóm giai đoạn | Tên Skill (`.agents/skills/` hoặc `.agent/skills/`) | Vai trò cốt lõi |
| :--- | :--- | :--- |
| **Đặc tả & Thiết kế** | `spec-driven-development` | Viết spec có kiểm chứng ngữ cảnh đa nguồn trước khi code. |
| | `planning-and-task-breakdown` | Chia nhỏ công việc thành Task DAG không xung đột file (`disjoint slices`). |
| | `api-and-interface-design` | Thiết kế REST/GraphQL/DTO và ranh giới hợp đồng giữa các module/sub-source. |
| | `source-driven-development` | Đối chiếu trực tiếp tài liệu chính thức của thư viện/framework trước khi cài đặt. |
| **Triển khai & Giao diện** | `incremental-implementation` | Triển khai từng bước nhỏ, kiểm chứng liên tục qua mỗi lần sửa file. |
| | `frontend-design` | Định hướng thẩm mỹ, typography, bảng màu và bố cục có chủ đích cho UI mới. |
| | `frontend-ui-engineering` | Xây dựng UI đạt chuẩn production, responsive, WCAG accessibility và state rõ ràng. |
| **Chất lượng & Kiểm thử** | `test-driven-development` | Viết và chạy test bám sát tiêu chí nghiệm thu của spec. |
| | `debugging-and-error-recovery` | Truy tìm nguyên nhân gốc rễ (root-cause) có hệ thống khi test/build lỗi. |
| | `code-review-and-quality` | Review đa trục (correctness, architecture, maintainability, performance, errors). |
| | `doubt-driven-development` | Phản biện đối kháng (adversarial review) với ngữ cảnh sạch cho các thay đổi rủi ro cao. |
| | `code-simplification` | Tinh gọn code tăng độ rõ ràng mà không làm thay đổi hành vi. |
| | `performance-optimization` | Tối ưu độ trễ, truy vấn DB/N+1, bộ nhớ và Core Web Vitals. |
| **Bảo mật & Vận hành** | `security-and-hardening` | Kiểm toán OWASP, RBAC, bí mật (secrets) và ranh giới tin cậy (trust boundaries). |
| | `observability-and-instrumentation` | Bổ sung logging có cấu trúc, metrics, tracing và alerting. |
| | `ci-cd-and-automation` | Tự động hóa quality gates, test runners và pipeline deploy. |
| | `shipping-and-launch` | Checklist trước khi phát hành production, rollout và kế hoạch rollback. |
| **Git, Tài liệu & Điều phối** | `git-workflow-and-versioning` | Quy chuẩn branch, commit, PR, semantic versioning và changelog. |
| | `documentation-and-adrs` | Ghi lại Architectural Decision Records (ADRs) và tài liệu API/hệ thống. |
| | `multi-agent-orchestrator` | Điều phối đội ngũ subagent chuyên biệt theo mô hình `1-Agent = 1-Skill` với SSE telemetry. |
| | `create-project-skill` | Hướng dẫn tạo và chuẩn hóa các file override trong `sub/*.md`. |
| | `gemini-desktop-worker` | Cầu nối offload tác vụ đọc/phân tích nặng sang Gemini Desktop App ở chi phí API token thấp. |
| **Mở rộng Workspace (`.agent/skills/`)** | `legacy-migration` | Bảo trì & chuyển đổi hệ thống kế thừa (dịch comment/tài liệu Nhật, ánh xạ call graph). |
| | `unified-workflow` | Điều phối liên hoàn Document Ingestion, OCR, CodeGraph, ProjectBrain và Migration MCP. |

---

### 4.3. Cách tạo và bảo toàn quy tắc dự án trong `sub/*.md`

Bạn có thể gõ lệnh **`/pb-create-sub-skill`** trong IDE hoặc nhờ Agent chạy theo skill `create-project-skill` để sinh nhanh file override chuẩn. Ví dụ cấu trúc file `.agents/skills/spec-driven-development/sub/spec.md`:

```markdown
---
parent_skill: spec-driven-development
topic: spec
applies_to_sources: ["*"]
---

# Spec Override: Quy ước riêng của dự án

## Override Rules (Takes Precedence Over SKILL.md)
- **Đầu ra Spec:** Lưu tất cả đặc tả tại `docs/specs/<feature_id>.spec.md`.
- **Ngôn ngữ mô tả:** Viết yêu cầu nghiệp vụ bằng tiếng Việt, giữ nguyên tên symbol/route tiếng Anh.

## Project Conventions & Commands
- **ProjectBrain Scope:** `project_id="my-org/my-app"`, ưu tiên `include_shared_docs=True`.
- **Test Gate bắt buộc:** `pytest -q` (backend) và `npm run typecheck` (frontend).

## Boundaries (Always / Ask First / Never)
- **Always:** Gọi `projectbrain_cross_source_impact` trước khi đổi schema API.
- **Ask first:** Thay đổi cấu trúc bảng DB hoặc hợp đồng auth.
- **Never:** Bỏ qua kiểm tra RBAC hoặc sửa trực tiếp `SKILL.md`.
```

> **Đồng bộ cho cả team:** Sau khi tạo `sub/<topic>.md`, có thể đẩy file này lên Shared Hub qua `projectbrain_upload_document(..., scope="project_shared", doc_role="reference")` để mọi thành viên và agent khác tự động tra cứu được.

---

### 4.4. Bộ 11 Workflow Commands `/pb-*` cho Điều phối Đa Nguồn

Các lệnh slash command được cài sẵn đồng bộ tại `.agents/workflows/pb-*.md` (Antigravity), `.claude/commands/` (Claude Code) và `.cursor/commands/` (Cursor):

| Lệnh | Tên & Mục đích | Skills sử dụng (`+ sub/*.md`) | Sản phẩm đầu ra / Hành động chính |
| :--- | :--- | :--- | :--- |
| **`/pb-bootstrap`** | **Zero-Touch Hub Setup** | Hub MCP Playbook | Chạy `projectbrain_discover_workspace_sources` & `projectbrain_bootstrap_project_hub`, kiểm tra topology. |
| **`/pb-orchestrate`** | **Master Multi-Source Orchestrator** | `multi-agent-orchestrator` | Điều phối trọn vẹn chuỗi 7 bước: `Spec -> Plan -> Parallel Build -> Test -> Review -> Security -> Sync`. |
| **`/pb-create-agent`** | **Dynamic Role Agent Creator** | Bất kỳ `<skill>` nào | Khởi tạo subagent chuyên trách nạp đúng `<skill>/SKILL.md` + `sub/*.md` + ngữ cảnh `proj@source:branch`. |
| **`/pb-create-sub-skill`** | **Project Sub-Skill Creator** | `create-project-skill` | Tạo hoặc cập nhật `.agents/skills/<skill>/sub/<topic>.md` mà không chạm vào `SKILL.md`. |
| **`/pb-spec`** | **Multi-Source Specification** | `spec-driven-development` (+ `api-and-interface-design`) | Truy vấn Hub + `__shared__`, xuất file `docs/specs/{feature_id}.spec.md`. |
| **`/pb-plan`** | **Architecture & Task DAG** | `planning-and-task-breakdown`, `api-and-interface-design` | Gọi `projectbrain_cross_source_impact`, xuất `docs/plans/{feature_id}.plan.md` với các lát cắt file rời nhau (`disjoint`). |
| **`/pb-build`** | **Scoped Sub-Source Build** | `incremental-implementation`, `frontend-ui-engineering`, `api-and-interface-design` | Sửa code đúng `source_id` được giao và chạy `projectbrain_sync_graphify(..., upgrade_mode="delta")`. |
| **`/pb-test`** | **Spec-Driven Verification** | `test-driven-development`, `debugging-and-error-recovery` | Viết & chạy test bám spec, xuất `docs/reports/test_report.json`. |
| **`/pb-review`** | **5-Axis & Cross-Source Review** | `code-review-and-quality`, `doubt-driven-development` | Kiểm tra chất lượng 5 trục và phát hiện lệch hợp đồng liên nguồn → `docs/reviews/review_signoff.md`. |
| **`/pb-security`** | **Security & Trust-Boundary Audit** | `security-and-hardening` | Kiểm toán OWASP, auth/RBAC, secrets, ranh giới tin cậy → `docs/security/security_audit.md`. |
| **`/pb-sync`** | **Incremental Multi-Source Sync** | `documentation-and-adrs`, `git-workflow-and-versioning` | Chạy `projectbrain_sync_graphify` (`delta`) cho mọi `source_id` đã sửa và upload các tài liệu `__shared__` cập nhật. |

---

### Tài liệu tham khảo liên quan

- [Hướng Dẫn Cài Đặt Từ Con Số 0 & Giao Diện Studio Workbench v3.0](HUONG_DAN_CAI_DAT_TU_DAU_VA_UI_V3.md)
- [Hướng Dẫn Multi-Source Project Hub & Auto-Setup Cho AI Agents](HUONG_DAN_MULTI_SOURCE_VA_AGENT_AUTO_SETUP.md)
- [Hướng Dẫn Web UI, Điều Phối Telemetry & Phân Quyền RBAC](HUONG_DAN_WEB_UI_VA_PHAN_QUYEN_RBAC.md)
- [Hướng Dẫn MCP, Branch Overlay, Document Versioning & Plugins](HUONG_DAN_MCP_BRANCH_VERSION_VA_PLUGINS.md)
- [Plugin Microkernel Architecture Guide](PLUGIN_ARCHITECTURE_GUIDE.md)
