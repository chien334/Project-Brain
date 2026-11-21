"""Built-in plugins for Project-Brain v3.0 microkernel.

Uses defensive try/except ImportError guards around sibling plugin modules so
partial/concurrent builds remain safely importable.
"""

from __future__ import annotations

from typing import List, Type

from ..base import BasePlugin

_BUILTIN_PLUGIN_CLASSES: List[Type[BasePlugin]] = []

# 1. Storage plugins (TASK-14A)
try:
    from .storage_plugins import (
        HSGMemoryPlugin,
        Neo4jGraphPlugin,
        Neo4jGraphStore,
        PostgresSchemaGraphPlugin,
        QdrantVectorPlugin,
        SqliteOverlayGraphPlugin,
    )

    _BUILTIN_PLUGIN_CLASSES.extend(
        [
            SqliteOverlayGraphPlugin,
            PostgresSchemaGraphPlugin,
            Neo4jGraphPlugin,
            HSGMemoryPlugin,
            QdrantVectorPlugin,
        ]
    )
except ImportError:  # pragma: no cover
    pass

# 2. ETL, Linker & Exporter plugins (TASK-14B)
try:
    from .etl_and_flow_plugins import (
        CodeDocLinkerPlugin,
        CppEmbeddedETLPlugin,
        DocxExporterPlugin,
        GraphifyASTETLPlugin,
        HardwareDatasheetETLPlugin,
        LegacyTestcaseETLPlugin,
        MarkdownWikiExporterPlugin,
        MultimodalDocETLPlugin,
        OpenAPI31ETLPlugin,
        PptxExporterPlugin,
        StateFlowGraphPlugin,
        XlsxExporterPlugin,
    )

    _BUILTIN_PLUGIN_CLASSES.extend(
        [
            GraphifyASTETLPlugin,
            MultimodalDocETLPlugin,
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
        ]
    )
except Exception:  # pragma: no cover
    pass

# 3. Script Cache & Telemetry Artifact plugins (TASK-14C)
try:
    from .cache_and_artifact_plugins import (
        LocalSqliteArtifactStorePlugin,
        MinioClickHouseArtifactStorePlugin,
        RedisScriptCachePlugin,
        SqlitePgScriptCachePlugin,
    )

    _BUILTIN_PLUGIN_CLASSES.extend(
        [
            SqlitePgScriptCachePlugin,
            RedisScriptCachePlugin,
            LocalSqliteArtifactStorePlugin,
            MinioClickHouseArtifactStorePlugin,
        ]
    )
except Exception:  # pragma: no cover
    pass

# 4. Brain Consumer & Webhook plugins (TASK-14D)
try:
    from .brain_consumer_plugins import (
        ImpactTestDAGPlannerPlugin,
        OracleKnowledgeJudgePlugin,
        RCATriageMemoryPlugin,
        WebhookEventIngestPlugin,
    )

    _BUILTIN_PLUGIN_CLASSES.extend(
        [
            ImpactTestDAGPlannerPlugin,
            OracleKnowledgeJudgePlugin,
            RCATriageMemoryPlugin,
            WebhookEventIngestPlugin,
        ]
    )
except Exception:  # pragma: no cover
    pass


def get_all_builtin_plugins() -> List[BasePlugin]:
    """Instantiate and return all available built-in plugins."""
    return [cls() for cls in _BUILTIN_PLUGIN_CLASSES]


__all__ = [cls.__name__ for cls in _BUILTIN_PLUGIN_CLASSES] + [
    "get_all_builtin_plugins",
]
