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
    folder_path: Annotated[str, Field(description="Duong dan thu muc chua anh")],
    output_path: Annotated[
        str | None,
        Field(description="File .md dich (combined) hoac thu muc dich (per_image)"),
    ] = None,
    max_concurrency: Annotated[
        int, Field(ge=1, le=5, description="So anh xu ly song song, 1-5 (mac dinh 3)")
    ] = 3,
    output_mode: Annotated[
        Literal["combined", "per_image"],
        Field(description="Gop 1 file hay tach tung file"),
    ] = "combined",
    model: Annotated[
        str | None, Field(description="Override model, mac dinh gemini-3.1-pro-preview")
    ] = None,
    prompt_override: Annotated[
        str | None, Field(description="Override prompt trich xuat")
    ] = None,
    recursive: Annotated[bool, Field(description="Quet ca thu muc con")] = False,
) -> dict:
    """Trich text moi anh trong thu muc thanh Markdown, theo dung thu tu TEN anh (natural sort).

    Anh duoc sap xep bang natural sort (vi du 2 < 10), xu ly song song (gioi han boi
    max_concurrency), roi ghep/ghi ket qua theo DUNG thu tu ten anh. Ghi ra file .md
    va tra ve thong ke.

    Args:
        folder_path: Thu muc chua anh.
        output_path: combined -> file .md dich; per_image -> thu muc dich. Mac dinh
            ghi vao chinh folder nguon (output.md hoac <ten_anh>.md).
        max_concurrency: So request song song (1-5, mac dinh 3).
        output_mode: "combined" (1 file gop) hoac "per_image" (moi anh 1 file).
        model: Override model vision (mac dinh gemini-3.1-pro-preview).
        prompt_override: Override prompt OCR.
        recursive: Quet ca thu muc con.

    Returns:
        dict voi schema:
        {
            "total": int,            # tong so anh xu ly
            "succeeded": int,        # so anh thanh cong
            "failed": int,           # so anh loi
            "output": str | [str],   # duong dan file ket qua (hoac danh sach neu per_image)
            "order": [str],          # ten anh theo dung thu tu da sort
            "items": [               # chi tiet tung anh (khong gom markdown)
                {"index": int, "file": str, "ok": bool, "error": str | None}
            ]
        }

    Error Handling:
        Raise ValueError neu thu muc khong ton tai hoac khong co anh hop le.
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
    folder_path: Annotated[str, Field(description="Thu muc chua anh")],
    recursive: Annotated[bool, Field(description="Quet ca thu muc con")] = False,
) -> dict:
    """Liet ke ten anh DA SAP XEP trong thu muc (khong goi API).

    Dung de xem truoc thu tu xu ly (natural sort theo ten file). Khong goi vision model,
    khong ghi file.

    Args:
        folder_path: Thu muc chua anh.
        recursive: Quet ca thu muc con.

    Returns:
        dict: {"count": int, "files": [str]}  # files theo dung thu tu natural sort.

    Error Handling:
        Raise ValueError neu thu muc khong ton tai.
    """
    folder = Path(folder_path).expanduser()
    if not folder.is_dir():
        raise ValueError(f"Khong tim thay thu muc: {folder}")
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
    image_path: Annotated[str, Field(description="Duong dan toi 1 anh")],
    model: Annotated[str | None, Field(description="Override model")] = None,
    prompt_override: Annotated[str | None, Field(description="Override prompt")] = None,
    output_path: Annotated[
        str | None, Field(description="Neu co, ghi Markdown ra file nay")
    ] = None,
) -> dict:
    """Trich text tu 1 anh, tra ve Markdown (tuy chon ghi ra file).

    Args:
        image_path: Duong dan toi anh can trich.
        model: Override model vision (mac dinh gemini-3.1-pro-preview).
        prompt_override: Override prompt OCR.
        output_path: Neu co, ghi Markdown ra duong dan nay.

    Returns:
        dict: {"file": str, "markdown": str}

    Error Handling:
        Raise ValueError neu khong tim thay anh; RuntimeError neu trich xuat that bai.
    """
    p = Path(image_path).expanduser()
    if not p.is_file():
        raise ValueError(f"Khong tim thay anh: {p}")
    results = await extract_paths(
        [p], 1, model or CONFIG.model, prompt_override or DEFAULT_PROMPT
    )
    r = results[0]
    if not r.ok:
        raise RuntimeError(f"Loi trich xuat: {r.error}")
    if output_path:
        from .utils import write_text

        write_text(Path(output_path).expanduser(), r.markdown or "")
    return {"file": r.file, "markdown": r.markdown}


def main() -> None:
    mcp.run()  # stdio (phu hop Claude Desktop/local)


if __name__ == "__main__":
    main()
