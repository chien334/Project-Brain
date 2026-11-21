import asyncio
from pathlib import Path
from typing import Annotated, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import CONFIG, LOCAL_AI_PROVIDERS
from .extractor import ImageResult, build_combined_md, extract_paths, run_extract_folder
from .paddle_client import extract_text_local, is_available as paddle_available
from .utils import IMAGE_EXTS, list_images, read_image_as_supported, write_text
from .vision_client import DEFAULT_PROMPT, extract_text_from_image

mcp = FastMCP("image_to_markdown_mcp")


def _resolve_local_ai_provider(engine: str) -> str:
    eng = (engine or "auto").strip().lower()
    if eng in LOCAL_AI_PROVIDERS:
        return eng
    if CONFIG.provider in LOCAL_AI_PROVIDERS:
        return CONFIG.provider
    return "ollama"


async def _extract_single_with_engine(
    path: Path,
    prompt: str,
    model: Optional[str],
    engine: str = "auto",
    device: str = "auto",
    local_ai_model: Optional[str] = None,
) -> str:
    """Extract Markdown from a single image using the requested OCR/Vision engine and device."""
    eng = (engine or "auto").strip().lower()
    eff_device = (device or CONFIG.ocr_device or "auto").strip().lower()
    img_bytes, mime = read_image_as_supported(path)

    # Lazy integration with UnifiedOCREngine when available (TASK-23A)
    if eng in ("paddle", "rapidocr", "tesseract", "hybrid_local") and prompt == DEFAULT_PROMPT:
        try:
            from projectbrain.ocr_runtime import UnifiedOCREngine  # type: ignore

            unified = UnifiedOCREngine()
            if hasattr(unified, "extract_image"):
                res = await unified.extract_image(
                    image_bytes=img_bytes,
                    mime_type=mime,
                    engine=eng,
                    device=eff_device,
                    local_ai_model=local_ai_model or model,
                )
                if isinstance(res, dict):
                    txt = str(res.get("text") or res.get("markdown") or "").strip()
                    if txt:
                        return txt
        except Exception:
            pass

    if eng == "paddle":
        if not paddle_available():
            raise RuntimeError(
                "PaddleOCR is not installed. Run: pip install paddleocr paddlepaddle"
            )
        return await extract_text_local(img_bytes, mime, device=eff_device)

    if eng in LOCAL_AI_PROVIDERS:
        eff_model = local_ai_model or model or (
            CONFIG.model if CONFIG.provider == eng else ("qwen2.5vl" if eng == "ollama" else "default")
        )
        return await extract_text_from_image(
            img_bytes,
            mime,
            prompt,
            eff_model,
            provider=eng,
        )

    if eng == "vision":
        eff_provider = None
        eff_model = local_ai_model or model or CONFIG.model
        return await extract_text_from_image(
            img_bytes,
            mime,
            prompt,
            eff_model,
            provider=eff_provider,
        )

    if eng == "hybrid_local":
        ocr_draft = ""
        if paddle_available():
            ocr_draft = (await extract_text_local(img_bytes, mime, device=eff_device)).strip()

        local_provider = _resolve_local_ai_provider("auto")
        eff_model = local_ai_model or model or (
            CONFIG.model if CONFIG.provider == local_provider else (
                "qwen2.5vl" if local_provider == "ollama" else "default"
            )
        )
        # Try Local AI Vision to structure/enhance or recover empty OCR; fall back to OCR draft
        try:
            if ocr_draft and not local_ai_model and CONFIG.provider not in LOCAL_AI_PROVIDERS:
                return ocr_draft
            hybrid_prompt = prompt
            if ocr_draft:
                hybrid_prompt = (
                    f"{prompt}\n\n"
                    "Reference raw local OCR draft (verify against the image and fix formatting/tables):\n"
                    f"{ocr_draft}"
                )
            vision_txt = await extract_text_from_image(
                img_bytes,
                mime,
                hybrid_prompt,
                eff_model,
                provider=local_provider,
            )
            if vision_txt and vision_txt.strip():
                return vision_txt.strip()
        except Exception:
            if ocr_draft:
                return ocr_draft
            raise
        return ocr_draft

    # eng == "auto"
    if local_ai_model:
        local_provider = _resolve_local_ai_provider("auto")
        return await extract_text_from_image(
            img_bytes,
            mime,
            prompt,
            local_ai_model,
            provider=local_provider,
        )

    if CONFIG.ocr_engine == "paddle" and paddle_available():
        txt = await extract_text_local(img_bytes, mime, device=eff_device)
        if txt and txt.strip():
            return txt

    if CONFIG.provider in LOCAL_AI_PROVIDERS:
        return await extract_text_from_image(
            img_bytes,
            mime,
            prompt,
            model or CONFIG.model,
            provider=CONFIG.provider,
        )

    if CONFIG.api_key and CONFIG.provider in ("gemini", "openai_compatible"):
        return await extract_text_from_image(
            img_bytes,
            mime,
            prompt,
            model or CONFIG.model,
        )

    if paddle_available():
        txt = await extract_text_local(img_bytes, mime, device=eff_device)
        if txt and txt.strip():
            return txt

    # Final fallback: try vision client (will raise helpful RuntimeError if unconfigured)
    return await extract_text_from_image(img_bytes, mime, prompt, model or CONFIG.model)


async def _extract_paths_with_engine(
    paths: list[Path],
    max_concurrency: int,
    model: Optional[str],
    prompt: str,
    engine: str = "auto",
    device: str = "auto",
    local_ai_model: Optional[str] = None,
) -> list[ImageResult]:
    eng = (engine or "auto").strip().lower()
    use_legacy_extractor = (
        eng == "vision"
        and local_ai_model is None
        and CONFIG.provider in ("gemini", "openai_compatible")
    ) or (
        eng == "auto"
        and device == "auto"
        and local_ai_model is None
        and CONFIG.ocr_engine in ("auto", "vision")
        and CONFIG.provider in ("gemini", "openai_compatible")
        and bool(CONFIG.api_key)
    )
    if use_legacy_extractor:
        return await extract_paths(paths, max_concurrency, model or CONFIG.model, prompt)

    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def work(idx: int, path: Path) -> ImageResult:
        async with sem:
            try:
                md = await _extract_single_with_engine(
                    path=path,
                    prompt=prompt,
                    model=model,
                    engine=eng,
                    device=device,
                    local_ai_model=local_ai_model,
                )
                return ImageResult(index=idx, file=path.name, ok=True, markdown=md)
            except Exception as e:  # noqa: BLE001
                return ImageResult(index=idx, file=path.name, ok=False, error=str(e))

    results = await asyncio.gather(*(work(i, p) for i, p in enumerate(paths)))
    return sorted(results, key=lambda r: r.index)


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
    engine: Annotated[
        str,
        Field(
            description="OCR/Vision engine: 'auto' | 'vision' | 'paddle' | 'ollama' | 'vllm' | 'lmstudio' | 'llamacpp' | 'hybrid_local'"
        ),
    ] = "auto",
    device: Annotated[
        str,
        Field(description="Compute device: 'auto' | 'gpu' | 'cuda:0' | 'mps' | 'cpu'"),
    ] = "auto",
    local_ai_model: Annotated[
        Optional[str],
        Field(description="Optional local vision model name for ollama/vllm/lmstudio/llamacpp/hybrid_local"),
    ] = None,
) -> dict:
    """Extract text from all images in a folder and format as Markdown, sorted naturally by filename.

    Processes images in parallel (up to max_concurrency) and aggregates the results in the correct order.
    Supports cloud vision, local PaddleOCR (GPU/CPU), local AI servers (Ollama/vLLM/LM Studio/llama.cpp),
    and hybrid_local mode.
    """
    eng = (engine or "auto").strip().lower()
    if (
        eng in ("auto", "vision")
        and device == "auto"
        and local_ai_model is None
        and CONFIG.provider in ("gemini", "openai_compatible")
        and bool(CONFIG.api_key)
        and (eng == "vision" or CONFIG.ocr_engine in ("auto", "vision"))
    ):
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

    folder = Path(folder_path).expanduser()
    if not folder.is_dir():
        raise ValueError(f"Khong tim thay thu muc: {folder}")

    paths = list_images(folder, recursive=recursive, exts=IMAGE_EXTS)
    if not paths:
        raise ValueError(f"Khong co anh hop le trong: {folder}")

    prompt = prompt_override or DEFAULT_PROMPT
    results = await _extract_paths_with_engine(
        paths=paths,
        max_concurrency=max_concurrency,
        model=model,
        prompt=prompt,
        engine=eng,
        device=device,
        local_ai_model=local_ai_model,
    )

    if output_mode == "per_image":
        out_dir = Path(output_path).expanduser() if output_path else folder
        written = []
        for r in results:
            target = out_dir / (Path(r.file).stem + ".md")
            body = (r.markdown or "") if r.ok else f"> Loi: {r.error}"
            write_text(target, body)
            written.append(str(target))
        out_info: str | list[str] = written
    else:
        target = Path(output_path).expanduser() if output_path else (folder / "output.md")
        write_text(target, build_combined_md(results))
        out_info = str(target)

    return {
        "total": len(results),
        "succeeded": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
        "output": out_info,
        "order": [r.file for r in results],
        "engine": eng,
        "device": device,
        "items": [r.model_dump(exclude={"markdown"}) for r in results],
    }


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
    """List images in natural sorted order within a directory (does not make LLM calls)."""
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
    engine: Annotated[
        str,
        Field(
            description="OCR/Vision engine: 'auto' | 'vision' | 'paddle' | 'ollama' | 'vllm' | 'lmstudio' | 'llamacpp' | 'hybrid_local'"
        ),
    ] = "auto",
    device: Annotated[
        str,
        Field(description="Compute device: 'auto' | 'gpu' | 'cuda:0' | 'mps' | 'cpu'"),
    ] = "auto",
    local_ai_model: Annotated[
        Optional[str],
        Field(description="Optional local vision model name for ollama/vllm/lmstudio/llamacpp/hybrid_local"),
    ] = None,
) -> dict:
    """Extract text from a single image and return it as Markdown (optionally saves to a file)."""
    p = Path(image_path).expanduser()
    if not p.is_file():
        raise ValueError(f"Image file not found: {p}")

    results = await _extract_paths_with_engine(
        paths=[p],
        max_concurrency=1,
        model=model,
        prompt=prompt_override or DEFAULT_PROMPT,
        engine=engine,
        device=device,
        local_ai_model=local_ai_model,
    )
    r = results[0]
    if not r.ok:
        raise RuntimeError(f"Extraction failed: {r.error}")
    if output_path:
        write_text(Path(output_path).expanduser(), r.markdown or "")
    return {
        "file": r.file,
        "markdown": r.markdown,
        "engine": engine,
        "device": device,
    }


def main() -> None:
    mcp.run()  # stdio (suitable for Claude Desktop/local)


if __name__ == "__main__":
    main()
