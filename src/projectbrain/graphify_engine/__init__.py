"""Graphify Engine Package for ProjectBrain.

Provides AST entity extraction, multimodal document pipeline, 3-tier hybrid resolver,
and multi-schema branch overlay GraphStore.
"""

from .models import (
    BaseGraphModel,
    BranchDiffResult,
    BranchDiffSummary,
    GraphDelta,
    GraphEdge,
    GraphHyperedge,
    GraphNode,
)
from .store import (
    GraphStore,
    PostgresSchemaGraphStore,
    SqliteOverlayGraphStore,
    get_graph_store,
    sanitize_branch_schema_name,
)

# Defensive imports for concurrent sibling builders (TASK-04B, TASK-04C)
try:
    from .extractor import GraphifyExtractor
except ImportError:
    GraphifyExtractor = None

try:
    from .doc_pipeline import DocumentPipeline
except ImportError:
    DocumentPipeline = None

try:
    from .hybrid_resolver import HybridResolver
except ImportError:
    HybridResolver = None

try:
    from .doc_synthesizer import (
        OfficeExportResult,
        ProjectDocSynthesizer,
        SynthesisResult,
        WikiPage,
    )
except ImportError:
    ProjectDocSynthesizer = None
    WikiPage = None
    OfficeExportResult = None
    SynthesisResult = None

try:
    from .kb_linker import (
        CodeDocDrift,
        CodeDocLinker,
        CodeSymbolRef,
        CrossLinkEdge,
        DocChunk,
        KBCoverageReport,
        KBTraceResult,
        UnifiedKBBuilder,
    )
except ImportError:
    CodeDocLinker = None
    UnifiedKBBuilder = None
    DocChunk = None
    CodeSymbolRef = None
    CrossLinkEdge = None
    CodeDocDrift = None
    KBTraceResult = None
    KBCoverageReport = None

__all__ = [
    "BaseGraphModel",
    "GraphNode",
    "GraphEdge",
    "GraphHyperedge",
    "GraphDelta",
    "BranchDiffSummary",
    "BranchDiffResult",
    "GraphStore",
    "PostgresSchemaGraphStore",
    "SqliteOverlayGraphStore",
    "get_graph_store",
    "sanitize_branch_schema_name",
    "GraphifyExtractor",
    "DocumentPipeline",
    "HybridResolver",
    "ProjectDocSynthesizer",
    "WikiPage",
    "OfficeExportResult",
    "SynthesisResult",
    "CodeDocLinker",
    "UnifiedKBBuilder",
    "DocChunk",
    "CodeSymbolRef",
    "CrossLinkEdge",
    "CodeDocDrift",
    "KBTraceResult",
    "KBCoverageReport",
]
