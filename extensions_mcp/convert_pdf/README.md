# PDF to Markdown MCP Server

A Model Context Protocol (MCP) server that provides tools to extract text and tables from PDF files and convert them to Markdown format.

## Features

- **Extract Text**: Extract text from all pages or specific pages of a PDF
- **Extract Tables**: Extract structured tables from PDF pages
- **Extract Images**: Extract images from PDF pages and save to a directory
- **Convert to Markdown**: Convert entire PDF files to Markdown format with text, tables, and images
- **Get Page Count**: Get the total number of pages in a PDF
- **Page-specific Extraction**: Extract text from individual pages

## Installation

### Prerequisites

- Python 3.10 or higher
- pip

### Setup

1. Clone or navigate to the project directory:
```bash
cd mcp-pdf
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

Or install with development dependencies:
```bash
pip install -e ".[dev]"
```

## Usage

### Running the Server

Start the MCP server with STDIO transport:

```bash
python server_stdio.py
```

### Available Tools

#### 1. `pdf_extract_text`
Extract text from all pages of a PDF file.

**Parameters:**
- `file_path` (string, required): Path to the PDF file

**Returns:**
```json
{
  "file": "path/to/file.pdf",
  "total_pages": 5,
  "pages": {
    "1": "Page 1 text content...",
    "2": "Page 2 text content...",
    ...
  }
}
```

#### 2. `pdf_extract_tables`
Extract tables from all pages of a PDF file.

**Parameters:**
- `file_path` (string, required): Path to the PDF file

**Returns:**
```json
{
  "file": "path/to/file.pdf",
  "total_pages": 5,
  "pages_with_tables": 2,
  "pages": {
    "1": [
      [["Header1", "Header2"], ["Row1Col1", "Row1Col2"]],
      ...
    ],
    ...
  }
}
```

#### 3. `pdf_extract_images`
Extract images from all pages of a PDF file.

**Parameters:**
- `file_path` (string, required): Path to the PDF file
- `output_dir` (string, required): Directory to save extracted images

**Returns:**
```json
{
  "file": "path/to/file.pdf",
  "output_dir": "path/to/images",
  "total_pages": 5,
  "pages_with_images": {
    "1": ["path/to/images/page_1_image_1.png"],
    "2": ["path/to/images/page_2_image_1.png", "path/to/images/page_2_image_2.png"],
    ...
  }
}
```

#### 4. `pdf_convert_to_markdown`
Convert a PDF file to Markdown format with optional images and tables.

**Parameters:**
- `file_path` (string, required): Path to the PDF file
- `include_tables` (boolean, optional, default: true): Whether to include extracted tables
- `include_images` (boolean, optional, default: false): Whether to extract and include images
- `images_dir` (string, optional): Directory to save extracted images (required if include_images is true)

**Returns:**
Markdown-formatted text with all content from the PDF:
```markdown
## Page 1

![Image](./images/page_1_image_1.png)

Page 1 text content...

### Table 1

| Header1 | Header2 |
| --- | --- |
| Row1Col1 | Row1Col2 |

## Page 2

![Image](./images/page_2_image_1.png)

Page 2 text content...
```

#### 5. `pdf_get_page_count`
Get the total number of pages in a PDF file.

**Parameters:**
- `file_path` (string, required): Path to the PDF file

**Returns:**
```json
{
  "file": "path/to/file.pdf",
  "page_count": 5
}
```

#### 6. `pdf_extract_page_text`
Extract text from a specific page of a PDF file.

**Parameters:**
- `file_path` (string, required): Path to the PDF file
- `page_number` (integer, required): Page number to extract (1-based indexing)

**Returns:**
```json
{
  "file": "path/to/file.pdf",
  "page": 1,
  "text": "Page 1 text content..."
}
```

## Examples

### Extract text from a PDF
```python
# Using the MCP client
result = await client.call_tool("pdf_extract_text", {
    "file_path": "C:/documents/report.pdf"
})
```

### Extract images from a PDF
```python
# Using the MCP client
result = await client.call_tool("pdf_extract_images", {
    "file_path": "C:/documents/report.pdf",
    "output_dir": "C:/output/images"
})
```

### Convert PDF to Markdown with images
```python
# Using the MCP client
result = await client.call_tool("pdf_convert_to_markdown", {
    "file_path": "C:/documents/report.pdf",
    "include_tables": True,
    "include_images": True,
    "images_dir": "C:/output/images"
})
```

### Get page count
```python
# Using the MCP client
result = await client.call_tool("pdf_get_page_count", {
    "file_path": "C:/documents/report.pdf"
})
```

## Architecture

### Project Structure

```
mcp-pdf/
├── server_stdio.py      # Main MCP server with STDIO transport
├── pyproject.toml       # Project configuration
├── README.md            # This file
└── requirements.txt     # Python dependencies (optional)
```

### Key Components

1. **Helper Functions**
   - `_extract_text_from_pdf()`: Extracts text from all pages
   - `_extract_tables_from_pdf()`: Extracts tables from all pages
   - `_table_to_markdown()`: Converts table data to Markdown format
   - `_pdf_to_markdown()`: Converts entire PDF to Markdown

2. **MCP Tools**
   - `pdf_extract_text`: Read-only tool for text extraction
   - `pdf_extract_tables`: Read-only tool for table extraction
   - `pdf_extract_images`: Read-only tool for image extraction
   - `pdf_convert_to_markdown`: Read-only tool for PDF to Markdown conversion with optional images
   - `pdf_get_page_count`: Read-only tool for getting page count
   - `pdf_extract_page_text`: Read-only tool for page-specific extraction

3. **Error Handling**
   - File existence validation
   - PDF format validation
   - Page number validation
   - Graceful error messages

## Dependencies

- **mcp**: Model Context Protocol SDK
- **pdfplumber**: PDF text and table extraction
- **pydantic**: Data validation using Python type annotations

## Development

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
black server_stdio.py
```

### Linting

```bash
ruff check server_stdio.py
```

## Limitations

- Text extraction quality depends on PDF structure and encoding
- Scanned PDFs (image-based) require OCR for text extraction
- Complex table layouts may not be extracted perfectly
- Some PDF features (forms, annotations) are not extracted
- Image extraction works best with PDFs that have embedded images
- Image quality depends on the original PDF image quality

## Future Enhancements

- OCR support for scanned PDFs
- PDF metadata extraction
- Support for PDF forms
- Batch processing multiple PDFs
- Custom Markdown formatting options
- PDF splitting and merging
- Image optimization and compression
- Support for different image formats (JPEG, WebP, etc.)

## License

MIT License - See LICENSE file for details

## Support

For issues, questions, or contributions, please refer to the main project documentation.

## Related Skills

- **mcp-builder**: Guide for creating MCP servers
- **pdf**: Advanced PDF processing techniques
- **excel-to-md**: Similar tool for Excel to Markdown conversion

## Version History

### v1.1.0 (2026-05-29)
- Added image extraction from PDFs
- Added image support in PDF to Markdown conversion
- Images are saved to a directory and linked in Markdown
- Improved documentation with image examples

### v1.0.0 (2026-05-29)
- Initial release
- Text extraction from PDFs
- Table extraction from PDFs
- PDF to Markdown conversion
- Page count retrieval
- Page-specific text extraction
