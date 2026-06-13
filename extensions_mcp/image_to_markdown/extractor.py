import asyncio
from pathlib import Path

from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .config import CONFIG
from .utils import list_images, read_image_as_supported, write_text
from .vision_client import DEFAULT_PROMPT, extract_text_from_image


class ImageResult(BaseModel):
    index: int
    file: str
    ok: bool
    error: str | None = None
    markdown: str | None = None


# Cac loi KHONG nen retry (loi dau vao / duong dan, retry vo ich).
_NON_RETRYABLE = (ValueError, FileNotFoundError, IsADirectoryError, NotADirectoryError)


def _is_retryable(exc: BaseException) -> bool:
    """Chi retry loi mang/429/5xx; bo qua loi dau vao co dinh."""
    return not isinstance(exc, _NON_RETRYABLE)


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
async def _extract_one(path: Path, prompt: str, model: str) -> str:
    img_bytes, mime = read_image_as_supported(path)
    return await extract_text_from_image(img_bytes, mime, prompt, model)


async def extract_paths(
    paths: list[Path], max_concurrency: int, model: str, prompt: str
) -> list[ImageResult]:
    sem = asyncio.Semaphore(max_concurrency)

    async def work(idx: int, path: Path) -> ImageResult:
        async with sem:  # gioi han so request dong thoi
            try:
                md = await _extract_one(path, prompt, model)
                return ImageResult(index=idx, file=path.name, ok=True, markdown=md)
            except Exception as e:  # noqa: BLE001 - gom loi tung anh, khong dung ca batch
                return ImageResult(index=idx, file=path.name, ok=False, error=str(e))

    results = await asyncio.gather(*(work(i, p) for i, p in enumerate(paths)))
    return sorted(results, key=lambda r: r.index)  # ghep lai DUNG thu tu


def build_combined_md(results: list[ImageResult]) -> str:
    parts: list[str] = []
    for r in results:
        parts.append(f"<!-- source: {r.file} -->\n## {r.file}\n")
        if r.ok:
            parts.append((r.markdown or "").strip() or "_(khong co text)_")
        else:
            parts.append(f"> [!WARNING] Loi trich xuat: {r.error}")
        parts.append("\n---\n")
    return "\n".join(parts).strip() + "\n"


async def run_extract_folder(
    folder_path: str,
    output_path: str | None,
    max_concurrency: int,
    output_mode: str,
    model: str | None,
    prompt_override: str | None,
    recursive: bool,
    exts: set[str] | None,
) -> dict:
    folder = Path(folder_path).expanduser()
    if not folder.is_dir():
        raise ValueError(f"Khong tim thay thu muc: {folder}")

    paths = list_images(folder, recursive=recursive, exts=exts)
    if not paths:
        raise ValueError(f"Khong co anh hop le trong: {folder}")

    model = model or CONFIG.model
    prompt = prompt_override or DEFAULT_PROMPT
    results = await extract_paths(paths, max_concurrency, model, prompt)

    if output_mode == "per_image":
        out_dir = Path(output_path).expanduser() if output_path else folder
        written = []
        for r in results:
            target = out_dir / (Path(r.file).stem + ".md")
            body = (r.markdown or "") if r.ok else f"> Loi: {r.error}"
            write_text(target, body)
            written.append(str(target))
        out_info: str | list[str] = written
    else:  # combined
        target = (
            Path(output_path).expanduser() if output_path else (folder / "output.md")
        )
        write_text(target, build_combined_md(results))
        out_info = str(target)

    return {
        "total": len(results),
        "succeeded": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
        "output": out_info,
        "order": [r.file for r in results],
        "items": [r.model_dump(exclude={"markdown"}) for r in results],
    }
