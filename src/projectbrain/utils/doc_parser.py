import io
import os
import pypdf
import mammoth
import markdownify

def pdf_to_markdown(file_bytes: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        text_parts = []
        for page_num, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                text_parts.append(f"## Page {page_num + 1}\n\n{text}")
        return "\n\n".join(text_parts)
    except Exception as e:
        raise ValueError(f"Failed to parse PDF: {str(e)}")

def docx_to_markdown(file_bytes: bytes) -> str:
    try:
        # mammoth expects a file-like object
        file_obj = io.BytesIO(file_bytes)
        result = mammoth.convert_to_html(file_obj)
        html = result.value
        # markdownify HTML to Markdown
        markdown = markdownify.markdownify(html)
        return markdown
    except Exception as e:
        raise ValueError(f"Failed to parse DOCX: {str(e)}")

def excel_to_markdown(file_bytes: bytes) -> str:
    try:
        # Try importing openpyxl dynamically
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        sheets = []
        for name in wb.sheetnames:
            ws = wb[name]
            sheets.append(f"## Sheet: {name}\n")
            rows = []
            for row in ws.iter_rows(values_only=True):
                if any(row):  # skip entirely empty rows
                    row_str = " | ".join([str(val) if val is not None else "" for val in row])
                    rows.append(f"| {row_str} |")
            if rows:
                sheets.append("\n".join(rows))
        return "\n\n".join(sheets)
    except ImportError:
        # Fallback if openpyxl is not installed
        return "Excel parsing requires 'openpyxl'. Run: pip install openpyxl"
    except Exception as e:
        raise ValueError(f"Failed to parse Excel: {str(e)}")

def parse_document(filename: str, file_bytes: bytes) -> str:
    _, ext = os.path.splitext(filename.lower())
    if ext == ".pdf":
        return pdf_to_markdown(file_bytes)
    elif ext == ".docx":
        return docx_to_markdown(file_bytes)
    elif ext in [".xlsx", ".xls"]:
        return excel_to_markdown(file_bytes)
    elif ext in [".txt", ".md", ".json", ".yml", ".yaml", ".ini", ".conf"]:
        return file_bytes.decode("utf-8", errors="ignore")
    else:
        # fallback to plain text decode
        return file_bytes.decode("utf-8", errors="ignore")
