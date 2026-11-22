# Hướng Dẫn Cài Đặt Từ Con Số 0 & Sử Dụng Giao Diện ProjectBrain v3.0 Studio Workbench

> **Tài liệu chuẩn v3.0:** Hướng dẫn trọn vẹn từ bước khởi tạo môi trường trên **macOS / Linux / Windows**, cấu hình **SQLite / PostgreSQL Multi-Schema / Ollama AI Local**, vận hành **Hệ thống Xác thực & Phân quyền Doanh nghiệp (Password Session, Scoped PAT `pb_pat_...`, RBAC, Lockout & Audit Logs)** cho tới thao tác chi tiết toàn bộ các màn hình trên **ProjectBrain v3.0 Engineering Workbench (`http://localhost:8080/dashboard/`)**.

[Xem Video Hướng Dẫn Toàn Bộ UI v3.0 (MP4)](assets/ui_v3/projectbrain_v3_walkthrough.mp4)

![Walkthrough Preview](assets/ui_v3/projectbrain_v3_walkthrough.webp)

---

## Mục Lục Nhanh

1. [Phần 1 — Bộ cài đặt từ con số 0 (macOS / Linux / Windows)](#phần-1--bộ-cài-đặt-từ-con-số-0-macos--linux--windows)
2. [Phần 2 — Hệ thống Xác thực, Định danh & Phân quyền Doanh nghiệp (Auth, Session, PAT, RBAC, Audit)](#phần-2--hệ-thống-xác-thực-định-danh--phân-quyền-doanh-nghiệp-auth-session-pat-rbac-audit)
3. [Phần 3 — Hướng dẫn chi tiết từng màn hình trên Giao diện Studio Workbench v3.0](#phần-3--hướng-dẫn-chi-tiết-từng-màn-hình-trên-giao-diện-studio-workbench-v30)

---

## Phần 1 — Bộ cài đặt từ con số 0 (macOS / Linux / Windows)

### 1.1. Yêu cầu hệ thống

| Thành phần | Yêu cầu tối thiểu | Khuyến nghị Production / AI Local |
| :--- | :--- | :--- |
| **Python** | **Python `>= 3.10`** (`3.10`, `3.11`, `3.12`) | Python `3.11` hoặc `3.12` kèm `venv` |
| **Git** | Git CLI | Git >= `2.30` |
| **RAM** | 4 GB (chế độ `standard` + SQLite) | 16 GB+ (khi chạy OCR/Embeddings hoặc Ollama Local) |
| **Phần cứng tăng tốc (tùy chọn)** | CPU đa nhân (hỗ trợ oneDNN/MKL-DNN trên x86_64) | Apple Silicon (**MPS**) hoặc NVIDIA GPU (**CUDA** + cuDNN) |
| **Cơ sở dữ liệu** | SQLite tích hợp sẵn (`OM_METADATA_BACKEND=sqlite`) | **PostgreSQL 15+** cho Multi-Schema Branch Overlay (`pb_root` + `pb_br_*`) |
| **Local AI / VLM (tùy chọn)** | Không bắt buộc | **Ollama** (`http://localhost:11434`) với model vision `qwen2.5vl:7b` |

### 1.2. Cài đặt nhanh bằng 1 lệnh (Khuyên dùng)

ProjectBrain cung cấp sẵn bộ script cài đặt tự động kiểm tra Python `>= 3.10`, tạo môi trường ảo `.venv`, nâng cấp `pip`, cài đặt profile phụ thuộc tương ứng, sao chép `.env.example` thành `.env` (nếu chưa có), và chạy kiểm tra nhanh `projectbrain hardware-status` + `projectbrain plugins list`.

#### A. Trên macOS / Linux (`scripts/install_from_scratch.sh`)

```bash
git clone https://github.com/chien334/Project-Brain.git
cd Project-Brain

# Chọn 1 trong 4 profile cài đặt:
./scripts/install_from_scratch.sh --profile standard    # Mặc định: Core Graphify AST, FastAPI, FastMCP, Office parsers
./scripts/install_from_scratch.sh --profile cpu-ocr     # Standard + RapidOCR ONNX Runtime (OCR offline nhẹ trên CPU)
./scripts/install_from_scratch.sh --profile gpu         # Standard + PaddleOCR GPU + PyTorch/Sentence-Transformers (CUDA/MPS)
./scripts/install_from_scratch.sh --profile cuda-full   # Standard + PaddleOCR GPU + PyTorch + NVIDIA RAPIDS cuGraph/cuDF (Linux x86_64)
```

> **Mẹo:** Nếu chạy `./scripts/install_from_scratch.sh` không truyền `--profile` trong terminal tương tác, bộ cài sẽ hiển thị menu `[1-4]` để bạn chọn trực tiếp. Các tùy chọn khác: `--venv-dir <path>`, `--python <bin>`, `--non-interactive`, `--skip-checks`.

#### B. Trên Windows PowerShell (`scripts/install_from_scratch.ps1`)

```powershell
git clone https://github.com/chien334/Project-Brain.git
cd Project-Brain

# Chọn 1 trong 4 profile cài đặt:
.\scripts\install_from_scratch.ps1 -Profile standard
.\scripts\install_from_scratch.ps1 -Profile cpu-ocr
.\scripts\install_from_scratch.ps1 -Profile gpu
.\scripts\install_from_scratch.ps1 -Profile cuda-full
```

*(Nếu PowerShell chặn script local, chạy `Set-ExecutionPolicy -Scope Process Bypass` trước khi thực thi).*

### 1.3. Bảng đối chiếu 4 Profile cài đặt (`pyproject.toml`)

| Profile Installer | Lệnh `pip` thực tế | Thành phần được cài đặt | Phù hợp cho |
| :--- | :--- | :--- | :--- |
| `standard` *(mặc định)* | `pip install -e .` | FastAPI, Uvicorn, FastMCP, NetworkX, Graphify (`graphifyy`), Office parsers (`pypdf`, `python-docx`, `openpyxl`, `python-pptx`, `pdfplumber`), `psycopg2-binary` | Máy cá nhân, CI/CD, phát triển phần mềm không cần OCR ảnh local |
| `cpu-ocr` | `pip install -e ".[ocr-onnx]"` | `standard` + `rapidocr-onnxruntime`, `onnxruntime`, `pymupdf` | Laptop / Server chỉ có CPU muốn đọc PDF scan & ảnh offline nhanh, nhẹ |
| `gpu` | `pip install -e ".[ocr-gpu,graphify-gpu]"` | `standard` + `paddleocr`, `paddlepaddle-gpu`, `pymupdf`, `torch`, `sentence-transformers`, `scipy` | Máy trạm có NVIDIA GPU hoặc Apple Silicon (MPS) cần tăng tốc embedding & OCR |
| `cuda-full` | `pip install -e ".[ocr-gpu,graphify-cuda]"` | `standard` + `paddleocr`, `paddlepaddle-gpu`, `torch`, `sentence-transformers`, `cugraph-cu12`, `cudf-cu12` | Server Linux x86_64 NVIDIA CUDA chuyên xử lý đồ thị lớn với RAPIDS cuGraph |

*(Lưu ý: Bạn cũng có thể cài thủ công các extra riêng lẻ trong `pyproject.toml` như `pip install -e ".[ocr-cpu]"` cho PaddleOCR CPU hoặc `pip install -e ".[ocr-onnx-gpu]"` cho RapidOCR GPU).*

### 1.4. Cài đặt thủ công từng bước (Step-by-Step)

Nếu bạn muốn kiểm soát từng bước thay vì dùng script tự động:

```bash
# 1. Tải mã nguồn & tạo môi trường ảo Python >= 3.10
git clone https://github.com/chien334/Project-Brain.git
cd Project-Brain
python3 -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .\.venv\Scripts\Activate.ps1

# 2. Nâng cấp pip và cài đặt ProjectBrain (ví dụ profile standard hoặc ocr-onnx)
python -m pip install --upgrade pip setuptools wheel
pip install -e .                 # Hoặc: pip install -e ".[ocr-onnx]"

# 3. Khởi tạo file cấu hình môi trường
cp .env.example .env             # Windows: copy .env.example .env

# 4. Kiểm tra phần cứng OCR/Graphify & 26 microkernel plugins
projectbrain hardware-status
projectbrain plugins list

# 5. Khởi chạy server REST API, MCP & Web Dashboard (cổng mặc định 8080)
projectbrain serve
# (Hoặc: python3 -m projectbrain.main serve)
```

Sau khi server khởi chạy thành công, mở trình duyệt truy cập **`http://localhost:8080/dashboard/`**.

### 1.5. Cấu hình vận hành: SQLite độc lập vs. PostgreSQL Multi-Schema + Ollama AI Local

File `.env.example` đã nhóm sẵn 5 phân mục cấu hình chuẩn. Sau khi sao chép sang `.env`, bạn chọn 1 trong 2 mô hình lưu trữ:

#### Chế độ 1: SQLite Độc Lập (Zero-Infrastructure — Mặc định)

Phù hợp chạy ngay trên laptop cá nhân hoặc workspace đơn lẻ, sử dụng SQLite CTE overlay fallback:

```dotenv
PB_PORT=8080
PB_ROOT_BRANCH=main
OM_METADATA_BACKEND=sqlite
PB_DB_PATH=projectbrain.db
```

#### Chế độ 2: PostgreSQL Multi-Schema + Enterprise Auth + Ollama AI Local

Phù hợp team kỹ thuật, server dùng chung và CI/CD. Ở cả hai backend (`sqlite` và `postgres`), ProjectBrain mặc định kích hoạt kiến trúc **3 tầng cô lập vật lý (`Master DB -> Project DB -> Branch Schema`)** (`PB_MULTI_DB_ENABLED=true`):
- **Master DB:** Lưu IAM/RBAC, migrations và danh mục `master_projects` / `master_branches`.
- **1 Project = 1 Project DB (`pb_proj_<slug>_<hash8>`):** PostgreSQL tự tạo database riêng (`CREATE DATABASE ...`); SQLite tạo thư mục riêng `<master_dir>/pb_projects/<db_name>/` (`graph.sqlite` + các file `.sqlite` theo nhánh).
- **1 Branch = 1 Schema (`pb_root` / `pb_br_<slug>_<hash8>`):** Nhánh gốc (`PB_ROOT_BRANCH`, mặc định `main`) nằm tại `pb_root`; mỗi nhánh tính năng được cấp phát schema riêng cho **cả Graphify và Documents/Memories**, hợp nhất qua virtual SQL views `v_resolved_*` và vẫn tái sử dụng unit không đổi (`reused_units`) khi upload tài liệu:

```dotenv
# 1. Server & PostgreSQL Multi-Schema
PB_PORT=8080
PB_ROOT_BRANCH=main
OM_METADATA_BACKEND=postgres
PB_DB_URL=postgresql://postgres:your_strong_password@127.0.0.1:5432/projectbrain
# (Hoặc dùng từng biến: OM_PG_HOST, OM_PG_PORT, OM_PG_DB, OM_PG_USER, OM_PG_PASSWORD)

# 2. Bảo mật & Xác thực doanh nghiệp
PB_REQUIRE_REST_AUTH=true
PB_ALLOW_REGISTRATION=false
PB_DEFAULT_SIGNUP_ROLE=reader
PB_SESSION_TTL_HOURS=24
PB_AUTH_LOCKOUT_MAX_ATTEMPTS=5
PB_AUTH_LOCKOUT_SECONDS=300
PB_WEBHOOK_SECRET=whsec_replace_with_random_secret

# 3. Graphify GPU / MPS / CUDA & Multi-Worker
PB_GRAPHIFY_DEVICE=auto
PB_GRAPHIFY_WORKERS=4
PB_GRAPHIFY_SEMANTIC_ENGINE=auto
PB_EMBED_DEVICE=auto

# 4. Document OCR & Ollama Vision Local AI
PB_OCR_ENGINE=hybrid_local       # Kết hợp OCR cổ điển (paddle/rapidocr) + Ollama VLM cho sơ đồ/bảng phức tạp
PB_OCR_DEVICE=auto
PB_OCR_CPU_THREADS=4
PB_OCR_ENABLE_MKLDNN=auto
OLLAMA_URL=http://localhost:11434
PB_OLLAMA_VISION_MODEL=qwen2.5vl:7b

# 5. Plugin Overrides (tùy chọn)
PB_PLUGINS_DIR=./plugins
# PB_ENABLED_PLUGINS=cache.redis,vector.qdrant
# PB_DISABLED_PLUGINS=graph.neo4j
```

> **Kích hoạt Ollama Local:** Cài đặt Ollama, kéo model vision (`ollama pull qwen2.5vl:7b`), đảm bảo dịch vụ đang lắng nghe tại `http://localhost:11434`, rồi kiểm tra lại trạng thái kết nối bằng lệnh `projectbrain hardware-status` hoặc xem trực tiếp trên tab **Plugins & Hardware** của Web UI.

---

## Phần 2 — Hệ thống Xác thực, Định danh & Phân quyền Doanh nghiệp (Auth, Session, PAT, RBAC, Audit)

ProjectBrain v3.0 nâng cấp toàn diện hệ thống định danh trong `src/projectbrain/server/routes/auth.py` với 4 bảng dữ liệu (`mcp_accounts`, `auth_sessions`, `auth_api_keys`, `auth_audit_logs`, cùng `auth_settings`), hỗ trợ đồng thời **Cookie Session (`pb_session`)**, **Scoped Personal Access Tokens (`pb_pat_...`)**, **Primary Account Tokens (`pb_tok_...`)**, băm mật khẩu chuẩn công nghiệp **PBKDF2-HMAC-SHA256 (310.000 vòng)**, chống brute-force và nhật ký kiểm toán bảo mật.

### 2.1. Cổng đăng nhập 3 chế độ (Enterprise Auth Gate)

Khi truy cập `http://localhost:8080/dashboard/`, người dùng được chào đón bởi màn hình **Authenticate Session** với 3 tab phương thức đăng nhập:

1. **Tab `Password` (Mặc định):**
   - Đăng nhập bằng **Username hoặc Email** (`loginIdentifier`) và **Mật khẩu** (`loginPassword`), hỗ trợ nút hiện/ẩn mật khẩu.
   - Tùy chọn **`Keep me signed in for 30 days`** (`remember_me`): nếu bật, phiên `pb_sess_...` có thời hạn **30 ngày**; nếu tắt, dùng thời hạn cấu hình từ `PB_SESSION_TTL_HOURS` (mặc định **24 giờ**).
   - Server gọi `POST /auth/login`, cấp token phiên `pb_sess_<32_hex>` và đặt cookie **`pb_session`** (`HttpOnly`, `SameSite=Lax`).
2. **Tab `Token / API Key`:**
   - Hỗ trợ nhập trực tiếp `pb_tok_...` (Account Token), `pb_pat_...` (Personal Access Token) hoặc `pb_sess_...` (Session Token).
   - Trên màn hình hiển thị gợi ý cho lần thiết lập cục bộ đầu tiên: `First-time local setup seed token: pb_tok_admin`.
3. **Tab `Create Account` (Tự đăng ký tài khoản):**
   - Chỉ hiển thị khi chính sách `allow_registration` đang bật (`GET /auth/config` trả `"allow_registration": true`).
   - Cho phép nhập `Username` (3–48 ký tự chữ/số/`_`/`.`/`-`), `Full Name`, `Email Address` (kiểm tra trùng lặp không phân biệt hoa/thường), và `Password` (tối thiểu **8 ký tự**).
   - Hệ thống tự động gán vai trò mặc định theo `PB_DEFAULT_SIGNUP_ROLE` (`collaborator` hoặc `reader`, không bao giờ tự gán `admin`), tạo ngay phiên làm việc và ghi nhận sự kiện `register` vào Audit Log.

![Cổng đăng nhập Password & Token trên ProjectBrain v3.0](assets/ui_v3/01_auth_gate_password.png)

![Tab Create Account cho phép tự đăng ký tài khoản mới](assets/ui_v3/02_auth_register_account.png)

### 2.2. Bảo mật tài khoản `admin` lần đầu (Bắt buộc trước khi đưa lên mạng nội bộ / Production)

Khi database khởi tạo lần đầu, hàm `init_default_admin()` tự động seed tài khoản quản trị mặc định:
- `username`: `admin`
- `token`: `pb_tok_admin`
- `role`: `admin`
- `allowed_tools`: `*`, `allowed_projects`: `*`

Ngay khi đăng nhập lần đầu bằng `pb_tok_admin`, màn hình **System Overview** sẽ hiển thị banner cảnh báo màu hổ phách **`Security Recommendation`** (`#securitySeedBanner`) cùng nút **`Set Password & Rotate Seed Token`**.

**Quy trình 3 bước làm cứng tài khoản `admin` ngay trên Web UI:**
1. Tại Login Gate, chọn tab **Token / API Key**, nhập `pb_tok_admin` và bấm **Sign In with Token**.
2. Tại thanh banner cảnh báo trên **Overview**, nhấn **Set Password & Rotate Seed Token** (hoặc vào mục **My Profile & API Keys**):
   - Trong thẻ **Password & Sign-In Security**, nhập mật khẩu mạnh (>= 8 ký tự) vào **New Password** và nhấn **Update Password**. Backend băm mật khẩu bằng **PBKDF2-HMAC-SHA256 với 310.000 vòng lặp** (`pbkdf2_sha256$310000$...`).
3. Chuyển sang tab **Identity & RBAC Admin**:
   - Trong bảng **Registered Accounts**, bấm **Rotate Token** tại dòng `admin` (hoặc gọi `POST /auth/accounts/admin/rotate-token`). Hệ thống sinh token mới `pb_tok_<24_hex>` và vô hiệu hóa vĩnh viễn `pb_tok_admin`.
   - Bật **Require REST auth** (`PB_REQUIRE_REST_AUTH=true`) trong thanh **Global Auth & Security Policy** và nhấn **Save Policy**.

### 2.3. Quản lý Hồ sơ cá nhân, Đổi mật khẩu, Scoped PAT (`pb_pat_...`) & Thu hồi phiên

Mọi vai trò (`admin`, `collaborator`, `reader`) đều có quyền truy cập màn hình **My Profile & API Keys** (hoặc bấm nút badge hồ sơ nhanh `#btnQuickProfile` trên thanh header):

- **Account Profile (`GET/PUT /auth/me`):** Xem `Username`, `Assigned Role`, phạm vi dự án được phép (`Allowed Projects Scope`), cập nhật `Display Name` và `Email Address`.
- **Password & Sign-In Security (`POST /auth/change-password`):**
  - Thiết lập mật khẩu lần đầu hoặc đổi mật khẩu (yêu cầu nhập `Current Password` nếu tài khoản đã có mật khẩu).
  - Tùy chọn **`Revoke all other active sessions after password change`** được chọn sẵn để đăng xuất toàn bộ thiết bị khác ngay sau khi đổi mật khẩu.
- **Personal API & MCP Keys (`pb_pat_...` — `GET/POST/DELETE /auth/api-keys`):**
  - Cho phép tạo token riêng cho từng IDE (Cursor, Claude Desktop, VS Code Copilot) hoặc pipeline CI/CD mà không phải chia sẻ mật khẩu hay token gốc.
  - **Cơ chế hạ quyền & giao thoa phạm vi (Scope Intersection):**
    - `Role Scope`: Chỉ có thể chọn vai trò **bằng hoặc thấp hơn** vai trò tài khoản của bạn (`reader` <= `collaborator` <= `admin`).
    - `Allowed Projects` & `Allowed Tools`: Hệ thống tự tính giao (`_intersect_scopes`) giữa phạm vi của Key và phạm vi của Account. Ví dụ: bạn có thể tạo `pb_pat_...` chỉ đọc (`reader`) giới hạn riêng cho dự án `org/payment-service` và 2 tool tra cứu.
    - `Expiry (Days)`: Đặt thời hạn hiệu lực theo ngày (ví dụ `90` ngày).
  - **Bảo mật hiển thị 1 lần (`Secret Reveal Dialog`):** Token thô `pb_pat_...` chỉ hiển thị duy nhất một lần trong hộp thoại **Credential Created**; database chỉ lưu mã băm SHA-256 (`token_hash`) và tiền tố `key_prefix` (ví dụ `pb_pat_a1b2...`). Có thể bấm **Revoke** bất kỳ lúc nào.
- **Active Login Sessions (`GET /auth/sessions`, `DELETE /auth/sessions/{id}`, `POST /auth/sessions/revoke-others`):**
  - Liệt kê các phiên đang hoạt động kèm `IP Address`, `User-Agent`, thời gian tạo, lần truy cập cuối (`last_seen_at`) và nhãn phiên hiện tại (`is_current`).
  - Hỗ trợ thu hồi từng phiên lẻ hoặc bấm **Revoke Other Sessions** để đá toàn bộ các phiên khác chỉ bằng 1 click.

![Màn hình My Profile, Password, Personal API Keys (pb_pat_...) và Active Login Sessions](assets/ui_v3/12_my_profile_api_keys.png)

### 2.4. Quản trị viên Identity & RBAC Admin (Chỉ `admin`)

Tab **Identity & RBAC Admin** (`authTab`) chỉ hiển thị với tài khoản có vai trò `admin`, cung cấp 4 khối quản trị tập trung:

1. **Global Auth & Security Policy (`GET/PUT /auth/settings`):**
   - **`Allow self-registration`** (`PB_ALLOW_REGISTRATION`): Bật/tắt tab `Create Account` trên Login Gate.
   - **`Default Role`** (`PB_DEFAULT_SIGNUP_ROLE`): Vai trò gán mặc định cho người tự đăng ký (`reader` hoặc `collaborator`).
   - **`Session TTL (hrs)`** (`PB_SESSION_TTL_HOURS`): Thời lượng sống mặc định của phiên đăng nhập thường (mặc định `24` giờ; trên UI có thể chỉnh tới `8760` giờ).
   - **`Require REST auth`** (`PB_REQUIRE_REST_AUTH`): Bật/tắt bắt buộc xác thực token/cookie cho toàn bộ REST API (các kết nối MCP `/mcp` và `/mcp-http` luôn bắt buộc xác thực).
   - Bấm **Save Policy** để lưu bền vững vào bảng `auth_settings` và đồng bộ biến môi trường runtime.
2. **Create or Update Account (`POST /auth/accounts`):**
   - Tạo mới hoặc cập nhật tài khoản người dùng / service account với `Username`, `Display Name`, `Email`, `Password`, `Custom Token` (để trống để tự sinh `pb_tok_...`), `Role` (`admin` / `collaborator` / `reader`), `Account Status` (`active` / `disabled` → lưu trạng thái `suspended`), `Allowed Projects`, và `Allowed Tools` (kèm bộ chọn checkbox **Toggle Authorized MCP Tools** tải từ `GET /auth/tools`).
3. **Registered Accounts — Chống Brute-Force, Khóa/Mở khóa, Tạm ngưng & Xoay Token:**
   - **Cơ chế chống Brute-force:** Khi đăng nhập sai mật khẩu liên tiếp **`PB_AUTH_LOCKOUT_MAX_ATTEMPTS`** lần (mặc định **5 lần**), tài khoản bị khóa tạm thời trong **`PB_AUTH_LOCKOUT_SECONDS`** giây (mặc định **300 giây** = 5 phút). Mọi nỗ lực đăng nhập trong thời gian khóa nhận **HTTP `429`** kèm header `Retry-After` và ghi sự kiện `account_locked` vào Audit Log.
   - Trên bảng tài khoản, Admin có thể thực hiện ngay các tác vụ:
     - **Suspend / Activate** (`PATCH /auth/accounts/{username}/status`): Tạm ngưng tài khoản (`suspended`, tự động thu hồi toàn bộ phiên đang chạy) hoặc kích hoạt lại (`active`, đồng thời xóa bộ đếm khóa `failed_login_count = 0`, `locked_until = 0` để mở khóa brute-force tức thì). Hệ thống bảo vệ không cho phép hạ quyền, tạm ngưng hoặc xóa tài khoản `admin` hoạt động cuối cùng.
     - **Rotate Token** (`POST /auth/accounts/{username}/rotate-token`): Cấp lại token chính `pb_tok_...` mới và hiện qua Secret Reveal Dialog.
     - **Reset Password** (`POST /auth/accounts/{username}/reset-password`): Đặt lại mật khẩu mới, mở khóa brute-force và thu hồi mọi phiên cũ.
     - **Delete** (`DELETE /auth/accounts/{username}`): Xóa tài khoản và thu hồi toàn bộ session + PAT liên quan.
4. **Security & Auth Audit Log (`GET /auth/audit-logs`):**
   - Ghi lại mọi sự kiện an ninh: `login_success`, `login_failed`, `account_locked`, `register`, `logout`, `profile_updated`, `password_changed`, `password_change_failed`, `session_revoked`, `api_key_created`, `api_key_revoked`, `account_upserted`, `account_status_changed`, `account_token_rotated`, `account_password_reset`, `account_deleted`, `auth_settings_updated`.
   - Hỗ trợ lọc nhanh theo **Event** (`auditLogEventFilter`, ví dụ `login_failed`) và **Username** (`auditLogUserFilter`), hiển thị `Timestamp`, `Event`, `Actor`, `Target`, `IP / Source` và `Details`.

![Màn hình Identity & RBAC Admin với Global Policy, Quản lý Tài khoản và Security Audit Logs](assets/ui_v3/13_identity_rbac_admin.png)

---

## Phần 3 — Hướng dẫn chi tiết từng màn hình trên Giao diện Studio Workbench v3.0

Giao diện v3.0 được thiết kế theo mô hình **Engineering Studio Workbench** với thanh điều hướng bên trái chia thành **4 nhóm Domain (13 màn hình)**, thanh ngữ cảnh cố định phía trên và bảng lệnh nhanh.

### 3.0. Thanh ngữ cảnh Workspace, Bảng lệnh nhanh `⌘K` & Giao diện Sáng/Tối

- **Thanh ngữ cảnh (`Workspace Context Pill`):**
  - **`Project` (`#currentProject`)** & **`Branch` (`#currentBranch`)**: Xác định không gian tri thức hiện hành (`<project>:<branch>` hoặc `<project>:%` cho `All Branches`). Lưu ý: khi chọn `All Branches`, hệ thống chỉ cho phép xem/tìm kiếm tổng hợp; để lưu Memory hoặc Upload Document, hãy chọn một nhánh cụ thể (ví dụ `main`).
  - **`+ New Project`**: Mở modal tạo nhanh Project ID (dạng `org/service-name`), Display Name và mô tả.
- **Bảng lệnh nhanh Command Palette (`⌘K` / `Ctrl+K` hoặc nút `Search ⌘K`):**
  - Cho phép gõ từ khóa để nhảy ngay tới bất kỳ view nào, chuyển dự án, kích hoạt Build Unified KB hoặc xoay khóa bảo mật mà không cần rời bàn phím.
- **Chuyển đổi Giao diện Sáng / Tối (`Theme Toggle`):**
  - Nút Mặt trời / Mặt trăng (`#btnThemeToggle`) trên góc phải header chuyển đổi tức thì giữa **Dark Slate** và **Light Precision** (lưu vào `localStorage` qua khóa `pb-color-scheme`), đồng thời hiển thị badge tăng tốc phần cứng (`#headerHardwareBadge`, ví dụ `CPU / MPS Ready`) và trạng thái kết nối server (`Connected`).

![Bảng lệnh nhanh Command Palette Cmd+K trên ProjectBrain v3.0](assets/ui_v3/04_command_palette.png)

![Giao diện Sáng (Light Theme) trên màn hình System Overview v3.0](assets/ui_v3/14_overview_light_theme.png)

---

### 3.1. Nhóm `Workspace & Memory`

#### 1. `Overview` (`System Overview`)
- **Chức năng:** Bảng điều khiển tổng quan theo thời gian thực cho Active Project.
- **Thành phần chính:**
  - **Security Recommendation Banner:** Tự động cảnh báo nếu đang dùng `pb_tok_admin` mặc định hoặc chưa đặt mật khẩu.
  - **KPI Strip (4 thẻ):** `Cognitive Memories`, `Temporal Facts`, `Active Plugins` (`26`), `Compute Backend`.
  - **Live Hardware & Local AI Acceleration:** Hiển thị `OCR Device`, `Graphify Backend`, `CPU / Accelerators`, và `Local AI Endpoints` (Ollama/vLLM).
  - **Memory Sectors & Active Tags:** Phân bổ ký ức theo các sector nhận thức (`semantic`, `episodic`, `procedural`, `reflective`, `emotional`). Click vào một tag bất kỳ để nhảy sang `Semantic Search` với truy vấn `tag:<tên_tag>`.
  - **Quick Workbench Actions & Danger Zone:** Lối tắt tới `Ingest Document`, `Build Unified KB`, `Inspect Plugins`, `v3.0 Layer 1 QA`, và nút `Clear All Memories` (ẩn với `reader`).

![Màn hình System Overview v3.0 ở chế độ Dark Theme](assets/ui_v3/03_overview_dark.png)

#### 2. `Memory & Docs` (`Memory & Document Ingestion`)
- **Chức năng:** Nạp ký ức nhận thức thủ công, upload & bóc tách tài liệu đa phương thức có kiểm soát phiên bản, và quản lý danh sách ký ức.
- **Thao tác chính:**
  - **Store Cognitive Memory:** Nhập nội dung quy tắc/quyết định kiến trúc và Tags phân tách dấu phẩy → **Store Memory** (`POST /memory/add`).
  - **Upload & Parse Document:** Kéo-thả hoặc chọn file **`.pdf`, `.docx`, `.xlsx`, `.xls`, `.pptx`, `.csv`, `.txt`, `.md`, `.png`, `.jpg`, `.jpeg`** (giới hạn `PB_MAX_UPLOAD_MB`, mặc định 50 MB).
    - Chọn **OCR Engine**: `auto` (Best Available), `paddle` (PaddleOCR), `rapidocr` (RapidOCR), `tesseract` (Tesseract), `ollama` (Ollama Vision), `vllm` (vLLM Vision), hoặc `hybrid_local` (Hybrid Local Pipeline).
    - Chọn **OCR Device**: `auto`, `gpu` (CUDA GPU), `mps` (Apple MPS), hoặc `cpu`.
    - Bấm **Upload & Parse** (`POST /sources/upload`): Backend chạy **Versioned Pre-OCR Delta Ingestion** (tái sử dụng các trang/sheet/slide có `unit_sha256` không đổi), bóc tách sang Markdown, nạp vào HSG Memory và cập nhật GraphStore overlay.
  - **Memory Feed & Filter:** Lọc nhanh bằng ô `Filter visible memory feed...`, xem thanh điểm `Salience`, nhấn **Reinforce** hoặc **Delete** (ẩn với `reader`).
  - **Ingested Document Versions:** Bấm **Refresh Docs** để xem danh sách tài liệu đã nạp và các phiên bản theo từng lần cập nhật.

![Màn hình Memory & Document Ingestion với tùy chọn OCR Engine và OCR Device](assets/ui_v3/05_memory_and_ocr_upload.png)

#### 3. `Semantic Search` (`AI Semantic & Fact Search`)
- **Chức năng:** Truy vấn tri thức bằng ngôn ngữ tự nhiên, từ khóa hoặc `tag:<tên_tag>`.
- **2 chế độ tìm kiếm:**
  - **`Gemini RAG Search`** (`POST /pm/search`): Truy xuất các memory liên quan và dùng Gemini tổng hợp thành câu trả lời hoàn chỉnh (**Gemini AI Answer**) kèm danh sách **Supporting Memories** để đối chứng.
  - **`Raw Context Retrieval`** (`POST /memory/search`): Trả thẳng danh sách memory node khớp nhất kèm điểm **Score** (tốc độ cao, không gọi LLM).

![Màn hình Semantic Search với 2 chế độ Gemini RAG Search và Raw Context Retrieval](assets/ui_v3/06_semantic_search.png)

---

### 3.2. Nhóm `Code & Knowledge Graph`

#### 4. `Graph Explorer` (`Codebase Structure Graph (CodeGraph)`)
- **Chức năng:** Khám phá đồ thị cấu trúc mã nguồn & tri thức tương tác bằng **Vis.js**.
- **Thao tác chính:**
  - Chọn **Active Project** (`Local Codegraph (Default)` hoặc bất kỳ `project:branch` / `project:%`).
  - Nhập từ khóa tại **Search Symbols** (tên class, function, đường dẫn file).
  - Chọn **Filter by Kind**: `Standard View (Clean)` (`class,function,method,file`), `PLC/LASAL View (with Servers & Clients)`, `All Kinds (with Imports & Variables)`, `Classes Only`, `Functions & Methods`, `Files Only`.
  - Nhấn **Load Graph**, sử dụng cụm HUD góc phải đồ thị (**Zoom `+`/`-`**, **Fit**, **Physics**, và **Fullscreen** — phím tắt **`F`** / **`Esc`**) để phóng toàn khung nhìn `100dvw × 100dvh`, và click vào bất kỳ node nào để xem bảng **Symbol Details** bên phải (`Name`, `Kind`, `File Path`, `Lines`, `Signature`, `Docstring`).

![Màn hình Graph Explorer trực quan hóa đồ thị mã nguồn bằng Vis.js](assets/ui_v3/16_graph_explorer_splitpane.png)

![Chế độ Fullscreen toàn màn hình trên Graph Explorer (phím F / Esc)](assets/ui_v3/17_graph_explorer_fullscreen.png)

#### 5. `Branch Diff` (`Compare Codebase Versions & Branches`)
- **Chức năng:** So sánh cấu trúc AST và bộ nhớ/quy tắc giữa hai nhánh hoặc hai phiên bản (`O(Delta)` Diff).
- **Thao tác:** Chọn **Base Version** (ví dụ `my-app:main`) và **Target Version** (ví dụ `my-app:feature-v2`) → bấm **Run Comparison**. Banner tổng hợp hiển thị số lượng **Added / Deleted / Modified** cho cả **Codebase Symbols** (cột trái) và **Memories & Rules** (cột phải).

![Màn hình Branch Diff so sánh Codebase Symbols và Memories giữa 2 nhánh](assets/ui_v3/08_branch_diff.png)

#### 6. `Unified KB & Trace` (`Unified Knowledge Base & Traceability`)
- **Chức năng:** Giao diện Web UI mới trong v3.0 giúp vận hành trọn vẹn **Unified Code–Doc Knowledge Base** mà không cần nhớ lệnh CLI.
- **3 phân khu trên màn hình:**
  1. **Build Complete KB (`POST /kb/build`):** Nhập `Workspace Path`, `Branch`, chọn `Compute Device` (`auto/gpu/mps/cpu`) và số lượng `Workers` (1–32) → bấm **Build Complete KB** để trích xuất Tree-sitter AST, phân cụm Leiden, chunking `H1 > H2 > H3` vào HSG memory và liên kết chéo Code–Doc–Test.
  2. **KB Coverage & Drift (`GET /kb/coverage`):** Bấm **Refresh Coverage** để kiểm tra tỷ lệ bao phủ tài liệu/API/MCP và danh sách cảnh báo lệch pha (`stale drift`) giữa code và tài liệu.
  3. **Bidirectional Code <-> Doc Trace (`GET /kb/trace`):** Nhập tên symbol, đường dẫn file hoặc tiêu đề tài liệu (ví dụ `AuthService.verify_token`) → bấm **Trace Links** để truy vết hai chiều toàn bộ symbol, callers/callees, doc chunks và test liên quan.

![Màn hình Unified KB & Trace với Build Complete KB, Coverage & Drift và Bidirectional Trace](assets/ui_v3/09_unified_kb_trace.png)

#### 7. `PM Copilot` (`Product Manager Documentation Copilot`)
- **Chức năng:** Biên soạn nhanh tài liệu sản phẩm từ bộ nhớ dự án (`POST /pm/generate-doc`).
- **Thao tác:** Chọn **Document Type** (`Project Status Update`, `Product Requirement Document (PRD)`, `User Stories & Acceptance Criteria`, `Product Development Roadmap`), nhập **Prompt / Guidance** (`Ctrl+Enter` để chạy nhanh) → bấm **Generate Document**. Kết quả hỗ trợ **Copy Markdown** và liệt kê **Utilized Source Memories** đã dùng.

---

### 3.3. Nhóm `Microkernel & v3 QA`

#### 8. `Plugins & Hardware` (`Microkernel Plugins & Hardware Telemetry`)
- **Chức năng:** Theo dõi năng lực phần cứng thực tế và quản trị trực quan **26 microkernel plugins thuộc 9 nhóm** ngay trên Web UI (không còn phải chạy CLI/API thủ công).
- **Thành phần chính:**
  - **GPU / CPU & Local AI Hardware Diagnostics (`GET /graphify/hardware`):** Bấm **Refresh Hardware** để xem chi tiết tình trạng PyTorch CUDA, Apple Silicon MPS, RAPIDS cuGraph, PaddleOCR, RapidOCR, Tesseract, CPU threads/oneDNN và khả năng kết nối tới **Ollama / vLLM / LM Studio / llama.cpp**.
  - **Microkernel Plugins Directory (`GET /plugins`):**
    - Lọc theo **9 nhóm plugin**: `all`, `graph_backend`, `vector_backend`, `etl_parser`, `knowledge_linker`, `doc_exporter`, `script_cache`, `artifact_store`, `brain_consumer`, `webhook`.
    - Ô tìm kiếm nhanh theo tên hoặc năng lực plugin (`Filter plugins by name or capability...`).
    - Quản trị viên (`admin`) có thể bấm **Reload Plugins** (`POST /plugins/reload`) và bật/tắt từng plugin (`enable`/`disable`). Toàn bộ giá trị cấu hình nhạy cảm (`secret`, `token`, `key`, `password`) luôn được che tự động.

![Màn hình Plugins & Hardware hiển thị chẩn đoán phần cứng và thư mục 26 microkernel plugins](assets/ui_v3/10_plugins_and_hardware.png)

#### 9. `v3.0 Testing Layer 1` (`v3.0 Testing Layer 1 Workbench`)
- **Chức năng:** Bàn làm việc QA & Tự động hóa kiểm thử đa thiết bị / hệ thống nhúng v3.0 với **4 sub-tabs**:
  1. **`Impact Test DAG Planner` (`POST /v3/planner/test-dag`):** Nhập danh sách file thay đổi (`Changed File Paths`) và `Version Tag` → bấm **Generate Test Plan** để tính toán bán kính ảnh hưởng (blast radius) và dựng DAG thứ tự các test cần chạy kèm điều kiện tiên quyết.
  2. **`UDCP Script Cache` (`POST /v3/script-cache/lookup`):** Nhập `Test Intent / Action Signature`, chọn `Target Platform` (`web` Playwright, `api` REST/MCP, `mobile` Appium, `desktop` UIA) và `App Version` → bấm **Lookup Cached Script** để tái sử dụng kịch bản UDCP đã kiểm chứng.
  3. **`Telemetry Evidence Window` (`GET /v3/artifacts/window`):** Nhập `Execution Run ID`, `Center Timestamp (ISO or Epoch ms)` và `Window (ms)` → bấm **Slice Evidence Window** để cắt cửa sổ bằng chứng đồng bộ NTP (log, UART, screenshot, video) quanh thời điểm xảy ra lỗi.
  4. **`AI Oracle & RCA Triage` (`POST /v3/rca/triage`):** Dán log lỗi, stacktrace hoặc sai lệch assertion → bấm **Run Root-Cause Triage** để chuẩn hóa stacktrace, gom nhóm sự cố trùng lặp và phản hồi đánh giá ổn định cho script cache.

![Màn hình v3.0 Testing Layer 1 Workbench với 4 module Planner, UDCP Cache, Evidence Window và RCA](assets/ui_v3/11_v3_testing_layer1.png)

#### 10. `Projects & Databases` (`3-Tier Topology: Master DB -> Project DB -> Branch Schema`)
- **Chức năng:** Quản trị trực quan toàn bộ kiến trúc **3 tầng cô lập vật lý** trên Web UI và bộ API `/master/*`:
  - **Topology Summary (`GET /master/topology` / MCP `projectbrain_database_topology`):** Hiển thị trạng thái **Master Control Plane** (`SQLite` hoặc `PostgreSQL`), tổng số **Project DBs (`pb_proj_*`)** và tổng số **Branch Schemas (`pb_root` / `pb_br_*`)**.
  - **Cấp phát & Xóa Project DB (`POST /master/projects`, `GET/POST/DELETE /codegraph/projects`):** Tạo dự án mới tự động cấp phát database vật lý `pb_proj_<slug>_<hash8>` kèm schema gốc `pb_root` (cho cả Graph và Docs/Memory); nút **Drop Project DB** (ẩn với `reader`) xóa sạch database vật lý và bản ghi trong `master_projects` / `master_branches`.
  - **Quản lý Branch Schema (`POST /master/projects/{id}/branches`, `DELETE /master/projects/{id}/branches/{branch}`):** Trên từng thẻ Project, bấm **+ New Branch Schema** để cấp phát schema `pb_br_<slug>_<hash8>` từ `base_branch`, xem số đếm trực tiếp (`Nodes`, `Docs`, `Memories`), bấm **Switch Here** để chuyển Workspace, hoặc **Drop Schema** với nhánh phụ (nhánh gốc `ROOT` luôn được bảo vệ, trả `HTTP 400` nếu cố xóa).
  - **Tiện ích đồng bộ:** Xem `Workspace Path`, `Last Sync` và sao chép nhanh lệnh CLI `projectbrain graphify-sync <project_id> <server_url> . <branch>`.

#### 11. `MCP Clients` (`Model Context Protocol (MCP) Client Integration`)
- **Chức năng:** Kết nối IDE và AI Agent (Claude Desktop, Cursor, VS Code Copilot) với ProjectBrain qua **Stdio** hoặc **HTTP/SSE**.
- **Thành phần chính:**
  - **Integration Settings & Active System Status:** Chọn Transport (`Stdio` hoặc `HTTP/SSE`) và bấm **Install & Apply Configuration** để ghi trực tiếp vào file `claude_desktop_config.json` (chỉ Admin).
  - **Local Project Configuration (`mcp.json`):** Tự động sinh sẵn 4 khối JSON tương ứng với Active Project & token đang đăng nhập (click để copy) cho VS Code Copilot (`.vscode/mcp.json`), Claude Code / Cursor (HTTP/SSE, Stdio Local, Stdio Remote DB).
  - **External MCP Servers Manager:** Đăng ký thêm các MCP server phụ trợ khác (như `postgres-mcp`) vào `claude_desktop_config.json`.

---

### 3.4. Nhóm `Identity & Security`

- **`My Profile & API Keys`:** Chi tiết thao tác đổi mật khẩu, tạo `pb_pat_...` theo phạm vi dự án/tool và thu hồi phiên xem tại [Mục 2.3](#23-quản-lý-hồ-sơ-cá-nhân-đổi-mật-khẩu-scoped-pat-pb_pat_-thu-hồi-phiên) (ảnh `12`).
- **`Identity & RBAC Admin` (chỉ `admin`):** Chi tiết cấu hình chính sách toàn cục, khóa/mở khóa brute-force, tạm ngưng tài khoản, xoay token và tra cứu Security Audit Logs xem tại [Mục 2.4](#24-quản-trị-viên-identity--rbac-admin-chỉ-admin) (ảnh `13`).

---

## Phụ Lục — Bảng Tra Cứu Nhanh 13 Màn Hình & Tài Nguyên Liên Quan

| Nhóm Sidebar | Tên Tab trên UI v3.0 | Quyền truy cập | Tài liệu / Đường dẫn liên quan |
| :--- | :--- | :--- | :--- |
| **Workspace & Memory** | `Overview` | Tất cả (ẩn Danger Zone với `reader`) | `GET /memory/stats`, `GET /graphify/hardware` |
| **Workspace & Memory** | `Memory & Docs` | Tất cả (ẩn Store/Upload/Delete với `reader`) | `POST /memory/add`, `POST /sources/upload`, `GET /sources/documents` |
| **Workspace & Memory** | `Semantic Search` | Tất cả | `POST /pm/search`, `POST /memory/search` |
| **Code & Knowledge Graph** | `Graph Explorer` | Tất cả | `GET /codegraph/data`, `GET /graphify/subgraph` |
| **Code & Knowledge Graph** | `Branch Diff` | Tất cả | `GET /codegraph/diff`, `GET /memory/diff` |
| **Code & Knowledge Graph** | `Unified KB & Trace` | Tất cả (Build chỉ `collaborator`/`admin`) | `POST /kb/build`, `GET /kb/coverage`, `GET /kb/trace` |
| **Code & Knowledge Graph** | `PM Copilot` | `collaborator`, `admin` (sinh doc) | `POST /pm/generate-doc` |
| **Microkernel & v3 QA** | `Plugins & Hardware` | Tất cả (Reload/Toggle chỉ `admin`) | `GET /plugins`, `POST /plugins/reload`, `GET /graphify/hardware` |
| **Microkernel & v3 QA** | `v3.0 Testing Layer 1` | Tất cả (RCA ghi chỉ `collaborator`/`admin`) | `POST /v3/planner/test-dag`, `POST /v3/script-cache/lookup`, `GET /v3/artifacts/window`, `POST /v3/rca/triage` |
| **Microkernel & v3 QA** | `Projects & Databases` | Tất cả (Tạo/Xóa DB & Branch chỉ `collaborator`/`admin`) | `GET /master/topology`, `POST /master/projects`, `POST/DELETE /master/projects/{id}/branches*`, `GET/POST/DELETE /codegraph/projects` |
| **Microkernel & v3 QA** | `MCP Clients` | Tất cả (ghi `claude_desktop_config.json` chỉ `admin`) | `GET/POST /memory/mcp/*` |
| **Identity & Security** | `My Profile & API Keys` | Tất cả tài khoản đăng nhập | `GET/PUT /auth/me`, `POST /auth/change-password`, `/auth/api-keys`, `/auth/sessions` |
| **Identity & Security** | `Identity & RBAC Admin` | Chỉ `admin` | `/auth/settings`, `/auth/accounts/*`, `GET /auth/audit-logs`, `GET /auth/tools` |

> **Tham khảo thêm:** Để xem ma trận phân quyền chi tiết từng API endpoint, từng FastMCP tool và kịch bản thực hành `curl` cho CI/CD, xem tiếp [HUONG_DAN_WEB_UI_VA_PHAN_QUYEN_RBAC.md](HUONG_DAN_WEB_UI_VA_PHAN_QUYEN_RBAC.md).
