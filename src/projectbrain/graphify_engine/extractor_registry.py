"""
Unified Parser Backend & Extractor Registry for Graphify AST Extraction.

Provides:
- UTF-8 staging for non-UTF-8 source files (UTF-16, CP932/Shift-JIS, EUC-JP,
  ISO-2022-JP, CP1252, Latin-1) so `graphify.extract` Tree-sitter parsers can
  parse legacy-encoded C/C++/C#/Python/JS/TS files cleanly while preserving
  original file stem, suffix, node IDs, and `source_file` paths.
- `ParserBackend` enum and `ExtractorRegistry` coordinating:
  - `graphify_ts`: `graphify.extract.extract` / `_extract_generic` under `_TREE_SITTER_LOCK`
  - `legacy_structural`: deterministic structural parsers (`_parse_st_lasal`,
    `_parse_idl`, `_parse_def`, `_parse_rc`, `_parse_vb6`, `_parse_delphi`,
    `_parse_cobol`, `_parse_sql_script_reg`)
  - `cpp_augment`: `_parse_cpp_legacy` augmenting Tree-sitter C/C++ graphs
  - `python_ast_fallback`: stdlib `ast` fallback for Python files
- Optional runtime Tree-sitter language registration (`register_optional_treesitter_language`)
  and zero-extra-dependency runtime probing for optional `tree_sitter_*` grammars
  (`tree_sitter_pascal`, `tree_sitter_cobol`, `tree_sitter_sql`,
  `tree_sitter_structured_text`).
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple, Union

from projectbrain.graphify_engine.legacy_encoding import read_text_with_encoding

logger = logging.getLogger(__name__)

# Guard external graphify.extract calls in case Tree-sitter reuses module-level C Parser state.
_TREE_SITTER_LOCK = threading.Lock()

# Maximum source file size (bytes) allowed for reading/staging (default 10 MiB).
MAX_SOURCE_FILE_BYTES: int = 10 * 1024 * 1024

# Strict allowlists for dynamic Tree-sitter module/function loading & LanguageConfig dict keys.
_TS_MODULE_RE = re.compile(r"tree_sitter_[a-z0-9_]+")
_TS_LANGUAGE_FN_RE = re.compile(r"language(?:_[a-z0-9_]+)?")
_ALLOWED_LANGUAGE_CONFIG_KEYS: FrozenSet[str] = frozenset(
    {
        "ts_module",
        "ts_language_fn",
        "class_types",
        "function_types",
        "import_types",
        "call_types",
        "name_field",
        "name_fallback_child_types",
        "body_field",
        "body_fallback_child_types",
        "call_function_field",
        "call_accessor_node_types",
        "call_accessor_field",
        "function_boundary_types",
        "function_label_parens",
    }
)

# UTF-8 / ASCII encodings that Tree-sitter can consume directly without staging.
_DIRECT_UTF8_ENCODINGS: Set[str] = {
    "utf-8",
    "utf8",
    "utf-8-sig",
    "utf8-sig",
    "ascii",
    "us-ascii",
}

_STAGING_DIR_PREFIX = "pb_utf8_"

# Extensions handled by C/C++ augmentation backend.
_CPP_AUGMENT_EXTS: Set[str] = {".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".inl", ".tpp"}
# Extensions not dispatched by graphify.extract._DISPATCH (handled directly by cpp_augment).
_GRAPHIFY_SKIP_EXTS: Set[str] = {".inl", ".tpp"}
# Extensions supported directly by graphify.extract._DISPATCH (case-sensitive in graphify).
_GRAPHIFY_DISPATCH_EXTS: Set[str] = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".rb", ".cs",
    ".kt", ".kts", ".scala", ".php", ".swift", ".lua", ".toc",
    ".zig", ".ps1", ".ex", ".exs", ".m", ".mm", ".jl",
}
# Extensions handled by deterministic legacy structural parsers when no optional TS grammar is installed.
_DEFAULT_LEGACY_EXTS: Set[str] = {
    ".st", ".scl", ".exp", ".pou",
    ".idl", ".def", ".rc",
    ".bas", ".cls", ".frm",
    ".pas", ".dfm",
    ".cbl", ".cob",
    ".sql", ".bat", ".cmd", ".reg",
}

# Type aliases for parser callables
LegacyParserFn = Callable[[str, str, str, str], Tuple[List[Any], List[Any]]]
CppAugmentFn = Callable[
    [str, str, str, str, Optional[List[Any]], Optional[List[Any]]],
    Tuple[List[Any], List[Any]],
]
PythonFallbackFn = Callable[[Path, str, str], Tuple[List[Any], List[Any]]]


class ParserBackend(str, Enum):
    """Supported code extraction backends in the unified Graphify pipeline."""

    GRAPHIFY_TS = "graphify_ts"
    LEGACY_STRUCTURAL = "legacy_structural"
    CPP_AUGMENT = "cpp_augment"
    PYTHON_AST_FALLBACK = "python_ast_fallback"


@dataclass(frozen=True)
class ExtractorStrategy:
    """Resolved extraction strategy descriptor for a file extension."""

    extension: str
    backend: ParserBackend
    backends: Tuple[ParserBackend, ...]
    is_legacy: bool = False
    language_config: Optional[Any] = None


@dataclass(frozen=True)
class TreeSitterStrategy(ExtractorStrategy):
    """Strategy backed by Tree-sitter (`graphify_ts`), built-in or optional `LanguageConfig`."""

    is_legacy: bool = False


@dataclass(frozen=True)
class CppAugmentStrategy(ExtractorStrategy):
    """Strategy combining Tree-sitter C/C++ extraction with `_parse_cpp_legacy` augmentation."""

    is_legacy: bool = False


@dataclass(frozen=True)
class LegacyStructuralStrategy(ExtractorStrategy):
    """Strategy backed by a deterministic legacy structural parser (`legacy_structural`)."""

    is_legacy: bool = True


def _normalize_ext(ext: str) -> str:
    cleaned = (ext or "").strip().lower()
    if cleaned and not cleaned.startswith("."):
        cleaned = f".{cleaned}"
    return cleaned


def staged_utf8_file(
    file_path: Union[str, Path],
    text: str,
    detected_encoding: str,
    staging_dir: Optional[Path] = None,
    force_stage: bool = False,
) -> Tuple[Path, bool]:
    """Return a UTF-8 readable path for Tree-sitter parsing.

    - If ``detected_encoding`` is ``utf-8``, ``utf-8-sig``, or ``ascii`` and the
      filename suffix is already lowercase (unless ``force_stage=True``),
      returns ``(Path(file_path), False)`` with zero I/O overhead.
    - Otherwise (e.g. ``utf-16*``, ``cp932``, ``shift_jis``, ``euc_jp``,
      ``iso2022_jp``, ``cp1252``, ``latin-1``, uppercase suffix, or
      ``force_stage=True``), writes ``text`` as UTF-8 into a unique temporary
      staging directory while preserving the original stem and lowercased suffix
      (e.g. ``.cpp``, ``.h``, ``.cs``) so ``graphify.extract`` Tree-sitter parses
      cleanly and generates identical node IDs. Returns ``(staged_path, True)``.
    """
    src_path = Path(file_path)
    target_name = f"{src_path.stem}{src_path.suffix.lower()}" if src_path.suffix else src_path.name
    if target_name in ("", ".", "..") or "/" in target_name or "\\" in target_name:
        raise ValueError(f"Unsafe staged target filename derived from {file_path!r}: {target_name!r}")

    if len(text) > MAX_SOURCE_FILE_BYTES:
        raise ValueError(
            f"Source text for {file_path!r} exceeds MAX_SOURCE_FILE_BYTES ({len(text)} > {MAX_SOURCE_FILE_BYTES})"
        )

    norm_enc = (detected_encoding or "").strip().lower().replace("_", "-")
    has_upper_ext = bool(src_path.suffix and src_path.suffix != src_path.suffix.lower())
    if not force_stage and not has_upper_ext and norm_enc in _DIRECT_UTF8_ENCODINGS:
        return src_path, False

    if staging_dir is not None:
        base_dir = Path(staging_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        sub_dir = Path(tempfile.mkdtemp(prefix=_STAGING_DIR_PREFIX, dir=str(base_dir)))
    else:
        sub_dir = Path(tempfile.mkdtemp(prefix=_STAGING_DIR_PREFIX))

    try:
        os.chmod(sub_dir, 0o700)
        staged_path = sub_dir / target_name
        if staged_path.resolve().parent != sub_dir.resolve():
            raise ValueError(
                f"Resolved staged path {staged_path.resolve()} escaped staging directory {sub_dir.resolve()}"
            )
        staged_path.write_text(text, encoding="utf-8")
    except Exception:
        shutil.rmtree(sub_dir, ignore_errors=True)
        raise
    return staged_path, True


def cleanup_staged_file(staged_path: Path, is_staged: bool) -> None:
    """Clean up a temporary UTF-8 staged file and its ``pb_utf8_*`` staging directory."""
    if not is_staged:
        return
    try:
        parent = staged_path.parent
        if parent.name.startswith(_STAGING_DIR_PREFIX):
            shutil.rmtree(parent, ignore_errors=True)
        elif staged_path.exists():
            staged_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("Failed to clean up staged UTF-8 file %s: %s", staged_path, exc)


def _convert_raw_graphify_payload(
    raw: Dict[str, Any],
    rel_path: str,
    project_id: str,
    graph_node_cls: Any,
    graph_edge_cls: Any,
    detected_encoding: Optional[str] = None,
) -> Tuple[List[Any], List[Any]]:
    """Convert raw dict output from `graphify.extract` into GraphNode/GraphEdge objects."""
    nodes: List[Any] = []
    edges: List[Any] = []

    norm_enc = (detected_encoding or "").strip().lower().replace("_", "-")

    for n in raw.get("nodes", []):
        nid = str(n.get("id") or "")
        if not nid:
            continue
        meta = {
            k: v
            for k, v in n.items()
            if k not in ("id", "label", "file_type", "source_file", "source_location", "community")
        }
        if norm_enc and norm_enc not in _DIRECT_UTF8_ENCODINGS:
            meta.setdefault("encoding", detected_encoding)
        nodes.append(
            graph_node_cls(
                id=nid,
                project_id=project_id,
                label=str(n.get("label") or nid),
                file_type="code",
                source_file=rel_path,
                source_location=n.get("source_location"),
                metadata=meta,
            )
        )

    for e in raw.get("edges", []):
        src = str(e.get("source") or "")
        tgt = str(e.get("target") or "")
        if not src or not tgt:
            continue
        edges.append(
            graph_edge_cls(
                project_id=project_id,
                source=src,
                target=tgt,
                relation=str(e.get("relation") or "calls"),
                confidence=str(e.get("confidence") or "EXTRACTED"),
                confidence_score=float(e.get("confidence_score", 1.0)),
                source_file=rel_path,
                source_location=e.get("source_location"),
                weight=float(e.get("weight", 1.0)),
            )
        )

    return nodes, edges


def _build_default_optional_ts_specs() -> List[Tuple[Tuple[str, ...], Dict[str, Any]]]:
    """Default LanguageConfig specs for optional external `tree_sitter_*` modules."""
    return [
        (
            (".pas",),
            {
                "ts_module": "tree_sitter_pascal",
                "ts_language_fn": "language",
                "class_types": frozenset({"declClass", "declIntf", "declRecord", "class_type", "interface_type"}),
                "function_types": frozenset({"defProc", "declProc", "procedure_declaration", "function_declaration"}),
                "import_types": frozenset({"declUses", "uses_clause"}),
                "call_types": frozenset({"exprCall", "call_expression"}),
            },
        ),
        (
            (".cbl", ".cob"),
            {
                "ts_module": "tree_sitter_cobol",
                "ts_language_fn": "language",
                "class_types": frozenset({"program_definition", "section_header"}),
                "function_types": frozenset({"paragraph_header", "paragraph"}),
                "import_types": frozenset({"copy_statement"}),
                "call_types": frozenset({"call_statement", "perform_statement"}),
            },
        ),
        (
            (".sql",),
            {
                "ts_module": "tree_sitter_sql",
                "ts_language_fn": "language",
                "class_types": frozenset({"create_table", "create_view", "create_table_statement"}),
                "function_types": frozenset({"create_function", "create_procedure", "create_trigger"}),
                "import_types": frozenset(),
                "call_types": frozenset({"invocation", "function_call"}),
            },
        ),
        (
            (".st", ".scl", ".exp"),
            {
                "ts_module": "tree_sitter_structured_text",
                "ts_language_fn": "language",
                "class_types": frozenset({"program", "function_block", "class_declaration", "interface_declaration"}),
                "function_types": frozenset({"function", "method_declaration"}),
                "import_types": frozenset(),
                "call_types": frozenset({"call_expression", "fb_invocation"}),
            },
        ),
    ]


def _load_default_parsers_from_extractor() -> Tuple[Dict[str, LegacyParserFn], CppAugmentFn, Set[str]]:
    """Lazily bind deterministic legacy parsers and C++ augmentation from `extractor.py`."""
    from projectbrain.graphify_engine import extractor as ext_mod

    legacy_map: Dict[str, LegacyParserFn] = {}
    for ext in ext_mod._ST_LASAL_EXTS:
        legacy_map[ext] = ext_mod._parse_st_lasal
    for ext in ext_mod._IDL_EXTS:
        legacy_map[ext] = ext_mod._parse_idl
    for ext in ext_mod._DEF_EXTS:
        legacy_map[ext] = ext_mod._parse_def
    for ext in ext_mod._RC_EXTS:
        legacy_map[ext] = ext_mod._parse_rc
    for ext in ext_mod._VB6_EXTS:
        legacy_map[ext] = ext_mod._parse_vb6
    for ext in ext_mod._DELPHI_EXTS:
        legacy_map[ext] = ext_mod._parse_delphi
    for ext in ext_mod._COBOL_EXTS:
        legacy_map[ext] = ext_mod._parse_cobol
    for ext in ext_mod._SQL_EXTS | ext_mod._SCRIPT_REG_EXTS:
        legacy_map[ext] = ext_mod._parse_sql_script_reg

    return legacy_map, ext_mod._parse_cpp_legacy, set(ext_mod._CPP_LEGACY_EXTS)


class ExtractorRegistry:
    """Unified registry routing code files across Tree-sitter, legacy structural, and C++ augment backends."""

    def __init__(
        self,
        *,
        legacy_parsers: Optional[Dict[str, LegacyParserFn]] = None,
        cpp_augment_parser: Optional[CppAugmentFn] = None,
        cpp_extensions: Optional[Set[str]] = None,
        auto_probe_optional_ts: bool = True,
    ):
        self._lock = threading.RLock()
        self._legacy_parsers: Dict[str, LegacyParserFn] = {}
        self._optional_ts_configs: Dict[str, Any] = {}

        if legacy_parsers is None or (cpp_augment_parser is None and cpp_extensions is None):
            default_legacy, default_cpp_fn, default_cpp_exts = _load_default_parsers_from_extractor()
        else:
            default_legacy, default_cpp_fn, default_cpp_exts = {}, None, set(_CPP_AUGMENT_EXTS)

        effective_legacy = default_legacy if legacy_parsers is None else legacy_parsers
        for ext, fn in effective_legacy.items():
            self._legacy_parsers[_normalize_ext(ext)] = fn

        self._cpp_augment_parser: Optional[CppAugmentFn] = (
            default_cpp_fn if cpp_augment_parser is None and legacy_parsers is None else cpp_augment_parser
        )
        self._cpp_extensions: Set[str] = {
            _normalize_ext(e) for e in (cpp_extensions if cpp_extensions is not None else default_cpp_exts)
        }

        if auto_probe_optional_ts:
            self.probe_optional_treesitter_languages()

    @classmethod
    def create_default(cls, auto_probe_optional_ts: bool = True) -> "ExtractorRegistry":
        """Build a registry pre-wired with all deterministic legacy parsers from `extractor.py`."""
        legacy_map, cpp_fn, cpp_exts = _load_default_parsers_from_extractor()
        return cls(
            legacy_parsers=legacy_map,
            cpp_augment_parser=cpp_fn,
            cpp_extensions=cpp_exts,
            auto_probe_optional_ts=auto_probe_optional_ts,
        )

    def register_legacy_parser(self, ext: str, parser_fn: LegacyParserFn) -> None:
        """Register or override a deterministic legacy structural parser for ``ext``."""
        norm = _normalize_ext(ext)
        if not norm:
            return
        with self._lock:
            self._legacy_parsers[norm] = parser_fn

    def probe_language_config(self, target: Any, ts_language_fn: str = "language") -> bool:
        """Probe at runtime whether a Tree-sitter grammar module or `LanguageConfig` is usable."""
        if isinstance(target, str):
            ts_module = target.strip()
            if not isinstance(ts_language_fn, str):
                return False
            lang_fn_name = (ts_language_fn or "language").strip()
        elif isinstance(target, dict):
            disallowed = set(target.keys()) - _ALLOWED_LANGUAGE_CONFIG_KEYS
            if disallowed:
                logger.debug("Rejected disallowed LanguageConfig keys: %s", sorted(disallowed))
                return False
            raw_mod = target.get("ts_module")
            if not isinstance(raw_mod, str):
                return False
            ts_module = raw_mod.strip()
            raw_fn = target.get("ts_language_fn")
            if raw_fn is not None:
                if not isinstance(raw_fn, str):
                    return False
                lang_fn_name = raw_fn.strip()
            else:
                if not isinstance(ts_language_fn, str):
                    return False
                lang_fn_name = (ts_language_fn or "language").strip()
        else:
            raw_mod = getattr(target, "ts_module", None)
            if not isinstance(raw_mod, str):
                return False
            ts_module = raw_mod.strip()
            raw_fn = getattr(target, "ts_language_fn", None)
            if raw_fn is not None:
                if not isinstance(raw_fn, str):
                    return False
                lang_fn_name = raw_fn.strip()
            else:
                if not isinstance(ts_language_fn, str):
                    return False
                lang_fn_name = (ts_language_fn or "language").strip()

        if not ts_module or not _TS_MODULE_RE.fullmatch(ts_module):
            logger.debug("Rejected unsafe tree-sitter module name: %r", ts_module)
            return False
        if not lang_fn_name or not _TS_LANGUAGE_FN_RE.fullmatch(lang_fn_name):
            logger.debug("Rejected unsafe tree-sitter language_fn name: %r", lang_fn_name)
            return False

        try:
            if importlib.util.find_spec("tree_sitter") is None:
                return False
            if importlib.util.find_spec(ts_module) is None:
                return False
            mod = importlib.import_module(ts_module)
            lang_factory = getattr(mod, lang_fn_name, None) or getattr(mod, "language", None)
            return callable(lang_factory)
        except Exception as exc:
            logger.debug("Optional tree-sitter module %s unavailable: %s", ts_module, exc)
            return False

    def register_optional_treesitter_language(self, ext: str, config: Any) -> bool:
        """Register an optional external Tree-sitter `LanguageConfig` for ``ext``.

        Verifies at runtime that `tree_sitter` and `config.ts_module` are installed
        and expose a callable language factory. Returns ``True`` if registered and
        usable, or ``False`` if the grammar is unavailable (leaving the deterministic
        `legacy_structural` fallback active).
        """
        norm = _normalize_ext(ext)
        if not norm or config is None:
            return False

        # Coerce dict specs into graphify.extract.LanguageConfig if needed.
        resolved_config = config
        if isinstance(config, dict):
            disallowed = set(config.keys()) - _ALLOWED_LANGUAGE_CONFIG_KEYS
            if disallowed:
                logger.debug(
                    "Rejected disallowed LanguageConfig keys for %s: %s",
                    norm,
                    sorted(disallowed),
                )
                return False
            raw_mod = config.get("ts_module")
            if not isinstance(raw_mod, str) or not _TS_MODULE_RE.fullmatch(raw_mod.strip()):
                logger.debug("Rejected unsafe ts_module for %s: %r", norm, raw_mod)
                return False
            safe_config: Dict[str, Any] = {
                k: v for k, v in config.items() if k in _ALLOWED_LANGUAGE_CONFIG_KEYS
            }
            safe_config["ts_module"] = raw_mod.strip()
            if "ts_language_fn" in safe_config and safe_config["ts_language_fn"] is not None:
                raw_fn = safe_config["ts_language_fn"]
                if not isinstance(raw_fn, str) or not _TS_LANGUAGE_FN_RE.fullmatch(raw_fn.strip()):
                    logger.debug("Rejected unsafe ts_language_fn for %s: %r", norm, raw_fn)
                    return False
                safe_config["ts_language_fn"] = raw_fn.strip()

            try:
                from graphify.extract import LanguageConfig  # type: ignore

                resolved_config = LanguageConfig(**safe_config)
            except Exception as exc:
                logger.debug("Cannot construct LanguageConfig for %s: %s", norm, exc)
                return False

        if not self.probe_language_config(resolved_config):
            return False

        with self._lock:
            self._optional_ts_configs[norm] = resolved_config
        return True

    def probe_optional_treesitter_languages(self) -> Dict[str, bool]:
        """Probe optional external `tree_sitter_*` packages at runtime.

        If a grammar package (e.g. `tree_sitter_pascal`, `tree_sitter_cobol`,
        `tree_sitter_sql`, `tree_sitter_structured_text`) is installed, registers
        it via `graphify.extract.LanguageConfig`; otherwise leaves `legacy_structural`
        active with zero extra dependencies required.
        """
        results: Dict[str, bool] = {}
        try:
            from graphify.extract import LanguageConfig  # type: ignore
        except Exception:
            for exts, _ in _build_default_optional_ts_specs():
                for ext in exts:
                    results[ext] = False
            return results

        for exts, spec in _build_default_optional_ts_specs():
            try:
                cfg = LanguageConfig(**spec)
            except Exception:
                for ext in exts:
                    results[ext] = False
                continue
            for ext in exts:
                results[ext] = self.register_optional_treesitter_language(ext, cfg)
        return results

    @property
    def optional_treesitter_extensions(self) -> Set[str]:
        """Return extensions currently backed by a registered optional Tree-sitter grammar."""
        with self._lock:
            return set(self._optional_ts_configs.keys())

    def get_backends_for_extension(self, ext: str) -> List[ParserBackend]:
        """Return the ordered list of backends active for ``ext``."""
        norm = _normalize_ext(ext)
        with self._lock:
            if norm in self._optional_ts_configs:
                backends = [ParserBackend.GRAPHIFY_TS]
                if norm in self._legacy_parsers or norm in _DEFAULT_LEGACY_EXTS:
                    backends.append(ParserBackend.LEGACY_STRUCTURAL)
                return backends
            if norm in self._legacy_parsers or norm in _DEFAULT_LEGACY_EXTS:
                return [ParserBackend.LEGACY_STRUCTURAL]
            if norm in self._cpp_extensions:
                if norm in _GRAPHIFY_SKIP_EXTS:
                    return [ParserBackend.CPP_AUGMENT]
                return [ParserBackend.GRAPHIFY_TS, ParserBackend.CPP_AUGMENT]
            if norm in (".py", ".pyw"):
                return [ParserBackend.GRAPHIFY_TS, ParserBackend.PYTHON_AST_FALLBACK]
            return [ParserBackend.GRAPHIFY_TS]

    def resolve(self, ext: str) -> ExtractorStrategy:
        """Resolve ``ext`` to an `ExtractorStrategy` descriptor for runtime inspection and dispatch."""
        norm = _normalize_ext(ext)
        backends = tuple(self.get_backends_for_extension(norm))
        primary = backends[0]
        with self._lock:
            opt_cfg = self._optional_ts_configs.get(norm)
        if primary is ParserBackend.LEGACY_STRUCTURAL:
            return LegacyStructuralStrategy(
                extension=norm,
                backend=primary,
                backends=backends,
                is_legacy=True,
                language_config=None,
            )
        if ParserBackend.CPP_AUGMENT in backends:
            return CppAugmentStrategy(
                extension=norm,
                backend=primary,
                backends=backends,
                is_legacy=(primary is ParserBackend.CPP_AUGMENT),
                language_config=opt_cfg,
            )
        return TreeSitterStrategy(
            extension=norm,
            backend=primary,
            backends=backends,
            is_legacy=False,
            language_config=opt_cfg,
        )

    def _run_graphify_ts(
        self,
        file_path: Path,
        text: str,
        detected_encoding: str,
        rel_path: str,
        project_id: str,
        graph_node_cls: Any,
        graph_edge_cls: Any,
        *,
        optional_config: Any = None,
        staging_dir: Optional[Path] = None,
    ) -> Tuple[List[Any], List[Any], bool]:
        """Run `graphify.extract` on a UTF-8 staged path under `_TREE_SITTER_LOCK`."""
        staged_path, is_staged = staged_utf8_file(
            file_path=file_path,
            text=text,
            detected_encoding=detected_encoding,
            staging_dir=staging_dir,
        )
        try:
            import graphify.extract as g_extract  # type: ignore

            with _TREE_SITTER_LOCK:
                if optional_config is not None:
                    raw = g_extract._extract_generic(staged_path, optional_config)
                else:
                    raw = g_extract.extract([staged_path])

            if raw and not raw.get("error") and (raw.get("nodes") or raw.get("edges")):
                nodes, edges = _convert_raw_graphify_payload(
                    raw,
                    rel_path=rel_path,
                    project_id=project_id,
                    graph_node_cls=graph_node_cls,
                    graph_edge_cls=graph_edge_cls,
                    detected_encoding=detected_encoding,
                )
                return nodes, edges, True
            return [], [], False
        except Exception as err:
            logger.debug("graphify.extract failed on %s: %s", rel_path, err)
            return [], [], False
        finally:
            cleanup_staged_file(staged_path, is_staged)

    def is_graphify_ts_batch_eligible(self, ext: str) -> bool:
        """Return True if ``ext`` should be routed through batch `graphify.extract.extract`."""
        norm = _normalize_ext(ext)
        if norm not in _GRAPHIFY_DISPATCH_EXTS:
            return False
        strategy = self.resolve(norm)
        return strategy.backend is ParserBackend.GRAPHIFY_TS and strategy.language_config is None

    def extract_graphify_ts_batch(
        self,
        items: List[Tuple[int, Path, str]],
        project_id: str,
        branch: str,
        *,
        staging_dir: Optional[Path] = None,
        python_fallback_fn: Optional[PythonFallbackFn] = None,
    ) -> Dict[int, Any]:
        """Batch-extract `graphify_ts` files (with UTF-8 staging) so pass-2 cross-file resolution runs.

        Maps every node and edge back to each workspace ``rel_path`` (never leaking staging
        paths), applies C/C++ legacy augmentation (`_parse_cpp_legacy`) and Python AST
        fallback when appropriate, and cleans up all temporary staged files.
        """
        if not items:
            return {}

        from projectbrain.graphify_engine.extractor import GraphDelta, GraphEdge, GraphNode

        owned_staging_dir: Optional[Path] = None
        effective_staging_dir = staging_dir
        if effective_staging_dir is None:
            owned_staging_dir = Path(tempfile.mkdtemp(prefix="pb_batch_"))
            os.chmod(owned_staging_dir, 0o700)
            effective_staging_dir = owned_staging_dir

        staged_entries: List[Tuple[int, Path, str, str, str, str, Path, bool]] = []
        path_to_entry: Dict[str, int] = {}
        skipped_oversized: Dict[int, Any] = {}

        try:
            with self._lock:
                cpp_exts = set(self._cpp_extensions)

            for idx, file_path, rel_path in items:
                ext = _normalize_ext(file_path.suffix)
                try:
                    file_size = file_path.stat().st_size
                except OSError:
                    file_size = 0
                if file_size > MAX_SOURCE_FILE_BYTES:
                    logger.warning(
                        "Skipping oversized file %s (%d bytes > MAX_SOURCE_FILE_BYTES=%d)",
                        rel_path,
                        file_size,
                        MAX_SOURCE_FILE_BYTES,
                    )
                    skipped_oversized[idx] = GraphDelta(
                        project_id=project_id,
                        branch=branch,
                        nodes=[],
                        edges=[],
                        hyperedges=[],
                    )
                    continue

                text = ""
                detected_encoding = "utf-8"
                try:
                    text, detected_encoding = read_text_with_encoding(file_path)
                except Exception as err:
                    logger.debug("read_text_with_encoding failed on %s: %s", rel_path, err)

                staged_path, is_staged = staged_utf8_file(
                    file_path=file_path,
                    text=text,
                    detected_encoding=detected_encoding,
                    staging_dir=effective_staging_dir,
                )
                kept_text = text if ext in cpp_exts else ""
                del text

                staged_entries.append((idx, file_path, rel_path, ext, kept_text, detected_encoding, staged_path, is_staged))
                path_to_entry[str(staged_path)] = idx
                path_to_entry[str(staged_path.resolve())] = idx
                path_to_entry[str(file_path)] = idx
                path_to_entry[str(file_path.resolve())] = idx

            if not staged_entries:
                return skipped_oversized

            def _invoke_extract(paths_subset: List[Path]) -> Dict[str, Any]:
                if not paths_subset:
                    return {}
                with _TREE_SITTER_LOCK:
                    return g_extract.extract(paths_subset) or {}

            raw: Dict[str, Any] = {"nodes": [], "edges": []}
            try:
                import graphify.extract as g_extract  # type: ignore

                batch_paths = [entry[6] for entry in staged_entries]
                # Ensure graphify.extract never computes '/' or system temp root as common root.
                split_batches = False
                if len(batch_paths) > 1:
                    try:
                        common = os.path.commonpath([str(p.resolve()) for p in batch_paths])
                        common_resolved = Path(common).resolve()
                        temp_root = Path(tempfile.gettempdir()).resolve()
                        has_direct = any(not entry[7] for entry in staged_entries)
                        has_staged = any(entry[7] for entry in staged_entries)
                        if len(common_resolved.parts) <= 1 or (
                            has_direct
                            and has_staged
                            and (common_resolved == temp_root or temp_root.is_relative_to(common_resolved))
                        ):
                            split_batches = True
                    except ValueError:
                        split_batches = True

                if split_batches:
                    direct_paths = [entry[6] for entry in staged_entries if not entry[7]]
                    staged_only_paths = [entry[6] for entry in staged_entries if entry[7]]
                    for sub_paths in (direct_paths, staged_only_paths):
                        sub_raw = _invoke_extract(sub_paths)
                        if isinstance(sub_raw, dict) and not sub_raw.get("error"):
                            raw["nodes"].extend(sub_raw.get("nodes", []))
                            raw["edges"].extend(sub_raw.get("edges", []))
                        elif isinstance(sub_raw, dict) and sub_raw.get("error"):
                            return dict(skipped_oversized)
                else:
                    res = _invoke_extract(batch_paths)
                    if not isinstance(res, dict) or res.get("error"):
                        return dict(skipped_oversized)
                    raw = res
            except Exception as err:
                logger.debug("Batch graphify.extract failed (%s); caller will fall back per-file.", err)
                return dict(skipped_oversized)

            if not isinstance(raw, dict) or raw.get("error"):
                return dict(skipped_oversized)

            per_idx_raw_nodes: Dict[int, List[Dict[str, Any]]] = {e[0]: [] for e in staged_entries}
            per_idx_raw_edges: Dict[int, List[Dict[str, Any]]] = {e[0]: [] for e in staged_entries}
            node_id_to_idx: Dict[str, int] = {}
            unmatched_nodes: Dict[str, Dict[str, Any]] = {}

            def _resolve_idx(raw_src: Any) -> Optional[int]:
                s = str(raw_src or "")
                if not s:
                    return None
                hit = path_to_entry.get(s)
                if hit is not None:
                    return hit
                try:
                    resolved = str(Path(s).resolve())
                    hit = path_to_entry.get(resolved)
                    if hit is not None:
                        return hit
                except Exception:
                    pass
                return None

            for n in raw.get("nodes", []):
                nid = str(n.get("id") or "")
                owner_idx = _resolve_idx(n.get("source_file"))
                if owner_idx is not None:
                    per_idx_raw_nodes[owner_idx].append(n)
                    if nid:
                        node_id_to_idx[nid] = owner_idx
                elif nid:
                    unmatched_nodes[nid] = n

            attached_unmatched: Dict[int, Set[str]] = {e[0]: set() for e in staged_entries}
            for e in raw.get("edges", []):
                owner_idx = _resolve_idx(e.get("source_file"))
                if owner_idx is None:
                    owner_idx = node_id_to_idx.get(str(e.get("source") or ""))
                if owner_idx is None:
                    continue
                per_idx_raw_edges[owner_idx].append(e)
                tgt = str(e.get("target") or "")
                if tgt in unmatched_nodes and tgt not in attached_unmatched[owner_idx]:
                    attached_unmatched[owner_idx].add(tgt)
                    per_idx_raw_nodes[owner_idx].append(unmatched_nodes[tgt])

            results: Dict[int, Any] = {}
            for idx, file_path, rel_path, ext, text, detected_encoding, _staged_p, _is_staged in staged_entries:
                file_raw = {
                    "nodes": per_idx_raw_nodes.get(idx, []),
                    "edges": per_idx_raw_edges.get(idx, []),
                }
                nodes, edges = _convert_raw_graphify_payload(
                    file_raw,
                    rel_path=rel_path,
                    project_id=project_id,
                    graph_node_cls=GraphNode,
                    graph_edge_cls=GraphEdge,
                    detected_encoding=detected_encoding,
                )

                with self._lock:
                    cpp_augment_fn = self._cpp_augment_parser if ext in self._cpp_extensions else None

                if cpp_augment_fn is not None:
                    nodes, edges = cpp_augment_fn(
                        text,
                        rel_path,
                        project_id,
                        detected_encoding,
                        existing_nodes=nodes,
                        existing_edges=edges,
                    )
                elif not nodes and not edges and ext in (".py", ".pyw") and python_fallback_fn is not None:
                    nodes, edges = python_fallback_fn(file_path, rel_path, project_id)

                results[idx] = GraphDelta(
                    project_id=project_id,
                    branch=branch,
                    nodes=nodes,
                    edges=edges,
                    hyperedges=[],
                )
            results.update(skipped_oversized)
            return results
        finally:
            for _idx, _fp, _rp, _ext, _txt, _enc, staged_path, is_staged in staged_entries:
                cleanup_staged_file(staged_path, is_staged)
            if owned_staging_dir is not None and owned_staging_dir.exists():
                shutil.rmtree(owned_staging_dir, ignore_errors=True)

    def extract_code(
        self,
        file_path: Path,
        rel_path: str,
        project_id: str,
        branch: str,
        *,
        text: Optional[str] = None,
        detected_encoding: Optional[str] = None,
        python_fallback_fn: Optional[PythonFallbackFn] = None,
        staging_dir: Optional[Path] = None,
    ) -> Any:
        """Extract a `GraphDelta` for a single code file via the appropriate backend(s)."""
        from projectbrain.graphify_engine.extractor import GraphDelta, GraphEdge, GraphNode

        ext = _normalize_ext(file_path.suffix)

        if text is None or detected_encoding is None:
            try:
                file_size = file_path.stat().st_size
            except OSError:
                file_size = 0
            if file_size > MAX_SOURCE_FILE_BYTES:
                logger.warning(
                    "Skipping oversized file %s (%d bytes > MAX_SOURCE_FILE_BYTES=%d)",
                    rel_path,
                    file_size,
                    MAX_SOURCE_FILE_BYTES,
                )
                return GraphDelta(
                    project_id=project_id,
                    branch=branch,
                    nodes=[],
                    edges=[],
                    hyperedges=[],
                )
            text = ""
            detected_encoding = "utf-8"
            try:
                text, detected_encoding = read_text_with_encoding(file_path)
            except Exception as err:
                logger.debug("read_text_with_encoding failed on %s: %s", rel_path, err)
        elif len(text) > MAX_SOURCE_FILE_BYTES:
            logger.warning(
                "Skipping oversized source text for %s (%d chars > MAX_SOURCE_FILE_BYTES=%d)",
                rel_path,
                len(text),
                MAX_SOURCE_FILE_BYTES,
            )
            return GraphDelta(
                project_id=project_id,
                branch=branch,
                nodes=[],
                edges=[],
                hyperedges=[],
            )

        with self._lock:
            optional_cfg = self._optional_ts_configs.get(ext)
            legacy_fn = self._legacy_parsers.get(ext)
            cpp_augment_fn = self._cpp_augment_parser if ext in self._cpp_extensions else None

        # 1. Optional external Tree-sitter grammar (with clean fallback to legacy_structural)
        if optional_cfg is not None:
            nodes, edges, ok = self._run_graphify_ts(
                file_path=file_path,
                text=text,
                detected_encoding=detected_encoding,
                rel_path=rel_path,
                project_id=project_id,
                graph_node_cls=GraphNode,
                graph_edge_cls=GraphEdge,
                optional_config=optional_cfg,
                staging_dir=staging_dir,
            )
            if ok and (legacy_fn is None or len(nodes) > 1 or len(edges) > 0):
                return GraphDelta(
                    project_id=project_id,
                    branch=branch,
                    nodes=nodes,
                    edges=edges,
                    hyperedges=[],
                )
            if legacy_fn is not None:
                nodes, edges = legacy_fn(text, rel_path, project_id, detected_encoding)
                return GraphDelta(
                    project_id=project_id,
                    branch=branch,
                    nodes=nodes,
                    edges=edges,
                    hyperedges=[],
                )

        # 2. Deterministic legacy/industrial structural parsers
        if legacy_fn is not None:
            nodes, edges = legacy_fn(text, rel_path, project_id, detected_encoding)
            return GraphDelta(
                project_id=project_id,
                branch=branch,
                nodes=nodes,
                edges=edges,
                hyperedges=[],
            )

        # 3. Built-in graphify Tree-sitter extraction (with UTF-8 staging for UTF-16/CP932/etc.)
        nodes: List[Any] = []
        edges: List[Any] = []
        extracted_ok = False
        if ext not in _GRAPHIFY_SKIP_EXTS:
            nodes, edges, extracted_ok = self._run_graphify_ts(
                file_path=file_path,
                text=text,
                detected_encoding=detected_encoding,
                rel_path=rel_path,
                project_id=project_id,
                graph_node_cls=GraphNode,
                graph_edge_cls=GraphEdge,
                staging_dir=staging_dir,
            )

        # 4. C/C++ legacy augmentation (macros, #pragma pack structs, enums, prototypes, Win32 APIs)
        if cpp_augment_fn is not None:
            nodes, edges = cpp_augment_fn(
                text,
                rel_path,
                project_id,
                detected_encoding,
                existing_nodes=nodes,
                existing_edges=edges,
            )
        # 5. Python AST Fallback if graphify produced nothing or errored on Python
        elif not extracted_ok and ext in (".py", ".pyw") and python_fallback_fn is not None:
            nodes, edges = python_fallback_fn(file_path, rel_path, project_id)

        return GraphDelta(
            project_id=project_id,
            branch=branch,
            nodes=nodes,
            edges=edges,
            hyperedges=[],
        )


_DEFAULT_REGISTRY_LOCK = threading.Lock()
_DEFAULT_REGISTRY: Optional[ExtractorRegistry] = None


def get_default_registry() -> ExtractorRegistry:
    """Return the lazily-initialized process-wide default `ExtractorRegistry`."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        with _DEFAULT_REGISTRY_LOCK:
            if _DEFAULT_REGISTRY is None:
                _DEFAULT_REGISTRY = ExtractorRegistry.create_default(auto_probe_optional_ts=True)
    return _DEFAULT_REGISTRY


def register_optional_treesitter_language(
    ext: str,
    config: Any,
    registry: Optional[ExtractorRegistry] = None,
) -> bool:
    """Register an optional external Tree-sitter `LanguageConfig` on ``registry`` (or default registry)."""
    target = registry or get_default_registry()
    return target.register_optional_treesitter_language(ext, config)


__all__ = [
    "CppAugmentStrategy",
    "ExtractorRegistry",
    "ExtractorStrategy",
    "LegacyStructuralStrategy",
    "MAX_SOURCE_FILE_BYTES",
    "ParserBackend",
    "TreeSitterStrategy",
    "_TREE_SITTER_LOCK",
    "cleanup_staged_file",
    "get_default_registry",
    "register_optional_treesitter_language",
    "staged_utf8_file",
]
