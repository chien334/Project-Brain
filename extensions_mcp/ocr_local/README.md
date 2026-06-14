# ocr-local — OCR offline trên CPU (PaddleOCR)

OCR ảnh và PDF scan hoàn toàn **offline, không cần API key**, chạy tốt trên CPU.
Là engine OCR mặc định của ProjectBrain (đặt qua `OCR_ENGINE`), tự fallback sang
cloud vision (Gemini/OpenAI) khi cần.

## Cài đặt

```bash
pip install -e ".[ocr]"
# hoặc trực tiếp:
pip install paddleocr paddlepaddle pymupdf
```

> Lần chạy đầu, PaddleOCR tự tải model (vài MB / ngôn ngữ) về `~/.paddleocr`
> (cần internet 1 lần). Sau đó chạy offline.

## Cấu hình (.env)

```ini
OCR_ENGINE=auto            # auto | paddle | vision | off
OCR_PADDLE_LANG=ch,vi,japan # đa ngữ; càng nhiều ngôn ngữ càng chậm
OCR_PADDLE_DPI=200         # DPI render trang PDF scan
OCR_PADDLE_USE_GPU=false   # CPU mặc định
```

Mã ngôn ngữ thường dùng: `ch` (Trung+Anh), `en`, `vi`, `japan`, `korean`, `latin`.
**Mẹo tốc độ:** chỉ để ngôn ngữ chính (vd `OCR_PADDLE_LANG=ch`) — mỗi ngôn ngữ là
một lượt OCR riêng, lấy kết quả có độ tin cậy cao nhất.

## Tools

| Tool | Việc |
|------|------|
| `ocr_engine_status` | Báo cáo: đã cài PaddleOCR/PyMuPDF chưa, ngôn ngữ, engine, có key cloud không |
| `ocr_image_to_markdown` | OCR một file ảnh → Markdown |
| `ocr_pdf_to_markdown` | Render từng trang PDF → OCR. `force_ocr=False` chỉ OCR trang không có text layer (PDF scan), `force_ocr=True` OCR toàn bộ |

## Tích hợp với parser tài liệu

`parse_document()` (upload dashboard + các tool convert) tự dùng engine theo
`OCR_ENGINE`: PDF scan / ảnh nhúng được OCR bằng PaddleOCR local trước, fallback
cloud. Office (docx/xlsx/pptx) vẫn dùng parser text gốc cho chính xác.
