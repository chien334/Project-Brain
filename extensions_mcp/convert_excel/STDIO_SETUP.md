# Hướng dẫn sử dụng STDIO Transport

## Vấn đề với HTTP Transport

CodeVista gặp lỗi "fetch failed" khi kết nối với HTTP transport (`streamableHttp`). Đây là vấn đề phổ biến do:
- CodeVista có thể không hỗ trợ đầy đủ streamableHttp
- Vấn đề với mcpGateway
- Conflict với Accept headers

## Giải pháp: Chuyển sang STDIO Transport

STDIO transport ổn định hơn và được hỗ trợ tốt hơn bởi hầu hết MCP clients.

### Đã thực hiện

✅ **Tạo server mới:** `server_stdio.py`
- Sử dụng STDIO transport thay vì HTTP
- Giữ nguyên 2 tools: `excel_list_sheets` và `excel_convert_to_markdown`

✅ **Cập nhật cấu hình:** `codevista_mcp_settings.json`
```json
{
  "mcpServers": {
    "excel-to-md": {
      "command": "python",
      "args": ["d:/Tools/ConvertExcelToMd/server_stdio.py"],
      "autoApprove": [],
      "disabled": false,
      "timeout": 60
    }
  }
}
```

### Cách sử dụng

#### 1. Restart CodeVista
**QUAN TRỌNG:** Phải restart hoàn toàn CodeVista để load cấu hình mới
```
1. Đóng hoàn toàn CodeVista (thoát ứng dụng)
2. Mở lại CodeVista
3. CodeVista sẽ tự động khởi động server_stdio.py
```

#### 2. Kiểm tra kết nối
Trong CodeVista:
- Mở Settings → MCP Servers
- Xem "excel-to-md" có status "Connected" không
- Nếu có lỗi, xem logs trong Output panel

#### 3. Sử dụng tools
Sau khi kết nối thành công, bạn có thể sử dụng 2 tools:

**Tool 1: excel_list_sheets**
```
Liệt kê các sheet trong file Excel/CSV
Input: file_path (đường dẫn đến file)
Output: JSON với danh sách sheet names
```

**Tool 2: excel_convert_to_markdown**
```
Chuyển đổi Excel/CSV sang Markdown
Input: 
  - file_path (đường dẫn đến file)
  - sheets (optional - danh sách sheet cần convert)
Output: Markdown tables
```

### So sánh HTTP vs STDIO

| Feature | HTTP (server.py) | STDIO (server_stdio.py) |
|---------|------------------|-------------------------|
| Transport | Streamable HTTP | STDIO |
| Port | 6000 | N/A |
| Khởi động | Thủ công: `python server.py` | Tự động bởi CodeVista |
| Tương thích | Có thể có vấn đề | Tốt hơn với CodeVista |
| Debug | Dễ test với curl/PowerShell | Khó test độc lập |
| Khuyến nghị | Nếu HTTP hoạt động | **Khuyến nghị cho CodeVista** |

### Troubleshooting

#### Lỗi: "Command not found: python"
**Giải pháp:** Sửa command trong config
```json
{
  "mcpServers": {
    "excel-to-md": {
      "command": "python3",  // hoặc đường dẫn đầy đủ
      "args": ["d:/Tools/ConvertExcelToMd/server_stdio.py"]
    }
  }
}
```

Hoặc dùng đường dẫn đầy đủ:
```json
{
  "mcpServers": {
    "excel-to-md": {
      "command": "C:/Users/chienlq1/AppData/Local/Programs/Python/Python311/python.exe",
      "args": ["d:/Tools/ConvertExcelToMd/server_stdio.py"]
    }
  }
}
```

#### Lỗi: "Module not found: openpyxl"
**Giải pháp:** Cài đặt dependencies
```bash
cd d:/Tools/ConvertExcelToMd
pip install -r requirements.txt
```

#### Lỗi: "Permission denied"
**Giải pháp:** Kiểm tra quyền thực thi
```bash
# Đảm bảo file có quyền đọc
icacls server_stdio.py
```

#### Server không khởi động
**Debug:**
1. Test thủ công:
```bash
cd d:/Tools/ConvertExcelToMd
python server_stdio.py
```

2. Nếu có lỗi, sửa và thử lại

3. Kiểm tra logs của CodeVista trong Output panel

### Test thủ công (Advanced)

Nếu muốn test STDIO server thủ công:

```python
# test_stdio.py
import subprocess
import json

proc = subprocess.Popen(
    ["python", "server_stdio.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

# Send initialize
init_msg = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"}
    }
}
proc.stdin.write(json.dumps(init_msg) + "\n")
proc.stdin.flush()

# Read response
response = proc.stdout.readline()
print("Initialize:", response)

# Send tools/list
list_msg = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
}
proc.stdin.write(json.dumps(list_msg) + "\n")
proc.stdin.flush()

# Read response
response = proc.stdout.readline()
print("Tools:", response)

proc.terminate()
```

### Quay lại HTTP Transport

Nếu muốn quay lại HTTP transport:

1. Khởi động HTTP server:
```bash
python server.py
```

2. Sửa config:
```json
{
  "mcpServers": {
    "excel-to-md": {
      "type": "streamableHttp",
      "url": "http://localhost:6000/mcp",
      "autoApprove": [],
      "disabled": false,
      "timeout": 60
    }
  }
}
```

3. Restart CodeVista

### Bảo mật

⚠️ **Lưu ý bảo mật vẫn áp dụng như HTTP version**
- Xem file `SECURITY_REVIEW.md` để biết chi tiết
- Không dùng với file chứa dữ liệu nhạy cảm
- Review file trước khi convert

### Kết luận

STDIO transport là giải pháp ổn định hơn cho CodeVista. Sau khi restart CodeVista, server sẽ tự động khởi động và bạn có thể sử dụng 2 tools để convert Excel sang Markdown.

**Các bước:**
1. ✅ Đã tạo `server_stdio.py`
2. ✅ Đã cập nhật `codevista_mcp_settings.json`
3. 🔄 **Restart CodeVista** (bạn cần làm)
4. ✅ Sử dụng tools
