"""Cross-Source Contract Surface Extractor, Multi-Signal Linker, and Drift Detector (Wave 2A).

Extracts contract surfaces across heterogeneous project sources (HTTP endpoints, HTTP client
calls, Pydantic/TypeScript/SQL/OpenAPI/Go/Java/Proto schemas, async pub/sub events, infra
compose/Terraform/env definitions, and project-shared specification docs), persists them to
``cross_source_contract_artifacts`` in Project DB ``pb_root``, reconciles typed
``cross_source_relations`` (``calls_api``, ``shares_schema``, ``consumes_events``,
``deploys``, ``depends_on``, ``documented_by``), preserves manual/pinned/rejected links
unconditionally, and flags signature drift or missing providers as ``drifted`` / ``stale``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

import yaml

from projectbrain.core.tenant_manager import (
    _ROOT_ONLY_STATEMENTS,
    _adapt_ddl_for_postgres,
    parse_tenant_from_user_id,
    tenant_manager,
)

logger = logging.getLogger("cross_source_linker")

_WALK_EXTENSIONS: Set[str] = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".java",
    ".tf",
    ".yml",
    ".yaml",
    ".json",
    ".proto",
    ".md",
    ".sql",
}

_SKIP_DIRS: Set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".projectbrain",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}

_HTTP_METHODS: Tuple[str, ...] = (
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "OPTIONS",
    "HEAD",
)

_SCHEMA_BASE_NAMES: Set[str] = {
    "BaseModel",
    "BaseSettings",
    "BaseSchema",
    "Schema",
    "TypedDict",
    "SQLModel",
    "NamedTuple",
    "Struct",
    "Base",
}

_SCHEMA_SUFFIXES: Tuple[str, ...] = (
    "DTO",
    "Dto",
    "Schema",
    "Model",
    "Payload",
    "Request",
    "Response",
    "Event",
    "Entity",
    "Contract",
)

_IGNORED_ASSET_EXTS: Tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".css",
    ".scss",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
)

_DOC_STOP_TOKENS: Set[str] = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "true",
    "false",
    "null",
    "none",
    "json",
    "yaml",
    "http",
    "https",
    "api",
    "main",
    "id",
    "string",
    "number",
    "integer",
    "boolean",
    "object",
    "array",
    "str",
    "int",
    "float",
    "bool",
    "dict",
    "list",
    "any",
}

_BACKEND_EXTS: Set[str] = {".py", ".go", ".java", ".sql", ".proto", ".yaml", ".yml", ".json"}
_FRONTEND_EXTS: Set[str] = {".ts", ".tsx", ".js", ".jsx"}


def _sha256_json(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _schema_signature_hash(fields: List[Tuple[str, str]]) -> str:
    normalized = sorted(
        (str(name).strip(), re.sub(r"\s+", " ", str(ftype).strip()))
        for name, ftype in fields
        if str(name).strip()
    )
    return _sha256_json(normalized)


def _normalize_http_path(raw_path: str) -> Tuple[str, str]:
    """Normalize URL/route path into `(canonical_path, short_suffix)`."""
    cleaned = str(raw_path or "").strip().strip("`'\"")
    cleaned = cleaned.split("?", 1)[0].split("#", 1)[0].strip()

    # Strip leading scheme+host or leading template base URL expressions like ${API_URL}/... or {base_url}/...
    cleaned = re.sub(r"^https?://[^/]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^(?:\$\{[^}]+\}|\{[a-zA-Z0-9_]*(?:url|base|host|origin|endpoint)[a-zA-Z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    cleaned = re.sub(r"/{2,}", "/", cleaned)
    if len(cleaned) > 1:
        cleaned = cleaned.rstrip("/")

    segments: List[str] = []
    for seg in cleaned.split("/"):
        if not seg:
            continue
        if (
            (seg.startswith("{") and seg.endswith("}"))
            or (seg.startswith("${") and seg.endswith("}"))
            or (seg.startswith("<") and seg.endswith(">"))
            or seg.startswith(":")
            or re.fullmatch(r"\d+", seg)
        ):
            segments.append("{param}")
        else:
            segments.append(seg)

    norm_path = "/" + "/".join(segments) if segments else "/"
    non_param = [s for s in segments if s != "{param}"]
    if len(segments) >= 2:
        short_suffix = "/" + "/".join(segments[-2:])
    elif non_param:
        short_suffix = "/" + non_param[-1]
    else:
        short_suffix = norm_path
    return norm_path, short_suffix


def _join_url_paths(prefix: str, path: str) -> str:
    p1 = (prefix or "").strip()
    p2 = (path or "").strip()
    if not p1:
        return p2 or "/"
    if not p2 or p2 == "/":
        return p1 if p1.startswith("/") else "/" + p1
    if p2.startswith(p1.rstrip("/") + "/") or p2 == p1.rstrip("/"):
        return p2
    return "/" + "/".join(
        seg for seg in (p1.strip("/"), p2.strip("/")) if seg
    )


def _normalize_schema_key(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(name or "").strip().lower())


def _canonical_contract_cmp(key: str) -> str:
    raw = str(key or "").strip()
    m = re.match(r"^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)[\s:]+(/.*)$", raw, re.IGNORECASE)
    if m:
        norm_p, _ = _normalize_http_path(m.group(2))
        return f"{m.group(1).upper()}:{norm_p}"
    return raw.lower()


def _derive_doc_key(filename: str) -> str:
    stem = Path(str(filename or "doc")).stem.lower()
    cleaned = re.sub(r"[^a-z0-9_\-]+", "_", stem).strip("_")
    return cleaned or "doc"


def _extract_balanced_block(text: str, open_idx: int, open_ch: str = "{", close_ch: str = "}") -> str:
    depth = 0
    in_sq = False
    in_dq = False
    in_bt = False
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        prev = text[i - 1] if i > 0 else ""
        if ch == "'" and not in_dq and not in_bt and prev != "\\":
            in_sq = not in_sq
        elif ch == '"' and not in_sq and not in_bt and prev != "\\":
            in_dq = not in_dq
        elif ch == "`" and not in_sq and not in_dq and prev != "\\":
            in_bt = not in_bt
        elif not in_sq and not in_dq and not in_bt:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[open_idx + 1 : i]
        i += 1
    return text[open_idx + 1 :]


def _strip_c_comments(text: str) -> str:
    no_block = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"//[^\n]*", "", no_block)


def _is_relevant_api_path(raw_path: str) -> bool:
    s = str(raw_path or "").strip()
    if not s or s.startswith(("#", "mailto:", "tel:", "data:", "javascript:")):
        return False
    low = s.split("?", 1)[0].lower()
    if low.endswith(_IGNORED_ASSET_EXTS):
        return False
    return s.startswith("/") or low.startswith(("http://", "https://")) or "/api/" in low


def _extract_host_service_from_url(val: str) -> Optional[str]:
    m = re.match(r"^https?://([a-zA-Z0-9_.\-]+?)(?::\d+)?(?:/|$)", str(val or "").strip(), re.IGNORECASE)
    if not m:
        return None
    host = m.group(1).lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return None
    return host.split(".", 1)[0]


def _make_artifact(
    *,
    project_id: str,
    source_id: str,
    branch: str,
    contract_kind: str,
    role: str,
    normalized_key: str,
    node_id: Optional[str] = None,
    file_path: Optional[str] = None,
    symbol_name: Optional[str] = None,
    signature_hash: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    sync_generation: int = 0,
) -> Dict[str, Any]:
    clean_file = str(file_path or "").replace("\\", "/")
    clean_sym = str(symbol_name or normalized_key)
    eff_node_id = str(node_id).strip() if node_id else f"{contract_kind}:{clean_file}:{clean_sym}"
    seed = f"{project_id}::{source_id}::{branch}::{contract_kind}::{role}::{normalized_key}::{eff_node_id}"
    art_id = f"art_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"
    return {
        "id": art_id,
        "project_id": project_id,
        "source_id": source_id,
        "branch": branch,
        "contract_kind": contract_kind,
        "role": role,
        "normalized_key": normalized_key,
        "node_id": eff_node_id,
        "file_path": clean_file or None,
        "symbol_name": symbol_name or normalized_key,
        "signature_hash": signature_hash or _sha256_json({"kind": contract_kind, "key": normalized_key}),
        "metadata": dict(metadata or {}),
        "sync_generation": int(sync_generation or 0),
    }


class CrossSourceLinker:
    """Extracts multi-source contract surfaces, links cross-source relations, and detects drift."""

    # ------------------------------------------------------------------
    # Schema bootstrap in Project DB pb_root (or single-DB fallback)
    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_tables(project_id: str) -> str:
        conn, is_pg, proj = tenant_manager._get_project_root_conn(project_id)
        for stmt in _ROOT_ONLY_STATEMENTS:
            clean_stmt = stmt.strip()
            if is_pg:
                clean_stmt = _adapt_ddl_for_postgres(clean_stmt)
                with conn.cursor() as cur:
                    cur.execute(clean_stmt)
            else:
                conn.execute(clean_stmt)
        if not is_pg:
            try:
                conn.commit()
            except Exception:
                pass
        return proj

    # ------------------------------------------------------------------
    # Phase A: Contract Surface Extraction
    # ------------------------------------------------------------------
    def _extract_from_python(
        self,
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError:
            return self._extract_from_python_regex(
                content, rel_path, project_id, source_id, branch, sync_gen
            )

        router_prefixes: Dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func_name = ""
                if isinstance(node.value.func, ast.Name):
                    func_name = node.value.func.id
                elif isinstance(node.value.func, ast.Attribute):
                    func_name = node.value.func.attr
                if func_name in ("APIRouter", "Blueprint", "FastAPI"):
                    prefix_val = ""
                    for kw in node.value.keywords:
                        if kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            prefix_val = kw.value.value
                    if prefix_val:
                        for tgt in node.targets:
                            if isinstance(tgt, ast.Name):
                                router_prefixes[tgt.id] = prefix_val

        def _const_str(expr: Optional[ast.AST]) -> Optional[str]:
            if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
                return expr.value
            if isinstance(expr, ast.JoinedStr):
                parts: List[str] = []
                for v in expr.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        parts.append(v.value)
                    elif isinstance(v, ast.FormattedValue):
                        try:
                            inner = ast.unparse(v.value)
                        except Exception:
                            inner = "param"
                        parts.append("{" + inner + "}")
                return "".join(parts)
            return None

        def _extract_methods_kw(call: ast.Call) -> List[str]:
            for kw in call.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                    methods = [
                        str(elt.value).upper()
                        for elt in kw.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
                    if methods:
                        return [m for m in methods if m in _HTTP_METHODS] or ["GET"]
            return ["GET"]

        def _is_schema_class(cls_node: ast.ClassDef) -> bool:
            for base in cls_node.bases:
                bname = base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else "")
                if bname in _SCHEMA_BASE_NAMES:
                    return True
            for dec in cls_node.decorator_list:
                dname = (
                    dec.id
                    if isinstance(dec, ast.Name)
                    else (
                        dec.attr
                        if isinstance(dec, ast.Attribute)
                        else (
                            dec.func.id
                            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                            else (
                                dec.func.attr
                                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                                else ""
                            )
                        )
                    )
                )
                if dname == "dataclass":
                    return True
            if cls_node.name.endswith(_SCHEMA_SUFFIXES):
                return any(isinstance(st, ast.AnnAssign) for st in cls_node.body)
            return False

        for node in ast.walk(tree):
            # 1. Pydantic / dataclass / TypedDict / SQLAlchemy schemas
            if isinstance(node, ast.ClassDef) and _is_schema_class(node):
                fields: List[Tuple[str, str]] = []
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        fname = stmt.target.id
                        if fname.startswith("_") or fname in ("model_config", "Config"):
                            continue
                        try:
                            ftype = ast.unparse(stmt.annotation)
                        except Exception:
                            ftype = "Any"
                        if ftype.startswith("ClassVar"):
                            continue
                        fields.append((fname, ftype))
                    elif isinstance(stmt, ast.Assign):
                        for tgt in stmt.targets:
                            if isinstance(tgt, ast.Name) and not tgt.id.startswith("_"):
                                if isinstance(stmt.value, ast.Call):
                                    cname = (
                                        stmt.value.func.attr
                                        if isinstance(stmt.value.func, ast.Attribute)
                                        else (stmt.value.func.id if isinstance(stmt.value.func, ast.Name) else "")
                                    )
                                    if cname in ("Column", "mapped_column", "Field") and stmt.value.args:
                                        try:
                                            ftype = ast.unparse(stmt.value.args[0])
                                        except Exception:
                                            ftype = "Any"
                                        fields.append((tgt.id, ftype))
                sig_hash = _schema_signature_hash(fields)
                norm_key = _normalize_schema_key(node.name)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=norm_key,
                        file_path=rel_path,
                        symbol_name=node.name,
                        signature_hash=sig_hash,
                        metadata={
                            "schema_name": node.name,
                            "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                            "language": "python",
                        },
                        sync_generation=sync_gen,
                    )
                )

            # 2. Function route decorators & event subscriber decorators
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params_sig: List[Tuple[str, str]] = []
                for arg in node.args.args:
                    if arg.arg in ("self", "cls"):
                        continue
                    try:
                        ann = ast.unparse(arg.annotation) if arg.annotation else "Any"
                    except Exception:
                        ann = "Any"
                    params_sig.append((arg.arg, ann))
                try:
                    ret_sig = ast.unparse(node.returns) if node.returns else ""
                except Exception:
                    ret_sig = ""

                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    dec_attr = ""
                    dec_obj = ""
                    if isinstance(dec.func, ast.Attribute):
                        dec_attr = dec.func.attr
                        if isinstance(dec.func.value, ast.Name):
                            dec_obj = dec.func.value.id
                    elif isinstance(dec.func, ast.Name):
                        dec_attr = dec.func.id

                    attr_up = dec_attr.upper()
                    attr_low = dec_attr.lower()
                    if attr_up in _HTTP_METHODS or attr_low in ("route", "api_route"):
                        raw_p = _const_str(dec.args[0]) if dec.args else None
                        if not raw_p:
                            for kw in dec.keywords:
                                if kw.arg == "path":
                                    raw_p = _const_str(kw.value)
                        if raw_p:
                            prefix = router_prefixes.get(dec_obj, "")
                            full_raw = _join_url_paths(prefix, raw_p)
                            norm_p, short_suf = _normalize_http_path(full_raw)
                            methods = (
                                [attr_up]
                                if attr_up in _HTTP_METHODS
                                else _extract_methods_kw(dec)
                            )
                            for m in methods:
                                norm_key = f"{m}:{norm_p}"
                                sig_hash = _sha256_json(
                                    {
                                        "method": m,
                                        "path": norm_p,
                                        "params": sorted(params_sig),
                                        "return": ret_sig,
                                    }
                                )
                                artifacts.append(
                                    _make_artifact(
                                        project_id=project_id,
                                        source_id=source_id,
                                        branch=branch,
                                        contract_kind="http_endpoint",
                                        role="provider",
                                        normalized_key=norm_key,
                                        file_path=rel_path,
                                        symbol_name=f"{m} {norm_p}",
                                        signature_hash=sig_hash,
                                        metadata={
                                            "method": m,
                                            "path": norm_p,
                                            "raw_path": full_raw,
                                            "short_suffix": short_suf,
                                            "handler": node.name,
                                            "language": "python",
                                        },
                                        sync_generation=sync_gen,
                                    )
                                )
                    elif attr_low in ("subscribe", "consume", "on_event", "event_handler"):
                        topic = _const_str(dec.args[0]) if dec.args else None
                        if not topic:
                            for kw in dec.keywords:
                                if kw.arg in ("topic", "event", "channel"):
                                    topic = _const_str(kw.value)
                        if topic and topic.strip():
                            clean_topic = topic.strip().lower()
                            artifacts.append(
                                _make_artifact(
                                    project_id=project_id,
                                    source_id=source_id,
                                    branch=branch,
                                    contract_kind="event_sub",
                                    role="consumer",
                                    normalized_key=clean_topic,
                                    file_path=rel_path,
                                    symbol_name=clean_topic,
                                    signature_hash=_sha256_json({"topic": clean_topic}),
                                    metadata={
                                        "topic": clean_topic,
                                        "handler": node.name,
                                        "language": "python",
                                    },
                                    sync_generation=sync_gen,
                                )
                            )

            # 3. Function calls: httpx / requests HTTP clients and event publish/emit/consume
            elif isinstance(node, ast.Call):
                callee_attr = ""
                callee_obj = ""
                if isinstance(node.func, ast.Attribute):
                    callee_attr = node.func.attr.lower()
                    if isinstance(node.func.value, ast.Name):
                        callee_obj = node.func.value.id.lower()
                    elif isinstance(node.func.value, ast.Attribute):
                        callee_obj = node.func.value.attr.lower()
                elif isinstance(node.func, ast.Name):
                    callee_attr = node.func.id.lower()

                # HTTP client calls
                if callee_obj in ("httpx", "requests", "client", "http_client", "session", "aiohttp") and (
                    callee_attr.upper() in _HTTP_METHODS or callee_attr == "request"
                ):
                    if callee_attr == "request" and len(node.args) >= 2:
                        m_str = (_const_str(node.args[0]) or "ANY").upper()
                        url_str = _const_str(node.args[1])
                        explicit_m = m_str in _HTTP_METHODS
                    else:
                        m_str = callee_attr.upper()
                        url_str = _const_str(node.args[0]) if node.args else None
                        if not url_str:
                            for kw in node.keywords:
                                if kw.arg == "url":
                                    url_str = _const_str(kw.value)
                        explicit_m = m_str in _HTTP_METHODS
                    if url_str and _is_relevant_api_path(url_str):
                        norm_p, short_suf = _normalize_http_path(url_str)
                        norm_key = f"{m_str}:{norm_p}" if explicit_m else f"ANY:{norm_p}"
                        artifacts.append(
                            _make_artifact(
                                project_id=project_id,
                                source_id=source_id,
                                branch=branch,
                                contract_kind="http_client_call",
                                role="consumer",
                                normalized_key=norm_key,
                                file_path=rel_path,
                                symbol_name=f"{m_str if explicit_m else 'CALL'} {norm_p}",
                                metadata={
                                    "method": m_str if explicit_m else "ANY",
                                    "method_explicit": explicit_m,
                                    "path": norm_p,
                                    "raw_path": url_str,
                                    "short_suffix": short_suf,
                                    "language": "python",
                                },
                                sync_generation=sync_gen,
                            )
                        )

                # Async event publish / emit / consume
                elif callee_attr in ("publish", "emit", "send_event", "produce", "publish_event", "emit_event"):
                    topic = _const_str(node.args[0]) if node.args else None
                    if not topic:
                        for kw in node.keywords:
                            if kw.arg in ("topic", "event", "channel"):
                                topic = _const_str(kw.value)
                    if topic and topic.strip():
                        clean_topic = topic.strip().lower()
                        artifacts.append(
                            _make_artifact(
                                project_id=project_id,
                                source_id=source_id,
                                branch=branch,
                                contract_kind="event_pub",
                                role="provider",
                                normalized_key=clean_topic,
                                file_path=rel_path,
                                symbol_name=clean_topic,
                                signature_hash=_sha256_json({"topic": clean_topic}),
                                metadata={"topic": clean_topic, "language": "python"},
                                sync_generation=sync_gen,
                            )
                        )
                elif callee_attr in ("consume",) or (callee_obj and callee_attr == "subscribe"):
                    # Avoid double-counting decorator calls already handled via FunctionDef
                    topic = _const_str(node.args[0]) if node.args else None
                    if topic and topic.strip():
                        clean_topic = topic.strip().lower()
                        artifacts.append(
                            _make_artifact(
                                project_id=project_id,
                                source_id=source_id,
                                branch=branch,
                                contract_kind="event_sub",
                                role="consumer",
                                normalized_key=clean_topic,
                                file_path=rel_path,
                                symbol_name=clean_topic,
                                signature_hash=_sha256_json({"topic": clean_topic}),
                                metadata={"topic": clean_topic, "language": "python"},
                                sync_generation=sync_gen,
                            )
                        )

        return artifacts

    def _extract_from_python_regex(
        self,
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for m in re.finditer(
            r"@(?:app|router|bp|\w+_router)\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']",
            content,
            re.IGNORECASE,
        ):
            method = m.group(1).upper()
            norm_p, short_suf = _normalize_http_path(m.group(2))
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="http_endpoint",
                    role="provider",
                    normalized_key=f"{method}:{norm_p}",
                    file_path=rel_path,
                    symbol_name=f"{method} {norm_p}",
                    metadata={"method": method, "path": norm_p, "short_suffix": short_suf},
                    sync_generation=sync_gen,
                )
            )
        artifacts.extend(
            self._extract_events_regex(content, rel_path, project_id, source_id, branch, sync_gen)
        )
        return artifacts

    @staticmethod
    def _extract_events_regex(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        pub_re = re.compile(
            r"(?:\.(?:publish|emit|send_event|produce|dispatch)|(?:publish|emit)_event)\(\s*[\"'`]([A-Za-z0-9_.:/\-]+)[\"'`]"
        )
        sub_re = re.compile(
            r"(?:@(?:[A-Za-z_][\w.]*\.)?subscribe|\.(?:subscribe|consume|on_event|handle_event)|consume)\(\s*[\"'`]([A-Za-z0-9_.:/\-]+)[\"'`]"
        )
        for m in pub_re.finditer(content):
            topic = m.group(1).strip().lower()
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="event_pub",
                    role="provider",
                    normalized_key=topic,
                    file_path=rel_path,
                    symbol_name=topic,
                    signature_hash=_sha256_json({"topic": topic}),
                    metadata={"topic": topic},
                    sync_generation=sync_gen,
                )
            )
        for m in sub_re.finditer(content):
            topic = m.group(1).strip().lower()
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="event_sub",
                    role="consumer",
                    normalized_key=topic,
                    file_path=rel_path,
                    symbol_name=topic,
                    signature_hash=_sha256_json({"topic": topic}),
                    metadata={"topic": topic},
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    def _extract_from_ts_js(
        self,
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        clean = _strip_c_comments(content)

        # 1. NestJS @Controller prefix + @Get/@Post...
        ctrl_match = re.search(r"@Controller\(\s*[\"'`]([^\"'`]*)[\"'`]\s*\)", clean)
        ctrl_prefix = ctrl_match.group(1) if ctrl_match else ""
        has_server_routes = bool(ctrl_match)

        for m in re.finditer(
            r"@(Get|Post|Put|Delete|Patch|Options|Head)\(\s*(?:[\"'`]([^\"'`]*)[\"'`])?\s*\)",
            clean,
        ):
            has_server_routes = True
            method = m.group(1).upper()
            sub_path = m.group(2) or ""
            full_raw = _join_url_paths(ctrl_prefix, sub_path)
            norm_p, short_suf = _normalize_http_path(full_raw)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="http_endpoint",
                    role="provider",
                    normalized_key=f"{method}:{norm_p}",
                    file_path=rel_path,
                    symbol_name=f"{method} {norm_p}",
                    metadata={
                        "method": method,
                        "path": norm_p,
                        "raw_path": full_raw,
                        "short_suffix": short_suf,
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        # 2. Express / Fastify routes: router.get("/...", ...) / app.post("/...", ...)
        for m in re.finditer(
            r"\b(?:app|router|fastify|server)\.(get|post|put|delete|patch|options|head)\(\s*[\"'`]([^\"'`]+)[\"'`]",
            clean,
            re.IGNORECASE,
        ):
            has_server_routes = True
            method = m.group(1).upper()
            raw_p = m.group(2)
            norm_p, short_suf = _normalize_http_path(raw_p)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="http_endpoint",
                    role="provider",
                    normalized_key=f"{method}:{norm_p}",
                    file_path=rel_path,
                    symbol_name=f"{method} {norm_p}",
                    metadata={
                        "method": method,
                        "path": norm_p,
                        "raw_path": raw_p,
                        "short_suffix": short_suf,
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        # 3. fetch(...) client calls
        for m in re.finditer(r"\bfetch\s*\(", clean):
            call_args = _extract_balanced_block(clean, m.end() - 1, "(", ")")
            url_m = re.match(r"^\s*[\"'`]([^\"'`]+)[\"'`]", call_args)
            if not url_m:
                continue
            raw_url = url_m.group(1)
            if not _is_relevant_api_path(raw_url):
                continue
            meth_m = re.search(r"\bmethod\s*:\s*[\"'`]?([A-Za-z]+)[\"'`]?", call_args, re.IGNORECASE)
            explicit_m = bool(meth_m and meth_m.group(1).upper() in _HTTP_METHODS)
            method = meth_m.group(1).upper() if explicit_m else "ANY"
            norm_p, short_suf = _normalize_http_path(raw_url)
            norm_key = f"{method}:{norm_p}" if explicit_m else f"ANY:{norm_p}"
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="http_client_call",
                    role="consumer",
                    normalized_key=norm_key,
                    file_path=rel_path,
                    symbol_name=f"{method if explicit_m else 'FETCH'} {norm_p}",
                    metadata={
                        "method": method,
                        "method_explicit": explicit_m,
                        "path": norm_p,
                        "raw_path": raw_url,
                        "short_suffix": short_suf,
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        # 4. axios.<method>(...) & generic client.<method>(...)
        for m in re.finditer(
            r"\b(?:axios|api|apiClient|httpClient|client)\.(get|post|put|delete|patch|head|options)\s*(?:<([^>]+)>)?\(\s*[\"'`]([^\"'`]+)[\"'`]",
            clean,
            re.IGNORECASE,
        ):
            method = m.group(1).upper()
            generic_type = (m.group(2) or "").strip()
            raw_url = m.group(3)
            if _is_relevant_api_path(raw_url):
                norm_p, short_suf = _normalize_http_path(raw_url)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="http_client_call",
                        role="consumer",
                        normalized_key=f"{method}:{norm_p}",
                        file_path=rel_path,
                        symbol_name=f"{method} {norm_p}",
                        metadata={
                            "method": method,
                            "method_explicit": True,
                            "path": norm_p,
                            "raw_path": raw_url,
                            "short_suffix": short_suf,
                            "language": "typescript",
                        },
                        sync_generation=sync_gen,
                    )
                )
            if generic_type:
                clean_gt = re.sub(r"[\[\]\s]", "", generic_type)
                if clean_gt and clean_gt[0].isupper() and clean_gt.lower() not in _DOC_STOP_TOKENS:
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="schema_ref",
                            role="consumer",
                            normalized_key=_normalize_schema_key(clean_gt),
                            file_path=rel_path,
                            symbol_name=clean_gt,
                            metadata={"schema_name": clean_gt, "language": "typescript"},
                            sync_generation=sync_gen,
                        )
                    )

        # 5. TypeScript interface / type object schemas
        schema_role = "provider" if has_server_routes else "consumer"
        for m in re.finditer(
            r"(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:extends\s+[^{]+)?\{",
            clean,
        ):
            name = m.group(1)
            body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
            fields = self._parse_ts_fields(body)
            sig_hash = _schema_signature_hash(fields)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="schema_def",
                    role=schema_role,
                    normalized_key=_normalize_schema_key(name),
                    file_path=rel_path,
                    symbol_name=name,
                    signature_hash=sig_hash,
                    metadata={
                        "schema_name": name,
                        "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        for m in re.finditer(
            r"(?:export\s+)?type\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{",
            clean,
        ):
            name = m.group(1)
            body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
            fields = self._parse_ts_fields(body)
            sig_hash = _schema_signature_hash(fields)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="schema_def",
                    role=schema_role,
                    normalized_key=_normalize_schema_key(name),
                    file_path=rel_path,
                    symbol_name=name,
                    signature_hash=sig_hash,
                    metadata={
                        "schema_name": name,
                        "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                        "language": "typescript",
                    },
                    sync_generation=sync_gen,
                )
            )

        # 6. Events
        artifacts.extend(
            self._extract_events_regex(clean, rel_path, project_id, source_id, branch, sync_gen)
        )
        return artifacts

    @staticmethod
    def _parse_ts_fields(body: str) -> List[Tuple[str, str]]:
        fields: List[Tuple[str, str]] = []
        # Split top-level lines/semicolons
        for raw_stmt in re.split(r"[;\n]+", body):
            stmt = raw_stmt.strip().rstrip(",")
            if not stmt or stmt.startswith(("//", "/*")):
                continue
            m = re.match(r"^(?:readonly\s+)?([A-Za-z_][A-Za-z0-9_]*)\??\s*:\s*(.+)$", stmt)
            if m:
                fields.append((m.group(1), m.group(2).strip()))
        return fields

    def _extract_from_go_java_proto(
        self,
        content: str,
        rel_path: str,
        ext: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        clean = _strip_c_comments(content)

        if ext == ".go":
            for m in re.finditer(
                r"\.(GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch)\(\s*\"([^\"]+)\"",
                clean,
            ):
                method = m.group(1).upper()
                norm_p, short_suf = _normalize_http_path(m.group(2))
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="http_endpoint",
                        role="provider",
                        normalized_key=f"{method}:{norm_p}",
                        file_path=rel_path,
                        symbol_name=f"{method} {norm_p}",
                        metadata={"method": method, "path": norm_p, "short_suffix": short_suf, "language": "go"},
                        sync_generation=sync_gen,
                    )
                )
            for m in re.finditer(r"\btype\s+([A-Z][A-Za-z0-9_]*)\s+struct\s*\{", clean):
                name = m.group(1)
                body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
                fields: List[Tuple[str, str]] = []
                for ln in body.splitlines():
                    fm = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+([^\s`]+)", ln)
                    if fm:
                        fields.append((fm.group(1), fm.group(2)))
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(name),
                        file_path=rel_path,
                        symbol_name=name,
                        signature_hash=_schema_signature_hash(fields),
                        metadata={"schema_name": name, "fields": [{"name": n, "type": t} for n, t in sorted(fields)], "language": "go"},
                        sync_generation=sync_gen,
                    )
                )
        elif ext == ".java":
            cls_req = re.search(r"@RequestMapping\(\s*(?:(?:value|path)\s*=\s*)?\"([^\"]+)\"", clean)
            cls_prefix = cls_req.group(1) if cls_req else ""
            for m in re.finditer(
                r"@(Get|Post|Put|Delete|Patch)Mapping\(\s*(?:(?:value|path)\s*=\s*)?(?:\"([^\"]*)\")?\s*\)",
                clean,
            ):
                method = m.group(1).upper()
                full_raw = _join_url_paths(cls_prefix, m.group(2) or "")
                norm_p, short_suf = _normalize_http_path(full_raw)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="http_endpoint",
                        role="provider",
                        normalized_key=f"{method}:{norm_p}",
                        file_path=rel_path,
                        symbol_name=f"{method} {norm_p}",
                        metadata={"method": method, "path": norm_p, "short_suffix": short_suf, "language": "java"},
                        sync_generation=sync_gen,
                    )
                )
            for m in re.finditer(r"(?:public\s+)?(?:class|record)\s+([A-Z][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s*\{", clean):
                name = m.group(1)
                if not name.endswith(_SCHEMA_SUFFIXES) and not m.group(2):
                    continue
                fields = []
                if m.group(2):
                    for part in m.group(2).split(","):
                        pm = re.match(r"^\s*([A-Za-z0-9_<>\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", part)
                        if pm:
                            fields.append((pm.group(2), pm.group(1)))
                body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
                for fm in re.finditer(r"(?:private|public|protected)\s+(?:final\s+)?([A-Za-z0-9_<>\[\]]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", body):
                    fields.append((fm.group(2), fm.group(1)))
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(name),
                        file_path=rel_path,
                        symbol_name=name,
                        signature_hash=_schema_signature_hash(fields),
                        metadata={"schema_name": name, "fields": [{"name": n, "type": t} for n, t in sorted(fields)], "language": "java"},
                        sync_generation=sync_gen,
                    )
                )
        elif ext == ".proto":
            for m in re.finditer(r"\bmessage\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{", clean):
                name = m.group(1)
                body = _extract_balanced_block(clean, m.end() - 1, "{", "}")
                fields = []
                for fm in re.finditer(r"^\s*(?:repeated\s+|optional\s+)?([A-Za-z0-9_.]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\d+\s*;", body, re.MULTILINE):
                    fields.append((fm.group(2), fm.group(1)))
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(name),
                        file_path=rel_path,
                        symbol_name=name,
                        signature_hash=_schema_signature_hash(fields),
                        metadata={"schema_name": name, "fields": [{"name": n, "type": t} for n, t in sorted(fields)], "language": "proto"},
                        sync_generation=sync_gen,
                    )
                )

        artifacts.extend(
            self._extract_events_regex(clean, rel_path, project_id, source_id, branch, sync_gen)
        )
        return artifacts

    @staticmethod
    def _extract_from_sql(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for m in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[\"`\w]+\.)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s*\(",
            content,
            re.IGNORECASE,
        ):
            table_name = m.group(1)
            body = _extract_balanced_block(content, m.end() - 1, "(", ")")
            fields: List[Tuple[str, str]] = []
            # Split top-level commas outside parens like DECIMAL(10, 2)
            depth = 0
            buf: List[str] = []
            parts: List[str] = []
            for ch in body:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth = max(0, depth - 1)
                if ch == "," and depth == 0:
                    parts.append("".join(buf))
                    buf.clear()
                else:
                    buf.append(ch)
            if buf:
                parts.append("".join(buf))
            for col_def in parts:
                cleaned = col_def.strip()
                if not cleaned:
                    continue
                upper = cleaned.upper()
                if upper.startswith(("PRIMARY ", "FOREIGN ", "UNIQUE ", "CHECK ", "CONSTRAINT ", "INDEX ", "KEY ")):
                    continue
                cm = re.match(r"^[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s+([A-Za-z0-9_]+(?:\([^)]*\))?)", cleaned)
                if cm:
                    fields.append((cm.group(1), cm.group(2).upper()))
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="schema_def",
                    role="provider",
                    normalized_key=_normalize_schema_key(table_name),
                    file_path=rel_path,
                    symbol_name=table_name,
                    signature_hash=_schema_signature_hash(fields),
                    metadata={
                        "schema_name": table_name,
                        "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                        "language": "sql",
                    },
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    @staticmethod
    def _extract_from_yaml_json(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        try:
            data = yaml.safe_load(content)
        except Exception:
            return artifacts
        if not isinstance(data, dict):
            return artifacts

        # 1. OpenAPI / Swagger paths & schemas
        if ("openapi" in data or "swagger" in data) and isinstance(data.get("paths"), dict):
            for raw_path, path_item in data["paths"].items():
                if not isinstance(path_item, dict):
                    continue
                norm_p, short_suf = _normalize_http_path(str(raw_path))
                for meth, op in path_item.items():
                    m_up = str(meth).upper()
                    if m_up not in _HTTP_METHODS or not isinstance(op, dict):
                        continue
                    sig_hash = _sha256_json(
                        {
                            "method": m_up,
                            "path": norm_p,
                            "parameters": op.get("parameters"),
                            "requestBody": op.get("requestBody"),
                            "responses": op.get("responses"),
                        }
                    )
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="http_endpoint",
                            role="provider",
                            normalized_key=f"{m_up}:{norm_p}",
                            file_path=rel_path,
                            symbol_name=str(op.get("operationId") or f"{m_up} {norm_p}"),
                            signature_hash=sig_hash,
                            metadata={
                                "method": m_up,
                                "path": norm_p,
                                "raw_path": raw_path,
                                "short_suffix": short_suf,
                                "openapi": True,
                            },
                            sync_generation=sync_gen,
                        )
                    )
            schemas = (data.get("components") or {}).get("schemas") if isinstance(data.get("components"), dict) else None
            if not isinstance(schemas, dict):
                schemas = data.get("definitions") if isinstance(data.get("definitions"), dict) else {}
            for sname, sdef in (schemas or {}).items():
                if not isinstance(sdef, dict):
                    continue
                props = sdef.get("properties") if isinstance(sdef.get("properties"), dict) else {}
                fields = [
                    (str(k), str(v.get("type") or v.get("$ref") or "any") if isinstance(v, dict) else "any")
                    for k, v in props.items()
                ]
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(str(sname)),
                        file_path=rel_path,
                        symbol_name=str(sname),
                        signature_hash=_schema_signature_hash(fields),
                        metadata={
                            "schema_name": str(sname),
                            "fields": [{"name": n, "type": t} for n, t in sorted(fields)],
                            "openapi": True,
                        },
                        sync_generation=sync_gen,
                    )
                )

        # 2. docker-compose services / depends_on / environment URLs
        if isinstance(data.get("services"), dict):
            for svc_name, svc_cfg in data["services"].items():
                svc_str = str(svc_name).strip()
                if not svc_str:
                    continue
                cfg = svc_cfg if isinstance(svc_cfg, dict) else {}
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="infra_deploy",
                        role="provider",
                        normalized_key=svc_str.lower(),
                        file_path=rel_path,
                        symbol_name=svc_str,
                        signature_hash=_sha256_json({"service": svc_str, "image": cfg.get("image"), "ports": cfg.get("ports")}),
                        metadata={"kind": "compose_service", "service": svc_str, "image": cfg.get("image")},
                        sync_generation=sync_gen,
                    )
                )
                deps = cfg.get("depends_on")
                dep_names: List[str] = []
                if isinstance(deps, list):
                    dep_names = [str(d).strip() for d in deps if str(d).strip()]
                elif isinstance(deps, dict):
                    dep_names = [str(k).strip() for k in deps.keys() if str(k).strip()]
                for dep in dep_names:
                    artifacts.append(
                        _make_artifact(
                            project_id=project_id,
                            source_id=source_id,
                            branch=branch,
                            contract_kind="infra_deploy",
                            role="consumer",
                            normalized_key=dep.lower(),
                            file_path=rel_path,
                            symbol_name=f"{svc_str}->depends_on:{dep}",
                            metadata={"kind": "depends_on", "from_service": svc_str, "target_service": dep},
                            sync_generation=sync_gen,
                        )
                    )
                env_cfg = cfg.get("environment")
                env_pairs: List[Tuple[str, str]] = []
                if isinstance(env_cfg, dict):
                    env_pairs = [(str(k), str(v)) for k, v in env_cfg.items()]
                elif isinstance(env_cfg, list):
                    for item in env_cfg:
                        if isinstance(item, str) and "=" in item:
                            k, v = item.split("=", 1)
                            env_pairs.append((k.strip(), v.strip()))
                for ek, ev in env_pairs:
                    if ek.upper().endswith(("_API_URL", "_URL", "_ENDPOINT")):
                        host = _extract_host_service_from_url(ev)
                        if host:
                            artifacts.append(
                                _make_artifact(
                                    project_id=project_id,
                                    source_id=source_id,
                                    branch=branch,
                                    contract_kind="infra_deploy",
                                    role="consumer",
                                    normalized_key=host.lower(),
                                    file_path=rel_path,
                                    symbol_name=f"{svc_str}:{ek}",
                                    metadata={"kind": "env_url", "env_var": ek, "value": ev, "target_service": host},
                                    sync_generation=sync_gen,
                                )
                            )
        return artifacts

    @staticmethod
    def _extract_from_terraform(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for m in re.finditer(r"\b(resource|service)\s+\"([^\"]+)\"(?:\s+\"([^\"]+)\")?", content):
            kind = m.group(1).lower()
            name = (m.group(3) or m.group(2)).strip()
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="infra_deploy",
                    role="provider",
                    normalized_key=name.lower(),
                    file_path=rel_path,
                    symbol_name=f"{kind}:{m.group(2)}" + (f".{m.group(3)}" if m.group(3) else ""),
                    metadata={"kind": "terraform", "tf_type": m.group(2), "tf_name": name},
                    sync_generation=sync_gen,
                )
            )
        return artifacts

    @staticmethod
    def _extract_from_env_file(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for ln in content.splitlines():
            stripped = ln.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            key = k.strip()
            val = v.strip().strip("\"'")
            if not re.search(r"(?:_API_URL|_SERVICE_URL|_URL|_ENDPOINT)$", key, re.IGNORECASE):
                continue
            host = _extract_host_service_from_url(val)
            if not host:
                # Fallback from env var prefix like ORDERS_API_URL -> orders
                prefix = re.sub(r"(?:_API_URL|_SERVICE_URL|_URL|_ENDPOINT)$", "", key, flags=re.IGNORECASE)
                host = prefix.lower().replace("_", "-") if prefix else ""
            if host:
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="infra_deploy",
                        role="consumer",
                        normalized_key=host.lower(),
                        file_path=rel_path,
                        symbol_name=key,
                        metadata={"kind": "env_url", "env_var": key, "value": val, "target_service": host},
                        sync_generation=sync_gen,
                    )
                )
        return artifacts

    @staticmethod
    def _extract_from_shared_doc(
        content: str,
        rel_path: str,
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
        doc_key: Optional[str] = None,
        doc_node_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        eff_doc_key = doc_key or _derive_doc_key(rel_path)
        seen_keys: Set[str] = set()

        def _emit_spec(ref_kind: str, raw_tok: str, norm_key: str, match_keys: List[str], heading: str = "") -> None:
            if norm_key in seen_keys:
                return
            seen_keys.add(norm_key)
            artifacts.append(
                _make_artifact(
                    project_id=project_id,
                    source_id=source_id,
                    branch=branch,
                    contract_kind="spec_req",
                    role="shared",
                    normalized_key=norm_key,
                    node_id=doc_node_id or f"spec_req:{eff_doc_key}:{norm_key}",
                    file_path=rel_path,
                    symbol_name=raw_tok,
                    signature_hash=_sha256_json({"doc_key": eff_doc_key, "key": norm_key}),
                    metadata={
                        "doc_key": eff_doc_key,
                        "ref_kind": ref_kind,
                        "raw_token": raw_tok,
                        "match_keys": match_keys,
                        "heading": heading,
                    },
                    sync_generation=sync_gen,
                )
            )

        # 1. Explicit METHOD + /path endpoints
        for m in re.finditer(r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(/[A-Za-z0-9_\-./{}:]+)", content):
            method = m.group(1).upper()
            norm_p, _ = _normalize_http_path(m.group(2))
            _emit_spec(
                "endpoint",
                f"{method} {norm_p}",
                f"endpoint:{norm_p}",
                [f"{method}:{norm_p}", norm_p],
            )

        # 2. Standalone /path endpoints (prose or backticked)
        for m in re.finditer(r"(?:^|[\s`(\"'])(/[a-zA-Z][A-Za-z0-9_\-]*(?:/[A-Za-z0-9_\-{}:.]+)+)", content):
            raw_p = m.group(1)
            if raw_p.lower().endswith(_IGNORED_ASSET_EXTS):
                continue
            norm_p, _ = _normalize_http_path(raw_p)
            _emit_spec("endpoint", norm_p, f"endpoint:{norm_p}", [norm_p])

        # 3. Backticked symbols
        for raw_cand in re.findall(r"`([^`\n]+)`", content):
            cand = raw_cand.strip()
            if not cand or len(cand) < 2 or cand.lower() in _DOC_STOP_TOKENS:
                continue
            if cand.startswith("/"):
                norm_p, _ = _normalize_http_path(cand)
                _emit_spec("endpoint", norm_p, f"endpoint:{norm_p}", [norm_p])
                continue
            m_ep = re.match(r"^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(/.*)$", cand)
            if m_ep:
                norm_p, _ = _normalize_http_path(m_ep.group(2))
                _emit_spec(
                    "endpoint",
                    f"{m_ep.group(1)} {norm_p}",
                    f"endpoint:{norm_p}",
                    [f"{m_ep.group(1)}:{norm_p}", norm_p],
                )
                continue
            clean_sym = re.sub(r"\(.*\)$", "", cand).strip()
            if not clean_sym or clean_sym.lower() in _DOC_STOP_TOKENS:
                continue
            _emit_spec(
                "symbol",
                clean_sym,
                f"symbol:{clean_sym.lower()}",
                [clean_sym.lower(), _normalize_schema_key(clean_sym)],
            )

        # 4. Headings & significant tokens inside headings
        for m in re.finditer(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$", content, re.MULTILINE):
            h_text = re.sub(r"`", "", m.group(2)).strip()
            if not h_text:
                continue
            h_slug = re.sub(r"[^a-z0-9]+", "_", h_text.lower()).strip("_")
            if h_slug:
                _emit_spec("heading", h_text, f"heading:{h_slug}", [h_text.lower()], heading=h_text)
            for tok in re.findall(r"\b([A-Z][A-Za-z0-9_]{2,}|[a-z]+_[a-z0-9_]+)\b", h_text):
                if tok.lower() not in _DOC_STOP_TOKENS:
                    _emit_spec(
                        "symbol",
                        tok,
                        f"symbol:{tok.lower()}",
                        [tok.lower(), _normalize_schema_key(tok)],
                        heading=h_text,
                    )

        return artifacts

    def _extract_from_graph_nodes(
        self,
        graph_nodes: Iterable[Any],
        project_id: str,
        source_id: str,
        branch: str,
        sync_gen: int,
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for raw in graph_nodes:
            node = raw if isinstance(raw, dict) else (raw.to_dict() if hasattr(raw, "to_dict") else raw.model_dump())
            nid = str(node.get("id") or "")
            lbl = str(node.get("label") or "")
            src_file = str(node.get("source_file") or "")
            ftype = str(node.get("file_type") or "code")
            meta = node.get("metadata") or {}

            if source_id == "__shared__" or ftype == "document":
                if source_id == "__shared__":
                    text_parts = [
                        lbl,
                        str(meta.get("heading_breadcrumb") or ""),
                        str(meta.get("content") or meta.get("snippet") or ""),
                    ]
                    combined = "\n".join(p for p in text_parts if p)
                    if combined:
                        artifacts.extend(
                            self._extract_from_shared_doc(
                                combined,
                                src_file or "shared_doc.md",
                                project_id,
                                source_id,
                                branch,
                                sync_gen,
                                doc_key=meta.get("doc_key"),
                                doc_node_id=nid or None,
                            )
                        )
                continue

            kind = str(meta.get("kind") or meta.get("symbol_type") or "").lower()
            m_route = re.match(r"^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(/.*)$", lbl.strip(), re.IGNORECASE)
            if kind == "route" or meta.get("route") or m_route:
                if m_route:
                    method = m_route.group(1).upper()
                    raw_p = m_route.group(2)
                else:
                    method = str(meta.get("method") or "GET").upper()
                    raw_p = str(meta.get("path") or lbl)
                norm_p, short_suf = _normalize_http_path(raw_p)
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="http_endpoint",
                        role="provider",
                        normalized_key=f"{method}:{norm_p}",
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=f"{method} {norm_p}",
                        signature_hash=_sha256_json(
                            {"method": method, "path": norm_p, "signature": meta.get("signature") or ""}
                        ),
                        metadata={"method": method, "path": norm_p, "short_suffix": short_suf},
                        sync_generation=sync_gen,
                    )
                )
            elif kind in ("schema", "interface", "table") or (
                kind == "class" and (meta.get("fields") or lbl.endswith(_SCHEMA_SUFFIXES))
            ):
                raw_fields = meta.get("fields") or []
                fields = [
                    (str(f.get("name") or ""), str(f.get("type") or "Any"))
                    for f in raw_fields
                    if isinstance(f, dict)
                ]
                sig_hash = _schema_signature_hash(fields) if fields else _sha256_json({"name": lbl, "sig": meta.get("signature") or ""})
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind="schema_def",
                        role="provider",
                        normalized_key=_normalize_schema_key(lbl),
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=lbl,
                        signature_hash=sig_hash,
                        metadata={"schema_name": lbl, "fields": raw_fields},
                        sync_generation=sync_gen,
                    )
                )
            elif meta.get("contract_kind") in ("event_pub", "event_sub") or meta.get("event_topic"):
                ck = str(meta.get("contract_kind") or "event_pub")
                topic = str(meta.get("event_topic") or lbl).strip().lower()
                artifacts.append(
                    _make_artifact(
                        project_id=project_id,
                        source_id=source_id,
                        branch=branch,
                        contract_kind=ck,
                        role="provider" if ck == "event_pub" else "consumer",
                        normalized_key=topic,
                        node_id=nid or None,
                        file_path=src_file,
                        symbol_name=topic,
                        signature_hash=_sha256_json({"topic": topic}),
                        metadata={"topic": topic},
                        sync_generation=sync_gen,
                    )
                )
        return artifacts

    def _load_shared_docs_from_db(
        self, project_id: str, branch: str
    ) -> List[Dict[str, Any]]:
        """Read active `project_shared` / `__shared__` documents & units from Project DB `pb_root`."""
        docs: List[Dict[str, Any]] = []
        try:
            ver_rows = tenant_manager._project_root_fetchall(
                project_id,
                "SELECT * FROM document_versions "
                "WHERE project_id = ? AND status = 'active' "
                "AND (scope = 'project_shared' OR source_id = '__shared__')",
                (project_id,),
            )
            for dv in ver_rows:
                d_br = str(dv.get("branch") or branch)
                d_key = str(dv.get("doc_key") or "")
                d_ver = str(dv.get("version") or "")
                fname = str(dv.get("filename") or f"{d_key}.md")
                units = tenant_manager._project_root_fetchall(
                    project_id,
                    "SELECT unit_title, markdown_text FROM document_units "
                    "WHERE project_id = ? AND branch = ? AND doc_key = ? AND version = ? "
                    "ORDER BY unit_index ASC",
                    (project_id, d_br, d_key, d_ver),
                )
                md_text = "\n\n".join(
                    str(u.get("markdown_text") or "") for u in units if u.get("markdown_text")
                )
                if md_text.strip():
                    docs.append(
                        {
                            "doc_key": d_key,
                            "filename": fname,
                            "content": md_text,
                            "version": d_ver,
                        }
                    )
        except Exception as exc:
            logger.debug("Shared doc DB load skipped for %s: %s", project_id, exc)
        return docs

    def extract_contract_artifacts(
        self,
        project_id: str,
        source_id: str,
        branch: str = "main",
        workspace_path: Optional[Union[str, Path]] = None,
        graph_nodes: Optional[List[Dict[str, Any]]] = None,
        docs: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Phase A: Extract contract surface artifacts for `(project_id, source_id, branch)` and persist in `pb_root`."""
        proj = self._ensure_tables(project_id)
        eff_src = str(source_id or "__default__").strip() or "__default__"
        eff_branch = str(branch or "main").strip() or "main"

        sync_gen = 0
        try:
            src_meta = tenant_manager.get_project_source(proj, eff_src)
            if src_meta:
                sync_gen = int(src_meta.get("sync_generation") or 0)
        except Exception:
            sync_gen = 0

        extracted: List[Dict[str, Any]] = []

        # 1. Walk workspace files if provided
        if workspace_path is not None:
            ws = Path(workspace_path)
            if ws.is_file():
                file_iter: List[Tuple[Path, str]] = [(ws, ws.name)]
            elif ws.is_dir():
                file_iter = []
                for root, dirs, files in os.walk(ws):
                    dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
                    for fname in sorted(files):
                        fpath = Path(root) / fname
                        ext = fpath.suffix.lower()
                        if ext in _WALK_EXTENSIONS or fname.startswith(".env"):
                            rel = os.path.relpath(fpath, ws).replace("\\", "/")
                            file_iter.append((fpath, rel))
            else:
                file_iter = []

            for fpath, rel_path in file_iter:
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                ext = fpath.suffix.lower()
                fname_low = fpath.name.lower()
                if eff_src == "__shared__" and ext == ".md":
                    extracted.extend(
                        self._extract_from_shared_doc(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".py":
                    extracted.extend(
                        self._extract_from_python(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in (".ts", ".tsx", ".js", ".jsx"):
                    extracted.extend(
                        self._extract_from_ts_js(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in (".go", ".java", ".proto"):
                    extracted.extend(
                        self._extract_from_go_java_proto(
                            text, rel_path, ext, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".sql":
                    extracted.extend(
                        self._extract_from_sql(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext in (".yml", ".yaml", ".json"):
                    extracted.extend(
                        self._extract_from_yaml_json(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".tf":
                    extracted.extend(
                        self._extract_from_terraform(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif fpath.name.startswith(".env") or fname_low.endswith(".env"):
                    extracted.extend(
                        self._extract_from_env_file(
                            text, rel_path, proj, eff_src, eff_branch, sync_gen
                        )
                    )
                elif ext == ".md":
                    # Non-shared markdown inside a source workspace can still mention endpoints/events in API specs if needed,
                    # or keep shared docs as `spec_req`. Only emit `spec_req` for `__shared__` unless explicit.
                    pass

        # 2. Extract from explicit graph_nodes (or GraphStore fallback if no workspace_path and no graph_nodes passed)
        if graph_nodes is not None:
            extracted.extend(
                self._extract_from_graph_nodes(
                    graph_nodes, proj, eff_src, eff_branch, sync_gen
                )
            )
        elif workspace_path is None and docs is None and eff_src != "__shared__":
            try:
                from projectbrain.graphify_engine.store import get_graph_store

                store = get_graph_store(project_id=proj)
                try:
                    target_id = proj if eff_src == "__default__" else f"{proj}@{eff_src}"
                    sub = store.get_resolved_subgraph(target_id, eff_branch, limit=50000)
                    g_nodes = sub.get("nodes") or []
                    if g_nodes:
                        extracted.extend(
                            self._extract_from_graph_nodes(
                                g_nodes, proj, eff_src, eff_branch, sync_gen
                            )
                        )
                finally:
                    store.close()
            except Exception as exc:
                logger.debug("GraphStore fallback read skipped for %s@%s: %s", proj, eff_src, exc)

        # 3. Extract from explicit docs OR DB-persisted shared docs when source_id == "__shared__"
        eff_docs: List[Dict[str, Any]] = list(docs or [])
        if eff_src == "__shared__" and docs is None:
            eff_docs.extend(self._load_shared_docs_from_db(proj, eff_branch))

        for d in eff_docs:
            if not isinstance(d, dict):
                continue
            d_text = str(d.get("content") or d.get("markdown_text") or "")
            d_path = str(d.get("doc_path") or d.get("filename") or d.get("doc_key") or "shared.md")
            d_key = d.get("doc_key")
            d_nid = d.get("doc_node_id") or d.get("id")
            if d_text.strip():
                extracted.extend(
                    self._extract_from_shared_doc(
                        d_text,
                        d_path,
                        proj,
                        eff_src,
                        eff_branch,
                        sync_gen,
                        doc_key=d_key,
                        doc_node_id=d_nid,
                    )
                )

        # Deduplicate by unique key `(project_id, source_id, branch, contract_kind, role, normalized_key, node_id)`
        deduped: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for art in extracted:
            ukey = (
                art["contract_kind"],
                art["role"],
                art["normalized_key"],
                art["node_id"],
            )
            deduped[ukey] = art
        final_artifacts = list(deduped.values())

        # Persist into cross_source_contract_artifacts (delete stale rows for (project_id, source_id, branch) first)
        now_ms = int(time.time() * 1000)
        tenant_manager._project_root_execute(
            proj,
            "DELETE FROM cross_source_contract_artifacts WHERE project_id = ? AND source_id = ? AND branch = ?",
            (proj, eff_src, eff_branch),
        )
        for art in final_artifacts:
            meta_json = json.dumps(art.get("metadata") or {})
            tenant_manager._project_root_execute(
                proj,
                """
                INSERT INTO cross_source_contract_artifacts (
                    id, project_id, source_id, branch, contract_kind, role,
                    normalized_key, node_id, file_path, symbol_name, signature_hash,
                    metadata_json, sync_generation, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, source_id, branch, contract_kind, role, normalized_key, node_id)
                DO UPDATE SET
                    file_path = excluded.file_path,
                    symbol_name = excluded.symbol_name,
                    signature_hash = excluded.signature_hash,
                    metadata_json = excluded.metadata_json,
                    sync_generation = excluded.sync_generation,
                    updated_at = excluded.updated_at
                """,
                (
                    art["id"],
                    proj,
                    eff_src,
                    eff_branch,
                    art["contract_kind"],
                    art["role"],
                    art["normalized_key"],
                    art["node_id"],
                    art["file_path"],
                    art["symbol_name"],
                    art["signature_hash"],
                    meta_json,
                    art["sync_generation"],
                    now_ms,
                ),
            )
            art["metadata_json"] = meta_json
            art["updated_at"] = now_ms

        return final_artifacts

    # ------------------------------------------------------------------
    # Phase B: Multi-Signal Cross-Source Reconciliation & Drift Detection
    # ------------------------------------------------------------------
    def _load_artifacts(self, project_id: str, branch: str) -> List[Dict[str, Any]]:
        rows = tenant_manager._project_root_fetchall(
            project_id,
            "SELECT * FROM cross_source_contract_artifacts "
            "WHERE project_id = ? AND (branch = ? OR (source_id = '__shared__' AND branch = 'main'))",
            (project_id, branch),
        )
        out: List[Dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            try:
                item["metadata"] = json.loads(item.get("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
            out.append(item)
        return out

    @staticmethod
    def _schema_provider_rank(art: Dict[str, Any]) -> Tuple[int, int, str]:
        role = str(art.get("role") or "")
        role_score = 2 if role == "provider" else (1 if role == "shared" else 0)
        ext = Path(str(art.get("file_path") or "")).suffix.lower()
        lang_score = 2 if ext in _BACKEND_EXTS else (0 if ext in _FRONTEND_EXTS else 1)
        return (role_score, lang_score, str(art.get("source_id") or ""))

    def _build_relation_candidates(
        self,
        proj: str,
        eff_branch: str,
        artifacts: List[Dict[str, Any]],
        registered_sources: Set[str],
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        seen_cand_keys: Set[Tuple[str, str, str, str, str]] = set()

        def _add_candidate(
            *,
            src_art: Dict[str, Any],
            dst_source_id: str,
            dst_branch: str,
            dst_node_id: str,
            dst_doc_key: Optional[str],
            relation_type: str,
            contract_key: str,
            confidence: str,
            confidence_score: float,
            provider_signature_hash: str,
            drift_reason_default: str,
            is_bidirectional: bool = False,
            extra_meta: Optional[Dict[str, Any]] = None,
        ) -> None:
            src_id = str(src_art["source_id"])
            if not dst_source_id or src_id == dst_source_id:
                return
            dedup_k = (src_id, dst_source_id, relation_type, contract_key, str(src_art.get("node_id") or ""))
            if dedup_k in seen_cand_keys:
                return
            seen_cand_keys.add(dedup_k)
            meta = {
                "provider_signature_hash": provider_signature_hash,
                "consumer_signature_hash": str(src_art.get("signature_hash") or ""),
                "contract_kind": src_art.get("contract_kind"),
                **(extra_meta or {}),
            }
            candidates.append(
                {
                    "project_id": proj,
                    "src_source_id": src_id,
                    "src_branch": str(src_art.get("branch") or eff_branch),
                    "src_node_id": str(src_art.get("node_id") or ""),
                    "src_file_path": src_art.get("file_path"),
                    "dst_source_id": dst_source_id,
                    "dst_branch": dst_branch,
                    "dst_node_id": dst_node_id,
                    "dst_doc_key": dst_doc_key,
                    "relation_type": relation_type,
                    "confidence": confidence,
                    "confidence_score": confidence_score,
                    "contract_key": contract_key,
                    "contract_metadata": meta,
                    "is_bidirectional": is_bidirectional,
                    "drift_reason_default": drift_reason_default,
                }
            )

        by_kind: Dict[str, List[Dict[str, Any]]] = {}
        for art in artifacts:
            by_kind.setdefault(str(art["contract_kind"]), []).append(art)

        # 1. http_client_call -> http_endpoint (calls_api)
        endpoints = by_kind.get("http_endpoint", [])
        ep_by_exact: Dict[str, List[Dict[str, Any]]] = {}
        ep_by_path: Dict[str, List[Dict[str, Any]]] = {}
        ep_by_suffix: Dict[str, List[Dict[str, Any]]] = {}
        for ep in endpoints:
            ep_by_exact.setdefault(str(ep["normalized_key"]), []).append(ep)
            ep_path = str((ep.get("metadata") or {}).get("path") or "")
            if ep_path:
                ep_by_path.setdefault(ep_path, []).append(ep)
            ep_suf = str((ep.get("metadata") or {}).get("short_suffix") or "")
            if ep_suf and ep_suf != "/" and ep_suf != "/{param}":
                ep_by_suffix.setdefault(ep_suf, []).append(ep)

        for call in by_kind.get("http_client_call", []):
            c_meta = call.get("metadata") or {}
            c_path = str(c_meta.get("path") or "")
            c_suf = str(c_meta.get("short_suffix") or "")
            explicit_m = bool(c_meta.get("method_explicit"))

            matched_eps: List[Tuple[Dict[str, Any], str, float]] = []
            if explicit_m and str(call["normalized_key"]) in ep_by_exact:
                for ep in ep_by_exact[str(call["normalized_key"])]:
                    if ep["source_id"] != call["source_id"]:
                        matched_eps.append((ep, "high", 1.0))
            elif not explicit_m and c_path in ep_by_path:
                for ep in ep_by_path[c_path]:
                    if ep["source_id"] != call["source_id"]:
                        matched_eps.append((ep, "high", 0.95))
            elif not matched_eps and c_suf in ep_by_suffix:
                cands = [ep for ep in ep_by_suffix[c_suf] if ep["source_id"] != call["source_id"]]
                if len(cands) == 1:
                    matched_eps.append((cands[0], "medium", 0.75))

            for ep, conf, score in matched_eps:
                _add_candidate(
                    src_art=call,
                    dst_source_id=str(ep["source_id"]),
                    dst_branch=str(ep.get("branch") or eff_branch),
                    dst_node_id=str(ep.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="calls_api",
                    contract_key=str(ep["normalized_key"]),
                    confidence=conf,
                    confidence_score=score,
                    provider_signature_hash=str(ep.get("signature_hash") or ""),
                    drift_reason_default="Provider endpoint signature changed",
                    extra_meta={
                        "endpoint_path": (ep.get("metadata") or {}).get("path"),
                        "method": (ep.get("metadata") or {}).get("method"),
                        "dst_file_path": ep.get("file_path"),
                    },
                )

        # 2. schema_def / schema_ref across different sources (shares_schema)
        schema_groups: Dict[str, List[Dict[str, Any]]] = {}
        for s_art in by_kind.get("schema_def", []) + by_kind.get("schema_ref", []):
            nkey = str(s_art["normalized_key"])
            if nkey:
                schema_groups.setdefault(nkey, []).append(s_art)

        for nkey, group in schema_groups.items():
            # Keep strongest schema artifact per source (prefer schema_def over schema_ref)
            by_src: Dict[str, Dict[str, Any]] = {}
            for art in group:
                sid = str(art["source_id"])
                prev = by_src.get(sid)
                if prev is None or (prev["contract_kind"] == "schema_ref" and art["contract_kind"] == "schema_def"):
                    by_src[sid] = art
            if len(by_src) < 2:
                continue

            # Identify provider(s) and consumers
            all_arts = list(by_src.values())
            # Sort descending by provider rank; highest ranked schema_def is canonical provider
            defs = [a for a in all_arts if a["contract_kind"] == "schema_def"]
            if not defs:
                continue
            defs.sort(key=self._schema_provider_rank, reverse=True)
            provider = defs[0]
            schema_label = str((provider.get("metadata") or {}).get("schema_name") or provider.get("symbol_name") or nkey)
            for other in all_arts:
                if other["source_id"] == provider["source_id"]:
                    continue
                is_bidir = other["contract_kind"] == "schema_def" and other.get("role") == provider.get("role")
                _add_candidate(
                    src_art=other,
                    dst_source_id=str(provider["source_id"]),
                    dst_branch=str(provider.get("branch") or eff_branch),
                    dst_node_id=str(provider.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="shares_schema",
                    contract_key=f"schema:{schema_label}",
                    confidence="high",
                    confidence_score=0.95 if other["contract_kind"] == "schema_def" else 0.90,
                    provider_signature_hash=str(provider.get("signature_hash") or ""),
                    drift_reason_default="Provider schema signature changed",
                    is_bidirectional=is_bidir,
                    extra_meta={
                        "schema_name": schema_label,
                        "provider_fields": (provider.get("metadata") or {}).get("fields", []),
                        "consumer_fields": (other.get("metadata") or {}).get("fields", []),
                        "dst_file_path": provider.get("file_path"),
                    },
                )

        # 3. event_sub -> event_pub (consumes_events)
        pubs_by_topic: Dict[str, List[Dict[str, Any]]] = {}
        for pub in by_kind.get("event_pub", []):
            pubs_by_topic.setdefault(str(pub["normalized_key"]), []).append(pub)
        for sub in by_kind.get("event_sub", []):
            for pub in pubs_by_topic.get(str(sub["normalized_key"]), []):
                if pub["source_id"] == sub["source_id"]:
                    continue
                _add_candidate(
                    src_art=sub,
                    dst_source_id=str(pub["source_id"]),
                    dst_branch=str(pub.get("branch") or eff_branch),
                    dst_node_id=str(pub.get("node_id") or ""),
                    dst_doc_key=None,
                    relation_type="consumes_events",
                    contract_key=f"event:{pub['normalized_key']}",
                    confidence="high",
                    confidence_score=0.95,
                    provider_signature_hash=str(pub.get("signature_hash") or ""),
                    drift_reason_default="Provider event contract changed",
                    extra_meta={"topic": pub["normalized_key"], "dst_file_path": pub.get("file_path")},
                )

        # 4. infra_deploy -> target service (depends_on / deploys)
        infra_arts = by_kind.get("infra_deploy", [])
        infra_prov_by_key: Dict[str, List[Dict[str, Any]]] = {}
        for ia in infra_arts:
            if ia["role"] == "provider":
                infra_prov_by_key.setdefault(str(ia["normalized_key"]), []).append(ia)
        reg_src_map = {s.lower(): s for s in registered_sources}

        for ia in infra_arts:
            key = str(ia["normalized_key"])
            if ia["role"] == "consumer":
                matched_provs = [p for p in infra_prov_by_key.get(key, []) if p["source_id"] != ia["source_id"]]
                for prov in matched_provs:
                    _add_candidate(
                        src_art=ia,
                        dst_source_id=str(prov["source_id"]),
                        dst_branch=str(prov.get("branch") or eff_branch),
                        dst_node_id=str(prov.get("node_id") or ""),
                        dst_doc_key=None,
                        relation_type="depends_on",
                        contract_key=f"infra:{key}",
                        confidence="high",
                        confidence_score=0.90,
                        provider_signature_hash=str(prov.get("signature_hash") or ""),
                        drift_reason_default="Target service deployment definition changed",
                    )
                if not matched_provs and key in reg_src_map and reg_src_map[key] != ia["source_id"]:
                    _add_candidate(
                        src_art=ia,
                        dst_source_id=reg_src_map[key],
                        dst_branch=eff_branch,
                        dst_node_id="",
                        dst_doc_key=None,
                        relation_type="depends_on",
                        contract_key=f"infra:{key}",
                        confidence="medium",
                        confidence_score=0.80,
                        provider_signature_hash=str(ia.get("signature_hash") or ""),
                        drift_reason_default="Target service deployment definition changed",
                    )
            elif ia["role"] == "provider":
                if key in reg_src_map and reg_src_map[key] != ia["source_id"]:
                    _add_candidate(
                        src_art=ia,
                        dst_source_id=reg_src_map[key],
                        dst_branch=eff_branch,
                        dst_node_id="",
                        dst_doc_key=None,
                        relation_type="deploys",
                        contract_key=f"deploy:{key}",
                        confidence="medium",
                        confidence_score=0.85,
                        provider_signature_hash=str(ia.get("signature_hash") or ""),
                        drift_reason_default="Deployment manifest changed",
                    )

        # 5. spec_req (from __shared__ docs) -> matching code/contract artifacts (documented_by)
        non_shared = [a for a in artifacts if a["source_id"] != "__shared__" and a["contract_kind"] != "spec_req"]
        target_by_token: Dict[str, List[Dict[str, Any]]] = {}
        for t_art in non_shared:
            t_meta = t_art.get("metadata") or {}
            tokens: Set[str] = set()
            if t_art["contract_kind"] in ("http_endpoint", "http_client_call"):
                if t_meta.get("path"):
                    tokens.add(str(t_meta["path"]))
                tokens.add(str(t_art["normalized_key"]))
            elif t_art["contract_kind"] in ("schema_def", "schema_ref"):
                tokens.add(str(t_art["normalized_key"]))
                if t_art.get("symbol_name"):
                    tokens.add(str(t_art["symbol_name"]).lower())
            elif t_art["contract_kind"] in ("event_pub", "event_sub"):
                tokens.add(str(t_art["normalized_key"]))
            elif t_art["contract_kind"] == "infra_deploy":
                tokens.add(str(t_art["normalized_key"]))
            for tok in tokens:
                if tok:
                    target_by_token.setdefault(tok, []).append(t_art)

        for spec in by_kind.get("spec_req", []):
            s_meta = spec.get("metadata") or {}
            doc_key = str(s_meta.get("doc_key") or _derive_doc_key(str(spec.get("file_path") or "shared")))
            ref_kind = str(s_meta.get("ref_kind") or "symbol")
            match_keys = [str(k) for k in (s_meta.get("match_keys") or []) if k]
            matched_targets: List[Dict[str, Any]] = []
            seen_t_ids: Set[str] = set()
            for mk in match_keys:
                for t_art in target_by_token.get(mk, []):
                    tid = str(t_art["id"])
                    if tid not in seen_t_ids:
                        seen_t_ids.add(tid)
                        matched_targets.append(t_art)

            for t_art in matched_targets:
                target_label = str(t_art.get("symbol_name") or t_art.get("normalized_key"))
                _add_candidate(
                    src_art=t_art,
                    dst_source_id=str(spec["source_id"]),
                    dst_branch=str(spec.get("branch") or eff_branch),
                    dst_node_id=str(spec.get("node_id") or ""),
                    dst_doc_key=doc_key,
                    relation_type="documented_by",
                    contract_key=f"spec:{doc_key}:{t_art['normalized_key']}",
                    confidence="high" if ref_kind in ("endpoint", "symbol") else "medium",
                    confidence_score=1.0 if ref_kind in ("endpoint", "symbol") else 0.8,
                    provider_signature_hash=str(t_art.get("signature_hash") or ""),
                    drift_reason_default="Documented artifact signature changed",
                    extra_meta={
                        "doc_key": doc_key,
                        "spec_ref": spec.get("symbol_name"),
                        "spec_signature_hash": spec.get("signature_hash"),
                        "target_symbol": target_label,
                    },
                )

        return candidates

    def reconcile_relations(
        self,
        project_id: str,
        target_source_id: Optional[str] = None,
        branch: str = "main",
    ) -> Dict[str, Any]:
        """Phase B: Match contract artifacts across sources, preserve manual/pinned/rejected links, and flag drift/stale."""
        proj = self._ensure_tables(project_id)
        eff_branch = str(branch or "main").strip() or "main"
        target_src = str(target_source_id).strip() if target_source_id and str(target_source_id).strip() else None

        artifacts = self._load_artifacts(proj, eff_branch)
        try:
            reg_sources = {
                str(s["source_id"])
                for s in tenant_manager.list_project_sources(proj)
                if s.get("source_id")
            }
        except Exception:
            reg_sources = set()

        candidates = self._build_relation_candidates(proj, eff_branch, artifacts, reg_sources)
        if target_src is not None:
            candidates = [
                c
                for c in candidates
                if c["src_source_id"] == target_src or c["dst_source_id"] == target_src
            ]

        existing_rels = tenant_manager.list_cross_source_relations(proj)

        exact_map: Dict[Tuple[str, str, str, str, str, str, str, str], Dict[str, Any]] = {}
        contract_map: Dict[Tuple[str, str, str, str, str, str], List[Dict[str, Any]]] = {}
        for rel in existing_rels:
            ek = (
                str(rel.get("src_source_id") or ""),
                str(rel.get("src_branch") or "main"),
                str(rel.get("dst_source_id") or ""),
                str(rel.get("dst_branch") or "main"),
                str(rel.get("relation_type") or ""),
                str(rel.get("contract_key") or ""),
                str(rel.get("src_node_id") or ""),
                str(rel.get("dst_node_id") or ""),
            )
            exact_map[ek] = rel
            ck = (
                str(rel.get("src_source_id") or ""),
                str(rel.get("src_branch") or "main"),
                str(rel.get("dst_source_id") or ""),
                str(rel.get("dst_branch") or "main"),
                str(rel.get("relation_type") or ""),
                _canonical_contract_cmp(str(rel.get("contract_key") or "")),
            )
            contract_map.setdefault(ck, []).append(rel)

        seen_existing_ids: Set[str] = set()
        relations_upserted = 0
        drifted_count = 0
        stale_count = 0
        now_ms = int(time.time() * 1000)

        for cand in candidates:
            ek = (
                cand["src_source_id"],
                cand["src_branch"],
                cand["dst_source_id"],
                cand["dst_branch"],
                cand["relation_type"],
                cand["contract_key"],
                cand["src_node_id"],
                cand["dst_node_id"],
            )
            ck = (
                cand["src_source_id"],
                cand["src_branch"],
                cand["dst_source_id"],
                cand["dst_branch"],
                cand["relation_type"],
                _canonical_contract_cmp(cand["contract_key"]),
            )

            matching_contract_rows = contract_map.get(ck, [])
            protected_rows = [
                r
                for r in matching_contract_rows
                if str(r.get("origin") or "") == "manual"
                or str(r.get("status") or "") in ("pinned", "rejected")
            ]
            if protected_rows:
                for pr in protected_rows:
                    seen_existing_ids.add(str(pr["id"]))
                continue

            existing = exact_map.get(ek)
            if existing is None and matching_contract_rows:
                auto_rows = [
                    r for r in matching_contract_rows if str(r.get("origin") or "") == "auto_detected"
                ]
                if auto_rows:
                    existing = auto_rows[0]

            if existing is not None:
                seen_existing_ids.add(str(existing["id"]))
                ex_origin = str(existing.get("origin") or "")
                ex_status = str(existing.get("status") or "active")
                if ex_origin == "manual" or ex_status in ("pinned", "rejected"):
                    continue

                ex_meta = dict(existing.get("contract_metadata") or {})
                old_prov_hash = str(ex_meta.get("provider_signature_hash") or "")
                new_prov_hash = str(cand["contract_metadata"].get("provider_signature_hash") or "")

                if old_prov_hash and new_prov_hash and old_prov_hash != new_prov_hash:
                    reason = cand["drift_reason_default"]
                    merged_meta = {
                        **ex_meta,
                        **cand["contract_metadata"],
                        # Keep baseline provider_signature_hash so subsequent runs remain drifted until reconciled/pinned
                        "provider_signature_hash": old_prov_hash,
                        "latest_provider_signature_hash": new_prov_hash,
                        "drift_reason": reason,
                        "drifted_at": now_ms,
                    }
                    tenant_manager.update_cross_source_relation(
                        str(existing["id"]),
                        project_id=proj,
                        status="drifted",
                        src_node_id=cand["src_node_id"],
                        dst_node_id=cand["dst_node_id"],
                        src_file_path=cand["src_file_path"],
                        dst_doc_key=cand["dst_doc_key"],
                        contract_metadata=merged_meta,
                    )
                    drifted_count += 1
                    continue

                if ex_status == "drifted":
                    # Still drifted
                    drifted_count += 1
                    continue

                # Active (or recovered from stale) with unchanged signature
                tenant_manager.update_cross_source_relation(
                    str(existing["id"]),
                    project_id=proj,
                    status="active",
                    src_node_id=cand["src_node_id"],
                    dst_node_id=cand["dst_node_id"],
                    src_file_path=cand["src_file_path"],
                    dst_doc_key=cand["dst_doc_key"],
                    confidence=cand["confidence"],
                    confidence_score=cand["confidence_score"],
                    contract_metadata=cand["contract_metadata"],
                )
                relations_upserted += 1
            else:
                tenant_manager.upsert_cross_source_relation(
                    project_id=proj,
                    src_source_id=cand["src_source_id"],
                    dst_source_id=cand["dst_source_id"],
                    relation_type=cand["relation_type"],
                    src_branch=cand["src_branch"],
                    dst_branch=cand["dst_branch"],
                    src_node_id=cand["src_node_id"],
                    dst_node_id=cand["dst_node_id"],
                    src_file_path=cand["src_file_path"],
                    dst_doc_key=cand["dst_doc_key"],
                    origin="auto_detected",
                    confidence=cand["confidence"],
                    confidence_score=cand["confidence_score"],
                    contract_key=cand["contract_key"],
                    contract_metadata=cand["contract_metadata"],
                    status="active",
                    is_bidirectional=cand["is_bidirectional"],
                    created_by="cross_source_linker",
                )
                relations_upserted += 1

        # Stale detection: any existing auto_detected active/drifted relation in scope whose contract vanished
        for rel in existing_rels:
            rid = str(rel["id"])
            if rid in seen_existing_ids:
                continue
            if str(rel.get("origin") or "") != "auto_detected":
                continue
            r_status = str(rel.get("status") or "")
            if r_status in ("pinned", "rejected", "stale"):
                continue
            if target_src is not None and (
                rel.get("src_source_id") != target_src and rel.get("dst_source_id") != target_src
            ):
                continue
            if rel.get("src_branch") != eff_branch and rel.get("dst_branch") != eff_branch:
                continue

            r_meta = dict(rel.get("contract_metadata") or {})
            r_meta["drift_reason"] = "Provider or consumer contract artifact no longer present"
            r_meta["stale_at"] = now_ms
            tenant_manager.update_cross_source_relation(
                rid,
                project_id=proj,
                status="stale",
                contract_metadata=r_meta,
            )
            stale_count += 1

        updated_rels = tenant_manager.list_cross_source_relations(
            proj,
            source_id=target_src,
            branch=eff_branch if target_src is not None else None,
        )
        for rel in updated_rels:
            meta = rel.get("contract_metadata") or {}
            if meta.get("drift_reason"):
                rel["drift_reason"] = meta["drift_reason"]

        return {
            "relations_upserted": relations_upserted,
            "drifted_count": drifted_count,
            "stale_count": stale_count,
            "relations": updated_rels,
        }

    # ------------------------------------------------------------------
    # Combined Auto-Link Orchestrator (Phase A + Phase B)
    # ------------------------------------------------------------------
    def auto_link_project(
        self,
        project_id: str,
        source_id: Optional[str] = None,
        branch: str = "main",
        workspace_map: Optional[Dict[str, Union[str, Path]]] = None,
    ) -> Dict[str, Any]:
        """Extract contract surfaces across all (or target) sources and reconcile cross-source relations."""
        proj = self._ensure_tables(project_id)
        eff_branch = str(branch or "main").strip() or "main"
        target_src = str(source_id).strip() if source_id and str(source_id).strip() else None

        eff_map: Dict[str, Optional[Union[str, Path]]] = {}
        if workspace_map:
            for k, v in workspace_map.items():
                eff_map[str(k).strip()] = v

        # Merge registered project sources that have workspace_subpath / metadata workspace_path
        try:
            reg_sources = tenant_manager.list_project_sources(proj)
        except Exception:
            reg_sources = []

        for s in reg_sources:
            sid = str(s.get("source_id") or "").strip()
            if not sid or sid in eff_map:
                continue
            subpath = s.get("workspace_subpath") or (s.get("metadata") or {}).get("workspace_path")
            if subpath:
                cand_p = Path(str(subpath))
                if not cand_p.is_absolute():
                    cand_p = Path.cwd() / cand_p
                eff_map[sid] = cand_p if cand_p.exists() else None
            else:
                eff_map[sid] = None

        total_artifacts = 0
        scanned_sources: List[str] = []

        for sid, ws_path in eff_map.items():
            if target_src is not None and sid not in (target_src, "__shared__"):
                continue
            arts = self.extract_contract_artifacts(
                project_id=proj,
                source_id=sid,
                branch=eff_branch,
                workspace_path=ws_path,
            )
            total_artifacts += len(arts)
            scanned_sources.append(sid)

        # Always extract DB shared docs into __shared__ if __shared__ wasn't already scanned via workspace_map
        if "__shared__" not in eff_map:
            shared_arts = self.extract_contract_artifacts(
                project_id=proj,
                source_id="__shared__",
                branch=eff_branch,
            )
            total_artifacts += len(shared_arts)
            if shared_arts:
                scanned_sources.append("__shared__")

        rec = self.reconcile_relations(
            project_id=proj,
            target_source_id=target_src,
            branch=eff_branch,
        )
        return {
            "status": "ok",
            "project_id": proj,
            "branch": eff_branch,
            "source_id": target_src,
            "artifacts_extracted": total_artifacts,
            "sources_scanned": scanned_sources,
            "relations_upserted": rec["relations_upserted"],
            "drifted_count": rec["drifted_count"],
            "stale_count": rec["stale_count"],
            "relations": rec["relations"],
        }


__all__ = ["CrossSourceLinker"]
