"""
Document Versioning (v1.0 -> v1.1) & Pre-OCR Page/Section Delta Engine.

Provides content-addressed unit extraction (PDF pages, PPTX slides, Excel sheets,
Markdown/DOCX sections, CSV, images), zero-OCR / zero-embedding reuse via vector
cloning for unchanged units, same-branch superseding with old memory cleanup,
cross-branch overlay isolation with tombstones, and version listing/diffing.
"""

from __future__ import annotations

import csv
import hashlib
import inspect
import io
import json
import logging
import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from ..core.db import (
    db,
    ensure_doc_versioning_tables,
    get_memory_branch_lineage,
    get_memory_tombstones,
    q,
    record_memory_tombstone,
    register_memory_branch,
)
from ..core.tenant_manager import (
    _effective_root_branch,
    get_current_source,
    parse_tenant_from_user_id,
    parse_tenant_spec,
    tenant_context,
    tenant_manager,
)
from ..core.vector_store import vector_store
from ..memory import hsg as _hsg_mod
from ..utils import doc_parser
from ..utils.chunking import chunk_text

logger = logging.getLogger(__name__)

TEXT_DOC_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".rst", ".adoc",
    ".json", ".yml", ".yaml", ".ini", ".conf",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"}


def derive_doc_key(filename: str, explicit_doc_key: Optional[str] = None) -> str:
    """Derive a canonical doc_key slug from explicit_doc_key or filename stem."""
    if explicit_doc_key and str(explicit_doc_key).strip():
        slug = re.sub(r"[^a-z0-9._-]+", "_", str(explicit_doc_key).strip().lower()).strip("_")
        return slug or "document"

    stem = Path(filename or "document").stem
    stripped = re.sub(
        r"(?:[._-]+(?:v|ver|version)\d+(?:\.\d+)*|[._-]+v\d+(?:\.\d+)*)$",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    target = stripped.strip() or stem.strip()
    slug = re.sub(r"[^a-z0-9_-]+", "_", target.lower()).strip("_")
    return slug or "document"


def _ctx_source_arg(eff_source: Optional[str]) -> Optional[str]:
    """Convert normalized effective source_id into `tenant_context(..., source_id=...)` arg."""
    if not eff_source or eff_source == "__default__":
        return None
    return eff_source


def _resolve_doc_target(
    project_id: str,
    branch: Optional[str] = "main",
    source_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> Tuple[str, str, str, str, str]:
    """Resolve `(target_proj, target_branch, effective_user_id, effective_source_id, effective_scope)`."""
    raw_proj = (project_id or "default").strip() or "default"
    raw_branch = (branch or "").strip()
    eff_default_br = raw_branch or "main"
    arg_src = str(source_id).strip() or None if source_id is not None else None

    proj, parsed_src, parsed_br = parse_tenant_spec(
        raw_proj,
        default_branch=eff_default_br,
        default_source=arg_src,
    )
    if arg_src is not None and "@" not in raw_proj:
        parsed_src = arg_src

    if ":" in raw_proj and raw_branch and raw_branch != "main":
        clean_branch = raw_branch
    else:
        clean_branch = parsed_br

    clean_scope = (scope or "").strip()
    if clean_scope == "project_shared" or parsed_src == "__shared__" or arg_src == "__shared__":
        eff_source = "__shared__"
        eff_scope = "project_shared"
        effective_user_id = f"{proj}@__shared__:{clean_branch}"
    elif parsed_src and parsed_src != "__default__":
        eff_source = parsed_src
        eff_scope = "source"
        effective_user_id = f"{proj}@{eff_source}:{clean_branch}"
    else:
        eff_source = "__default__"
        eff_scope = "source"
        if ":" in raw_proj:
            effective_user_id = f"{proj}:{clean_branch}"
        else:
            effective_user_id = (
                proj if clean_branch == "main" else f"{proj}:{clean_branch}"
            )
    return proj, clean_branch, effective_user_id, eff_source, eff_scope


def _get_doc_branch_lineage(
    project_id: str,
    branch: str,
    effective_source: str = "__default__",
    max_depth: int = 8,
) -> List[str]:
    """Return branch lineage `[branch, ..., root]` scoped to project root or a sub-source."""
    if effective_source in ("__default__", "__shared__"):
        return get_memory_branch_lineage(project_id, branch, max_depth=max_depth)

    br = (branch or "").strip()
    if not br:
        return []
    lineage: List[str] = [br]
    visited = {br}
    cur = br
    while len(lineage) <= max_depth:
        if not tenant_manager._master_table_exists("master_source_branches"):
            break
        row = tenant_manager._master_fetchone(
            "SELECT base_branch FROM master_source_branches "
            "WHERE project_id=? AND source_id=? AND branch_name=?",
            (project_id, effective_source, cur),
        )
        parent = str(row["base_branch"]).strip() if row and row.get("base_branch") else None
        if not parent or parent == cur or parent in visited:
            break
        lineage.append(parent)
        visited.add(parent)
        cur = parent
    return lineage


def resolve_doc_user_id(
    project_id: str,
    branch: Optional[str] = "main",
    source_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Resolve `(base_project, clean_branch, effective_user_id)`."""
    target_proj, target_branch, effective_user_id, _, _ = _resolve_doc_target(
        project_id,
        branch=branch,
        source_id=source_id,
        scope=scope,
    )
    return target_proj, target_branch, effective_user_id


def next_doc_version(
    project_id: str,
    branch: str,
    doc_key: str,
    base_branch: Optional[str] = None,
    source_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> str:
    """Compute the next sequential version string (v1, v2, ...) for a doc_key."""
    ensure_doc_versioning_tables()
    target_proj, target_branch, _, eff_source, _ = _resolve_doc_target(
        project_id,
        branch=branch,
        source_id=source_id,
        scope=scope,
    )
    ctx_src = _ctx_source_arg(eff_source)
    is_child_src = eff_source not in ("__default__", "__shared__")

    rows: List[Dict[str, Any]] = []
    if not is_child_src or tenant_manager.has_branch(target_proj, target_branch, source_id=ctx_src):
        with tenant_context(target_proj, target_branch, source_id=ctx_src):
            rows = db.fetchall(
                "SELECT version FROM document_versions WHERE project_id=? AND branch=? AND doc_key=?",
                (target_proj, target_branch, doc_key),
            )
    if not rows:
        candidate_branches: List[str] = []
        if base_branch and base_branch != target_branch:
            candidate_branches.append(base_branch)
        for b in _get_doc_branch_lineage(target_proj, target_branch, eff_source):
            if b != target_branch and b not in candidate_branches:
                candidate_branches.append(b)
        for cand_br in candidate_branches:
            if is_child_src and not tenant_manager.has_branch(target_proj, cand_br, source_id=ctx_src):
                continue
            with tenant_context(target_proj, cand_br, source_id=ctx_src):
                cand_rows = db.fetchall(
                    "SELECT version FROM document_versions WHERE project_id=? AND branch=? AND doc_key=?",
                    (target_proj, cand_br, doc_key),
                )
            if cand_rows:
                rows = cand_rows
                break

    if not rows:
        return "v1"

    max_num = 0
    for r in rows:
        ver = str(r.get("version") or "")
        m = re.search(r"(\d+)", ver)
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num
    return f"v{max_num + 1}"


def split_markdown_sections(markdown_content: str) -> List[Dict[str, Any]]:
    """Split Markdown by #/##/### headings outside fenced code blocks into hashed section units."""
    if not markdown_content or not markdown_content.strip():
        return []

    lines = markdown_content.splitlines()
    sections: List[Tuple[str, List[str]]] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []
    in_fence = False
    found_heading = False

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            current_lines.append(line)
            continue

        if not in_fence:
            m = re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
            if m:
                found_heading = True
                if current_lines and any(l.strip() for l in current_lines):
                    sections.append((current_title or "Document", current_lines))
                current_title = m.group(2).strip()
                current_lines = [line]
                continue

        current_lines.append(line)

    if current_lines and any(l.strip() for l in current_lines):
        sections.append((current_title or "Document", current_lines))

    if not found_heading:
        clean_full = markdown_content.strip()
        if not clean_full:
            return []
        return [
            {
                "unit_index": 0,
                "unit_type": "section",
                "unit_title": "Document",
                "unit_sha256": hashlib.sha256(clean_full.encode("utf-8")).hexdigest(),
                "markdown_text": clean_full,
            }
        ]

    units: List[Dict[str, Any]] = []
    for title, sec_lines in sections:
        sec_text = "\n".join(sec_lines).strip()
        if not sec_text:
            continue
        units.append(
            {
                "unit_index": len(units),
                "unit_type": "section",
                "unit_title": title,
                "unit_sha256": hashlib.sha256(sec_text.encode("utf-8")).hexdigest(),
                "markdown_text": sec_text,
            }
        )
    return units


def _format_csv_to_markdown(raw_bytes: bytes) -> str:
    text = raw_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not rows:
        return "_Empty CSV Document_\n"

    col_count = max(len(r) for r in rows)

    def clean_cell(val: str) -> str:
        return val.replace("|", "\\|").replace("\n", " ").strip()

    lines = []
    header = [clean_cell(rows[0][i]) if i < len(rows[0]) else "" for i in range(col_count)]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for r in rows[1:]:
        row_cells = [clean_cell(r[i]) if i < len(r) else "" for i in range(col_count)]
        lines.append("| " + " | ".join(row_cells) + " |")
    return "\n".join(lines) + "\n"


async def extract_raw_units(filename: str, file_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Extract content-addressed candidate units WITHOUT eagerly running OCR.

    Each returned item contains:
      - unit_index: int
      - unit_type: str ("page" | "slide" | "sheet" | "section" | "image")
      - unit_title: str
      - unit_sha256: str
      - pre_extracted_text: Optional[str]
      - parse_fn: Optional[Callable[[], Awaitable[str]]]
    """
    ext = Path(filename or "").suffix.lower()

    if ext in TEXT_DOC_EXTENSIONS:
        text = file_bytes.decode("utf-8", errors="replace")
        return [
            {
                "unit_index": u["unit_index"],
                "unit_type": u["unit_type"],
                "unit_title": u["unit_title"],
                "unit_sha256": u["unit_sha256"],
                "pre_extracted_text": u["markdown_text"],
                "parse_fn": None,
            }
            for u in split_markdown_sections(text)
        ]

    if ext == ".csv":
        unit_sha = hashlib.sha256(file_bytes).hexdigest()

        async def _parse_csv() -> str:
            return _format_csv_to_markdown(file_bytes)

        return [
            {
                "unit_index": 0,
                "unit_type": "sheet",
                "unit_title": Path(filename).stem or "CSV",
                "unit_sha256": unit_sha,
                "pre_extracted_text": None,
                "parse_fn": _parse_csv,
            }
        ]

    if ext == ".docx":
        md = await doc_parser.docx_to_markdown(file_bytes)
        return [
            {
                "unit_index": u["unit_index"],
                "unit_type": u["unit_type"],
                "unit_title": u["unit_title"],
                "unit_sha256": u["unit_sha256"],
                "pre_extracted_text": u["markdown_text"],
                "parse_fn": None,
            }
            for u in split_markdown_sections(md)
        ]

    if ext in (".xlsx", ".xls"):
        try:
            sheets = doc_parser.inspect_excel_sheets_raw(file_bytes)
            units: List[Dict[str, Any]] = []
            for s in sheets:
                sheet_name = s["unit_title"]
                raw_rows = s["raw_rows"]

                async def _parse_sheet(sn=sheet_name, rr=raw_rows) -> str:
                    return doc_parser.format_excel_sheet_markdown(sn, rr)

                units.append(
                    {
                        "unit_index": s["unit_index"],
                        "unit_type": "sheet",
                        "unit_title": sheet_name,
                        "unit_sha256": s["unit_sha256"],
                        "pre_extracted_text": None,
                        "parse_fn": _parse_sheet,
                    }
                )
            return units
        except Exception as e:
            logger.debug("inspect_excel_sheets_raw failed for %s (%s); falling back to parse_document", filename, e)
            md = await doc_parser.parse_document(filename, file_bytes)
            return [
                {
                    "unit_index": u["unit_index"],
                    "unit_type": u["unit_type"],
                    "unit_title": u["unit_title"],
                    "unit_sha256": u["unit_sha256"],
                    "pre_extracted_text": u["markdown_text"],
                    "parse_fn": None,
                }
                for u in split_markdown_sections(md)
            ]

    if ext == ".pptx":
        try:
            slides = doc_parser.inspect_pptx_slides_pre_ocr(file_bytes)
            units = []
            for s in slides:
                async def _parse_slide(slide_info=s) -> str:
                    return await doc_parser.parse_pptx_slide_markdown(slide_info)

                units.append(
                    {
                        "unit_index": s["unit_index"],
                        "unit_type": "slide",
                        "unit_title": s["unit_title"],
                        "unit_sha256": s["unit_sha256"],
                        "pre_extracted_text": None,
                        "parse_fn": _parse_slide,
                    }
                )
            return units
        except Exception as e:
            logger.debug("inspect_pptx_slides_pre_ocr failed for %s (%s); falling back to parse_document", filename, e)
            md = await doc_parser.parse_document(filename, file_bytes)
            return [
                {
                    "unit_index": u["unit_index"],
                    "unit_type": u["unit_type"],
                    "unit_title": u["unit_title"],
                    "unit_sha256": u["unit_sha256"],
                    "pre_extracted_text": u["markdown_text"],
                    "parse_fn": None,
                }
                for u in split_markdown_sections(md)
            ]

    if ext == ".pdf":
        try:
            pages = doc_parser.inspect_pdf_pages_pre_ocr(file_bytes)
            if pages:
                units = []
                for p in pages:
                    page_idx = p["page_index"]

                    async def _parse_page(idx=page_idx) -> str:
                        return await doc_parser.parse_pdf_page_markdown(file_bytes, idx)

                    units.append(
                        {
                            "unit_index": page_idx,
                            "unit_type": "page",
                            "unit_title": p["unit_title"],
                            "unit_sha256": p["unit_sha256"],
                            "pre_extracted_text": None,
                            "parse_fn": _parse_page,
                        }
                    )
                return units
        except Exception as e:
            logger.debug("inspect_pdf_pages_pre_ocr failed for %s (%s); falling back to parse_document", filename, e)

        md = await doc_parser.parse_document(filename, file_bytes)
        return [
            {
                "unit_index": u["unit_index"],
                "unit_type": u["unit_type"],
                "unit_title": u["unit_title"],
                "unit_sha256": u["unit_sha256"],
                "pre_extracted_text": u["markdown_text"],
                "parse_fn": None,
            }
            for u in split_markdown_sections(md)
        ]

    if ext in IMAGE_EXTENSIONS:
        img_name = Path(filename).name
        mime_type, _ = mimetypes.guess_type(img_name)
        mime_type = mime_type or "image/png"
        unit_sha = hashlib.sha256(file_bytes).hexdigest()

        async def _parse_image() -> str:
            try:
                ocr_res = await doc_parser.call_ocr_if_available(file_bytes, mime_type)
                if ocr_res and ocr_res.strip() and ocr_res.strip() != "![Image]":
                    return f"# Image: {img_name}\n\n{ocr_res.strip()}\n"
            except Exception as exc:
                logger.debug("Image OCR extraction failed for %s: %s", img_name, exc)
            return f"# Image: {img_name}\n\n![{img_name}]({img_name})\n"

        return [
            {
                "unit_index": 0,
                "unit_type": "image",
                "unit_title": img_name,
                "unit_sha256": unit_sha,
                "pre_extracted_text": None,
                "parse_fn": _parse_image,
            }
        ]

    # Generic text fallback
    text = file_bytes.decode("utf-8", errors="replace")
    return [
        {
            "unit_index": u["unit_index"],
            "unit_type": u["unit_type"],
            "unit_title": u["unit_title"],
            "unit_sha256": u["unit_sha256"],
            "pre_extracted_text": u["markdown_text"],
            "parse_fn": None,
        }
        for u in split_markdown_sections(text)
    ]


async def clone_memory_with_vectors(
    old_memory_id: str,
    target_user_id: str,
    new_meta: Dict[str, Any],
    source_project_id: Optional[str] = None,
    source_branch: Optional[str] = None,
    source_id: Optional[str] = None,
    scope: Optional[str] = None,
    doc_role: Optional[str] = None,
) -> Optional[str]:
    """Clone an existing memory row and all its vectors with ZERO LLM/embedding calls."""
    if source_project_id is not None:
        src_proj, inf_src_src, inf_src_br = parse_tenant_spec(
            source_project_id,
            default_branch=source_branch or _effective_root_branch(),
            default_source=source_id,
        )
        src_br = (source_branch or inf_src_br or _effective_root_branch()).strip() or _effective_root_branch()
        src_ctx = _ctx_source_arg(inf_src_src)
        with tenant_context(src_proj, src_br, source_id=src_ctx):
            old = q.get_mem(old_memory_id)
            if not old:
                return None
            vectors = await vector_store.getVectorsById(old_memory_id)
    else:
        with tenant_context(None):
            old = q.get_mem(old_memory_id)
            if not old:
                return None
            hint = tenant_manager.lookup_memory_hint(old_memory_id)
            hint_src = tenant_manager.lookup_memory_source_hint(old_memory_id)
            if hint is not None:
                with tenant_context(hint[0], hint[1], source_id=hint_src):
                    vectors = await vector_store.getVectorsById(old_memory_id)
            else:
                vectors = await vector_store.getVectorsById(old_memory_id)

    eff_uid = target_user_id or old.get("user_id") or "anonymous"
    target_proj, inf_tgt_src, target_br = parse_tenant_spec(
        eff_uid,
        default_branch=_effective_root_branch(),
        default_source=source_id,
    )
    if isinstance(new_meta, dict) and new_meta.get("project_id"):
        target_proj = parse_tenant_spec(str(new_meta["project_id"]))[0]
        if new_meta.get("branch"):
            target_br = str(new_meta["branch"]).strip() or target_br

    meta_src = new_meta.get("source_id") if isinstance(new_meta, dict) else None
    meta_scope = new_meta.get("scope") if isinstance(new_meta, dict) else None
    meta_role = new_meta.get("doc_role") if isinstance(new_meta, dict) else None

    tgt_eff_src = (
        str(meta_src).strip()
        if meta_src is not None and str(meta_src).strip()
        else (inf_tgt_src or (old.get("source_id") if old.get("source_id") else "__default__"))
    )
    tgt_eff_scope = (
        str(meta_scope).strip()
        if meta_scope is not None and str(meta_scope).strip()
        else (
            "project_shared"
            if (tgt_eff_src == "__shared__" or scope == "project_shared")
            else (str(old.get("scope") or "").strip() or "source")
        )
    )
    if tgt_eff_scope == "project_shared":
        tgt_eff_src = "__shared__"
    tgt_eff_role = (
        str(meta_role).strip()
        if meta_role is not None and str(meta_role).strip()
        else (
            str(doc_role).strip()
            if doc_role is not None and str(doc_role).strip()
            else (str(old.get("doc_role") or "").strip() or "general")
        )
    )
    tgt_ctx = _ctx_source_arg(tgt_eff_src)

    now_ms = int(time.time() * 1000)

    merged_meta: Dict[str, Any] = {}
    raw_old_meta = old.get("meta")
    if isinstance(raw_old_meta, str):
        try:
            parsed_old = json.loads(raw_old_meta)
            if isinstance(parsed_old, dict):
                merged_meta.update(parsed_old)
        except Exception:
            pass
    elif isinstance(raw_old_meta, dict):
        merged_meta.update(raw_old_meta)
    merged_meta.update(new_meta)

    new_id = str(uuid.uuid4())
    with tenant_context(target_proj, target_br, source_id=tgt_ctx):
        if target_user_id:
            db.execute(
                "INSERT OR IGNORE INTO users(user_id, summary, reflection_count, created_at, updated_at) VALUES (?,?,?,?,?)",
                (target_user_id, "User profile initializing...", 0, now_ms, now_ms),
            )
            db.commit()

        q.ins_mem(
            id=new_id,
            user_id=eff_uid,
            segment=old.get("segment", 0),
            content=old.get("content"),
            simhash=old.get("simhash"),
            primary_sector=old.get("primary_sector"),
            tags=old.get("tags"),
            meta=json.dumps(merged_meta),
            created_at=now_ms,
            updated_at=now_ms,
            last_seen_at=now_ms,
            salience=old.get("salience", 1.0),
            decay_lambda=old.get("decay_lambda", 0.02),
            version=old.get("version", 1),
            mean_dim=old.get("mean_dim"),
            mean_vec=old.get("mean_vec"),
            compressed_vec=old.get("compressed_vec"),
            feedback_score=old.get("feedback_score", 0),
        )
        db.execute(
            "UPDATE memories SET source_id=?, scope=?, doc_role=? WHERE id=?",
            (tgt_eff_src, tgt_eff_scope, tgt_eff_role, new_id),
        )
        db.commit()

        for vr in vectors:
            await vector_store.storeVector(new_id, vr.sector, vr.vector, vr.dim, eff_uid)
        tenant_manager.remember_memory_id(
            new_id, target_proj, target_br, source_id=tgt_ctx
        )

    return new_id


async def _finalize_ingested_memory(
    mid: str,
    target_proj: str,
    target_branch: str,
    effective_user_id: str,
    effective_source: str,
    effective_scope: str,
    doc_role: str,
) -> None:
    """Ensure ingested memory resides in target source schema and records source_id/scope/doc_role."""
    ctx_src = _ctx_source_arg(effective_source)
    with tenant_context(target_proj, target_branch, source_id=ctx_src):
        existing_row = q.get_mem(mid)

    if existing_row is None and effective_source not in ("__default__", "__shared__"):
        with tenant_context(target_proj, target_branch, source_id="__default__"):
            src_row = q.get_mem(mid)
            if src_row is not None:
                vecs = await vector_store.getVectorsById(mid)
                wps = db.fetchall(
                    "SELECT * FROM waypoints WHERE src_id=? OR dst_id=?",
                    (mid, mid),
                )
                user_row = db.fetchone(
                    "SELECT * FROM users WHERE user_id=?",
                    (effective_user_id,),
                )
                await vector_store.deleteVectors(mid)
                q.del_mem(mid)
            else:
                vecs = []
                wps = []
                user_row = None

        if src_row is not None:
            with tenant_context(target_proj, target_branch, source_id=ctx_src):
                if user_row is not None:
                    db.execute(
                        "INSERT OR IGNORE INTO users(user_id, summary, reflection_count, created_at, updated_at) "
                        "VALUES (?,?,?,?,?)",
                        (
                            user_row.get("user_id") or effective_user_id,
                            user_row.get("summary") or "User profile initializing...",
                            user_row.get("reflection_count", 0),
                            user_row.get("created_at") or int(time.time() * 1000),
                            user_row.get("updated_at") or int(time.time() * 1000),
                        ),
                    )
                elif effective_user_id:
                    now_ms = int(time.time() * 1000)
                    db.execute(
                        "INSERT OR IGNORE INTO users(user_id, summary, reflection_count, created_at, updated_at) "
                        "VALUES (?,?,?,?,?)",
                        (effective_user_id, "User profile initializing...", 0, now_ms, now_ms),
                    )
                q.ins_mem(
                    id=src_row["id"],
                    user_id=src_row.get("user_id") or effective_user_id,
                    segment=src_row.get("segment", 0),
                    content=src_row.get("content"),
                    simhash=src_row.get("simhash"),
                    primary_sector=src_row.get("primary_sector"),
                    tags=src_row.get("tags"),
                    meta=src_row.get("meta"),
                    created_at=src_row.get("created_at"),
                    updated_at=src_row.get("updated_at"),
                    last_seen_at=src_row.get("last_seen_at"),
                    salience=src_row.get("salience", 1.0),
                    decay_lambda=src_row.get("decay_lambda", 0.02),
                    version=src_row.get("version", 1),
                    mean_dim=src_row.get("mean_dim"),
                    mean_vec=src_row.get("mean_vec"),
                    compressed_vec=src_row.get("compressed_vec"),
                    feedback_score=src_row.get("feedback_score", 0),
                )
                for vr in vecs:
                    await vector_store.storeVector(
                        mid, vr.sector, vr.vector, vr.dim, effective_user_id
                    )
                for wp in wps:
                    db.execute(
                        "INSERT OR IGNORE INTO waypoints(src_id, dst_id, dst_sector, user_id, weight, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            wp.get("src_id"),
                            wp.get("dst_id"),
                            wp.get("dst_sector"),
                            wp.get("user_id") or effective_user_id,
                            wp.get("weight", 1.0),
                            wp.get("created_at"),
                            wp.get("updated_at"),
                        ),
                    )
                db.commit()

    with tenant_context(target_proj, target_branch, source_id=ctx_src):
        db.execute(
            "UPDATE memories SET source_id=?, scope=?, doc_role=? WHERE id=?",
            (effective_source, effective_scope, doc_role, mid),
        )
        db.commit()
    tenant_manager.remember_memory_id(
        mid, target_proj, target_branch, source_id=ctx_src
    )


def _resolve_parent_version(
    project_id: str,
    branch: str,
    doc_key: str,
    base_version: Optional[str] = None,
    base_branch: Optional[str] = None,
    source_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve (parent_version, parent_branch) for delta reuse."""
    target_proj, target_branch, _, eff_source, _ = _resolve_doc_target(
        project_id,
        branch=branch,
        source_id=source_id,
        scope=scope,
    )
    ctx_src = _ctx_source_arg(eff_source)
    is_child_src = eff_source not in ("__default__", "__shared__")
    lineage = _get_doc_branch_lineage(target_proj, target_branch, eff_source)
    search_branches: List[str] = []
    if base_version:
        if base_branch:
            search_branches.append(base_branch)
        if target_branch not in search_branches:
            search_branches.append(target_branch)
        for b in lineage:
            if b not in search_branches:
                search_branches.append(b)
        for b in search_branches:
            if is_child_src and not tenant_manager.has_branch(target_proj, b, source_id=ctx_src):
                continue
            with tenant_context(target_proj, b, source_id=ctx_src):
                row = db.fetchone(
                    "SELECT version, branch FROM document_versions "
                    "WHERE project_id=? AND branch=? AND doc_key=? AND version=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (target_proj, b, doc_key, base_version),
                )
            if row:
                return row["version"], row["branch"]
        return base_version, (base_branch or target_branch)

    # No explicit base_version: check active version on target branch first
    if not is_child_src or tenant_manager.has_branch(target_proj, target_branch, source_id=ctx_src):
        with tenant_context(target_proj, target_branch, source_id=ctx_src):
            row = db.fetchone(
                "SELECT version, branch FROM document_versions "
                "WHERE project_id=? AND branch=? AND doc_key=? AND status='active' "
                "ORDER BY created_at DESC LIMIT 1",
                (target_proj, target_branch, doc_key),
            )
        if row:
            return row["version"], row["branch"]

    # Then check explicit base_branch and registered branch lineage
    fallback_branches: List[str] = []
    if base_branch and base_branch != target_branch:
        fallback_branches.append(base_branch)
    for b in lineage:
        if b != target_branch and b not in fallback_branches:
            fallback_branches.append(b)

    for b in fallback_branches:
        if is_child_src and not tenant_manager.has_branch(target_proj, b, source_id=ctx_src):
            continue
        with tenant_context(target_proj, b, source_id=ctx_src):
            row = db.fetchone(
                "SELECT version, branch FROM document_versions "
                "WHERE project_id=? AND branch=? AND doc_key=? AND status='active' "
                "ORDER BY created_at DESC LIMIT 1",
                (target_proj, b, doc_key),
            )
        if row:
            return row["version"], row["branch"]

    return None, None


def lookup_parent_units_by_hash(
    project_id: str,
    branch: str,
    doc_key: str,
    base_version: Optional[str] = None,
    base_branch: Optional[str] = None,
    source_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Dict[str, List[Dict[str, Any]]]]:
    """
    Look up parent version's units keyed by unit_sha256 (FIFO queue per hash).
    Used by both ingest_document_versioned and DocumentPipeline pre-OCR reuse.
    """
    ensure_doc_versioning_tables()
    target_proj, target_branch, _, eff_source, _ = _resolve_doc_target(
        project_id,
        branch=branch,
        source_id=source_id,
        scope=scope,
    )
    ctx_src = _ctx_source_arg(eff_source)
    parent_version, parent_branch = _resolve_parent_version(
        project_id=target_proj,
        branch=target_branch,
        doc_key=doc_key,
        base_version=base_version,
        base_branch=base_branch,
        source_id=ctx_src,
        scope=scope,
    )
    by_hash: Dict[str, List[Dict[str, Any]]] = {}
    if parent_version and parent_branch:
        with tenant_context(target_proj, parent_branch, source_id=ctx_src):
            rows = db.fetchall(
                "SELECT * FROM document_units "
                "WHERE project_id=? AND branch=? AND doc_key=? AND version=? "
                "ORDER BY unit_index ASC",
                (target_proj, parent_branch, doc_key, parent_version),
            )
        for r in rows:
            by_hash.setdefault(r["unit_sha256"], []).append(dict(r))
    return parent_version, parent_branch, by_hash


async def ingest_document_versioned(
    filename: str,
    file_bytes: Optional[bytes] = None,
    markdown_content: Optional[str] = None,
    chunks: Optional[List[Dict[str, Any]]] = None,
    precomputed_units: Optional[List[Dict[str, Any]]] = None,
    project_id: str = "default",
    branch: str = "main",
    author: Optional[str] = None,
    tags: Optional[List[str]] = None,
    doc_key: Optional[str] = None,
    doc_version: Optional[str] = None,
    base_version: Optional[str] = None,
    base_branch: Optional[str] = None,
    source_id: Optional[str] = None,
    scope: str = "source",
    doc_role: str = "general",
) -> Dict[str, Any]:
    """
    Versioned document ingestion with Pre-OCR & Pre-Embedding Unit Delta Engine.

    - Reuses unchanged units (matching unit_sha256 in parent version) with 0 OCR calls
      and 0 embedding calls by cloning existing vectors.
    - Runs OCR & embedding ONLY on missed/new units.
    - Same-branch update supersedes prior active version and cleans up old memories.
    - Cross-branch update preserves parent branch intact and records branch overlay
      link + doc tombstone.
    - Multi-source aware: routes child sub-sources (`source_id`) to `pb_src_*` and
      project-shared docs (`scope='project_shared'` or `source_id='__shared__'`)
      to `pb_root` with `master_shared_doc_bindings` registration.
    """
    ensure_doc_versioning_tables()
    (
        target_proj,
        target_branch,
        effective_user_id,
        effective_source,
        effective_scope,
    ) = _resolve_doc_target(
        project_id,
        branch=branch,
        source_id=source_id,
        scope=scope,
    )
    eff_doc_role = (doc_role or "general").strip() or "general"
    ctx_src = _ctx_source_arg(effective_source)
    is_child_src = effective_source not in ("__default__", "__shared__")
    d_key = derive_doc_key(filename, doc_key)

    parent_version, parent_branch, parent_by_hash = lookup_parent_units_by_hash(
        project_id=target_proj,
        branch=target_branch,
        doc_key=d_key,
        base_version=base_version,
        base_branch=base_branch,
        source_id=ctx_src,
        scope=effective_scope,
    )

    effective_base_branch = base_branch or (
        parent_branch if (parent_branch and parent_branch != target_branch) else None
    )
    d_ver = (
        doc_version.strip()
        if (doc_version and str(doc_version).strip())
        else next_doc_version(
            target_proj,
            target_branch,
            d_key,
            base_branch=effective_base_branch,
            source_id=ctx_src,
            scope=effective_scope,
        )
    )
    has_explicit_base = bool(
        effective_base_branch and effective_base_branch != target_branch
    )
    if is_child_src:
        if tenant_manager.get_project_source(target_proj, effective_source) is None:
            tenant_manager.register_project_source(
                target_proj,
                effective_source,
                root_branch=_effective_root_branch(),
            )
        proj_meta = tenant_manager.ensure_source_branch_schema(
            target_proj,
            effective_source,
            target_branch,
            base_branch=effective_base_branch or _effective_root_branch(),
            author=author or "system",
            has_doc=True,
        )
    else:
        proj_meta = tenant_manager.ensure_branch_schema(
            target_proj,
            target_branch,
            base_branch=effective_base_branch or _effective_root_branch(),
            author=author or "system",
            has_doc=True,
            record_lineage=has_explicit_base,
            preserve_existing_base=not has_explicit_base,
        )

    with tenant_context(target_proj, target_branch, source_id=ctx_src):
        ensure_doc_versioning_tables()

    # Build raw candidate units
    raw_units: List[Dict[str, Any]] = []
    if precomputed_units is not None:
        for idx, pu in enumerate(precomputed_units):
            md_txt = pu.get("markdown_text")
            sha = pu.get("unit_sha256") or hashlib.sha256(str(md_txt or "").encode("utf-8")).hexdigest()
            raw_units.append(
                {
                    "unit_index": pu.get("unit_index", idx),
                    "unit_type": pu.get("unit_type", "section"),
                    "unit_title": pu.get("unit_title") or f"Unit {idx + 1}",
                    "unit_sha256": sha,
                    "pre_extracted_text": md_txt,
                    "parse_fn": None,
                }
            )
    elif file_bytes is not None:
        raw_units = await extract_raw_units(filename, file_bytes)
    elif markdown_content is not None:
        raw_units = [
            {
                "unit_index": u["unit_index"],
                "unit_type": u["unit_type"],
                "unit_title": u["unit_title"],
                "unit_sha256": u["unit_sha256"],
                "pre_extracted_text": u["markdown_text"],
                "parse_fn": None,
            }
            for u in split_markdown_sections(markdown_content)
        ]

    # Collect all parent memory IDs for potential same-branch cleanup
    all_parent_memory_ids: List[str] = []
    for unit_list in parent_by_hash.values():
        for pu in unit_list:
            try:
                mids = json.loads(pu.get("memory_ids_json") or "[]")
                if isinstance(mids, list):
                    all_parent_memory_ids.extend(str(m) for m in mids if m)
            except Exception:
                pass

    # Plan reuse vs cache-miss BEFORE mutating any memories
    planned_units: List[Dict[str, Any]] = []
    reused_units = 0
    new_units = 0

    for idx, u in enumerate(raw_units):
        u_sha = u["unit_sha256"]
        matched_parent: Optional[Dict[str, Any]] = None
        if u_sha in parent_by_hash and parent_by_hash[u_sha]:
            matched_parent = parent_by_hash[u_sha].pop(0)

        if matched_parent is not None:
            reused_units += 1
            unit_md = matched_parent.get("markdown_text") or ""
            try:
                parent_mids = json.loads(matched_parent.get("memory_ids_json") or "[]")
                if not isinstance(parent_mids, list):
                    parent_mids = []
            except Exception:
                parent_mids = []
            planned_units.append(
                {
                    "unit_index": idx,
                    "unit_type": u.get("unit_type") or matched_parent.get("unit_type") or "section",
                    "unit_title": u.get("unit_title") or matched_parent.get("unit_title") or f"Unit {idx + 1}",
                    "unit_sha256": u_sha,
                    "markdown_text": unit_md,
                    "reused": True,
                    "parent_memory_ids": parent_mids,
                    "memory_ids": [],
                }
            )
        else:
            new_units += 1
            if u.get("pre_extracted_text") is not None:
                unit_md = str(u["pre_extracted_text"])
            elif u.get("parse_fn") is not None:
                parsed = u["parse_fn"]()
                unit_md = await parsed if inspect.isawaitable(parsed) else str(parsed)
            else:
                unit_md = str(u.get("markdown_text") or "")

            planned_units.append(
                {
                    "unit_index": idx,
                    "unit_type": u.get("unit_type") or "section",
                    "unit_title": u.get("unit_title") or f"Unit {idx + 1}",
                    "unit_sha256": u_sha,
                    "markdown_text": unit_md,
                    "reused": False,
                    "parent_memory_ids": [],
                    "memory_ids": [],
                }
            )

    deleted_units = sum(len(rem) for rem in parent_by_hash.values())
    total_units = len(planned_units)

    full_markdown = "\n\n".join(
        p["markdown_text"].strip()
        for p in planned_units
        if p.get("markdown_text") and str(p["markdown_text"]).strip()
    )
    if not full_markdown.strip() and markdown_content and markdown_content.strip():
        full_markdown = markdown_content.strip()

    if not full_markdown.strip() and not chunks and file_bytes is not None:
        raise ValueError("Document content is empty or could not be parsed.")

    if file_bytes is not None:
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    elif markdown_content is not None:
        file_sha256 = hashlib.sha256(markdown_content.encode("utf-8")).hexdigest()
    else:
        file_sha256 = hashlib.sha256(full_markdown.encode("utf-8")).hexdigest()

    tag_list = list(tags) if tags else ["document"]
    if "document" not in tag_list:
        tag_list.append("document")
    tag_json = json.dumps(tag_list)

    # Step 1: Clone vectors of reused units FIRST (before deleting parent memories)
    all_memory_ids: List[str] = []
    units_needing_embed: List[Dict[str, Any]] = []

    for p in planned_units:
        if p["reused"]:
            cloned_ids: List[str] = []
            for old_mid in p["parent_memory_ids"]:
                new_meta = {
                    "source": "document_versioning",
                    "filename": filename,
                    "doc_key": d_key,
                    "doc_version": d_ver,
                    "unit_index": p["unit_index"],
                    "unit_title": p["unit_title"],
                    "unit_type": p["unit_type"],
                    "project_id": target_proj,
                    "branch": target_branch,
                    "source_id": effective_source,
                    "scope": effective_scope,
                    "doc_role": eff_doc_role,
                    "reused_from_version": parent_version,
                    "reused_from_branch": parent_branch,
                }
                if author:
                    new_meta["author"] = author
                cloned_id = await clone_memory_with_vectors(
                    old_mid,
                    effective_user_id,
                    new_meta,
                    source_project_id=target_proj,
                    source_branch=parent_branch,
                    source_id=ctx_src,
                    scope=effective_scope,
                    doc_role=eff_doc_role,
                )
                if cloned_id:
                    cloned_ids.append(cloned_id)
            if cloned_ids:
                p["memory_ids"] = cloned_ids
                all_memory_ids.extend(cloned_ids)
            elif p["markdown_text"].strip():
                # Parent memory row no longer exists in DB; re-embed cleanly
                units_needing_embed.append(p)
        else:
            units_needing_embed.append(p)

    # Step 2: Superseding & Branch Isolation
    same_branch_update = bool(parent_version and parent_branch == target_branch)
    cross_branch_update = bool(
        (parent_branch and parent_branch != target_branch)
        or (base_branch and base_branch != target_branch)
    )

    if same_branch_update:
        with tenant_context(target_proj, target_branch, source_id=ctx_src):
            for old_mid in all_parent_memory_ids:
                await vector_store.deleteVectors(old_mid)
                q.del_mem(old_mid)
            db.execute(
                "UPDATE document_versions SET status='superseded' "
                "WHERE project_id=? AND branch=? AND doc_key=? AND status='active'",
                (target_proj, target_branch, d_key),
            )
            db.commit()
    elif cross_branch_update:
        if not is_child_src:
            if base_branch and base_branch != target_branch:
                register_memory_branch(target_proj, target_branch, base_branch)
            elif parent_branch and parent_branch != target_branch:
                register_memory_branch(target_proj, target_branch, parent_branch)
        with tenant_context(target_proj, target_branch, source_id=ctx_src):
            record_memory_tombstone(target_proj, target_branch, "doc", d_key)

    if len(_get_doc_branch_lineage(target_proj, target_branch, effective_source)) > 1:
        with tenant_context(target_proj, target_branch, source_id=ctx_src):
            record_memory_tombstone(target_proj, target_branch, "doc", d_key)

    # Step 3: Embed missed units (or preserve legacy explicit chunks on initial push-processed)
    use_explicit_chunks = bool(chunks and parent_version is None)
    with tenant_context(target_proj, target_branch, source_id=ctx_src):
        if use_explicit_chunks:
            chunk_mids: List[str] = []
            for idx_c, ch in enumerate(chunks or []):
                ch_text = ch.get("content") or ch.get("text") or ""
                if not ch_text.strip():
                    continue
                ch_tags = ch.get("tags") or tag_list
                if isinstance(ch_tags, list):
                    ch_tag_str = json.dumps(ch_tags)
                else:
                    ch_tag_str = json.dumps([t.strip() for t in str(ch_tags).split(",") if t.strip()])
                ch_meta = {
                    "source": "push-processed",
                    "filename": filename,
                    "sha256": file_sha256,
                    "doc_key": d_key,
                    "doc_version": d_ver,
                    "unit_index": 0,
                    "chunk_id": ch.get("chunk_id") or f"p_{idx_c + 1}",
                    "project_id": target_proj,
                    "branch": target_branch,
                    "source_id": effective_source,
                    "scope": effective_scope,
                    "doc_role": eff_doc_role,
                }
                if author:
                    ch_meta["author"] = author
                res_mem = await _hsg_mod.add_hsg_memory(
                    content=ch_text,
                    tags=ch_tag_str,
                    metadata=ch_meta,
                    user_id=effective_user_id,
                )
                mid = res_mem.get("id")
                if mid:
                    await _finalize_ingested_memory(
                        mid,
                        target_proj,
                        target_branch,
                        effective_user_id,
                        effective_source,
                        effective_scope,
                        eff_doc_role,
                    )
                    chunk_mids.append(mid)
                    all_memory_ids.append(mid)
            if planned_units:
                planned_units[0]["memory_ids"] = list(chunk_mids)
            elif chunk_mids:
                # Record a synthetic unit so future versions can supersede/reuse cleanly
                synth_text = full_markdown or "\n\n".join(
                    (ch.get("content") or ch.get("text") or "") for ch in (chunks or [])
                ).strip()
                planned_units.append(
                    {
                        "unit_index": 0,
                        "unit_type": "section",
                        "unit_title": "Document",
                        "unit_sha256": hashlib.sha256(synth_text.encode("utf-8")).hexdigest(),
                        "markdown_text": synth_text,
                        "reused": False,
                        "memory_ids": list(chunk_mids),
                    }
                )
                total_units = 1
                new_units = 1
        else:
            for p in units_needing_embed:
                unit_md = (p.get("markdown_text") or "").strip()
                if not unit_md:
                    continue
                unit_chunks = chunk_text(unit_md, tgt=768, ovr=0.1)
                unit_mids: List[str] = []
                for c_idx, c in enumerate(unit_chunks):
                    c_text = (c.get("text") or "").strip()
                    if not c_text:
                        continue
                    unit_meta = {
                        "source": "document_versioning",
                        "filename": filename,
                        "sha256": file_sha256,
                        "doc_key": d_key,
                        "doc_version": d_ver,
                        "unit_index": p["unit_index"],
                        "unit_title": p["unit_title"],
                        "unit_type": p["unit_type"],
                        "chunk_index": c_idx,
                        "project_id": target_proj,
                        "branch": target_branch,
                        "source_id": effective_source,
                        "scope": effective_scope,
                        "doc_role": eff_doc_role,
                    }
                    if author:
                        unit_meta["author"] = author
                    res_mem = await _hsg_mod.add_hsg_memory(
                        content=c_text,
                        tags=tag_json,
                        metadata=unit_meta,
                        user_id=effective_user_id,
                    )
                    mid = res_mem.get("id")
                    if mid:
                        await _finalize_ingested_memory(
                            mid,
                            target_proj,
                            target_branch,
                            effective_user_id,
                            effective_source,
                            effective_scope,
                            eff_doc_role,
                        )
                        unit_mids.append(mid)
                        all_memory_ids.append(mid)
                p["memory_ids"] = unit_mids

        # Step 4: Persist document_versions and document_units
        now_ms = int(time.time() * 1000)
        ver_id = str(uuid.uuid4())
        db.execute(
            "INSERT OR REPLACE INTO document_versions("
            "id, project_id, branch, doc_key, version, parent_version, base_branch, "
            "filename, file_sha256, total_units, reused_units, new_units, deleted_units, status, created_at, "
            "source_id, scope, doc_role"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ver_id,
                target_proj,
                target_branch,
                d_key,
                d_ver,
                parent_version,
                effective_base_branch,
                filename,
                file_sha256,
                total_units,
                reused_units,
                new_units,
                deleted_units,
                "active",
                now_ms,
                effective_source,
                effective_scope,
                eff_doc_role,
            ),
        )
        db.execute(
            "UPDATE document_versions SET source_id=?, scope=?, doc_role=? "
            "WHERE project_id=? AND branch=? AND doc_key=? AND version=?",
            (effective_source, effective_scope, eff_doc_role, target_proj, target_branch, d_key, d_ver),
        )

        # Clear any pre-existing units for this exact (project, branch, doc_key, version) if re-written
        db.execute(
            "DELETE FROM document_units WHERE project_id=? AND branch=? AND doc_key=? AND version=?",
            (target_proj, target_branch, d_key, d_ver),
        )
        for p in planned_units:
            unit_id = str(uuid.uuid4())
            db.execute(
                "INSERT OR REPLACE INTO document_units("
                "id, project_id, branch, doc_key, version, unit_index, unit_type, "
                "unit_title, unit_sha256, markdown_text, memory_ids_json, "
                "source_id, scope, doc_role"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    unit_id,
                    target_proj,
                    target_branch,
                    d_key,
                    d_ver,
                    p["unit_index"],
                    p["unit_type"],
                    p["unit_title"],
                    p["unit_sha256"],
                    p["markdown_text"],
                    json.dumps(p.get("memory_ids") or []),
                    effective_source,
                    effective_scope,
                    eff_doc_role,
                ),
            )
        db.commit()

    shared_binding: Optional[Dict[str, Any]] = None
    if effective_scope == "project_shared" or effective_source == "__shared__":
        shared_binding = tenant_manager.bind_shared_doc(
            target_proj,
            d_key,
            target_source_id="*",
            binding_role=eff_doc_role,
        )

    _hsg_mod.invalidate_query_cache()

    res_payload: Dict[str, Any] = {
        "doc_key": d_key,
        "doc_version": d_ver,
        "parent_version": parent_version,
        "base_branch": effective_base_branch,
        "db_name": proj_meta["db_name"],
        "doc_schema": proj_meta["schema_name"],
        "source_id": effective_source,
        "scope": effective_scope,
        "doc_role": eff_doc_role,
        "total_units": total_units,
        "reused_units": reused_units,
        "new_units": new_units,
        "deleted_units": deleted_units,
        "markdown_content": full_markdown,
        "memory_ids": all_memory_ids,
        "memories_created": len(all_memory_ids),
    }
    if shared_binding is not None:
        res_payload["shared_binding"] = shared_binding
    return res_payload


def _tag_doc_row(
    row: Dict[str, Any],
    default_source: str = "__default__",
    default_scope: str = "source",
) -> Dict[str, Any]:
    out = dict(row)
    src_val = str(out.get("source_id") or "").strip() or default_source
    scope_val = str(out.get("scope") or "").strip() or (
        "project_shared" if src_val == "__shared__" else default_scope
    )
    role_val = str(out.get("doc_role") or "").strip() or "general"
    out["source_id"] = src_val
    out["scope"] = scope_val
    out["doc_role"] = role_val
    return out


def _resolve_project_root_branch(project_id: str) -> str:
    if tenant_manager._master_table_exists("master_projects"):
        row = tenant_manager._master_fetchone(
            "SELECT root_branch FROM master_projects WHERE project_id = ?",
            (project_id,),
        )
        if row and row.get("root_branch"):
            return str(row["root_branch"]).strip() or _effective_root_branch()
    return _effective_root_branch()


def _fetch_shared_docs_for_project(
    target_proj: str,
    eff_branch: Optional[str],
    status: str,
) -> List[Dict[str, Any]]:
    """Fetch `project_shared` / `__shared__` docs from `pb_root` (and optional branch)."""
    clauses = [
        "project_id=?",
        "(scope='project_shared' OR source_id='__shared__')",
    ]
    params: List[Any] = [target_proj]
    if status and status != "all":
        clauses.append("status=?")
        params.append(status)
    sql = (
        f"SELECT * FROM document_versions WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at DESC"
    )
    root_br = _resolve_project_root_branch(target_proj)
    candidate_branches: List[str] = []
    if eff_branch:
        if eff_branch != root_br:
            candidate_branches.append(eff_branch)
        candidate_branches.append(root_br)
    else:
        registered = tenant_manager.list_project_branches(target_proj)
        if registered:
            candidate_branches.extend(registered)
        else:
            candidate_branches.append(root_br)

    shared_rows: List[Dict[str, Any]] = []
    for cand_br in candidate_branches:
        if not tenant_manager.has_branch(target_proj, cand_br, source_id="__shared__"):
            continue
        with tenant_context(target_proj, cand_br, source_id="__shared__"):
            for r in db.fetchall(sql, tuple(params)):
                shared_rows.append(
                    _tag_doc_row(
                        r,
                        default_source="__shared__",
                        default_scope="project_shared",
                    )
                )
    return shared_rows


def list_documents(
    project_id: str,
    branch: Optional[str] = None,
    status: str = "active",
    source_id: Optional[str] = None,
    include_shared: bool = True,
) -> List[Dict[str, Any]]:
    """List documents registered in document_versions for a project/branch/source."""
    ensure_doc_versioning_tables()
    raw_proj = (project_id or "default").strip() or "default"
    arg_src = str(source_id).strip() or None if source_id is not None else None
    parsed_proj, parsed_src, parsed_br = parse_tenant_spec(
        raw_proj,
        default_branch=branch or _effective_root_branch(),
        default_source=arg_src,
    )
    if arg_src is not None and "@" not in raw_proj:
        parsed_src = arg_src

    target_proj = parsed_proj
    eff_branch = branch if branch is not None else (parsed_br if ":" in raw_proj else None)
    eff_source = parsed_src.strip() if parsed_src and str(parsed_src).strip() else None
    is_child_src = bool(eff_source and eff_source not in ("__default__", "__shared__"))
    ctx_src = _ctx_source_arg(eff_source)

    source_rows: List[Dict[str, Any]] = []
    if eff_source == "__shared__":
        source_rows = _fetch_shared_docs_for_project(target_proj, eff_branch, status)
    elif eff_branch:
        clauses = ["project_id=?", "branch=?"]
        params: List[Any] = [target_proj, eff_branch]
        if status and status != "all":
            clauses.append("status=?")
            params.append(status)
        sql = (
            f"SELECT * FROM document_versions WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC"
        )
        has_br = (
            tenant_manager.has_branch(target_proj, eff_branch, source_id=ctx_src)
            if is_child_src
            else True
        )
        rows: List[Dict[str, Any]] = []
        if has_br:
            with tenant_context(target_proj, eff_branch, source_id=ctx_src):
                rows = db.fetchall(sql, tuple(params))
        if rows:
            source_rows = [
                _tag_doc_row(
                    r,
                    default_source=eff_source or "__default__",
                    default_scope="source",
                )
                for r in rows
            ]
        else:
            lineage = _get_doc_branch_lineage(
                target_proj, eff_branch, eff_source or "__default__"
            )
            if len(lineage) > 1:
                tombstoned_docs: set = set()
                for anc_idx in range(len(lineage)):
                    b_name = lineage[anc_idx]
                    if is_child_src and not tenant_manager.has_branch(
                        target_proj, b_name, source_id=ctx_src
                    ):
                        continue
                    with tenant_context(target_proj, b_name, source_id=ctx_src):
                        t_map = get_memory_tombstones(target_proj, b_name)
                        tombstoned_docs.update(t_map.get("doc", set()))
                    if anc_idx == 0:
                        continue
                    anc_clauses = ["project_id=?", "branch=?"]
                    anc_params: List[Any] = [target_proj, b_name]
                    if status and status != "all":
                        anc_clauses.append("status=?")
                        anc_params.append(status)
                    anc_sql = (
                        f"SELECT * FROM document_versions WHERE {' AND '.join(anc_clauses)} "
                        "ORDER BY created_at DESC"
                    )
                    with tenant_context(target_proj, b_name, source_id=ctx_src):
                        anc_rows = db.fetchall(anc_sql, tuple(anc_params))
                    filtered = [
                        _tag_doc_row(
                            r,
                            default_source=eff_source or "__default__",
                            default_scope="source",
                        )
                        for r in anc_rows
                        if r.get("doc_key") not in tombstoned_docs
                    ]
                    if filtered:
                        source_rows = filtered
                        break
    else:
        clauses = ["project_id=?"]
        params = [target_proj]
        if status and status != "all":
            clauses.append("status=?")
            params.append(status)
        sql = (
            f"SELECT * FROM document_versions WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC"
        )
        registered_branches = tenant_manager.list_project_branches(
            target_proj, source_id=ctx_src
        )
        if not registered_branches:
            if not is_child_src:
                with tenant_context(None):
                    source_rows = [
                        _tag_doc_row(r, default_source="__default__", default_scope="source")
                        for r in db.fetchall(sql, tuple(params))
                    ]
        else:
            for br in registered_branches:
                with tenant_context(target_proj, br, source_id=ctx_src):
                    for r in db.fetchall(sql, tuple(params)):
                        source_rows.append(
                            _tag_doc_row(
                                r,
                                default_source=eff_source or "__default__",
                                default_scope="source",
                            )
                        )

    if not include_shared and eff_source != "__shared__":
        source_rows = [
            r
            for r in source_rows
            if r.get("scope") != "project_shared" and r.get("source_id") != "__shared__"
        ]
    elif is_child_src and include_shared:
        source_rows.extend(_fetch_shared_docs_for_project(target_proj, eff_branch, status))

    merged: List[Dict[str, Any]] = []
    seen_ids: set = set()
    seen_sigs: set = set()
    for r in source_rows:
        rid = r.get("id")
        sig = (
            r.get("project_id"),
            r.get("branch"),
            r.get("doc_key"),
            r.get("version"),
            r.get("source_id"),
        )
        if (rid is not None and rid in seen_ids) or sig in seen_sigs:
            continue
        if rid is not None:
            seen_ids.add(rid)
        seen_sigs.add(sig)
        merged.append(r)
    merged.sort(key=lambda r: int(r.get("created_at") or 0), reverse=True)
    return merged


def list_document_versions(
    project_id: str,
    doc_key: str,
    branch: Optional[str] = None,
    source_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List all versions of a specific doc_key for a project (optionally filtered by branch/source)."""
    ensure_doc_versioning_tables()
    raw_proj = (project_id or "default").strip() or "default"
    arg_src = str(source_id).strip() or None if source_id is not None else None
    target_proj, parsed_src, parsed_br = parse_tenant_spec(
        raw_proj,
        default_branch=branch or _effective_root_branch(),
        default_source=arg_src,
    )
    if arg_src is not None and "@" not in raw_proj:
        parsed_src = arg_src

    eff_branch = branch if branch is not None else (parsed_br if ":" in raw_proj else None)
    eff_source = parsed_src.strip() if parsed_src and str(parsed_src).strip() else None
    is_child_src = bool(eff_source and eff_source not in ("__default__", "__shared__"))
    ctx_src = _ctx_source_arg(eff_source)

    if eff_branch:
        sql = (
            "SELECT * FROM document_versions WHERE project_id=? AND doc_key=? AND branch=? "
            "ORDER BY created_at DESC"
        )
        if is_child_src and not tenant_manager.has_branch(target_proj, eff_branch, source_id=ctx_src):
            return []
        with tenant_context(target_proj, eff_branch, source_id=ctx_src):
            rows = db.fetchall(sql, (target_proj, doc_key, eff_branch))
            return [
                _tag_doc_row(
                    r,
                    default_source=eff_source or "__default__",
                    default_scope="project_shared" if eff_source == "__shared__" else "source",
                )
                for r in rows
            ]

    sql = (
        "SELECT * FROM document_versions WHERE project_id=? AND doc_key=? "
        "ORDER BY created_at DESC"
    )
    registered_branches = tenant_manager.list_project_branches(
        target_proj, source_id=ctx_src
    )
    if not registered_branches:
        if is_child_src:
            return []
        with tenant_context(None):
            return [
                _tag_doc_row(r, default_source="__default__", default_scope="source")
                for r in db.fetchall(sql, (target_proj, doc_key))
            ]

    merged: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for br in registered_branches:
        with tenant_context(target_proj, br, source_id=ctx_src):
            for r in db.fetchall(sql, (target_proj, doc_key)):
                rid = r.get("id") or (r.get("project_id"), r.get("branch"), r.get("doc_key"), r.get("version"))
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    merged.append(
                        _tag_doc_row(
                            r,
                            default_source=eff_source or "__default__",
                            default_scope="project_shared" if eff_source == "__shared__" else "source",
                        )
                    )
    merged.sort(key=lambda r: int(r.get("created_at") or 0), reverse=True)
    return merged


def diff_document_versions(
    project_id: str,
    doc_key: str,
    from_version: str,
    to_version: str,
    branch: str = "main",
    from_branch: Optional[str] = None,
    to_branch: Optional[str] = None,
    source_id: Optional[str] = None,
    from_source_id: Optional[str] = None,
    to_source_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute unit-level diff between two versions of a document."""
    ensure_doc_versioning_tables()
    target_proj, parsed_src, _ = parse_tenant_spec(
        project_id,
        default_branch=branch or "main",
        default_source=source_id,
    )
    if source_id is not None and "@" not in str(project_id or ""):
        parsed_src = str(source_id).strip() or None
    f_br = from_branch or branch or "main"
    t_br = to_branch or branch or "main"
    f_src = _ctx_source_arg(from_source_id or parsed_src)
    t_src = _ctx_source_arg(to_source_id or parsed_src)

    with tenant_context(target_proj, f_br, source_id=f_src):
        from_meta = db.fetchone(
            "SELECT * FROM document_versions WHERE project_id=? AND branch=? AND doc_key=? AND version=?",
            (target_proj, f_br, doc_key, from_version),
        )
        if not from_meta:
            raise KeyError(
                f"Document '{doc_key}' version '{from_version}' not found on branch '{f_br}' for project '{target_proj}'"
            )
        from_units = db.fetchall(
            "SELECT * FROM document_units WHERE project_id=? AND branch=? AND doc_key=? AND version=? ORDER BY unit_index ASC",
            (target_proj, f_br, doc_key, from_version),
        )

    with tenant_context(target_proj, t_br, source_id=t_src):
        to_meta = db.fetchone(
            "SELECT * FROM document_versions WHERE project_id=? AND branch=? AND doc_key=? AND version=?",
            (target_proj, t_br, doc_key, to_version),
        )
        if not to_meta:
            raise KeyError(
                f"Document '{doc_key}' version '{to_version}' not found on branch '{t_br}' for project '{target_proj}'"
            )
        to_units = db.fetchall(
            "SELECT * FROM document_units WHERE project_id=? AND branch=? AND doc_key=? AND version=? ORDER BY unit_index ASC",
            (target_proj, t_br, doc_key, to_version),
        )

    from_by_hash: Dict[str, List[Dict[str, Any]]] = {}
    for fu in from_units:
        from_by_hash.setdefault(fu["unit_sha256"], []).append(dict(fu))

    unchanged: List[Dict[str, Any]] = []
    unmatched_to: List[Dict[str, Any]] = []

    for tu in to_units:
        h = tu["unit_sha256"]
        if h in from_by_hash and from_by_hash[h]:
            matched_from = from_by_hash[h].pop(0)
            unchanged.append(
                {
                    "unit_type": tu["unit_type"],
                    "from_unit_index": matched_from["unit_index"],
                    "to_unit_index": tu["unit_index"],
                    "unit_title": tu["unit_title"],
                    "unit_sha256": h,
                }
            )
        else:
            unmatched_to.append(dict(tu))

    unmatched_from: List[Dict[str, Any]] = []
    for rem_list in from_by_hash.values():
        unmatched_from.extend(rem_list)
    unmatched_from.sort(key=lambda x: x["unit_index"])

    modified: List[Dict[str, Any]] = []
    added: List[Dict[str, Any]] = []
    used_from_ids: set = set()

    from_by_index = {fu["unit_index"]: fu for fu in unmatched_from}
    from_by_title: Dict[str, List[Dict[str, Any]]] = {}
    for fu in unmatched_from:
        t_key = (fu.get("unit_title") or "").strip().lower()
        if t_key:
            from_by_title.setdefault(t_key, []).append(fu)

    for tu in unmatched_to:
        matched_fu: Optional[Dict[str, Any]] = None
        cand_by_idx = from_by_index.get(tu["unit_index"])
        if cand_by_idx and cand_by_idx["id"] not in used_from_ids:
            matched_fu = cand_by_idx
        else:
            t_key = (tu.get("unit_title") or "").strip().lower()
            if t_key and t_key in from_by_title:
                for cand in from_by_title[t_key]:
                    if cand["id"] not in used_from_ids:
                        matched_fu = cand
                        break

        if matched_fu is not None:
            used_from_ids.add(matched_fu["id"])
            modified.append(
                {
                    "unit_type": tu["unit_type"],
                    "from_unit_index": matched_fu["unit_index"],
                    "to_unit_index": tu["unit_index"],
                    "from_title": matched_fu["unit_title"],
                    "to_title": tu["unit_title"],
                    "from_sha256": matched_fu["unit_sha256"],
                    "to_sha256": tu["unit_sha256"],
                }
            )
        else:
            added.append(
                {
                    "unit_type": tu["unit_type"],
                    "unit_index": tu["unit_index"],
                    "unit_title": tu["unit_title"],
                    "unit_sha256": tu["unit_sha256"],
                }
            )

    removed: List[Dict[str, Any]] = [
        {
            "unit_type": fu["unit_type"],
            "unit_index": fu["unit_index"],
            "unit_title": fu["unit_title"],
            "unit_sha256": fu["unit_sha256"],
        }
        for fu in unmatched_from
        if fu["id"] not in used_from_ids
    ]

    return {
        "project_id": target_proj,
        "doc_key": doc_key,
        "from_version": from_version,
        "to_version": to_version,
        "from_branch": f_br,
        "to_branch": t_br,
        "summary": {
            "from_total_units": len(from_units),
            "to_total_units": len(to_units),
            "unchanged": len(unchanged),
            "modified": len(modified),
            "added": len(added),
            "removed": len(removed),
        },
        "unchanged": unchanged,
        "modified": modified,
        "added": added,
        "removed": removed,
    }
