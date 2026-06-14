# Hướng dẫn chạy OCR local (PaddleOCR, CPU)

Tính năng OCR offline trên CPU bằng **PaddleOCR**, dùng để đọc chữ từ **ảnh** và
**PDF scan** mà không cần API key. Là engine mặc định của ProjectBrain, tự fallback
sang cloud vision (Gemini/OpenAI) khi cần.

> Môi trường tham chiếu: Windows + Miniconda (`base`), Python 3.13.
> `python` trên PATH bị Microsoft Store alias chặn nên cần kích hoạt conda (xem dưới).

---

## 1. Cài đặt (một lần)

```powershell
& "C:\Users\sonpln\AppData\Local\Miniconda3\shell\condabin\conda-hook.ps1"
conda activate base
cd C:\Users\sonpln\Desktop\Project-Brain
pip install -e ".[ocr]"
```

Lệnh này cài `paddleocr`, `paddlepaddle`, `pymupdf` + toàn bộ core của project.

> ⏳ `paddlepaddle` (~105 MB) tải hơi lâu. **Lần OCR đầu tiên** PaddleOCR còn tự tải
> model về `C:\Users\sonpln\.paddlex\official_models` (cần internet 1 lần), sau đó chạy offline.

---

## 2. Thiết lập mỗi khi mở terminal mới

```powershell
& "C:\Users\sonpln\AppData\Local\Miniconda3\shell\condabin\conda-hook.ps1"
conda activate base
cd C:\Users\sonpln\Desktop\Project-Brain
$env:PYTHONPATH = "C:\Users\sonpln\Desktop\Project-Brain"
```

> `$env:PYTHONPATH` cần thiết khi chạy **script lẻ** trong `scripts\` (để import được
> `extensions_mcp`). Khi chạy server (`serve`/`mcp`) thì **không cần** — loader tự thêm.

---

## 3. Kiểm tra engine đã sẵn sàng

```powershell
python scripts\check_ocr.py
```

Kết quả mong đợi:
```
ocr_engine: auto
paddleocr_available: True        <- engine local OK
pdf_renderer_available: True     <- render PDF OK
cloud_vision_key_set: False      <- bình thường: OCR local KHÔNG cần key
```

- `(no file given — status only)` ở cuối là **bình thường**: không truyền file thì chỉ in trạng thái.
- `cloud_vision_key_set: False` **không phải lỗi** — chỉ cho biết chưa có fallback cloud. OCR local vẫn chạy tốt.

---

## 4. Chạy OCR bằng tay

### OCR một ảnh
```powershell
python scripts\check_ocr.py "C:\duong\dan\anh.png"
```

### OCR một PDF (kể cả PDF scan)
```powershell
python scripts\check_ocr.py "C:\duong\dan\tai-lieu.pdf"
```
In ra `pages` / `ocr_pages` (số trang phải OCR) + Markdown.

### Tạo file mẫu để thử nhanh
```powershell
python scripts\make_sample.py
python scripts\check_ocr.py "scripts\sample\sample_text.png"
python scripts\check_ocr.py "scripts\sample\sample_scan.pdf"
```

---

## 5. Cấu hình (đặt biến môi trường trước khi chạy, hoặc ghi vào `.env`)

| Biến | Ý nghĩa | Giá trị |
|------|---------|---------|
| `OCR_ENGINE` | Chọn engine | `auto` (Paddle→cloud, mặc định) · `paddle` (chỉ local) · `vision` (chỉ cloud) · `off` |
| `OCR_PADDLE_LANG` | Ngôn ngữ | `ch` (Trung+Anh) · `en` · `vi` · `japan` · `korean` · hoặc nhiều: `ch,vi,japan` |
| `OCR_PADDLE_DPI` | Độ phân giải render PDF scan | `200` (mặc định) · `300` nét hơn (chậm hơn) |
| `OCR_PADDLE_USE_GPU` | Dùng GPU | `false` (mặc định, CPU) |

Ví dụ chạy với tiếng Anh, ép local-only, DPI cao:
```powershell
$env:OCR_PADDLE_LANG = "en"
$env:OCR_ENGINE      = "paddle"
$env:OCR_PADDLE_DPI  = "300"
python scripts\check_ocr.py "scripts\sample\sample_scan.pdf"
```

> ⚠️ **Nhẹ vs đa ngữ:** mỗi ngôn ngữ trong `OCR_PADDLE_LANG` là **một lượt OCR riêng**
> (lấy kết quả tin cậy cao nhất). Càng nhiều ngôn ngữ → càng chậm. Để nhanh nhất,
> chỉ để đúng ngôn ngữ chính của tài liệu.

---

## 6. Test đúng luồng upload (`parse_document`)

Đây là hàm mà upload dashboard và các tool convert thực sự gọi:

```powershell
python scripts\check_parse.py "C:\duong\dan\tai-lieu.pdf"
```
(Nếu chưa có `scripts\check_parse.py`, tạo với nội dung:)
```python
import asyncio, sys
from pathlib import Path
from projectbrain.utils.doc_parser import parse_document

async def main():
    p = Path(sys.argv[1])
    print(await parse_document(p.name, p.read_bytes()))

asyncio.run(main())
```
PDF scan sẽ ra text nằm trong khối `<!-- Start Page OCR --> ... <!-- End Page OCR -->`.

---

## 7. Chạy qua server (upload tự đi qua OCR local)

```powershell
python -m projectbrain.main serve      # Dashboard + API
# hoặc
python -m projectbrain.main mcp        # MCP server
```

MCP tool có sẵn:
- `ocr_engine_status` — báo cáo trạng thái engine
- `ocr_image_to_markdown` — OCR một ảnh
- `ocr_pdf_to_markdown` — render từng trang PDF → OCR (`force_ocr=True` để OCR toàn bộ kể cả khi có text layer)

---

## 8. Cách hoạt động (tóm tắt)

1. `parse_document()` dispatch theo đuôi file.
2. **PDF**: lấy text layer; trang nào **thiếu text** (PDF scan) → render trang đó thành ảnh (PyMuPDF) → OCR.
3. **OCR dispatcher** (`OCR_ENGINE`): thử **PaddleOCR local** trước → fallback **cloud vision** → cuối cùng `![Image]`.
4. **Office (docx/xlsx/pptx)**: giữ parser text gốc (born-digital chính xác hơn OCR).
5. Cấu hình "nhẹ": đã tắt MKLDNN (tránh lỗi paddlepaddle 3.x) và tắt các model preprocessing nặng (doc-orientation / unwarping / textline-orientation) — chỉ dùng detection + recognition.

---

## 9. Khắc phục sự cố

| Triệu chứng | Nguyên nhân & cách xử lý |
|-------------|--------------------------|
| `ModuleNotFoundError: extensions_mcp` | Chưa set `$env:PYTHONPATH` về gốc project (xem mục 2). |
| `ModuleNotFoundError: psycopg2` | Chưa `pip install -e ".[ocr]"` trong env đang dùng. |
| `paddleocr_available: False` | Sai env; chạy `python -c "import paddleocr"` để kiểm tra; đảm bảo đã `conda activate base`. |
| Lần OCR đầu treo lâu | Đang tải model về `~/.paddlex` (bình thường, cần mạng 1 lần). Server chỉ load model 1 lần. |
| **`chars=0`** (không có chữ đọc ra) | Thường do **ảnh thật sự ít/không có chữ rõ**, ảnh **độ phân giải thấp**, hoặc **chữ bị xoay/nghiêng**. Thử: tăng `OCR_PADDLE_DPI`, đổi `OCR_PADDLE_LANG` cho đúng ngôn ngữ (vd `vi` cho tiếng Việt có dấu), hoặc dùng ảnh nét hơn. |
| `NotImplementedError ... onednn` | Lỗi MKLDNN của paddlepaddle 3.x — **đã xử lý sẵn** (tắt MKLDNN trong code). Nếu vẫn gặp, kiểm tra biến `FLAGS_use_mkldnn` không bị set lại thành `1`. |
| PowerShell báo lỗi dấu nháy khi dùng `python -c "..."` | Dùng file script thay vì `-c` (PowerShell 5.1 nuốt dấu nháy truyền vào exe). |
