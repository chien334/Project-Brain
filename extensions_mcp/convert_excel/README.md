# Excel → Markdown MCP Server

MCP Server chuyển đổi file Excel/CSV thành bảng Markdown. Hỗ trợ kết nối với **Claude Code**, **GitHub Copilot (VS Code)** và bất kỳ MCP client nào.

---

## Yêu cầu

- Python **3.11+**
- Git

---

## Cài đặt

### 1. Clone repository

```bash
git clone <repository-url>
cd ConvertExcelToMd
```

### 2. Tạo virtual environment và cài dependencies

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## Chạy server

Project cung cấp **2 transport mode**. Chọn một trong hai:

### Mode A — HTTP (khuyến nghị cho VS Code Copilot)

```powershell
.venv\Scripts\python server.py
```

Server khởi động tại `http://localhost:6000/mcp`.

> Đổi port nếu cần: `$env:PORT=7000; .venv\Scripts\python server.py`

### Mode B — STDIO (khuyến nghị cho Claude Code)

Không cần chạy thủ công — Claude Code tự khởi động qua config (xem bên dưới).

---

## Kết nối với GitHub Copilot (VS Code)

### Bước 1 — Bật MCP support trong VS Code

Mở Settings (`Ctrl+,`) → tìm `chat.mcp.enabled` → bật **On**.

### Bước 2 — Cấu hình MCP server

File `.vscode/mcp.json` đã có sẵn trong repo:

```json
{
  "servers": {
    "excel-to-md": {
      "type": "http",
      "url": "http://localhost:6000/mcp"
    }
  }
}
```

> Nếu muốn dùng STDIO thay vì HTTP, thay bằng:
> ```json
> {
>   "servers": {
>     "excel-to-md": {
>       "type": "stdio",
>       "command": "python",
>       "args": ["server_stdio.py"],
>       "cwd": "${workspaceFolder}"
>     }
>   }
> }
> ```

### Bước 3 — Khởi động server và reload VS Code

```powershell
.venv\Scripts\python server.py
```

Nhấn `Ctrl+Shift+P` → **"MCP: List Servers"** để xác nhận server đã kết nối.

### Bước 4 — Sử dụng trong Copilot Chat

Chuyển sang **Agent mode** trong Copilot Chat, sau đó gọi tool:

```
@excel-to-md Liệt kê các sheet trong file C:\data\report.xlsx
```

```
@excel-to-md Chuyển đổi file C:\data\report.xlsx sang Markdown
```

---

## Kết nối với Claude Code

### Cách 1 — STDIO (tự động, không cần chạy server trước)

Thêm vào file `~/.claude/settings.json` hoặc `.claude/settings.local.json` trong project:

```json
{
  "mcpServers": {
    "excel-to-md": {
      "type": "stdio",
      "command": "D:\\Tools\\ConvertExcelToMd\\.venv\\Scripts\\python.exe",
      "args": ["D:\\Tools\\ConvertExcelToMd\\server_stdio.py"]
    }
  }
}
```

### Cách 2 — HTTP (cần chạy server trước)

File `.mcp.json` đã có sẵn trong root project:

```json
{
  "mcpServers": {
    "excel-to-md": {
      "type": "http",
      "url": "http://127.0.0.1:6000/mcp"
    }
  }
}
```

Chạy server: `.venv\Scripts\python server.py`

Sau khi thêm config, chạy lệnh `/mcp` trong Claude Code để reload.

---

## Các tools

| Tool | Mô tả |
|------|-------|
| `excel_list_sheets` | Liệt kê tất cả sheet trong file Excel/CSV |
| `excel_convert_to_markdown` | Convert toàn bộ hoặc các sheet chỉ định sang Markdown |
| `excel_convert_sheet_to_markdown` | Convert từng sheet với hỗ trợ phân trang (`row_offset`, `row_limit`) — dùng cho file lớn |
| `markdown_convert_to_excel` | **MỚI**: Convert Markdown tables ngược lại thành file Excel |

### Tham số `excel_convert_sheet_to_markdown`

| Tham số | Bắt buộc | Mặc định | Mô tả |
|---------|----------|----------|-------|
| `file_path` | ✅ | — | Đường dẫn file Excel/CSV |
| `sheet_name` | ✅ | — | Tên sheet cần convert |
| `row_offset` | ❌ | `0` | Bỏ qua N hàng đầu (không tính header) |
| `row_limit` | ❌ | `null` | Giới hạn số hàng trả về |

Kết quả trả về JSON gồm `markdown`, `total_data_rows`, `has_more` để biết còn trang tiếp theo không.

### Tham số `markdown_convert_to_excel`

| Tham số | Bắt buộc | Mô tả |
|---------|----------|-------|
| `markdown_content` | ✅ | Nội dung Markdown với format: `## Sheet Name` theo sau bởi bảng Markdown |
| `output_file_path` | ✅ | Đường dẫn file Excel output (`.xlsx` hoặc `.xlsm`). Tự động tạo thư mục nếu chưa tồn tại |

**Ví dụ Markdown format:**
```markdown
## Sheet1
| Header1 | Header2 |
| --- | --- |
| Value1 | Value2 |

## Sheet2
| ColA | ColB |
| --- | --- |
| Data1 | Data2 |
```

Kết quả trả về JSON gồm `file`, `sheet_count`, `sheets` để xác nhận file đã được tạo.

---

## Định dạng file hỗ trợ

| Extension | Hỗ trợ |
|-----------|--------|
| `.xlsx` | ✅ |
| `.xlsm` | ✅ |
| `.xltx` / `.xltm` | ✅ |
| `.csv` | ✅ |

---

## Cấu trúc project

```
ConvertExcelToMd/
├── server.py          # MCP server — HTTP transport (port 6000)
├── server_stdio.py    # MCP server — STDIO transport
├── requirements.txt   # Python dependencies
├── .mcp.json          # Config cho Claude Code (HTTP)
└── .vscode/
    └── mcp.json       # Config cho VS Code Copilot (HTTP)
```

---

## Troubleshooting

**Server không khởi động được**
```powershell
# Kiểm tra port có bị chiếm không
netstat -ano | findstr :6000
# Đổi port
$env:PORT=7001; .venv\Scripts\python server.py
```

**VS Code không nhận server**
- Đảm bảo server đang chạy trước khi mở VS Code
- Kiểm tra `chat.mcp.enabled` đã bật
- Reload window: `Ctrl+Shift+P` → **"Developer: Reload Window"**

**Claude Code không thấy tool mới**
- Chạy `/mcp` để reload MCP servers
- Hoặc restart Claude Code hoàn toàn
