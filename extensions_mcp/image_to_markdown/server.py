from pathlib import Path
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import CONFIG
from .extractor import extract_paths, run_extract_folder
from .utils import IMAGE_EXTS, list_images
from .vision_client import DEFAULT_PROMPT

mcp = FastMCP("image_to_markdown_mcp")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Extract folder of images to Markdown",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def img2md_extract_folder(
    folder_path: Annotated[str, Field(description="Path to the directory containing images")],
    output_path: Annotated[
        str | None,
        Field(description="Path to the destination Markdown file (combined mode) or destination directory (per_image mode)"),
    ] = None,
    max_concurrency: Annotated[
        int, Field(ge=1, le=5, description="Number of images to process in parallel, 1-5 (default 3)")
    ] = 3,
    output_mode: Annotated[
        Literal["combined", "per_image"],
        Field(description="Output mode: 'combined' to merge results into one file, or 'per_image' to write one file per image"),
    ] = "combined",
    model: Annotated[
        str | None, Field(description="Override model name (default: gemini-3.1-pro-preview)")
    ] = None,
    prompt_override: Annotated[
        str | None, Field(description="Override the extraction prompt")
    ] = None,
    recursive: Annotated[bool, Field(description="Whether to scan subdirectories recursively")] = False,
) -> dict:
    """Extract text from all images in a folder and format as Markdown, sorted naturally by filename.

    Processes images in parallel (up to max_concurrency) and aggregates the results in the correct order.
    Outputs to file(s) and returns execution stats.

    Args:
        folder_path: Path to the directory containing images.
        output_path: Destination file (combined mode) or destination directory (per_image mode).
            Defaults to writing in the source folder (output.md or <image_name>.md).
        max_concurrency: Number of parallel requests (1-5, default 3).
        output_mode: "combined" (one merged file) or "per_image" (one file per image).
        model: Override vision model (default: gemini-3.1-pro-preview).
        prompt_override: Override OCR prompt.
        recursive: Scan subdirectories recursively.

    Returns:
        dict with schema:
        {
            "total": int,            # total images processed
            "succeeded": int,        # count of successful extractions
            "failed": int,           # count of failed extractions
            "output": str | [str],   # path to output file(s)
            "order": [str],          # sorted image filenames
            "items": [               # details of each processed image
                {"index": int, "file": str, "ok": bool, "error": str | None}
            ]
        }

    Error Handling:
        Raises ValueError if folder doesn't exist or contains no valid images.
    """
    return await run_extract_folder(
        folder_path=folder_path,
        output_path=output_path,
        max_concurrency=max_concurrency,
        output_mode=output_mode,
        model=model,
        prompt_override=prompt_override,
        recursive=recursive,
        exts=IMAGE_EXTS,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="List images in sorted order",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
async def img2md_list_images(
    folder_path: Annotated[str, Field(description="Path to the directory containing images")],
    recursive: Annotated[bool, Field(description="Whether to scan subdirectories recursively")] = False,
) -> dict:
    """List images in natural sorted order within a directory (does not make LLM calls).

    Used to preview the processing order. Does not invoke the vision model or write files.

    Args:
        folder_path: Path to the directory containing images.
        recursive: Scan subdirectories recursively.

    Returns:
        dict: {"count": int, "files": [str]}  # files in natural sorted order.

    Error Handling:
        Raises ValueError if folder does not exist.
    """
    folder = Path(folder_path).expanduser()
    if not folder.is_dir():
        raise ValueError(f"Directory not found: {folder}")
    files = list_images(folder, recursive=recursive)
    return {"count": len(files), "files": [p.name for p in files]}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Extract a single image to Markdown",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def img2md_extract_image(
    image_path: Annotated[str, Field(description="Path to a single image")],
    model: Annotated[str | None, Field(description="Override model name")] = None,
    prompt_override: Annotated[str | None, Field(description="Override the extraction prompt")] = None,
    output_path: Annotated[
        str | None, Field(description="Destination file path to save the generated Markdown")
    ] = None,
) -> dict:
    """Extract text from a single image and return it as Markdown (optionally saves to a file).

    Args:
        image_path: Path to the image to extract.
        model: Override vision model (default: gemini-3.1-pro-preview).
        prompt_override: Override OCR prompt.
        output_path: Destination file path if saving results.

    Returns:
        dict: {"file": str, "markdown": str}

    Error Handling:
        Raises ValueError if image file not found; RuntimeError if extraction fails.
    """
    p = Path(image_path).expanduser()
    if not p.is_file():
        raise ValueError(f"Image file not found: {p}")
    results = await extract_paths(
        [p], 1, model or CONFIG.model, prompt_override or DEFAULT_PROMPT
    )
    r = results[0]
    if not r.ok:
        raise RuntimeError(f"Extraction failed: {r.error}")
    if output_path:
        from .utils import write_text

        write_text(Path(output_path).expanduser(), r.markdown or "")
    return {"file": r.file, "markdown": r.markdown}


def main() -> None:
    mcp.run()  # stdio (suitable for Claude Desktop/local)


if __name__ == "__main__":
    main()
