#!/usr/bin/env python3
"""
MCP Server for converting PDF files to Markdown - STDIO Transport.

Provides tools to extract text from PDF files and convert them to Markdown format
via STDIO transport.
"""

import json
import os
from pathlib import Path
from typing import Annotated, Optional
from PIL import Image
import io

import pdfplumber
from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("pdf_to_md_mcp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(file_path: str) -> dict[int, str]:
    """Extract text from each page of a PDF file.
    
    Returns: {page_number: text_content}
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"File must be a PDF, got '{path.suffix}'")
    
    pages_text = {}
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text(x_tolerance=2)
            pages_text[i] = text if text else ""
    
    return pages_text


def _extract_tables_from_pdf(file_path: str) -> dict[int, list]:
    """Extract tables from each page of a PDF file.
    
    Returns: {page_number: [tables]}
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"File must be a PDF, got '{path.suffix}'")
    
    pages_tables = {}
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            pages_tables[i] = tables if tables else []
    
    return pages_tables


def _table_to_markdown(table: list[list]) -> str:
    """Convert a table (list of lists) to Markdown format."""
    if not table or len(table) == 0:
        return ""
    
    # Determine column count
    col_count = max((len(row) for row in table), default=0)
    if col_count == 0:
        return ""
    
    def normalize_row(row: list) -> str:
        cells = [str(row[i] if i < len(row) else "").replace("|", "\\|").replace("\n", " ") for i in range(col_count)]
        return "| " + " | ".join(cells) + " |"
    
    # Header
    header = normalize_row(table[0])
    separator = "| " + " | ".join(["---"] * col_count) + " |"
    
    # Body
    body_rows = [normalize_row(row) for row in table[1:]]
    body = "\n".join(body_rows) if body_rows else ""
    
    if body:
        return f"{header}\n{separator}\n{body}"
    else:
        return f"{header}\n{separator}"


def _extract_images_from_pdf(file_path: str, output_dir: str) -> dict[int, list[str]]:
    """Extract images from each page of a PDF file.
    
    Returns: {page_number: [image_file_paths]}
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"File must be a PDF, got '{path.suffix}'")
    
    # Create output directory
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    
    pages_images = {}
    image_count = 0
    
    with pdfplumber.open(path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            page_images = []
            
            # Extract images from page
            for img_idx, img in enumerate(page.images, 1):
                try:
                    # Get image from PDF
                    image_data = page.crop(img["bbox"]).to_image()
                    
                    # Save image
                    image_count += 1
                    image_filename = f"page_{page_num}_image_{img_idx}.png"
                    image_path = output_path / image_filename
                    image_data.save(image_path)
                    
                    page_images.append(str(image_path))
                except Exception as e:
                    # Skip images that can't be extracted
                    continue
            
            if page_images:
                pages_images[page_num] = page_images
    
    return pages_images


def _pdf_to_markdown(file_path: str, include_tables: bool = True, include_images: bool = False, images_dir: Optional[str] = None) -> str:
    """Convert PDF to Markdown format with text, optional tables, and optional images."""
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"File must be a PDF, got '{path.suffix}'")
    
    # Extract images if requested
    pages_images = {}
    if include_images and images_dir:
        pages_images = _extract_images_from_pdf(file_path, images_dir)
    
    markdown_parts = []
    
    with pdfplumber.open(path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            # Add page heading
            markdown_parts.append(f"## Page {page_num}\n")
            
            # Extract and add images if requested
            if include_images and page_num in pages_images:
                for img_path in pages_images[page_num]:
                    # Create relative path for markdown
                    rel_path = os.path.relpath(img_path, Path(images_dir).parent if images_dir else ".")
                    markdown_parts.append(f"![Image](./{ rel_path})\n")
                markdown_parts.append("")
            
            # Extract and add text
            text = page.extract_text(x_tolerance=2)
            if text:
                markdown_parts.append(text)
                markdown_parts.append("")
            
            # Extract and add tables if requested
            if include_tables:
                tables = page.extract_tables()
                if tables:
                    for table_idx, table in enumerate(tables, 1):
                        markdown_parts.append(f"### Table {table_idx}\n")
                        table_md = _table_to_markdown(table)
                        if table_md:
                            markdown_parts.append(table_md)
                        markdown_parts.append("")
    
    return "\n".join(markdown_parts)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

FilePath = Annotated[str, Field(
    description="Absolute or relative path to the PDF file (e.g., 'C:/data/document.pdf')",
    min_length=1
)]


@mcp.tool(
    name="pdf_extract_text",
    annotations={
        "title": "Extract Text from PDF",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def pdf_extract_text(file_path: FilePath) -> str:
    """Extract text from all pages of a PDF file.

    Returns JSON with page-by-page text content.
    Format: {"total_pages": int, "pages": {page_number: text_content}}
    
    On error returns: "Error: <message>"
    """
    try:
        pages_text = _extract_text_from_pdf(file_path)
        result = {
            "file": str(Path(file_path).resolve()),
            "total_pages": len(pages_text),
            "pages": pages_text,
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (FileNotFoundError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error - {type(e).__name__}: {e}"


@mcp.tool(
    name="pdf_extract_tables",
    annotations={
        "title": "Extract Tables from PDF",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def pdf_extract_tables(file_path: FilePath) -> str:
    """Extract tables from all pages of a PDF file.

    Returns JSON with page-by-page table data.
    Format: {"total_pages": int, "pages_with_tables": {page_number: [tables]}}
    
    On error returns: "Error: <message>"
    """
    try:
        pages_tables = _extract_tables_from_pdf(file_path)
        
        # Filter out pages with no tables
        pages_with_tables = {k: v for k, v in pages_tables.items() if v}
        
        result = {
            "file": str(Path(file_path).resolve()),
            "total_pages": len(pages_tables),
            "pages_with_tables": len(pages_with_tables),
            "pages": pages_with_tables,
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (FileNotFoundError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error - {type(e).__name__}: {e}"


@mcp.tool(
    name="pdf_extract_images",
    annotations={
        "title": "Extract Images from PDF",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def pdf_extract_images(
    file_path: FilePath,
    output_dir: Annotated[str, Field(
        description="Directory to save extracted images (e.g., 'C:/output/images')",
        min_length=1
    )],
) -> str:
    """Extract images from all pages of a PDF file.

    Saves images to the specified directory with names like page_1_image_1.png.
    
    Returns JSON: {"file": str, "output_dir": str, "total_pages": int, "pages_with_images": {page_number: [image_paths]}}
    
    On error returns: "Error: <message>"
    """
    try:
        pages_images = _extract_images_from_pdf(file_path, output_dir)
        
        result = {
            "file": str(Path(file_path).resolve()),
            "output_dir": str(Path(output_dir).resolve()),
            "total_pages": len(pages_images),
            "pages_with_images": pages_images,
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (FileNotFoundError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error - {type(e).__name__}: {e}"


@mcp.tool(
    name="pdf_convert_to_markdown",
    annotations={
        "title": "Convert PDF to Markdown",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def pdf_convert_to_markdown(
    file_path: FilePath,
    include_tables: Annotated[bool, Field(
        default=True,
        description="Whether to include extracted tables in the output"
    )] = True,
    include_images: Annotated[bool, Field(
        default=False,
        description="Whether to extract and include images in the output"
    )] = False,
    images_dir: Annotated[Optional[str], Field(
        default=None,
        description="Directory to save extracted images (required if include_images is True)"
    )] = None,
) -> str:
    """Convert a PDF file to Markdown format.

    Extracts text from all pages and optionally includes tables and images.
    Each page becomes a level-2 heading (## Page N).
    Tables are formatted as Markdown tables.
    Images are saved to a directory and linked in the Markdown.

    Returns: Markdown-formatted text with all content from the PDF.
    
    On error returns: "Error: <message>"
    """
    try:
        if include_images and not images_dir:
            return "Error: images_dir is required when include_images is True"
        
        markdown = _pdf_to_markdown(
            file_path,
            include_tables=include_tables,
            include_images=include_images,
            images_dir=images_dir
        )
        return markdown
    except (FileNotFoundError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error - {type(e).__name__}: {e}"


@mcp.tool(
    name="pdf_get_page_count",
    annotations={
        "title": "Get PDF Page Count",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def pdf_get_page_count(file_path: FilePath) -> str:
    """Get the total number of pages in a PDF file.

    Returns JSON: {"file": str, "page_count": int}
    
    On error returns: "Error: <message>"
    """
    try:
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"File must be a PDF, got '{path.suffix}'")
        
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
        
        result = {
            "file": str(path),
            "page_count": page_count,
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (FileNotFoundError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error - {type(e).__name__}: {e}"


@mcp.tool(
    name="pdf_extract_page_text",
    annotations={
        "title": "Extract Text from Specific PDF Page",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def pdf_extract_page_text(
    file_path: FilePath,
    page_number: Annotated[int, Field(
        description="Page number to extract (1-based indexing)",
        ge=1
    )],
) -> str:
    """Extract text from a specific page of a PDF file.

    Returns JSON: {"file": str, "page": int, "text": str}
    
    On error returns: "Error: <message>"
    """
    try:
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"File must be a PDF, got '{path.suffix}'")
        
        with pdfplumber.open(path) as pdf:
            if page_number < 1 or page_number > len(pdf.pages):
                raise ValueError(f"Page {page_number} not found. PDF has {len(pdf.pages)} pages.")
            
            page = pdf.pages[page_number - 1]
            text = page.extract_text(x_tolerance=2)
        
        result = {
            "file": str(path),
            "page": page_number,
            "text": text if text else "",
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (FileNotFoundError, ValueError) as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error - {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Entry point - STDIO transport
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Use STDIO transport
    mcp.run(transport="stdio")
