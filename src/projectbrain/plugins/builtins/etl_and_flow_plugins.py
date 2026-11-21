"""Built-in ETL, Knowledge Linker, and Document Exporter plugins for Project-Brain v3.0."""

from __future__ import annotations

import csv
import hashlib
import inspect
import io
import json
import mimetypes
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import yaml

# ---------------------------------------------------------------------------
# Concurrency-Safe Defensive Base Import Pattern (Plan Section 3.2)
# ---------------------------------------------------------------------------
try:
    from projectbrain.plugins.base import (
        BasePlugin,
        BasePluginModel,
        CanonicalOracleAssertion,
        CanonicalTestIntent,
        CanonicalTestStep,
        DocExporterPlugin,
        ErrorCodeRule,
        ETLParserPlugin,
        KnowledgeLinkerPlugin,
        PluginCategory,
        PluginContext,
        PluginMetadata,
        StateFlowNode,
        StateFlowTransition,
    )
except ImportError:  # pragma: no cover - defensive fallback for concurrent Stage 3 build
    from abc import ABC, abstractmethod
    from enum import Enum
    from pydantic import BaseModel, Field

    try:
        from pydantic import ConfigDict
        _HAS_CONFIG_DICT = True
    except ImportError:
        ConfigDict = None
        _HAS_CONFIG_DICT = False

    class PluginCategory(str, Enum):
        GRAPH_BACKEND = "graph_backend"
        VECTOR_BACKEND = "vector_backend"
        ETL_PARSER = "etl_parser"
        KNOWLEDGE_LINKER = "knowledge_linker"
        DOC_EXPORTER = "doc_exporter"
        SCRIPT_CACHE = "script_cache"
        ARTIFACT_STORE = "artifact_store"
        BRAIN_CONSUMER = "brain_consumer"
        WEBHOOK = "webhook"
        SOURCE_CONNECTOR = "source_connector"

    class BasePluginModel(BaseModel):
        if _HAS_CONFIG_DICT:
            model_config = ConfigDict(extra="allow")
        else:
            class Config:
                extra = "allow"

        def to_dict(self) -> Dict[str, Any]:
            if hasattr(self, "model_dump"):
                return self.model_dump()
            return self.dict()

    class PluginMetadata(BasePluginModel):
        plugin_id: str
        name: str
        version: str = "1.0.0"
        category: str
        description: str = ""
        author: str = "projectbrain"
        priority: int = 100
        capabilities: List[str] = Field(default_factory=list)
        requires_env: List[str] = Field(default_factory=list)
        config_schema: Dict[str, Any] = Field(default_factory=dict)
        default_enabled: bool = True
        entrypoint: Optional[str] = None

    class PluginContext(BasePluginModel):
        project_id: str = "default"
        branch: str = "main"
        version_tag: Optional[str] = None
        workspace_path: str = "."
        config: Dict[str, Any] = Field(default_factory=dict)
        registry: Optional[Any] = None

    class CanonicalTestStep(BasePluginModel):
        step_index: int
        action: str
        target_intent: str = ""
        parameters: Dict[str, Any] = Field(default_factory=dict)
        timeout_ms: int = 10000

    class CanonicalOracleAssertion(BasePluginModel):
        assertion_type: str
        expected_value: Any
        tolerance: Optional[str] = None
        severity: str = "error"

    class CanonicalTestIntent(BasePluginModel):
        intent_id: str
        title: str
        description: str = ""
        priority: str = "P1"
        target_platforms: List[str] = Field(default_factory=list)
        preconditions: List[Dict[str, Any]] = Field(default_factory=list)
        steps: List[CanonicalTestStep] = Field(default_factory=list)
        expected_oracle_assertions: List[CanonicalOracleAssertion] = Field(default_factory=list)
        linked_requirements: List[str] = Field(default_factory=list)
        tags: List[str] = Field(default_factory=list)
        version_tag: Optional[str] = None

    class ErrorCodeRule(BasePluginModel):
        rule_id: str
        code: str
        protocol: str
        severity: str = "error"
        pattern: str
        description: str = ""
        fault_domain: str = "Firmware"
        remediation_hint: str = ""
        source_file: str = ""
        version_tag: Optional[str] = None

    class StateFlowNode(BasePluginModel):
        node_id: str
        node_kind: str = "STATE_NODE"
        label: str
        domain: str = "firmware"
        platform: Optional[str] = None
        screen_hash: Optional[str] = None
        ui_fingerprint: Dict[str, Any] = Field(default_factory=dict)
        version_tag: Optional[str] = None
        metadata: Dict[str, Any] = Field(default_factory=dict)

    class StateFlowTransition(BasePluginModel):
        source_state: str
        target_state: str
        trigger_action: str
        guard_condition: Optional[str] = None
        expected_signals: List[str] = Field(default_factory=list)
        timeout_ms: int = 5000
        version_tag: Optional[str] = None
        metadata: Dict[str, Any] = Field(default_factory=dict)

    class BasePlugin(ABC):
        metadata: PluginMetadata

        def __init__(self, config: Optional[Dict[str, Any]] = None):
            self.config: Dict[str, Any] = dict(config or {})
            self._initialized: bool = False

        def on_load(self, config: Optional[Dict[str, Any]] = None) -> None:
            if config:
                self.config.update(config)

        def initialize(self, context: Optional[PluginContext] = None) -> None:
            if context and context.config:
                self.config.update(context.config)
            self._initialized = True

        def shutdown(self) -> None:
            self._initialized = False

        def validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
            return dict(config)

        def health_check(self) -> Dict[str, Any]:
            return {
                "plugin_id": self.metadata.plugin_id,
                "category": self.metadata.category,
                "status": "healthy",
                "initialized": self._initialized,
            }

    class ETLParserPlugin(BasePlugin):
        supported_extensions: Tuple[str, ...] = ()
        supported_formats: Tuple[str, ...] = ()

        @abstractmethod
        def can_parse(
            self,
            source_name: str,
            format_hint: Optional[str] = None,
            content_preview: Optional[str] = None,
        ) -> bool:
            pass

        @abstractmethod
        async def parse(
            self,
            source: Union[str, bytes],
            source_name: str = "input",
            project_id: str = "default",
            branch: str = "main",
            version_tag: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            pass

    class KnowledgeLinkerPlugin(BasePlugin):
        @abstractmethod
        def link(
            self,
            project_id: str,
            branch: str = "main",
            graph_data: Optional[Any] = None,
            chunks: Optional[List[Any]] = None,
            version_tag: Optional[str] = None,
            context: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            pass

    class DocExporterPlugin(BasePlugin):
        export_format: str = "wiki"

        @abstractmethod
        def export(
            self,
            graph_data: Any,
            output_path: str,
            linker_index: Optional[Dict[str, Any]] = None,
            context: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            pass


from projectbrain.graphify_engine.models import GraphDelta, GraphEdge, GraphNode


# ---------------------------------------------------------------------------
# Shared Helpers
# ---------------------------------------------------------------------------
def _model_to_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict()


def _is_existing_file(source: Union[str, Path, bytes]) -> Optional[Path]:
    if isinstance(source, Path):
        return source if source.is_file() else None
    if isinstance(source, str) and "\n" not in source and len(source) < 1024:
        try:
            p = Path(source)
            if p.is_file():
                return p
        except OSError:
            return None
    return None


def _read_source_bytes(source: Union[str, Path, bytes]) -> Tuple[bytes, Optional[Path]]:
    path = _is_existing_file(source)
    if path is not None:
        return path.read_bytes(), path
    if isinstance(source, bytes):
        return source, None
    return str(source).encode("utf-8"), None


def _read_source_text(source: Union[str, Path, bytes]) -> Tuple[str, Optional[Path]]:
    raw, path = _read_source_bytes(source)
    return raw.decode("utf-8", errors="replace"), path


def _short_hash(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.\-]+", "_", value.strip())
    return slug.strip("_") or "item"


def _build_delta(
    project_id: str,
    branch: str,
    nodes: List[GraphNode],
    edges: List[GraphEdge],
) -> Dict[str, Any]:
    delta = GraphDelta(
        project_id=project_id,
        branch=branch,
        nodes=nodes,
        edges=edges,
    )
    return _model_to_dict(delta)


def _resolve_workspace(
    output_path: Optional[Union[str, Path]],
    config: Dict[str, Any],
    context: Optional[Dict[str, Any]],
) -> Union[str, Path]:
    if context and context.get("workspace_path"):
        return context["workspace_path"]
    if config.get("workspace_path"):
        return config["workspace_path"]
    if output_path:
        p = Path(output_path)
        if p.is_absolute():
            return p.parent
    return "."


# ---------------------------------------------------------------------------
# 1-7: Built-in Wrappers for Existing v1.5 Engines
# ---------------------------------------------------------------------------
class GraphifyASTETLPlugin(ETLParserPlugin):
    """Wraps GraphifyExtractor for AST code and Markdown document graph extraction."""

    metadata = PluginMetadata(
        plugin_id="etl.graphify_ast",
        name="Graphify AST & Doc Extractor",
        version="1.5.0",
        category=PluginCategory.ETL_PARSER.value,
        description="Wraps GraphifyExtractor for multi-language AST and Markdown graph extraction.",
        priority=100,
        capabilities=["ast_extraction", "markdown_graph", "workspace_scan"],
    )
    supported_extensions: Tuple[str, ...] = (
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".md",
    )
    supported_formats: Tuple[str, ...] = ("ast", "code", "workspace", "graphify")

    def can_parse(
        self,
        source_name: str,
        format_hint: Optional[str] = None,
        content_preview: Optional[str] = None,
    ) -> bool:
        if format_hint and format_hint.lower() in self.supported_formats:
            return True
        ext = Path(source_name).suffix.lower()
        return ext in self.supported_extensions or Path(source_name).is_dir()

    async def parse(
        self,
        source: Union[str, bytes],
        source_name: str = "input",
        project_id: str = "default",
        branch: str = "main",
        version_tag: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from projectbrain.graphify_engine.extractor import GraphifyExtractor

        cache_dir = self.config.get("cache_dir")
        if isinstance(source, (str, Path)) and len(str(source)) < 1024 and "\n" not in str(source):
            src_path = Path(str(source))
            if src_path.is_dir():
                extractor = GraphifyExtractor(cache_dir=cache_dir or (src_path / ".projectbrain" / "cache"))
                delta = extractor.extract_workspace(
                    workspace_path=src_path,
                    project_id=project_id,
                    branch=branch,
                    use_cache=bool(self.config.get("use_cache", True)),
                )
                return {
                    "plugin_id": self.metadata.plugin_id,
                    "graph_delta": _model_to_dict(delta),
                    "canonical_intents": [],
                    "error_rules": [],
                    "flow_nodes": [],
                    "flow_edges": [],
                    "chunks": [],
                }
            if src_path.is_file():
                extractor = GraphifyExtractor(cache_dir=cache_dir or (src_path.parent / ".projectbrain" / "cache"))
                if src_path.suffix.lower() == ".md":
                    delta = extractor.extract_document_graph(
                        file_path=str(src_path),
                        markdown_content=src_path.read_text(encoding="utf-8", errors="replace"),
                        project_id=project_id,
                        branch=branch,
                        metadata={**(metadata or {}), **({"version_tag": version_tag} if version_tag else {})},
                    )
                else:
                    delta = extractor.extract_workspace(
                        workspace_path=src_path.parent,
                        project_id=project_id,
                        branch=branch,
                        scoped_paths=[src_path.name],
                        use_cache=bool(self.config.get("use_cache", False)),
                    )
                return {
                    "plugin_id": self.metadata.plugin_id,
                    "graph_delta": _model_to_dict(delta),
                    "canonical_intents": [],
                    "error_rules": [],
                    "flow_nodes": [],
                    "flow_edges": [],
                    "chunks": [],
                }

        raw_bytes, _ = _read_source_bytes(source)
        file_name = Path(source_name).name or "input.py"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            target = tmp_path / file_name
            target.write_bytes(raw_bytes)
            extractor = GraphifyExtractor(cache_dir=cache_dir or (tmp_path / ".cache"))
            if target.suffix.lower() == ".md":
                delta = extractor.extract_document_graph(
                    file_path=source_name,
                    markdown_content=raw_bytes.decode("utf-8", errors="replace"),
                    project_id=project_id,
                    branch=branch,
                    metadata={**(metadata or {}), **({"version_tag": version_tag} if version_tag else {})},
                )
            else:
                delta = extractor.extract_workspace(
                    workspace_path=tmp_path,
                    project_id=project_id,
                    branch=branch,
                    scoped_paths=[file_name],
                    use_cache=False,
                )
        return {
            "plugin_id": self.metadata.plugin_id,
            "graph_delta": _model_to_dict(delta),
            "canonical_intents": [],
            "error_rules": [],
            "flow_nodes": [],
            "flow_edges": [],
            "chunks": [],
        }


class MultimodalDocETLPlugin(ETLParserPlugin):
    """Wraps DocumentPipeline for multimodal PDF, Office, Markdown, and Image ingestion."""

    metadata = PluginMetadata(
        plugin_id="etl.multimodal_doc",
        name="Multimodal Document Pipeline",
        version="1.5.0",
        category=PluginCategory.ETL_PARSER.value,
        description="Wraps DocumentPipeline for multimodal document parsing, chunking, and graph delta extraction.",
        priority=90,
        capabilities=["pdf", "docx", "xlsx", "pptx", "markdown", "image_ocr"],
    )
    supported_extensions: Tuple[str, ...] = (
        ".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt", ".csv", ".png", ".jpg", ".jpeg",
    )
    supported_formats: Tuple[str, ...] = ("document", "pdf", "docx", "xlsx", "pptx", "markdown", "image")

    def can_parse(
        self,
        source_name: str,
        format_hint: Optional[str] = None,
        content_preview: Optional[str] = None,
    ) -> bool:
        if format_hint and format_hint.lower() in self.supported_formats:
            return True
        return Path(source_name).suffix.lower() in self.supported_extensions

    @staticmethod
    async def _invoke_process_file(
        pipeline: Any,
        base_kwargs: Dict[str, Any],
        ocr_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Invoke DocumentPipeline.process_file forwarding OCR/Local-AI kwargs safely."""
        if not ocr_kwargs:
            return await pipeline.process_file(**base_kwargs)

        accepted_extra: Dict[str, Any] = {}
        try:
            sig = inspect.signature(pipeline.process_file)
            params = sig.parameters
            has_var_kw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            for k, v in ocr_kwargs.items():
                if has_var_kw or k in params:
                    accepted_extra[k] = v
        except (TypeError, ValueError):
            accepted_extra = dict(ocr_kwargs)

        if not accepted_extra:
            return await pipeline.process_file(**base_kwargs)

        try:
            return await pipeline.process_file(**base_kwargs, **accepted_extra)
        except TypeError:
            return await pipeline.process_file(**base_kwargs)

    async def parse(
        self,
        source: Union[str, bytes],
        source_name: str = "input.md",
        project_id: str = "default",
        branch: str = "main",
        version_tag: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from projectbrain.graphify_engine.doc_pipeline import DocumentPipeline
        from projectbrain.graphify_engine.extractor import GraphifyExtractor

        meta = metadata or {}
        ocr_kwargs: Dict[str, Any] = {}
        for opt_key in ("ocr_engine", "ocr_device", "local_ai_model", "local_ai_url"):
            val = meta.get(opt_key) if meta.get(opt_key) is not None else self.config.get(opt_key)
            if val is not None:
                ocr_kwargs[opt_key] = val

        existing = _is_existing_file(source)
        if existing is not None:
            extractor = GraphifyExtractor(cache_dir=self.config.get("cache_dir") or (existing.parent / ".projectbrain" / "cache"))
            pipeline = DocumentPipeline(extractor=extractor)
            base_kwargs = {
                "file_path": existing,
                "project_id": project_id,
                "branch": branch,
                "author": meta.get("author"),
                "tags": meta.get("tags"),
                "sector": meta.get("sector", "semantic"),
                "doc_version": version_tag,
            }
            res = await self._invoke_process_file(pipeline, base_kwargs, ocr_kwargs)
        else:
            raw_bytes, _ = _read_source_bytes(source)
            file_name = Path(source_name).name or "document.md"
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                file_path = tmp_path / file_name
                file_path.write_bytes(raw_bytes)
                extractor = GraphifyExtractor(cache_dir=self.config.get("cache_dir") or (tmp_path / ".cache"))
                pipeline = DocumentPipeline(extractor=extractor)
                base_kwargs = {
                    "file_path": file_path,
                    "project_id": project_id,
                    "branch": branch,
                    "author": meta.get("author"),
                    "tags": meta.get("tags"),
                    "sector": meta.get("sector", "semantic"),
                    "doc_version": version_tag,
                }
                res = await self._invoke_process_file(pipeline, base_kwargs, ocr_kwargs)

        doc_info = res.get("document", {})
        return {
            "plugin_id": self.metadata.plugin_id,
            "graph_delta": res.get("graph_delta", {}),
            "document": doc_info,
            "chunks": doc_info.get("chunks", []),
            "canonical_intents": [],
            "error_rules": [],
            "flow_nodes": [],
            "flow_edges": [],
        }


class CodeDocLinkerPlugin(KnowledgeLinkerPlugin):
    """Wraps CodeDocLinker for multi-signal code-to-documentation cross-linking and drift detection."""

    metadata = PluginMetadata(
        plugin_id="linker.code_doc",
        name="Code-Doc Cross-Linker",
        version="1.5.0",
        category=PluginCategory.KNOWLEDGE_LINKER.value,
        description="Wraps CodeDocLinker to build symbol indexes, link doc chunks to code, and detect drift.",
        priority=100,
        capabilities=["code_doc_linking", "drift_detection", "markdown_chunking"],
    )

    def link(
        self,
        project_id: str,
        branch: str = "main",
        graph_data: Optional[Any] = None,
        chunks: Optional[List[Any]] = None,
        version_tag: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from projectbrain.graphify_engine.kb_linker import CodeDocLinker, DocChunk

        ctx = context or {}
        ws = ctx.get("workspace_path") or self.config.get("workspace_path") or "."
        linker = CodeDocLinker(workspace_path=ws)
        gdata = graph_data if graph_data is not None else {"nodes": [], "edges": []}
        symbol_index = linker.build_symbol_index(gdata)

        doc_chunks: List[DocChunk] = []
        for item in chunks or []:
            if isinstance(item, DocChunk):
                doc_chunks.append(item)
            elif isinstance(item, dict):
                if "doc_path" in item and "content" in item and "chunk_id" not in item:
                    doc_chunks.extend(
                        linker.chunk_markdown_document(
                            doc_path=item["doc_path"],
                            content=item["content"],
                            doc_node_id=item.get("doc_node_id"),
                        )
                    )
                else:
                    doc_chunks.append(
                        DocChunk(
                            chunk_id=item.get("chunk_id") or _short_hash(item.get("content", "")),
                            doc_node_id=item.get("doc_node_id") or f"doc:{item.get('doc_path', 'doc.md')}",
                            doc_path=item.get("doc_path", "doc.md"),
                            heading_breadcrumb=item.get("heading_breadcrumb", "Overview"),
                            heading_level=int(item.get("heading_level", 1)),
                            content=item.get("content", ""),
                            start_line=int(item.get("start_line", 1)),
                            end_line=int(item.get("end_line", 1)),
                            token_count=int(item.get("token_count", len(item.get("content", "").split()))),
                            sha256=item.get("sha256") or _short_hash(item.get("content", ""), 64),
                        )
                    )

        edges, drift_items = linker.link_chunks_to_code(doc_chunks, symbol_index, gdata)
        return {
            "plugin_id": self.metadata.plugin_id,
            "project_id": project_id,
            "branch": branch,
            "version_tag": version_tag,
            "symbol_index_keys": list(symbol_index.keys()),
            "symbol_count": sum(len(v) for v in symbol_index.values()),
            "edges": [_model_to_dict(e) for e in edges],
            "drift_items": [_model_to_dict(d) for d in drift_items],
        }


class MarkdownWikiExporterPlugin(DocExporterPlugin):
    """Wraps ProjectDocSynthesizer.synthesize_wiki."""

    metadata = PluginMetadata(
        plugin_id="exporter.wiki",
        name="Markdown Knowledge Wiki Exporter",
        version="1.5.0",
        category=PluginCategory.DOC_EXPORTER.value,
        description="Generates structured Markdown knowledge wiki and GRAPH_REPORT.md.",
        priority=100,
        capabilities=["wiki", "markdown", "graph_report"],
    )
    export_format: str = "wiki"

    def export(
        self,
        graph_data: Any,
        output_path: str,
        linker_index: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from projectbrain.graphify_engine.doc_synthesizer import ProjectDocSynthesizer

        ws = _resolve_workspace(output_path, self.config, context)
        synth = ProjectDocSynthesizer(workspace_path=ws)
        pages = synth.synthesize_wiki(graph_data=graph_data, output_dir=output_path)
        return {
            "plugin_id": self.metadata.plugin_id,
            "format": self.export_format,
            "output_path": str(output_path),
            "page_count": len(pages),
            "files": [p.relative_path for p in pages],
        }


class DocxExporterPlugin(DocExporterPlugin):
    """Wraps ProjectDocSynthesizer.export_docx_specification."""

    metadata = PluginMetadata(
        plugin_id="exporter.docx",
        name="DOCX Specification Exporter",
        version="1.5.0",
        category=PluginCategory.DOC_EXPORTER.value,
        description="Exports Microsoft Word (.docx) system architecture specification.",
        priority=90,
        capabilities=["docx", "office_export"],
    )
    export_format: str = "docx"

    def export(
        self,
        graph_data: Any,
        output_path: str,
        linker_index: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from projectbrain.graphify_engine.doc_synthesizer import ProjectDocSynthesizer

        ws = _resolve_workspace(output_path, self.config, context)
        synth = ProjectDocSynthesizer(workspace_path=ws)
        res = synth.export_docx_specification(graph_data=graph_data, output_path=output_path)
        payload = _model_to_dict(res)
        payload.update({"plugin_id": self.metadata.plugin_id, "format": self.export_format})
        return payload


class XlsxExporterPlugin(DocExporterPlugin):
    """Wraps ProjectDocSynthesizer.export_xlsx_traceability_matrix."""

    metadata = PluginMetadata(
        plugin_id="exporter.xlsx",
        name="XLSX Traceability Matrix Exporter",
        version="1.5.0",
        category=PluginCategory.DOC_EXPORTER.value,
        description="Exports multi-sheet Excel (.xlsx) traceability matrix.",
        priority=90,
        capabilities=["xlsx", "traceability_matrix", "office_export"],
    )
    export_format: str = "xlsx"

    def export(
        self,
        graph_data: Any,
        output_path: str,
        linker_index: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from projectbrain.graphify_engine.doc_synthesizer import ProjectDocSynthesizer

        ws = _resolve_workspace(output_path, self.config, context)
        synth = ProjectDocSynthesizer(workspace_path=ws)
        res = synth.export_xlsx_traceability_matrix(
            graph_data=graph_data,
            output_path=output_path,
            linker_index=linker_index,
        )
        payload = _model_to_dict(res)
        payload.update({"plugin_id": self.metadata.plugin_id, "format": self.export_format})
        return payload


class PptxExporterPlugin(DocExporterPlugin):
    """Wraps ProjectDocSynthesizer.export_pptx_architecture_deck."""

    metadata = PluginMetadata(
        plugin_id="exporter.pptx",
        name="PPTX Architecture Deck Exporter",
        version="1.5.0",
        category=PluginCategory.DOC_EXPORTER.value,
        description="Exports executive & engineering PowerPoint (.pptx) architecture deck.",
        priority=90,
        capabilities=["pptx", "architecture_deck", "office_export"],
    )
    export_format: str = "pptx"

    def export(
        self,
        graph_data: Any,
        output_path: str,
        linker_index: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from projectbrain.graphify_engine.doc_synthesizer import ProjectDocSynthesizer

        ws = _resolve_workspace(output_path, self.config, context)
        synth = ProjectDocSynthesizer(workspace_path=ws)
        res = synth.export_pptx_architecture_deck(graph_data=graph_data, output_path=output_path)
        payload = _model_to_dict(res)
        payload.update({"plugin_id": self.metadata.plugin_id, "format": self.export_format})
        return payload


# ---------------------------------------------------------------------------
# 8. LegacyTestcaseETLPlugin (CSV, Excel .xlsx, TestRail JSON/XML -> CanonicalTestIntent)
# ---------------------------------------------------------------------------
_PRECONDITION_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("flash_firmware", ("flash", "firmware", "dfu", "ota", "jtag", "swd", "bootloader", ".hex", ".bin")),
    ("power_cycle", ("power cycle", "power_cycle", "reboot", "cold boot", "reset device", "power off", "power on", "cycle power")),
    ("seed_db", ("seed db", "seed_db", "seed database", "database", "fixture", "populate db", "reset db", "migrate")),
    ("pair_ble", ("pair ble", "pair_ble", "bluetooth", "ble", "gatt", "bonding", "advertising", "pair device")),
    ("login_session", ("login", "authenticate", "auth token", "session", "sign in", "logged in")),
)

_PRIORITY_MAP: Dict[str, str] = {
    "p0": "P0", "0": "P0", "1": "P0", "critical": "P0", "blocker": "P0", "high": "P0",
    "p1": "P1", "2": "P1", "medium": "P1", "normal": "P1",
    "p2": "P2", "3": "P2", "low": "P2",
    "p3": "P3", "4": "P3", "trivial": "P3",
}


class LegacyTestcaseETLPlugin(ETLParserPlugin):
    """Parses CSV, Excel (.xlsx), and TestRail JSON/XML testcases into CanonicalTestIntent & graph deltas."""

    metadata = PluginMetadata(
        plugin_id="etl.legacy_testcase",
        name="Legacy Testcase Canonical ETL",
        version="3.0.0",
        category=PluginCategory.ETL_PARSER.value,
        description="Converts CSV, Excel .xlsx, and TestRail JSON/XML suites into CanonicalTestIntent DTOs.",
        priority=95,
        capabilities=["csv", "xlsx", "testrail_json", "testrail_xml", "canonical_test_intent"],
    )
    supported_extensions: Tuple[str, ...] = (".csv", ".xlsx", ".json", ".xml")
    supported_formats: Tuple[str, ...] = ("csv", "xlsx", "testrail_json", "testrail_xml", "testcase")

    def can_parse(
        self,
        source_name: str,
        format_hint: Optional[str] = None,
        content_preview: Optional[str] = None,
    ) -> bool:
        if format_hint:
            hint = format_hint.lower()
            if hint in self.supported_formats:
                return True
            if hint in ("openapi", "openapi3", "openapi31", "swagger"):
                return False

        ext = Path(source_name).suffix.lower()
        preview_lower = (content_preview or "").lower()
        if ext == ".csv":
            return "uart_err" not in preview_lower and "at+err" not in preview_lower
        if ext == ".xlsx":
            return True
        if ext == ".xml":
            if preview_lower:
                return any(tag in preview_lower for tag in ("<suite", "<cases", "<case", "<testcases", "<testcase"))
            return True
        if ext == ".json":
            if '"openapi"' in preview_lower or "'openapi'" in preview_lower:
                return False
            if preview_lower:
                return any(k in preview_lower for k in ("cases", "sections", "tc_id", "expected", "steps", "title"))
            return any(tok in source_name.lower() for tok in ("test", "case", "suite", "rail"))
        return False

    @staticmethod
    def _pick_field(row: Dict[str, Any], aliases: Sequence[str]) -> Any:
        norm = {str(k).strip().lower().replace(" ", "_").replace("-", "_"): v for k, v in row.items()}
        for alias in aliases:
            val = norm.get(alias)
            if val is not None and str(val).strip() != "":
                return val
        return None

    @staticmethod
    def _split_list(val: Any) -> List[str]:
        if val is None:
            return []
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        return [part.strip() for part in re.split(r"[,;\n]+", str(val)) if part.strip()]

    @staticmethod
    def _infer_preconditions(text: str) -> List[Dict[str, Any]]:
        if not text or not text.strip():
            return []
        lower = text.lower()
        found: List[Dict[str, Any]] = []
        seen_types: Set[str] = set()
        for pre_type, keywords in _PRECONDITION_PATTERNS:
            if any(kw in lower for kw in keywords):
                seen_types.add(pre_type)
                found.append({"type": pre_type, "raw": text.strip(), "parameters": {}})
        if not found:
            found.append({"type": "custom_setup", "raw": text.strip(), "parameters": {}})
        return found

    @staticmethod
    def _infer_step_action(text: str) -> str:
        lower = text.lower()
        if any(k in lower for k in ("uart", "at+", "serial", "baud")):
            return "uart_send"
        if any(k in lower for k in ("wait", "signal", "event", "notify", "timeout")):
            return "wait_signal"
        if any(k in lower for k in ("http", "api", "get ", "post ", "put ", "delete ", "endpoint")):
            return "api_call"
        if any(k in lower for k in ("power", "current", "ma", "voltage", "measure")):
            return "power_measure"
        if any(k in lower for k in ("verify", "assert", "check", "expect")):
            return "assert"
        if any(k in lower for k in ("enter", "type", "input", "fill")):
            return "input"
        return "tap"

    @staticmethod
    def _parse_steps(raw_steps: Any) -> Tuple[List[CanonicalTestStep], List[str]]:
        steps: List[CanonicalTestStep] = []
        step_expected: List[str] = []
        if not raw_steps:
            return steps, step_expected

        if isinstance(raw_steps, list):
            for idx, item in enumerate(raw_steps, start=1):
                if isinstance(item, dict):
                    content = str(item.get("content") or item.get("action") or item.get("step") or "").strip()
                    exp = str(item.get("expected") or "").strip()
                    if exp:
                        step_expected.append(exp)
                else:
                    content = str(item).strip()
                if content:
                    steps.append(
                        CanonicalTestStep(
                            step_index=idx,
                            action=LegacyTestcaseETLPlugin._infer_step_action(content),
                            target_intent=content,
                            parameters={"raw": content},
                        )
                    )
            return steps, step_expected

        lines = [ln.strip() for ln in re.split(r"\r?\n+", str(raw_steps)) if ln.strip()]
        if len(lines) == 1 and ";" in lines[0]:
            lines = [ln.strip() for ln in lines[0].split(";") if ln.strip()]
        for idx, line in enumerate(lines, start=1):
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
            if cleaned:
                steps.append(
                    CanonicalTestStep(
                        step_index=idx,
                        action=LegacyTestcaseETLPlugin._infer_step_action(cleaned),
                        target_intent=cleaned,
                        parameters={"raw": cleaned},
                    )
                )
        return steps, step_expected

    @staticmethod
    def _infer_assertions(expected_texts: List[str]) -> List[CanonicalOracleAssertion]:
        assertions: List[CanonicalOracleAssertion] = []
        expanded: List[str] = []
        for raw in expected_texts:
            for part in re.split(r"[;\r\n]+", str(raw)):
                if part.strip():
                    expanded.append(part.strip())

        for text in expanded:
            lower = text.lower()
            power_match = re.search(r"([<>]=?\s*\d+(?:\.\d+)?\s*(?:ma|mw|ua|v))", text, re.IGNORECASE)
            if power_match or any(k in lower for k in ("power budget", "max current", "ma)", " ma")):
                assertions.append(
                    CanonicalOracleAssertion(
                        assertion_type="power_budget_ma",
                        expected_value=text,
                        tolerance=power_match.group(1) if power_match else None,
                        severity="error",
                    )
                )
            elif any(k in lower for k in ("uart", "at+", "boot_ok", "paired_event", "0x", "serial")) or re.search(r"\bOK\b", text):
                assertions.append(
                    CanonicalOracleAssertion(
                        assertion_type="uart_pattern",
                        expected_value=text,
                        severity="error",
                    )
                )
            elif any(k in lower for k in ("http", "status code", "json", "rest", "api")) or re.search(r"\b[245]\d{2}\b", text):
                assertions.append(
                    CanonicalOracleAssertion(
                        assertion_type="api_status",
                        expected_value=text,
                        severity="error",
                    )
                )
            elif any(k in lower for k in ("log", "syslog", "dmesg", "regex", "hardfault", "crash")):
                assertions.append(
                    CanonicalOracleAssertion(
                        assertion_type="log_regex",
                        expected_value=text,
                        severity="error",
                    )
                )
            else:
                assertions.append(
                    CanonicalOracleAssertion(
                        assertion_type="ui_state",
                        expected_value=text,
                        severity="error",
                    )
                )
        return assertions

    def _rows_from_csv(self, text: str) -> List[Dict[str, Any]]:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        return [dict(row) for row in reader if any(str(v or "").strip() for v in row.values())]

    def _rows_from_xlsx(self, raw_bytes: bytes) -> List[Dict[str, Any]]:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        rows_out: List[Dict[str, Any]] = []
        for sheet in wb.worksheets:
            all_rows = list(sheet.iter_rows(values_only=True))
            if not all_rows:
                continue
            header_idx = None
            for i, r in enumerate(all_rows):
                if any(c is not None and str(c).strip() != "" for c in r):
                    header_idx = i
                    break
            if header_idx is None:
                continue
            headers = [str(c).strip() if c is not None else f"col_{j}" for j, c in enumerate(all_rows[header_idx])]
            for r in all_rows[header_idx + 1:]:
                if not any(c is not None and str(c).strip() != "" for c in r):
                    continue
                row_dict = {headers[j]: r[j] for j in range(min(len(headers), len(r)))}
                row_dict.setdefault("section", sheet.title)
                rows_out.append(row_dict)
        return rows_out

    def _rows_from_testrail_json(self, text: str) -> List[Dict[str, Any]]:
        data = json.loads(text)
        rows: List[Dict[str, Any]] = []

        def walk_sections(sections: List[Dict[str, Any]], trail: List[str]) -> None:
            for sec in sections:
                sec_name = str(sec.get("name") or sec.get("title") or "").strip()
                new_trail = trail + ([sec_name] if sec_name else [])
                for c in sec.get("cases", []) or []:
                    item = dict(c)
                    if new_trail:
                        item.setdefault("section", " / ".join(new_trail))
                    rows.append(item)
                if sec.get("sections"):
                    walk_sections(sec["sections"], new_trail)

        if isinstance(data, list):
            return [dict(item) for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            if "cases" in data and isinstance(data["cases"], list):
                rows.extend(dict(c) for c in data["cases"] if isinstance(c, dict))
            if "sections" in data and isinstance(data["sections"], list):
                walk_sections(data["sections"], [])
            if "suite" in data and isinstance(data["suite"], dict):
                suite = data["suite"]
                if isinstance(suite.get("cases"), list):
                    rows.extend(dict(c) for c in suite["cases"] if isinstance(c, dict))
                if isinstance(suite.get("sections"), list):
                    walk_sections(suite["sections"], [])
        return rows

    def _rows_from_testrail_xml(self, text: str) -> List[Dict[str, Any]]:
        root = ET.fromstring(text)
        rows: List[Dict[str, Any]] = []

        def extract_case(elem: ET.Element, section_name: Optional[str] = None) -> Dict[str, Any]:
            row: Dict[str, Any] = {}
            for attr_k, attr_v in elem.attrib.items():
                row[attr_k] = attr_v
            if section_name:
                row["section"] = section_name
            for child in elem:
                tag = child.tag.split("}")[-1].lower()
                if tag in ("steps_separated", "custom_steps_separated", "steps") and list(child):
                    step_list: List[Dict[str, str]] = []
                    for st in child:
                        content = st.findtext("content") or st.findtext("action") or (st.text or "")
                        expected = st.findtext("expected") or ""
                        step_list.append({"content": content.strip(), "expected": expected.strip()})
                    row["steps"] = step_list
                elif tag == "custom":
                    for sub in child:
                        sub_tag = sub.tag.split("}")[-1].lower()
                        if sub_tag == "steps_separated" and list(sub):
                            row["steps"] = [
                                {
                                    "content": (st.findtext("content") or "").strip(),
                                    "expected": (st.findtext("expected") or "").strip(),
                                }
                                for st in sub
                            ]
                        elif sub.text and sub.text.strip():
                            row[sub_tag] = sub.text.strip()
                else:
                    if child.text and child.text.strip():
                        row[tag] = child.text.strip()
            return row

        def walk_section_elem(sec_elem: ET.Element, trail: List[str]) -> None:
            sec_name = (sec_elem.findtext("name") or sec_elem.attrib.get("name") or "").strip()
            new_trail = trail + ([sec_name] if sec_name else [])
            sec_label = " / ".join(new_trail) if new_trail else None
            cases_container = sec_elem.find("cases")
            if cases_container is not None:
                for c in cases_container.findall("case"):
                    rows.append(extract_case(c, sec_label))
            for c in sec_elem.findall("case"):
                rows.append(extract_case(c, sec_label))
            nested_secs = sec_elem.find("sections")
            if nested_secs is not None:
                for sub in nested_secs.findall("section"):
                    walk_section_elem(sub, new_trail)

        sections_elem = root.find("sections")
        if sections_elem is not None:
            for sec in sections_elem.findall("section"):
                walk_section_elem(sec, [])
        if not rows:
            for tag_name in ("case", "testcase"):
                for c in root.findall(f".//{tag_name}"):
                    rows.append(extract_case(c))
        return rows

    async def parse(
        self,
        source: Union[str, bytes],
        source_name: str = "testcases.csv",
        project_id: str = "default",
        branch: str = "main",
        version_tag: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raw_bytes, path = _read_source_bytes(source)
        effective_name = path.name if path else source_name
        ext = Path(effective_name).suffix.lower()

        rows: List[Dict[str, Any]]
        if ext == ".xlsx" or raw_bytes[:2] == b"PK":
            rows = self._rows_from_xlsx(raw_bytes)
        else:
            text = raw_bytes.decode("utf-8", errors="replace").strip()
            if ext == ".xml" or text.startswith("<"):
                rows = self._rows_from_testrail_xml(text)
            elif ext == ".json" or text.startswith("{") or text.startswith("["):
                rows = self._rows_from_testrail_json(text)
            else:
                rows = self._rows_from_csv(text)

        intents: List[CanonicalTestIntent] = []
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        seen_aux_nodes: Set[str] = set()

        for idx, row in enumerate(rows, start=1):
            raw_id = self._pick_field(row, ("id", "case_id", "tc_id", "testcase_id", "key"))
            intent_id = str(raw_id).strip() if raw_id is not None else f"TC-{idx:03d}"
            title = str(self._pick_field(row, ("title", "name", "case_name", "summary", "test_name")) or f"Test Case {intent_id}").strip()
            desc = str(self._pick_field(row, ("description", "desc", "objective")) or "").strip()
            precond_raw = str(self._pick_field(row, ("preconditions", "precondition", "custom_preconds", "setup", "prereq", "prerequisites")) or "").strip()
            steps_raw = self._pick_field(row, ("steps", "actions", "custom_steps", "custom_steps_separated", "test_steps", "procedure"))
            expected_raw = self._pick_field(row, ("expected", "expected_result", "custom_expected", "oracle", "pass_criteria", "expected_output"))
            priority_raw = str(self._pick_field(row, ("priority", "priority_id", "severity")) or "P1").strip().lower()
            priority = _PRIORITY_MAP.get(priority_raw, priority_raw.upper() if priority_raw.upper() in ("P0", "P1", "P2", "P3") else "P1")
            platforms = self._split_list(self._pick_field(row, ("platform", "platforms", "targets", "target_platforms", "os", "device")))
            reqs = self._split_list(self._pick_field(row, ("requirement", "requirements", "srs_ref", "refs", "req_id", "linked_requirements")))
            tags = self._split_list(self._pick_field(row, ("tags", "labels", "section")))

            preconditions = self._infer_preconditions(f"{precond_raw} {title}") if precond_raw else (
                self._infer_preconditions(title) if any(kw in title.lower() for _, kws in _PRECONDITION_PATTERNS for kw in kws) else []
            )
            steps, step_expected = self._parse_steps(steps_raw)
            expected_list = ([str(expected_raw).strip()] if expected_raw else []) + step_expected
            assertions = self._infer_assertions(expected_list)

            intent = CanonicalTestIntent(
                intent_id=intent_id,
                title=title,
                description=desc,
                priority=priority,
                target_platforms=platforms,
                preconditions=preconditions,
                steps=steps,
                expected_oracle_assertions=assertions,
                linked_requirements=reqs,
                tags=tags,
                version_tag=version_tag,
            )
            intents.append(intent)

            intent_node_id = f"test_intent:{intent_id}"
            nodes.append(
                GraphNode(
                    id=intent_node_id,
                    project_id=project_id,
                    label=title,
                    file_type="test_intent",
                    source_file=effective_name,
                    metadata={**_model_to_dict(intent), **({"version_tag": version_tag} if version_tag else {})},
                )
            )
            for req in reqs:
                req_nid = f"req:{req}"
                if req_nid not in seen_aux_nodes:
                    seen_aux_nodes.add(req_nid)
                    nodes.append(
                        GraphNode(
                            id=req_nid,
                            project_id=project_id,
                            label=req,
                            file_type="document",
                            source_file=effective_name,
                            metadata={"requirement_id": req},
                        )
                    )
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        source=intent_node_id,
                        target=req_nid,
                        relation="verifies_requirement",
                        source_file=effective_name,
                    )
                )
            for pre in preconditions:
                pre_type = pre["type"]
                pre_nid = f"precondition:{pre_type}"
                if pre_nid not in seen_aux_nodes:
                    seen_aux_nodes.add(pre_nid)
                    nodes.append(
                        GraphNode(
                            id=pre_nid,
                            project_id=project_id,
                            label=pre_type,
                            file_type="config",
                            source_file=effective_name,
                            metadata={"precondition_type": pre_type},
                        )
                    )
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        source=intent_node_id,
                        target=pre_nid,
                        relation="requires_precondition",
                        source_file=effective_name,
                    )
                )

        return {
            "plugin_id": self.metadata.plugin_id,
            "graph_delta": _build_delta(project_id, branch, nodes, edges),
            "canonical_intents": [_model_to_dict(i) for i in intents],
            "error_rules": [],
            "flow_nodes": [],
            "flow_edges": [],
            "chunks": [],
        }


# ---------------------------------------------------------------------------
# 9. HardwareDatasheetETLPlugin (UART/AT Error Tables, HardFault/CFSR, Electrical Limits)
# ---------------------------------------------------------------------------
_CORTEX_FAULT_BITS: Dict[str, Tuple[str, str]] = {
    "FORCED": ("HFSR", "Escalated configurable fault forced into HardFault"),
    "VECTTBL": ("HFSR", "Bus error on vector table read during exception processing"),
    "IACCVIOL": ("MMFSR", "Instruction access violation in MemManage"),
    "DACCVIOL": ("MMFSR", "Data access violation in MemManage"),
    "MUNSTKERR": ("MMFSR", "MemManage fault on unstacking for return from exception"),
    "MSTKERR": ("MMFSR", "MemManage fault on stacking for exception entry"),
    "IBUSERR": ("BFSR", "Instruction bus error"),
    "PRECISERR": ("BFSR", "Precise data bus error"),
    "IMPRECISERR": ("BFSR", "Imprecise data bus error"),
    "UNDEFINSTR": ("UFSR", "Undefined instruction UsageFault"),
    "INVSTATE": ("UFSR", "Invalid state UsageFault"),
    "INVPC": ("UFSR", "Invalid PC load UsageFault"),
    "NOCP": ("UFSR", "No coprocessor UsageFault"),
    "UNALIGNED": ("UFSR", "Unaligned memory access UsageFault"),
    "DIVBYZERO": ("UFSR", "Divide by zero UsageFault"),
}


class HardwareDatasheetETLPlugin(ETLParserPlugin):
    """Extracts UART/AT error codes, ARM HardFault/CFSR rules, and electrical/timing limits."""

    metadata = PluginMetadata(
        plugin_id="etl.hardware_datasheet",
        name="Hardware Datasheet & Error Rule ETL",
        version="3.0.0",
        category=PluginCategory.ETL_PARSER.value,
        description="Extracts UART/AT error tables, ARM HardFault/CFSR bits, and electrical/timing limits.",
        priority=95,
        capabilities=["uart_errors", "at_errors", "hardfault_cfsr", "electrical_limits"],
    )
    supported_extensions: Tuple[str, ...] = (".md", ".csv", ".txt")
    supported_formats: Tuple[str, ...] = ("datasheet", "hardware_datasheet", "uart_errors", "errata")

    def can_parse(
        self,
        source_name: str,
        format_hint: Optional[str] = None,
        content_preview: Optional[str] = None,
    ) -> bool:
        if format_hint and format_hint.lower() in self.supported_formats:
            return True
        ext = Path(source_name).suffix.lower()
        if ext not in self.supported_extensions:
            return False
        name_lower = source_name.lower()
        preview_lower = (content_preview or "").lower()
        if any(k in name_lower for k in ("datasheet", "errata", "hardware", "uart", "fault")):
            return True
        return any(
            k in preview_lower
            for k in ("at+err", "uart_err", "hardfault", "cfsr", "hfsr", "max_current_ma", "boot_timeout_ms", "e_ble_")
        )

    @staticmethod
    def _detect_protocol(code: str, context_text: str) -> str:
        c_up = code.upper()
        ctx_up = context_text.upper()
        if any(b in c_up for b in _CORTEX_FAULT_BITS) or any(r in ctx_up for r in ("CFSR", "HFSR", "HARDFAULT", "BFSR", "UFSR", "MMFSR")):
            return "hardfault"
        if c_up.startswith("AT") or "AT+ERR" in ctx_up or "CME ERROR" in ctx_up:
            return "at"
        if "UART" in c_up or "UART" in ctx_up:
            return "uart"
        return "custom"

    async def parse(
        self,
        source: Union[str, bytes],
        source_name: str = "datasheet.md",
        project_id: str = "default",
        branch: str = "main",
        version_tag: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text, path = _read_source_text(source)
        effective_name = path.name if path else source_name

        rules: List[ErrorCodeRule] = []
        seen_rule_codes: Set[str] = set()
        electrical_limits: List[Dict[str, Any]] = []
        seen_limits: Set[str] = set()

        def add_rule(
            code: str,
            protocol: str,
            severity: str,
            pattern: str,
            description: str,
            remediation_hint: str = "",
        ) -> None:
            clean_code = code.strip()
            if not clean_code or clean_code in seen_rule_codes:
                return
            seen_rule_codes.add(clean_code)
            rules.append(
                ErrorCodeRule(
                    rule_id=f"hw:{_slugify(clean_code)}:{_short_hash(effective_name + clean_code, 6)}",
                    code=clean_code,
                    protocol=protocol,
                    severity=severity,
                    pattern=pattern,
                    description=description.strip(),
                    fault_domain="Firmware",
                    remediation_hint=remediation_hint.strip(),
                    source_file=effective_name,
                    version_tag=version_tag,
                )
            )

        # 1. CSV Error Table
        if Path(effective_name).suffix.lower() == ".csv":
            reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
            for row in reader:
                norm = {str(k).strip().lower(): str(v or "").strip() for k, v in row.items()}
                code = norm.get("code") or norm.get("error_code") or norm.get("symbol") or ""
                if not code:
                    continue
                desc = norm.get("description") or norm.get("meaning") or norm.get("message") or ""
                rem = norm.get("remediation") or norm.get("remediation_hint") or norm.get("action") or ""
                sev = (norm.get("severity") or ("critical" if "fault" in desc.lower() else "error")).lower()
                proto = (norm.get("protocol") or self._detect_protocol(code, desc)).lower()
                add_rule(code, proto, sev, re.escape(code), desc, rem)

        # 2. Markdown Tables (| Code | ... |) and Limits Tables
        for line in text.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("|") and stripped.endswith("|")):
                continue
            cells = [c.strip().strip("`") for c in stripped.strip("|").split("|")]
            if len(cells) < 2 or all(set(c) <= {"-", ":", " "} for c in cells):
                continue
            first_lower = cells[0].lower()
            if first_lower in ("code", "error", "error_code", "bit", "register", "parameter", "metric", "limit"):
                continue

            col0, col1 = cells[0], cells[1]
            col2 = cells[2] if len(cells) > 2 else ""
            col3 = cells[3] if len(cells) > 3 else ""

            # Check if electrical/timing limit row
            limit_match = re.match(
                r"^(max_current_ma|idle_current_ma|peak_current_ma|boot_timeout_ms|uart_timeout_ms|voltage_mv|vdd_min_mv|vdd_max_mv|reset_pulse_ms|[a-z][a-z0-9_]*(?:_ma|_ms|_mv|_us|_hz))$",
                col0,
                re.IGNORECASE,
            )
            if limit_match:
                num_match = re.search(r"(\d+(?:\.\d+)?)\s*(mA|uA|ms|us|mV|V|Hz|kHz|MHz)?", col1, re.IGNORECASE)
                if num_match and col0 not in seen_limits:
                    seen_limits.add(col0)
                    electrical_limits.append(
                        {
                            "name": col0,
                            "value": float(num_match.group(1)) if "." in num_match.group(1) else int(num_match.group(1)),
                            "unit": num_match.group(2) or col2 or "",
                            "notes": col3 or col2,
                            "version_tag": version_tag,
                        }
                    )
                continue

            # Otherwise check error / fault table row
            if re.search(r"(AT\+ERR|UART_ERR|E_[A-Z0-9_]+|ERR_[A-Z0-9_]+|0x[0-9A-Fa-f]+|FORCED|VECTTBL|PRECISERR|IMPRECISERR|UNDEFINSTR|DIVBYZERO|IBUSERR|DACCVIOL|IACCVIOL|INVSTATE|INVPC|NOCP|UNALIGNED)", col0 + " " + col1, re.IGNORECASE):
                code = col0
                desc = col2 or col1
                if re.match(r"^0x[0-9A-Fa-f]+$", col1) and not col0.startswith("AT"):
                    desc = col2 or col1
                    pattern = f"(?:{re.escape(col0)}|{re.escape(col1)})"
                else:
                    pattern = re.escape(code)
                proto = self._detect_protocol(code, f"{col0} {col1} {desc}")
                sev = "critical" if proto == "hardfault" or "fatal" in desc.lower() or "critical" in col3.lower() else "error"
                rem = col3 if col3 and col3.lower() not in ("error", "critical", "warning") else ""
                add_rule(code, proto, sev, pattern, desc, rem)

        # 3. Line-based AT/UART Error & HardFault/CFSR patterns outside tables
        kv_err_re = re.compile(
            r"^\s*(?:[-*]\s*)?`?(AT\+ERR\s*=\s*0x[0-9A-Fa-f]+|UART_ERR_[A-Z0-9_]+|E_[A-Z0-9_]+)`?\s*[:=\-–]\s*(.+)$",
            re.MULTILINE,
        )
        for m in kv_err_re.finditer(text):
            code = re.sub(r"\s+", "", m.group(1))
            desc = m.group(2).strip()
            proto = self._detect_protocol(code, desc)
            add_rule(code, proto, "error", re.escape(code), desc)

        for bit_name, (reg_name, default_desc) in _CORTEX_FAULT_BITS.items():
            if re.search(rf"\b{bit_name}\b", text):
                add_rule(
                    code=bit_name,
                    protocol="hardfault",
                    severity="critical",
                    pattern=rf"\b(?:{reg_name}|{bit_name})\b",
                    description=f"{reg_name}.{bit_name}: {default_desc}",
                    remediation_hint=f"Inspect {reg_name} register and stacked PC/LR for {bit_name}.",
                )

        # 4. Inline electrical / timing limits (e.g. max_current_ma: 120 mA, boot_timeout_ms = 2500)
        limit_inline_re = re.compile(
            r"\b(max_current_ma|idle_current_ma|peak_current_ma|boot_timeout_ms|uart_timeout_ms|voltage_mv|vdd_min_mv|vdd_max_mv|reset_pulse_ms)\b\s*[:=]\s*(\d+(?:\.\d+)?)\s*(mA|uA|ms|us|mV|V)?",
            re.IGNORECASE,
        )
        for m in limit_inline_re.finditer(text):
            lname = m.group(1)
            if lname in seen_limits:
                continue
            seen_limits.add(lname)
            val_str = m.group(2)
            unit = m.group(3) or (lname.split("_")[-1] if "_" in lname else "")
            electrical_limits.append(
                {
                    "name": lname,
                    "value": float(val_str) if "." in val_str else int(val_str),
                    "unit": unit,
                    "notes": "",
                    "version_tag": version_tag,
                }
            )

        doc_node_id = f"doc:{effective_name}"
        nodes: List[GraphNode] = [
            GraphNode(
                id=doc_node_id,
                project_id=project_id,
                label=effective_name,
                file_type="document",
                source_file=effective_name,
                metadata={"doc_kind": "hardware_datasheet", "version_tag": version_tag},
            )
        ]
        edges: List[GraphEdge] = []

        for rule in rules:
            nid = f"hw_rule:{rule.rule_id}"
            nodes.append(
                GraphNode(
                    id=nid,
                    project_id=project_id,
                    label=f"{rule.code} ({rule.protocol})",
                    file_type="hardware_rule",
                    source_file=effective_name,
                    metadata=_model_to_dict(rule),
                )
            )
            edges.append(
                GraphEdge(
                    project_id=project_id,
                    source=doc_node_id,
                    target=nid,
                    relation="defines_error_rule",
                    source_file=effective_name,
                )
            )

        for lim in electrical_limits:
            lid = f"hw_limit:{lim['name']}"
            nodes.append(
                GraphNode(
                    id=lid,
                    project_id=project_id,
                    label=f"{lim['name']}={lim['value']}{lim['unit']}",
                    file_type="hardware_rule",
                    source_file=effective_name,
                    metadata={**lim, "rule_kind": "electrical_timing_limit"},
                )
            )
            edges.append(
                GraphEdge(
                    project_id=project_id,
                    source=doc_node_id,
                    target=lid,
                    relation="defines_error_rule",
                    source_file=effective_name,
                )
            )

        return {
            "plugin_id": self.metadata.plugin_id,
            "graph_delta": _build_delta(project_id, branch, nodes, edges),
            "canonical_intents": [],
            "error_rules": [_model_to_dict(r) for r in rules],
            "electrical_limits": electrical_limits,
            "flow_nodes": [],
            "flow_edges": [],
            "chunks": [],
        }


# ---------------------------------------------------------------------------
# 10. OpenAPI31ETLPlugin (OpenAPI 3.0 / 3.1 YAML/JSON)
# ---------------------------------------------------------------------------
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


class OpenAPI31ETLPlugin(ETLParserPlugin):
    """Parses OpenAPI 3.0/3.1 YAML/JSON specs into endpoint/schema nodes and 4xx/5xx ErrorCodeRules."""

    metadata = PluginMetadata(
        plugin_id="etl.openapi31",
        name="OpenAPI 3.0/3.1 Contract ETL",
        version="3.0.0",
        category=PluginCategory.ETL_PARSER.value,
        description="Extracts OpenAPI 3.0/3.1 endpoints, schemas, and 4xx/5xx HTTP ErrorCodeRules.",
        priority=95,
        capabilities=["openapi30", "openapi31", "api_endpoints", "http_error_rules"],
    )
    supported_extensions: Tuple[str, ...] = (".yaml", ".yml", ".json")
    supported_formats: Tuple[str, ...] = ("openapi", "openapi3", "openapi31", "swagger")

    def can_parse(
        self,
        source_name: str,
        format_hint: Optional[str] = None,
        content_preview: Optional[str] = None,
    ) -> bool:
        if format_hint and format_hint.lower() in self.supported_formats:
            return True
        ext = Path(source_name).suffix.lower()
        preview_lower = (content_preview or "").lower()
        if ext in (".yaml", ".yml"):
            return True if not preview_lower else ("openapi" in preview_lower or "paths" in preview_lower)
        if ext == ".json":
            return "openapi" in preview_lower or "openapi" in source_name.lower() or "swagger" in source_name.lower()
        return False

    @staticmethod
    def _collect_schema_refs(obj: Any, refs: Set[str]) -> None:
        if isinstance(obj, dict):
            ref = obj.get("$ref")
            if isinstance(ref, str) and "#/components/schemas/" in ref:
                refs.add(ref.split("#/components/schemas/")[-1])
            for v in obj.values():
                OpenAPI31ETLPlugin._collect_schema_refs(v, refs)
        elif isinstance(obj, list):
            for item in obj:
                OpenAPI31ETLPlugin._collect_schema_refs(item, refs)

    async def parse(
        self,
        source: Union[str, bytes],
        source_name: str = "openapi.yaml",
        project_id: str = "default",
        branch: str = "main",
        version_tag: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text, path = _read_source_text(source)
        effective_name = path.name if path else source_name

        if Path(effective_name).suffix.lower() == ".json" or text.lstrip().startswith("{"):
            spec = json.loads(text)
        else:
            spec = yaml.safe_load(text)

        if not isinstance(spec, dict) or "openapi" not in spec:
            raise ValueError(f"Invalid OpenAPI specification '{effective_name}': missing 'openapi' header")

        openapi_ver = str(spec["openapi"]).strip()
        if not (openapi_ver.startswith("3.0") or openapi_ver.startswith("3.1")):
            raise ValueError(f"Unsupported OpenAPI version '{openapi_ver}' (expected 3.0.* or 3.1.*)")

        info = spec.get("info") or {}
        effective_ver = version_tag or info.get("version")

        spec_node_id = f"openapi:{effective_name}"
        nodes: List[GraphNode] = [
            GraphNode(
                id=spec_node_id,
                project_id=project_id,
                label=str(info.get("title") or effective_name),
                file_type="document",
                source_file=effective_name,
                metadata={"openapi_version": openapi_ver, "api_version": info.get("version"), "version_tag": effective_ver},
            )
        ]
        edges: List[GraphEdge] = []
        error_rules: List[ErrorCodeRule] = []
        endpoints: List[Dict[str, Any]] = []
        schemas_out: List[Dict[str, Any]] = []

        schemas = ((spec.get("components") or {}).get("schemas") or {})
        for schema_name, schema_def in schemas.items():
            if not isinstance(schema_def, dict):
                continue
            props = list((schema_def.get("properties") or {}).keys())
            required = list(schema_def.get("required") or [])
            schema_nid = f"schema:{schema_name}"
            nodes.append(
                GraphNode(
                    id=schema_nid,
                    project_id=project_id,
                    label=schema_name,
                    file_type="api_schema",
                    source_file=effective_name,
                    metadata={"properties": props, "required": required, "version_tag": effective_ver},
                )
            )
            schemas_out.append({"name": schema_name, "properties": props, "required": required})

        paths = spec.get("paths") or {}
        for path_str, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method_key, op in path_item.items():
                if method_key.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                    continue
                method_up = method_key.upper()
                op_id = op.get("operationId") or f"{method_up} {path_str}"
                ep_nid = f"api:{method_up} {path_str}"
                responses = op.get("responses") or {}

                nodes.append(
                    GraphNode(
                        id=ep_nid,
                        project_id=project_id,
                        label=f"{method_up} {path_str}",
                        file_type="api_endpoint",
                        source_file=effective_name,
                        metadata={
                            "method": method_up,
                            "path": path_str,
                            "operation_id": op_id,
                            "summary": op.get("summary", ""),
                            "tags": op.get("tags", []),
                            "responses": list(responses.keys()),
                            "version_tag": effective_ver,
                        },
                    )
                )
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        source=spec_node_id,
                        target=ep_nid,
                        relation="specifies_endpoint",
                        source_file=effective_name,
                    )
                )
                endpoints.append({"method": method_up, "path": path_str, "operation_id": op_id})

                referenced_schemas: Set[str] = set()
                self._collect_schema_refs(op, referenced_schemas)
                for sname in sorted(referenced_schemas):
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=ep_nid,
                            target=f"schema:{sname}",
                            relation="uses_schema",
                            source_file=effective_name,
                        )
                    )

                for status_raw, resp_obj in responses.items():
                    status_str = str(status_raw).strip()
                    if not re.match(r"^[45](?:\d{2}|XX|xx)$", status_str):
                        continue
                    resp_desc = resp_obj.get("description", "") if isinstance(resp_obj, dict) else ""
                    sev = "critical" if status_str.startswith("5") else "error"
                    status_pat = status_str.replace("X", r"\d").replace("x", r"\d")
                    rule = ErrorCodeRule(
                        rule_id=f"http:{method_up}:{_slugify(path_str)}:{status_str}",
                        code=f"HTTP_{status_str.upper()}",
                        protocol="http",
                        severity=sev,
                        pattern=rf"\b{status_pat}\b.*{re.escape(path_str)}|HTTP\s+{status_pat}\b",
                        description=f"{method_up} {path_str} -> {status_str}: {resp_desc}".strip(),
                        fault_domain="Backend",
                        remediation_hint=f"Inspect {method_up} {path_str} ({op_id}) handler for HTTP {status_str}.",
                        source_file=effective_name,
                        version_tag=effective_ver,
                    )
                    error_rules.append(rule)
                    rule_nid = f"http_rule:{rule.rule_id}"
                    nodes.append(
                        GraphNode(
                            id=rule_nid,
                            project_id=project_id,
                            label=f"{rule.code} {method_up} {path_str}",
                            file_type="api_error_rule",
                            source_file=effective_name,
                            metadata=_model_to_dict(rule),
                        )
                    )
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=ep_nid,
                            target=rule_nid,
                            relation="defines_error_rule",
                            source_file=effective_name,
                        )
                    )

        return {
            "plugin_id": self.metadata.plugin_id,
            "openapi_version": openapi_ver,
            "graph_delta": _build_delta(project_id, branch, nodes, edges),
            "canonical_intents": [],
            "error_rules": [_model_to_dict(r) for r in error_rules],
            "endpoints": endpoints,
            "schemas": schemas_out,
            "flow_nodes": [],
            "flow_edges": [],
            "chunks": [],
        }


# ---------------------------------------------------------------------------
# 11. CppEmbeddedETLPlugin (C/C++ AST/Regex, Qt .ui XML, GNU .ld/.map)
# ---------------------------------------------------------------------------
def _parse_mem_size(raw: str) -> int:
    s = raw.strip()
    m = re.match(r"^(0x[0-9A-Fa-f]+|\d+)\s*([KkMmGg])?$", s)
    if not m:
        return 0
    base = int(m.group(1), 16) if m.group(1).lower().startswith("0x") else int(m.group(1))
    mult = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}.get((m.group(2) or "").lower(), 1)
    return base * mult


class CppEmbeddedETLPlugin(ETLParserPlugin):
    """Parses C/C++ firmware/Qt code, Qt .ui XML files, and GNU .ld/.map linker artifacts."""

    metadata = PluginMetadata(
        plugin_id="etl.cpp_embedded",
        name="C/C++, Qt UI & Linker Map ETL",
        version="3.0.0",
        category=PluginCategory.ETL_PARSER.value,
        description="Extracts C/C++ symbols, AT/ISR handlers, Qt .ui widgets/signals/slots, and .ld/.map memory regions.",
        priority=95,
        capabilities=["c_cpp", "qt_ui", "qt_signals_slots", "gnu_ld", "gnu_map", "isr_at_handlers"],
    )
    supported_extensions: Tuple[str, ...] = (".c", ".h", ".hpp", ".cpp", ".cc", ".cxx", ".ui", ".ld", ".map")
    supported_formats: Tuple[str, ...] = ("c", "cpp", "qt_ui", "linker_script", "linker_map", "embedded")

    def can_parse(
        self,
        source_name: str,
        format_hint: Optional[str] = None,
        content_preview: Optional[str] = None,
    ) -> bool:
        if format_hint and format_hint.lower() in self.supported_formats:
            return True
        return Path(source_name).suffix.lower() in self.supported_extensions

    def _parse_qt_ui(
        self,
        text: str,
        effective_name: str,
        project_id: str,
        version_tag: Optional[str],
    ) -> Tuple[List[GraphNode], List[GraphEdge], List[Dict[str, Any]]]:
        root = ET.fromstring(text)
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        widgets: List[Dict[str, Any]] = []

        def walk_widget(elem: ET.Element, parent_nid: Optional[str]) -> None:
            cls = elem.attrib.get("class", "QWidget")
            wname = elem.attrib.get("name", f"anon_{len(widgets)}")
            text_prop = ""
            for prop in elem.findall("property"):
                if prop.attrib.get("name") == "text":
                    s_el = prop.find("string")
                    if s_el is not None and s_el.text:
                        text_prop = s_el.text.strip()
            nid = f"qt_widget:{wname}"
            widgets.append({"name": wname, "class": cls, "text": text_prop})
            nodes.append(
                GraphNode(
                    id=nid,
                    project_id=project_id,
                    label=wname,
                    file_type="screen",
                    source_file=effective_name,
                    metadata={
                        "symbol_type": "qt_widget",
                        "widget_class": cls,
                        "text": text_prop,
                        "version_tag": version_tag,
                    },
                )
            )
            if parent_nid:
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        source=parent_nid,
                        target=nid,
                        relation="contains_widget",
                        source_file=effective_name,
                    )
                )
            for child in elem:
                if child.tag == "widget":
                    walk_widget(child, nid)
                elif child.tag == "layout":
                    for item in child.findall(".//widget"):
                        if item is not elem:
                            # Direct widgets inside layout items
                            pass
                    for item in child.findall("item"):
                        for sub_w in item.findall("widget"):
                            walk_widget(sub_w, nid)

        top_widget = root.find("widget")
        if top_widget is not None:
            walk_widget(top_widget, None)

        connections = root.find("connections")
        if connections is not None:
            for conn in connections.findall("connection"):
                sender = (conn.findtext("sender") or "").strip()
                signal = (conn.findtext("signal") or "").strip()
                receiver = (conn.findtext("receiver") or "").strip()
                slot = (conn.findtext("slot") or "").strip()
                if sender and receiver:
                    sig_nid = f"qt_signal:{signal}"
                    nodes.append(
                        GraphNode(
                            id=sig_nid,
                            project_id=project_id,
                            label=signal or "signal",
                            file_type="code",
                            source_file=effective_name,
                            metadata={"symbol_type": "qt_signal", "signal": signal, "version_tag": version_tag},
                        )
                    )
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=f"qt_widget:{sender}",
                            target=sig_nid,
                            relation="emits_signal",
                            source_file=effective_name,
                            metadata={"signal": signal},
                        )
                    )
                    edges.append(
                        GraphEdge(
                            project_id=project_id,
                            source=f"qt_widget:{sender}",
                            target=f"qt_widget:{receiver}",
                            relation="connects_slot",
                            source_file=effective_name,
                            metadata={"signal": signal, "slot": slot},
                        )
                    )
        return nodes, edges, widgets

    def _parse_linker_ld(
        self,
        text: str,
        effective_name: str,
        project_id: str,
        version_tag: Optional[str],
    ) -> Tuple[List[GraphNode], List[Dict[str, Any]]]:
        nodes: List[GraphNode] = []
        regions: List[Dict[str, Any]] = []
        mem_block = re.search(r"MEMORY\s*\{([^}]*)\}", text, re.DOTALL | re.IGNORECASE)
        body = mem_block.group(1) if mem_block else text
        region_re = re.compile(
            r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s*:\s*ORIGIN\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*,\s*LENGTH\s*=\s*([0-9A-Za-z]+)",
            re.MULTILINE | re.IGNORECASE,
        )
        for m in region_re.finditer(body):
            name, attrs, origin, length = m.group(1), (m.group(2) or "").strip(), m.group(3), m.group(4)
            size_bytes = _parse_mem_size(length)
            regions.append({"name": name, "attributes": attrs, "origin": origin, "length": length, "size_bytes": size_bytes})
            nodes.append(
                GraphNode(
                    id=f"ld_region:{name}",
                    project_id=project_id,
                    label=name,
                    file_type="memory_region",
                    source_file=effective_name,
                    metadata={"attributes": attrs, "origin": origin, "length": length, "size_bytes": size_bytes, "version_tag": version_tag},
                )
            )
        return nodes, regions

    def _parse_linker_map(
        self,
        text: str,
        effective_name: str,
        project_id: str,
        version_tag: Optional[str],
    ) -> Tuple[List[GraphNode], List[Dict[str, Any]]]:
        nodes: List[GraphNode] = []
        regions: List[Dict[str, Any]] = []
        # Memory Configuration lines (Name Origin Length Attributes)
        mem_cfg_re = re.compile(
            r"^\s*(FLASH|RAM|SRAM|CCMRAM|ITCM|DTCM|[A-Z][A-Z0-9_]{1,15})\s+(0x[0-9A-Fa-f]+)\s+(0x[0-9A-Fa-f]+)\s*([rwxal!]*)\s*$",
            re.MULTILINE,
        )
        for m in mem_cfg_re.finditer(text):
            name, origin, length_hex, attrs = m.group(1), m.group(2), m.group(3), m.group(4)
            size_bytes = _parse_mem_size(length_hex)
            regions.append({"name": name, "origin": origin, "length": length_hex, "size_bytes": size_bytes, "attributes": attrs})
            nodes.append(
                GraphNode(
                    id=f"ld_region:{name}",
                    project_id=project_id,
                    label=name,
                    file_type="memory_region",
                    source_file=effective_name,
                    metadata={"origin": origin, "length": length_hex, "size_bytes": size_bytes, "attributes": attrs, "version_tag": version_tag},
                )
            )

        # Section footprints (.text, .rodata, .data, .bss)
        sec_re = re.compile(r"^(\.[A-Za-z0-9_.]+)\s+(0x[0-9A-Fa-f]+)\s+(0x[0-9A-Fa-f]+)", re.MULTILINE)
        for m in sec_re.finditer(text):
            sec_name, addr, size_hex = m.group(1), m.group(2), m.group(3)
            size_bytes = _parse_mem_size(size_hex)
            regions.append({"section": sec_name, "address": addr, "size_hex": size_hex, "size_bytes": size_bytes})
            nodes.append(
                GraphNode(
                    id=f"map_section:{sec_name}",
                    project_id=project_id,
                    label=sec_name,
                    file_type="memory_section",
                    source_file=effective_name,
                    metadata={"address": addr, "size_hex": size_hex, "size_bytes": size_bytes, "version_tag": version_tag},
                )
            )
        return nodes, regions

    def _parse_cpp_source(
        self,
        text: str,
        effective_name: str,
        project_id: str,
        version_tag: Optional[str],
    ) -> Tuple[List[GraphNode], List[GraphEdge], List[Dict[str, Any]], List[ErrorCodeRule]]:
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        symbols: List[Dict[str, Any]] = []
        error_rules: List[ErrorCodeRule] = []
        seen_ids: Set[str] = set()

        def add_sym(nid: str, label: str, sym_type: str, extra: Optional[Dict[str, Any]] = None) -> None:
            if nid in seen_ids:
                return
            seen_ids.add(nid)
            meta = {"symbol_type": sym_type, "version_tag": version_tag, **(extra or {})}
            nodes.append(
                GraphNode(
                    id=nid,
                    project_id=project_id,
                    label=label,
                    file_type="code",
                    source_file=effective_name,
                    metadata=meta,
                )
            )
            symbols.append({"id": nid, "name": label, "type": sym_type, **(extra or {})})

        # 1. #define ERR_* / AT_CMD_* / E_*
        define_re = re.compile(r"^\s*#define\s+((?:ERR_|AT_CMD_|E_)[A-Z0-9_]+)\s+([^\r\n/]+)", re.MULTILINE)
        for m in define_re.finditer(text):
            macro_name, macro_val = m.group(1).strip(), m.group(2).strip()
            add_sym(f"cpp:macro:{macro_name}", macro_name, "macro", {"value": macro_val})
            if macro_name.startswith(("ERR_", "E_")):
                error_rules.append(
                    ErrorCodeRule(
                        rule_id=f"cpp:{macro_name}",
                        code=macro_name,
                        protocol="custom",
                        severity="error",
                        pattern=rf"\b{re.escape(macro_name)}\b",
                        description=f"C/C++ macro {macro_name} = {macro_val}",
                        fault_domain="Firmware",
                        source_file=effective_name,
                        version_tag=version_tag,
                    )
                )

        # 2. enums & enum classes
        enum_re = re.compile(r"\benum\s+(?:class\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[A-Za-z0-9_:]+)?\s*\{([^}]*)\}", re.DOTALL)
        for m in enum_re.finditer(text):
            ename = m.group(1)
            members = [p.strip().split("=")[0].strip() for p in m.group(2).split(",") if p.strip()]
            add_sym(f"cpp:enum:{ename}", ename, "enum", {"members": members})

        # 3. classes & structs (including Qt signals/slots blocks)
        cls_re = re.compile(r"\b(class|struct)\s+(?:[A-Z_]+_EXPORT\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^{;]+))?\{", re.MULTILINE)
        for m in cls_re.finditer(text):
            kind, cname, bases = m.group(1), m.group(2), (m.group(3) or "").strip()
            cls_nid = f"cpp:{kind}:{cname}"
            add_sym(cls_nid, cname, kind, {"bases": bases})

            # Extract brace-matched body (bounded scan)
            start = m.end()
            depth = 1
            pos = start
            while pos < len(text) and depth > 0:
                if text[pos] == "{":
                    depth += 1
                elif text[pos] == "}":
                    depth -= 1
                pos += 1
            body = text[start:pos]

            for sec_match in re.finditer(
                r"(?:(signals|Q_SIGNALS)|(?:public|private|protected)\s+(slots|Q_SLOTS))\s*:(.*?)(?=(?:public|private|protected)\s+(?:slots|Q_SLOTS)\s*:|(?:public|private|protected|signals|Q_SIGNALS)\s*:|\Z)",
                body,
                re.DOTALL,
            ):
                is_signal = bool(sec_match.group(1))
                sec_body = sec_match.group(3)
                for fn_m in re.finditer(r"\bvoid\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", sec_body):
                    sig_name = fn_m.group(1)
                    if is_signal:
                        sig_nid = f"qt_signal:{cname}.{sig_name}"
                        add_sym(sig_nid, f"{cname}.{sig_name}", "qt_signal")
                        edges.append(
                            GraphEdge(
                                project_id=project_id,
                                source=cls_nid,
                                target=sig_nid,
                                relation="emits_signal",
                                source_file=effective_name,
                            )
                        )
                    else:
                        slot_nid = f"qt_slot:{cname}.{sig_name}"
                        add_sym(slot_nid, f"{cname}.{sig_name}", "qt_slot")
                        edges.append(
                            GraphEdge(
                                project_id=project_id,
                                source=cls_nid,
                                target=slot_nid,
                                relation="connects_slot",
                                source_file=effective_name,
                            )
                        )

        # 4. Qt connect(...) calls
        connect_re = re.compile(
            r"\b(?:QObject::)?connect\s*\(\s*([^,]+)\s*,\s*(?:SIGNAL\s*\(\s*([^)]+)\)|&[A-Za-z0-9_]+::([A-Za-z0-9_]+))\s*,\s*([^,]+)\s*,\s*(?:SLOT\s*\(\s*([^)]+)\)|&[A-Za-z0-9_]+::([A-Za-z0-9_]+))\s*\)"
        )
        for m in connect_re.finditer(text):
            sender = m.group(1).strip()
            signal = (m.group(2) or m.group(3) or "").strip()
            receiver = m.group(4).strip()
            slot = (m.group(5) or m.group(6) or "").strip()
            sig_nid = f"qt_signal:{signal}"
            slot_nid = f"qt_slot:{slot}"
            add_sym(sig_nid, signal, "qt_signal", {"sender": sender})
            add_sym(slot_nid, slot, "qt_slot", {"receiver": receiver})
            edges.append(
                GraphEdge(
                    project_id=project_id,
                    source=sig_nid,
                    target=slot_nid,
                    relation="connects_slot",
                    source_file=effective_name,
                    metadata={"sender": sender, "receiver": receiver},
                )
            )

        # 5. ISR / IRQHandler & C/C++ functions
        isr_macro_re = re.compile(r"\bISR\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")
        for m in isr_macro_re.finditer(text):
            vec = m.group(1)
            add_sym(f"cpp:isr:{vec}", vec, "isr")

        fn_re = re.compile(
            r"^(?!\s*(?:if|for|while|switch|return|else)\b)\s*(?:static\s+|inline\s+|extern\s+|virtual\s+)*([A-Za-z_][A-Za-z0-9_:<>\*\s]+?)\s+([A-Za-z_][A-Za-z0-9_:]*)\s*\(([^;{}]*)\)\s*(?:const\s*)?\{",
            re.MULTILINE,
        )
        for m in fn_re.finditer(text):
            ret_t, fn_name, params = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            if fn_name in ("if", "for", "while", "switch", "catch"):
                continue
            sym_type = "isr" if fn_name.endswith("IRQHandler") or fn_name.startswith("ISR_") else "function"
            add_sym(f"cpp:{sym_type}:{fn_name}", fn_name, sym_type, {"return_type": ret_t, "params": params})

        # 6. Firmware AT Command Dispatch Table entries: {"AT+FOO", handle_foo} or AT_CMD("AT+FOO", handle_foo)
        at_tbl_re = re.compile(r'(?:\{|AT_CMD\s*\()\s*"(AT\+[A-Z0-9_?]+)"\s*,\s*([A-Za-z_][A-Za-z0-9_]*)')
        for m in at_tbl_re.finditer(text):
            cmd, handler = m.group(1), m.group(2)
            cmd_nid = f"at_cmd:{cmd}"
            add_sym(cmd_nid, cmd, "at_handler", {"handler": handler})
            edges.append(
                GraphEdge(
                    project_id=project_id,
                    source=cmd_nid,
                    target=f"cpp:function:{handler}",
                    relation="handles_at_command",
                    source_file=effective_name,
                )
            )

        return nodes, edges, symbols, error_rules

    async def parse(
        self,
        source: Union[str, bytes],
        source_name: str = "firmware.c",
        project_id: str = "default",
        branch: str = "main",
        version_tag: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text, path = _read_source_text(source)
        effective_name = path.name if path else source_name
        ext = Path(effective_name).suffix.lower()

        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        symbols: List[Dict[str, Any]] = []
        memory_regions: List[Dict[str, Any]] = []
        qt_widgets: List[Dict[str, Any]] = []
        error_rules: List[ErrorCodeRule] = []

        if ext == ".ui" or "<ui " in text[:200]:
            nodes, edges, qt_widgets = self._parse_qt_ui(text, effective_name, project_id, version_tag)
        elif ext == ".ld" or "MEMORY" in text and "ORIGIN" in text and "LENGTH" in text:
            nodes, memory_regions = self._parse_linker_ld(text, effective_name, project_id, version_tag)
        elif ext == ".map":
            nodes, memory_regions = self._parse_linker_map(text, effective_name, project_id, version_tag)
        else:
            nodes, edges, symbols, error_rules = self._parse_cpp_source(text, effective_name, project_id, version_tag)

        return {
            "plugin_id": self.metadata.plugin_id,
            "graph_delta": _build_delta(project_id, branch, nodes, edges),
            "canonical_intents": [],
            "error_rules": [_model_to_dict(r) for r in error_rules],
            "symbols": symbols,
            "memory_regions": memory_regions,
            "qt_widgets": qt_widgets,
            "flow_nodes": [],
            "flow_edges": [],
            "chunks": [],
        }


# ---------------------------------------------------------------------------
# 12. StateFlowGraphPlugin (STATE_NODE / SCREEN_NODE / TRANSITION_EDGE + Diff)
# ---------------------------------------------------------------------------
_SCREEN_KEYWORDS = ("screen", "page", "dialog", "window", "modal", "view", "ui_", "_ui", "panel")


class StateFlowGraphPlugin(KnowledgeLinkerPlugin):
    """Extracts version-tagged STATE_NODE, SCREEN_NODE, and TRANSITION_EDGE flow graphs and diffs releases."""

    metadata = PluginMetadata(
        plugin_id="linker.state_flow",
        name="State & Screen Flow Graph Linker",
        version="3.0.0",
        category=PluginCategory.KNOWLEDGE_LINKER.value,
        description="Extracts versioned STATE_NODE/SCREEN_NODE/TRANSITION_EDGE from Mermaid, tables, sequences, and Qt graphs.",
        priority=100,
        capabilities=["state_flow", "screen_flow", "version_tag", "flow_diff"],
    )

    @staticmethod
    def _infer_node_kind(label: str, explicit_kind: Optional[str] = None) -> str:
        if explicit_kind:
            k_up = explicit_kind.strip().upper()
            if k_up in ("STATE_NODE", "SCREEN_NODE"):
                return k_up
        if any(kw in label.lower() for kw in _SCREEN_KEYWORDS):
            return "SCREEN_NODE"
        return "STATE_NODE"

    def extract_from_text(
        self,
        text: str,
        version_tag: Optional[str] = None,
        domain: str = "firmware",
        source_name: str = "state_flow.md",
    ) -> Tuple[Dict[str, StateFlowNode], List[StateFlowTransition]]:
        nodes_map: Dict[str, StateFlowNode] = {}
        transitions: List[StateFlowTransition] = []

        def ensure_node(
            raw_id: str,
            label: Optional[str] = None,
            kind: Optional[str] = None,
            node_domain: Optional[str] = None,
            extra_meta: Optional[Dict[str, Any]] = None,
        ) -> str:
            nid = "START" if raw_id.strip() == "[*]" else raw_id.strip()
            lbl = (label or nid).strip()
            if nid not in nodes_map:
                nodes_map[nid] = StateFlowNode(
                    node_id=nid,
                    node_kind=self._infer_node_kind(lbl, kind),
                    label=lbl,
                    domain=node_domain or domain,
                    version_tag=version_tag,
                    metadata={"source_file": source_name, **(extra_meta or {})},
                )
            elif label and nodes_map[nid].label == nid:
                nodes_map[nid].label = lbl
                nodes_map[nid].node_kind = self._infer_node_kind(lbl, kind)
            return nid

        # 1. Mermaid state aliases: state "BLE Pairing Screen" as BlePairScreen
        for m in re.finditer(r'^\s*state\s+"([^"]+)"\s+as\s+([A-Za-z0-9_.\-]+)', text, re.MULTILINE):
            ensure_node(m.group(2), label=m.group(1))

        # 2. Mermaid transitions: Source --> Target : trigger [guard] / signal1, signal2
        mermaid_re = re.compile(
            r"^\s*(\[\*\]|[A-Za-z0-9_.\-]+)\s*-->\s*(\[\*\]|[A-Za-z0-9_.\-]+)(?:\s*:\s*(.+))?$",
            re.MULTILINE,
        )
        for m in mermaid_re.finditer(text):
            src_raw, dst_raw, label_raw = m.group(1), m.group(2), (m.group(3) or "transition").strip()
            src = ensure_node(src_raw)
            dst = ensure_node(dst_raw)

            guard = None
            guard_m = re.search(r"\[([^\]]+)\]", label_raw)
            if guard_m:
                guard = guard_m.group(1).strip()
                label_wo_guard = re.sub(r"\[[^\]]+\]", "", label_raw).strip()
            else:
                label_wo_guard = label_raw

            expected_signals: List[str] = []
            timeout_ms = 5000
            to_m = re.search(r"(?:timeout\s*=?\s*|within\s+)(\d+)\s*ms", label_wo_guard, re.IGNORECASE)
            if to_m:
                timeout_ms = int(to_m.group(1))

            if "->" in label_wo_guard:
                parts = [p.strip() for p in label_wo_guard.split("->", 1)]
                trigger = parts[0] or "transition"
                expected_signals = [s.strip() for s in re.split(r"[,;/]+", parts[1]) if s.strip()]
            elif "/" in label_wo_guard:
                parts = [p.strip() for p in label_wo_guard.split("/", 1)]
                trigger = parts[0] or "transition"
                expected_signals = [s.strip() for s in re.split(r"[,;]+", parts[1]) if s.strip()]
            else:
                trigger = label_wo_guard or "transition"

            transitions.append(
                StateFlowTransition(
                    source_state=src,
                    target_state=dst,
                    trigger_action=trigger,
                    guard_condition=guard,
                    expected_signals=expected_signals,
                    timeout_ms=timeout_ms,
                    version_tag=version_tag,
                )
            )

        # 3. Markdown State-Transition Tables
        lines = text.splitlines()
        table_headers: Optional[List[str]] = None
        for line in lines:
            stripped = line.strip()
            if not (stripped.startswith("|") and stripped.endswith("|")):
                table_headers = None
                continue
            cells = [c.strip().strip("`") for c in stripped.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue
            lower_cells = [c.lower() for c in cells]
            if table_headers is None:
                if any(h in lower_cells for h in ("source", "from", "current_state", "state")) and any(
                    h in lower_cells for h in ("target", "to", "next_state")
                ):
                    table_headers = lower_cells
                continue

            row = {table_headers[i]: cells[i] for i in range(min(len(table_headers), len(cells)))}
            src_raw = row.get("source") or row.get("from") or row.get("current_state") or row.get("state") or ""
            dst_raw = row.get("target") or row.get("to") or row.get("next_state") or ""
            if not src_raw or not dst_raw:
                continue
            row_domain = row.get("domain") or domain
            node_kind = row.get("node_kind") or row.get("type")
            src = ensure_node(src_raw, kind=node_kind, node_domain=row_domain)
            dst = ensure_node(dst_raw, kind=node_kind, node_domain=row_domain)
            trigger = row.get("trigger") or row.get("action") or row.get("trigger_action") or row.get("event") or "transition"
            guard = row.get("guard") or row.get("guard_condition") or row.get("condition") or None
            if guard in ("", "-", "none", "N/A"):
                guard = None
            sigs_raw = row.get("expected_signals") or row.get("signals") or row.get("signal") or ""
            expected_signals = [s.strip() for s in re.split(r"[,;]+|->", sigs_raw) if s.strip()]
            to_raw = row.get("timeout_ms") or row.get("timeout") or "5000"
            to_digits = re.search(r"\d+", to_raw)
            timeout_ms = int(to_digits.group(0)) if to_digits else 5000

            transitions.append(
                StateFlowTransition(
                    source_state=src,
                    target_state=dst,
                    trigger_action=trigger,
                    guard_condition=guard,
                    expected_signals=expected_signals,
                    timeout_ms=timeout_ms,
                    version_tag=version_tag,
                )
            )

        return nodes_map, transitions

    def link(
        self,
        project_id: str,
        branch: str = "main",
        graph_data: Optional[Any] = None,
        chunks: Optional[List[Any]] = None,
        version_tag: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = context or {}
        eff_ver = version_tag or ctx.get("version_tag")
        default_domain = ctx.get("domain", "firmware")

        nodes_map: Dict[str, StateFlowNode] = {}
        transitions: List[StateFlowTransition] = []

        def merge_extracted(n_map: Dict[str, StateFlowNode], trans: List[StateFlowTransition]) -> None:
            for k, v in n_map.items():
                if k not in nodes_map:
                    nodes_map[k] = v
            transitions.extend(trans)

        # 1. Text / Mermaid / Table sources in context or chunks
        for idx, src in enumerate(ctx.get("flow_sources", []) or []):
            if isinstance(src, str):
                n_map, trans = self.extract_from_text(src, version_tag=eff_ver, domain=default_domain, source_name=f"flow_source_{idx}")
                merge_extracted(n_map, trans)
            elif isinstance(src, dict):
                txt = src.get("content") or src.get("text") or ""
                if txt:
                    n_map, trans = self.extract_from_text(
                        txt,
                        version_tag=src.get("version_tag") or eff_ver,
                        domain=src.get("domain") or default_domain,
                        source_name=src.get("source_file", f"flow_source_{idx}"),
                    )
                    merge_extracted(n_map, trans)

        for idx, ch in enumerate(chunks or []):
            ch_text = ch.get("content") if isinstance(ch, dict) else getattr(ch, "content", "")
            ch_file = ch.get("doc_path") if isinstance(ch, dict) else getattr(ch, "doc_path", f"chunk_{idx}")
            if ch_text:
                n_map, trans = self.extract_from_text(ch_text, version_tag=eff_ver, domain=default_domain, source_name=str(ch_file))
                merge_extracted(n_map, trans)

        # 2. Structured firmware/BLE sequences from context
        for seq in ctx.get("sequences", []) or []:
            steps = seq.get("transitions") or seq.get("steps") or []
            seq_domain = seq.get("domain", default_domain)
            seq_ver = seq.get("version_tag") or eff_ver
            for step in steps:
                src = str(step.get("source_state") or step.get("from") or "")
                dst = str(step.get("target_state") or step.get("to") or "")
                if not src or not dst:
                    continue
                if src not in nodes_map:
                    nodes_map[src] = StateFlowNode(
                        node_id=src,
                        node_kind=self._infer_node_kind(src, step.get("source_kind")),
                        label=src,
                        domain=seq_domain,
                        version_tag=seq_ver,
                    )
                if dst not in nodes_map:
                    nodes_map[dst] = StateFlowNode(
                        node_id=dst,
                        node_kind=self._infer_node_kind(dst, step.get("target_kind")),
                        label=dst,
                        domain=seq_domain,
                        version_tag=seq_ver,
                    )
                transitions.append(
                    StateFlowTransition(
                        source_state=src,
                        target_state=dst,
                        trigger_action=str(step.get("trigger_action") or step.get("trigger") or "step"),
                        guard_condition=step.get("guard_condition"),
                        expected_signals=list(step.get("expected_signals") or []),
                        timeout_ms=int(step.get("timeout_ms", 5000)),
                        version_tag=seq_ver,
                    )
                )

        # 3. Existing graph_data (e.g., Qt UI screens & connects_slot edges)
        if isinstance(graph_data, dict):
            raw_nodes = graph_data.get("nodes", [])
            raw_edges = graph_data.get("edges", [])
        elif graph_data is not None:
            raw_nodes = [_model_to_dict(n) for n in getattr(graph_data, "nodes", [])]
            raw_edges = [_model_to_dict(e) for e in getattr(graph_data, "edges", [])]
        else:
            raw_nodes, raw_edges = [], []

        for rn in raw_nodes:
            ftype = rn.get("file_type", "")
            meta = rn.get("metadata") or {}
            if ftype in ("screen", "state") or meta.get("symbol_type") == "qt_widget":
                nid = str(rn.get("id") or rn.get("label") or "")
                clean_id = nid.split(":", 1)[-1] if ":" in nid else nid
                if clean_id and clean_id not in nodes_map:
                    nodes_map[clean_id] = StateFlowNode(
                        node_id=clean_id,
                        node_kind="SCREEN_NODE" if ftype == "screen" or meta.get("symbol_type") == "qt_widget" else "STATE_NODE",
                        label=str(rn.get("label") or clean_id),
                        domain=meta.get("domain", "app" if ftype == "screen" else default_domain),
                        platform=meta.get("platform"),
                        screen_hash=meta.get("screen_hash"),
                        ui_fingerprint=meta.get("ui_fingerprint") or {},
                        version_tag=meta.get("version_tag") or eff_ver,
                    )

        for re_edge in raw_edges:
            rel = re_edge.get("relation", "")
            if rel in ("connects_slot", "transitions_to", "navigates_to"):
                src = str(re_edge.get("source", "")).split(":", 1)[-1]
                dst = str(re_edge.get("target", "")).split(":", 1)[-1]
                meta = re_edge.get("metadata") or {}
                if src and dst and src in nodes_map and dst in nodes_map:
                    transitions.append(
                        StateFlowTransition(
                            source_state=src,
                            target_state=dst,
                            trigger_action=str(meta.get("trigger_action") or meta.get("signal") or rel),
                            guard_condition=meta.get("guard_condition"),
                            expected_signals=list(meta.get("expected_signals") or ([meta["slot"]] if meta.get("slot") else [])),
                            timeout_ms=int(meta.get("timeout_ms", 5000)),
                            version_tag=meta.get("version_tag") or eff_ver,
                        )
                    )

        # Deduplicate transitions deterministically
        unique_trans: List[StateFlowTransition] = []
        seen_trans: Set[Tuple[str, str, str, Optional[str]]] = set()
        for t in transitions:
            key = (t.source_state, t.target_state, t.trigger_action, t.version_tag)
            if key not in seen_trans:
                seen_trans.add(key)
                unique_trans.append(t)

        graph_nodes: List[GraphNode] = []
        for sf_node in nodes_map.values():
            graph_nodes.append(
                GraphNode(
                    id=f"flow:{sf_node.node_id}",
                    project_id=project_id,
                    label=sf_node.label,
                    file_type="screen" if sf_node.node_kind == "SCREEN_NODE" else "state",
                    source_file=str((sf_node.metadata or {}).get("source_file", "state_flow")),
                    metadata=_model_to_dict(sf_node),
                )
            )

        graph_edges: List[GraphEdge] = []
        for t in unique_trans:
            graph_edges.append(
                GraphEdge(
                    project_id=project_id,
                    source=f"flow:{t.source_state}",
                    target=f"flow:{t.target_state}",
                    relation="transitions_to",
                    source_file="state_flow",
                    metadata=_model_to_dict(t),
                )
            )

        return {
            "plugin_id": self.metadata.plugin_id,
            "project_id": project_id,
            "branch": branch,
            "version_tag": eff_ver,
            "flow_nodes": [_model_to_dict(n) for n in nodes_map.values()],
            "flow_edges": [_model_to_dict(t) for t in unique_trans],
            "graph_delta": _build_delta(project_id, branch, graph_nodes, graph_edges),
        }

    def diff_flow_versions(
        self,
        graph_a: Dict[str, Any],
        graph_b: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compare two version-tagged flow graphs and return added/removed/modified nodes and transitions."""
        def _norm_nodes(g: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
            raw = g.get("flow_nodes") if "flow_nodes" in g else g.get("nodes", [])
            out: Dict[str, Dict[str, Any]] = {}
            for n in raw:
                nid = str(n.get("node_id") or n.get("id") or "")
                if nid:
                    out[nid] = n
            return out

        def _norm_edges(g: Dict[str, Any]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
            raw = g.get("flow_edges") if "flow_edges" in g else g.get("edges", [])
            out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
            for e in raw:
                src = str(e.get("source_state") or e.get("source") or "")
                dst = str(e.get("target_state") or e.get("target") or "")
                trig = str(e.get("trigger_action") or (e.get("metadata") or {}).get("trigger_action") or e.get("relation") or "")
                out[(src, dst, trig)] = e
            return out

        na, nb = _norm_nodes(graph_a), _norm_nodes(graph_b)
        ea, eb = _norm_edges(graph_a), _norm_edges(graph_b)

        added_nodes = [nb[k] for k in sorted(nb.keys() - na.keys())]
        removed_nodes = [na[k] for k in sorted(na.keys() - nb.keys())]
        modified_nodes: List[Dict[str, Any]] = []
        for k in sorted(na.keys() & nb.keys()):
            a_cmp = {x: na[k].get(x) for x in ("node_kind", "label", "domain", "screen_hash")}
            b_cmp = {x: nb[k].get(x) for x in ("node_kind", "label", "domain", "screen_hash")}
            if a_cmp != b_cmp:
                modified_nodes.append({"node_id": k, "before": na[k], "after": nb[k]})

        added_transitions = [eb[k] for k in sorted(eb.keys() - ea.keys())]
        removed_transitions = [ea[k] for k in sorted(ea.keys() - eb.keys())]
        modified_transitions: List[Dict[str, Any]] = []
        for k in sorted(ea.keys() & eb.keys()):
            a_cmp = {x: ea[k].get(x) for x in ("guard_condition", "expected_signals", "timeout_ms")}
            b_cmp = {x: eb[k].get(x) for x in ("guard_condition", "expected_signals", "timeout_ms")}
            if a_cmp != b_cmp:
                modified_transitions.append({"key": list(k), "before": ea[k], "after": eb[k]})

        return {
            "version_a": graph_a.get("version_tag"),
            "version_b": graph_b.get("version_tag"),
            "added_nodes": added_nodes,
            "removed_nodes": removed_nodes,
            "modified_nodes": modified_nodes,
            "added_transitions": added_transitions,
            "removed_transitions": removed_transitions,
            "modified_transitions": modified_transitions,
            "summary": {
                "added_nodes": len(added_nodes),
                "removed_nodes": len(removed_nodes),
                "modified_nodes": len(modified_nodes),
                "added_transitions": len(added_transitions),
                "removed_transitions": len(removed_transitions),
                "modified_transitions": len(modified_transitions),
                "has_breaking_changes": bool(removed_nodes or removed_transitions),
            },
        }


# ---------------------------------------------------------------------------
# 13. LocalAIOCRParserPlugin (GPU/CPU OCR + Local AI Vision Document Parser)
# ---------------------------------------------------------------------------
class LocalAIOCRParserPlugin(ETLParserPlugin):
    """Parses scanned PDFs and images via UnifiedOCREngine (GPU/CPU OCR & Local AI backends)."""

    SUPPORTED_ENGINES: Tuple[str, ...] = (
        "auto",
        "paddle",
        "rapidocr",
        "tesseract",
        "ollama",
        "vllm",
        "lmstudio",
        "llamacpp",
        "hybrid_local",
    )
    SUPPORTED_DEVICES: Tuple[str, ...] = ("auto", "gpu", "cpu")

    metadata = PluginMetadata(
        plugin_id="etl.local_ai_ocr",
        name="Local AI & GPU/CPU OCR Document Parser",
        version="3.0.0",
        category=PluginCategory.ETL_PARSER.value,
        description=(
            "Lazily invokes projectbrain.ocr_runtime.UnifiedOCREngine to parse scanned PDFs "
            "and images into Markdown chunks and Graphify document nodes/edges across GPU/CPU "
            "OCR (PaddleOCR, RapidOCR, Tesseract) and Local AI Vision backends (Ollama, vLLM, "
            "LM Studio, llama.cpp, hybrid_local)."
        ),
        priority=85,
        capabilities=[
            "ocr_local",
            "ocr_gpu",
            "ocr_cpu",
            "local_ai_vision",
            "paddleocr",
            "rapidocr",
            "tesseract",
            "ollama",
            "vllm",
            "lmstudio",
            "llamacpp",
            "hybrid_local",
            "pdf_ocr",
            "image_ocr",
        ],
        config_schema={
            "ocr_engine": {"type": "string", "default": "auto", "enum": list(SUPPORTED_ENGINES)},
            "ocr_device": {"type": "string", "default": "auto"},
            "local_ai_model": {"type": "string", "default": None},
            "local_ai_url": {"type": "string", "default": None},
            "dpi": {"type": "integer", "default": 200},
            "force_ocr": {"type": "boolean", "default": False},
        },
    )
    supported_extensions: Tuple[str, ...] = (
        ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp", ".pdf",
    )
    supported_formats: Tuple[str, ...] = (
        "ocr",
        "local_ai_ocr",
        "scanned_pdf",
        "image_ocr",
        "paddle",
        "rapidocr",
        "tesseract",
        "ollama",
        "vllm",
        "lmstudio",
        "llamacpp",
        "hybrid_local",
    )

    def can_parse(
        self,
        source_name: str,
        format_hint: Optional[str] = None,
        content_preview: Optional[str] = None,
    ) -> bool:
        if format_hint and format_hint.lower() in self.supported_formats:
            return True
        return Path(source_name).suffix.lower() in self.supported_extensions

    def health_check(self) -> Dict[str, Any]:
        base = super().health_check()
        engine = self.config.get("ocr_engine") or self.config.get("engine") or os.getenv("PB_OCR_ENGINE", "auto")
        device = self.config.get("ocr_device") or self.config.get("device") or os.getenv("PB_OCR_DEVICE", "auto")
        base.update({"ocr_engine": engine, "ocr_device": device})
        try:
            from projectbrain.ocr_runtime import ComputeDeviceManager, UnifiedOCREngine

            base["ocr_runtime_available"] = True
            if hasattr(UnifiedOCREngine, "get_status"):
                try:
                    base["runtime_status"] = UnifiedOCREngine.get_status()
                except TypeError:
                    base["runtime_status"] = UnifiedOCREngine().get_status()
            elif hasattr(ComputeDeviceManager, "detect_hardware"):
                base["hardware"] = ComputeDeviceManager.detect_hardware()
        except Exception as exc:  # noqa: BLE001
            base["ocr_runtime_available"] = False
            base["runtime_detail"] = str(exc)
        return base

    @staticmethod
    def _instantiate_unified_engine(
        engine_cls: Any,
        engine: str,
        device: str,
        local_ai_model: Optional[str],
        local_ai_url: Optional[str],
        dpi: int,
        force_ocr: bool,
    ) -> Any:
        candidate_kwargs: Dict[str, Any] = {
            "engine": engine,
            "ocr_engine": engine,
            "device": device,
            "ocr_device": device,
            "local_ai_model": local_ai_model,
            "model": local_ai_model,
            "local_ai_url": local_ai_url,
            "base_url": local_ai_url,
            "dpi": dpi,
            "force_ocr": force_ocr,
        }
        candidate_kwargs = {k: v for k, v in candidate_kwargs.items() if v is not None}
        try:
            sig = inspect.signature(engine_cls.__init__)
            params = sig.parameters
            has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            init_kwargs: Dict[str, Any] = {}
            if has_var_kw:
                init_kwargs = {
                    "engine": engine,
                    "device": device,
                    **({"local_ai_model": local_ai_model} if local_ai_model else {}),
                    **({"local_ai_url": local_ai_url} if local_ai_url else {}),
                }
            else:
                for k, v in candidate_kwargs.items():
                    if k in params:
                        init_kwargs[k] = v
            return engine_cls(**init_kwargs)
        except TypeError:
            return engine_cls()

    @staticmethod
    async def _invoke_ocr_engine(
        ocr_engine_obj: Any,
        target_path: Path,
        raw_bytes: bytes,
        is_pdf: bool,
        engine: str,
        device: str,
        local_ai_model: Optional[str],
        local_ai_url: Optional[str],
        dpi: int,
        force_ocr: bool,
    ) -> Tuple[str, Dict[str, Any]]:
        method_order = (
            ("parse_pdf", "ocr_pdf", "pdf_to_markdown", "parse_file", "process_file", "extract_markdown", "extract_text", "ocr_file", "parse")
            if is_pdf
            else ("parse_image", "ocr_image", "image_to_markdown", "extract_image", "extract_image_sync", "parse_file", "process_file", "extract_markdown", "extract_text", "ocr_file", "parse")
        )
        method = None
        for mname in method_order:
            cand = getattr(ocr_engine_obj, mname, None)
            if callable(cand):
                method = cand
                break

        # Real UnifiedOCREngine exposes extract_image/extract_batch rather than parse_pdf;
        # when parsing a PDF without a dedicated PDF method, extract digital text or render pages.
        if method is None and is_pdf and (
            callable(getattr(ocr_engine_obj, "extract_image", None))
            or callable(getattr(ocr_engine_obj, "extract_image_sync", None))
        ):
            return await LocalAIOCRParserPlugin._invoke_pdf_via_image_ocr(
                ocr_engine_obj=ocr_engine_obj,
                raw_bytes=raw_bytes,
                engine=engine,
                device=device,
                local_ai_model=local_ai_model,
                local_ai_url=local_ai_url,
                dpi=dpi,
                force_ocr=force_ocr,
            )

        if method is None:
            raise RuntimeError(
                "UnifiedOCREngine does not expose a supported OCR parse method "
                "(expected one of parse_pdf/ocr_pdf/parse_image/ocr_image/extract_image/parse_file/extract_markdown)."
            )

        mime_type, _ = mimetypes.guess_type(target_path.name)
        mime_type = mime_type or ("application/pdf" if is_pdf else "image/png")
        optional_kwargs: Dict[str, Any] = {
            "engine": engine,
            "ocr_engine": engine,
            "device": device,
            "ocr_device": device,
            "local_ai_model": local_ai_model,
            "local_ai_url": local_ai_url,
            "dpi": dpi,
            "force_ocr": force_ocr,
            "mime_type": mime_type,
        }
        optional_kwargs = {k: v for k, v in optional_kwargs.items() if v is not None}

        sig = inspect.signature(method)
        params = list(sig.parameters.values())
        param_names = {p.name for p in params}
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

        call_kwargs: Dict[str, Any] = {}
        if "pdf_path" in param_names:
            call_kwargs["pdf_path"] = str(target_path)
        elif "image_path" in param_names:
            call_kwargs["image_path"] = str(target_path)
        elif "file_path" in param_names:
            call_kwargs["file_path"] = str(target_path)
        elif "path" in param_names:
            call_kwargs["path"] = str(target_path)
        elif "image_bytes" in param_names:
            call_kwargs["image_bytes"] = raw_bytes
        elif "data" in param_names:
            call_kwargs["data"] = raw_bytes
        elif "raw_bytes" in param_names:
            call_kwargs["raw_bytes"] = raw_bytes

        for k, v in optional_kwargs.items():
            if k in param_names:
                call_kwargs[k] = v
        if has_var_kw:
            for k in ("engine", "device", "local_ai_model", "local_ai_url", "dpi", "force_ocr"):
                if k in optional_kwargs and k not in call_kwargs:
                    call_kwargs[k] = optional_kwargs[k]

        if call_kwargs:
            try:
                result = method(**call_kwargs)
            except TypeError:
                result = method(str(target_path))
        else:
            result = method(str(target_path))

        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, str):
            return result, {}
        if isinstance(result, dict):
            md = result.get("markdown") or result.get("markdown_content") or result.get("text") or result.get("content") or ""
            if not md and isinstance(result.get("pages"), list):
                md = "\n\n".join(
                    str(p.get("markdown") or p.get("text") or p) for p in result["pages"] if p
                )
            meta_out = {k: v for k, v in result.items() if k not in ("markdown", "markdown_content", "text", "content")}
            return str(md), meta_out

        md_attr = getattr(result, "markdown", None) or getattr(result, "text", None) or getattr(result, "content", None)
        if md_attr is not None:
            meta_out = _model_to_dict(result) if hasattr(result, "to_dict") or hasattr(result, "model_dump") or hasattr(result, "dict") else {}
            meta_out.pop("markdown", None)
            meta_out.pop("text", None)
            meta_out.pop("content", None)
            return str(md_attr), meta_out
        return str(result or ""), {}

    @staticmethod
    async def _invoke_pdf_via_image_ocr(
        ocr_engine_obj: Any,
        raw_bytes: bytes,
        engine: str,
        device: str,
        local_ai_model: Optional[str],
        local_ai_url: Optional[str],
        dpi: int,
        force_ocr: bool,
    ) -> Tuple[str, Dict[str, Any]]:
        """Render PDF pages and run UnifiedOCREngine image OCR on pages needing OCR."""
        layer: Dict[int, str] = {}
        if not force_ocr:
            try:
                import pdfplumber

                with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                    for idx, page in enumerate(pdf.pages):
                        layer[idx] = page.extract_text(x_tolerance=2) or ""
            except Exception:
                layer = {}

        try:
            from projectbrain.utils.pdf_render import (
                is_available as pdf_render_available,
                page_count,
                render_page_png,
            )
        except Exception as exc:
            raise RuntimeError("PDF rendering utilities are unavailable for UnifiedOCREngine PDF OCR.") from exc

        if not pdf_render_available():
            if layer:
                parts = [f"## Page {idx + 1}\n\n{txt.strip()}" for idx, txt in sorted(layer.items()) if txt.strip()]
                return "\n\n".join(parts), {"engine_used": "pdfplumber", "device_used": "cpu", "pages": len(layer)}
            raise RuntimeError("PyMuPDF (pymupdf) is required to render scanned PDFs for OCR.")

        total_pages = page_count(raw_bytes)
        page_texts: Dict[int, str] = {}
        ocr_indices: List[int] = []
        for idx in range(total_pages):
            txt = layer.get(idx, "").strip()
            if not force_ocr and len(txt) >= 15:
                page_texts[idx] = txt
            else:
                ocr_indices.append(idx)

        img_method = getattr(ocr_engine_obj, "extract_image", None) or getattr(ocr_engine_obj, "extract_image_sync", None)
        last_meta: Dict[str, Any] = {}
        for idx in ocr_indices:
            png = render_page_png(raw_bytes, idx, dpi=dpi)
            if not png:
                page_texts[idx] = ""
                continue
            call_kw: Dict[str, Any] = {
                "image_bytes": png,
                "mime_type": "image/png",
                "engine": engine,
                "device": device,
                **({"local_ai_model": local_ai_model} if local_ai_model else {}),
                **({"local_ai_url": local_ai_url} if local_ai_url else {}),
            }
            try:
                res = img_method(**call_kw)
            except TypeError:
                res = img_method(png)
            if inspect.isawaitable(res):
                res = await res
            if isinstance(res, dict):
                page_texts[idx] = str(res.get("text") or res.get("markdown") or res.get("content") or "").strip()
                last_meta = {k: v for k, v in res.items() if k not in ("text", "markdown", "content")}
            else:
                page_texts[idx] = str(res or "").strip()

        parts = [f"## Page {idx + 1}\n\n{page_texts.get(idx, '')}".strip() for idx in range(total_pages)]
        return "\n\n".join(parts).strip(), {
            **last_meta,
            "pages": total_pages,
            "ocr_pages": len(ocr_indices),
        }

    @staticmethod
    def _chunk_markdown(
        markdown_content: str,
        stem: str,
        sector: str,
        tags: List[str],
        ocr_engine: str,
        ocr_device: str,
    ) -> List[Dict[str, Any]]:
        if not markdown_content.strip():
            return []
        try:
            from projectbrain.utils.chunking import chunk_text

            raw_chunks = chunk_text(markdown_content, tgt=768, ovr=0.1)
        except Exception:
            paragraphs = [p.strip() for p in re.split(r"\n{2,}", markdown_content) if p.strip()]
            raw_chunks = [{"text": p, "start": 0, "end": len(p), "tokens": len(p.split())} for p in paragraphs]

        chunks: List[Dict[str, Any]] = []
        for idx, ch in enumerate(raw_chunks):
            chunks.append(
                {
                    "chunk_id": f"{stem}_ocr_p{idx + 1}",
                    "content": ch.get("text", ""),
                    "start": ch.get("start", 0),
                    "end": ch.get("end", 0),
                    "tokens": ch.get("tokens", len(str(ch.get("text", "")).split())),
                    "sector": sector,
                    "tags": tags,
                    "ocr_engine": ocr_engine,
                    "ocr_device": ocr_device,
                }
            )
        return chunks

    async def parse(
        self,
        source: Union[str, bytes],
        source_name: str = "scanned_document.pdf",
        project_id: str = "default",
        branch: str = "main",
        version_tag: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            from projectbrain.ocr_runtime import UnifiedOCREngine
        except ImportError as exc:
            raise RuntimeError(
                "projectbrain.ocr_runtime.UnifiedOCREngine is not available. "
                "Install an OCR extra (e.g. `pip install -e '.[ocr-cpu]'`, `'.[ocr-gpu]'`, "
                "`'.[ocr-onnx]'`, or `'.[ocr-onnx-gpu]'`) or configure a Local AI backend."
            ) from exc

        meta = metadata or {}
        engine = str(
            meta.get("ocr_engine")
            or meta.get("engine")
            or self.config.get("ocr_engine")
            or self.config.get("engine")
            or os.getenv("PB_OCR_ENGINE")
            or "auto"
        ).strip().lower()
        device = str(
            meta.get("ocr_device")
            or meta.get("device")
            or self.config.get("ocr_device")
            or self.config.get("device")
            or os.getenv("PB_OCR_DEVICE")
            or "auto"
        ).strip().lower()
        local_ai_model = (
            meta.get("local_ai_model")
            or self.config.get("local_ai_model")
            or os.getenv("PB_LOCAL_AI_MODEL")
            or os.getenv("PB_OLLAMA_VISION_MODEL")
        )
        local_ai_url = (
            meta.get("local_ai_url")
            or self.config.get("local_ai_url")
            or os.getenv("PB_LOCAL_AI_URL")
            or os.getenv("OLLAMA_URL")
        )
        dpi = int(meta.get("dpi") or self.config.get("dpi") or 200)
        force_ocr = bool(meta.get("force_ocr") if "force_ocr" in meta else self.config.get("force_ocr", False))
        sector = str(meta.get("sector", "semantic"))
        tags = list(meta.get("tags") or [])

        raw_bytes, existing_path = _read_source_bytes(source)
        effective_name = existing_path.name if existing_path else (Path(source_name).name or "scanned_document.pdf")
        ext = Path(effective_name).suffix.lower()
        is_pdf = ext == ".pdf"
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()

        ocr_engine_obj = self._instantiate_unified_engine(
            UnifiedOCREngine,
            engine=engine,
            device=device,
            local_ai_model=local_ai_model,
            local_ai_url=local_ai_url,
            dpi=dpi,
            force_ocr=force_ocr,
        )

        if existing_path is not None:
            markdown_content, ocr_meta = await self._invoke_ocr_engine(
                ocr_engine_obj,
                existing_path,
                raw_bytes,
                is_pdf=is_pdf,
                engine=engine,
                device=device,
                local_ai_model=local_ai_model,
                local_ai_url=local_ai_url,
                dpi=dpi,
                force_ocr=force_ocr,
            )
            doc_path_str = str(existing_path)
            cache_base = existing_path.parent / ".projectbrain" / "cache"
        else:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                target_file = tmp_path / effective_name
                target_file.write_bytes(raw_bytes)
                markdown_content, ocr_meta = await self._invoke_ocr_engine(
                    ocr_engine_obj,
                    target_file,
                    raw_bytes,
                    is_pdf=is_pdf,
                    engine=engine,
                    device=device,
                    local_ai_model=local_ai_model,
                    local_ai_url=local_ai_url,
                    dpi=dpi,
                    force_ocr=force_ocr,
                )
            doc_path_str = source_name
            cache_base = None

        resolved_engine = str(ocr_meta.get("engine_used") or ocr_meta.get("engine") or engine)
        resolved_device = str(ocr_meta.get("device_used") or ocr_meta.get("device") or device)
        stem = Path(effective_name).stem or "ocr_doc"
        chunks = self._chunk_markdown(
            markdown_content=markdown_content,
            stem=stem,
            sector=sector,
            tags=tags,
            ocr_engine=resolved_engine,
            ocr_device=resolved_device,
        )

        doc_metadata = {
            **meta,
            "sha256": file_sha256,
            "file_type": "paper" if is_pdf else "image",
            "ocr_engine": resolved_engine,
            "ocr_device": resolved_device,
            **({"local_ai_model": local_ai_model} if local_ai_model else {}),
            **({"local_ai_url": local_ai_url} if local_ai_url else {}),
            **({"version_tag": version_tag} if version_tag else {}),
        }

        graph_delta_dict: Dict[str, Any] = {}
        try:
            from projectbrain.graphify_engine.extractor import GraphifyExtractor

            cache_dir = self.config.get("cache_dir") or cache_base
            extractor = GraphifyExtractor(cache_dir=cache_dir) if cache_dir else GraphifyExtractor()
            delta = extractor.extract_document_graph(
                file_path=doc_path_str,
                markdown_content=markdown_content,
                project_id=project_id,
                branch=branch,
                metadata=doc_metadata,
            )
            graph_delta_dict = _model_to_dict(delta)
        except Exception:
            graph_delta_dict = {}

        if not graph_delta_dict.get("nodes"):
            doc_node_id = f"doc:{effective_name}"
            nodes: List[GraphNode] = [
                GraphNode(
                    id=doc_node_id,
                    project_id=project_id,
                    label=effective_name,
                    file_type="document",
                    source_file=doc_path_str,
                    metadata=doc_metadata,
                )
            ]
            edges: List[GraphEdge] = []
            for ch in chunks:
                cid = f"doc_chunk:{ch['chunk_id']}"
                nodes.append(
                    GraphNode(
                        id=cid,
                        project_id=project_id,
                        label=ch["chunk_id"],
                        file_type="document",
                        source_file=doc_path_str,
                        metadata={
                            "ocr_engine": resolved_engine,
                            "ocr_device": resolved_device,
                            "tokens": ch["tokens"],
                            "version_tag": version_tag,
                        },
                    )
                )
                edges.append(
                    GraphEdge(
                        project_id=project_id,
                        source=doc_node_id,
                        target=cid,
                        relation="contains_section",
                        source_file=doc_path_str,
                    )
                )
            graph_delta_dict = _build_delta(project_id, branch, nodes, edges)

        return {
            "plugin_id": self.metadata.plugin_id,
            "graph_delta": graph_delta_dict,
            "document": {
                "filename": doc_path_str,
                "sha256": file_sha256,
                "file_type": "paper" if is_pdf else "image",
                "markdown_content": markdown_content,
                "chunks": chunks,
                "ocr_engine": resolved_engine,
                "ocr_device": resolved_device,
                "local_ai_model": local_ai_model,
                "local_ai_url": local_ai_url,
                "ocr_metadata": ocr_meta,
                "version_tag": version_tag,
            },
            "chunks": chunks,
            "canonical_intents": [],
            "error_rules": [],
            "flow_nodes": [],
            "flow_edges": [],
        }


# ---------------------------------------------------------------------------
# Plugin Discovery / Registration Entrypoints
# ---------------------------------------------------------------------------
_BUILTIN_PLUGIN_CLASSES = (
    GraphifyASTETLPlugin,
    MultimodalDocETLPlugin,
    LocalAIOCRParserPlugin,
    CodeDocLinkerPlugin,
    MarkdownWikiExporterPlugin,
    DocxExporterPlugin,
    XlsxExporterPlugin,
    PptxExporterPlugin,
    LegacyTestcaseETLPlugin,
    HardwareDatasheetETLPlugin,
    OpenAPI31ETLPlugin,
    CppEmbeddedETLPlugin,
    StateFlowGraphPlugin,
)


def get_plugins(config: Optional[Dict[str, Any]] = None) -> List[BasePlugin]:
    """Instantiate and return all 13 built-in ETL, Linker, and Exporter plugins."""
    return [cls(config=config) for cls in _BUILTIN_PLUGIN_CLASSES]


def register_plugins(registry: Any, config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Register all 13 built-in ETL, Linker, and Exporter plugins into the given PluginRegistry."""
    registered: List[str] = []
    for plugin in get_plugins(config=config):
        registry.register(plugin)
        registered.append(plugin.metadata.plugin_id)
    return registered


__all__ = [
    "GraphifyASTETLPlugin",
    "MultimodalDocETLPlugin",
    "LocalAIOCRParserPlugin",
    "CodeDocLinkerPlugin",
    "MarkdownWikiExporterPlugin",
    "DocxExporterPlugin",
    "XlsxExporterPlugin",
    "PptxExporterPlugin",
    "LegacyTestcaseETLPlugin",
    "HardwareDatasheetETLPlugin",
    "OpenAPI31ETLPlugin",
    "CppEmbeddedETLPlugin",
    "StateFlowGraphPlugin",
    "get_plugins",
    "register_plugins",
]
