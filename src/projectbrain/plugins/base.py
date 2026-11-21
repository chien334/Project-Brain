"""Canonical locked plugin contracts and v3.0 domain DTOs for Project-Brain microkernel."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

try:
    from pydantic import ConfigDict

    _HAS_CONFIG_DICT = True
except ImportError:
    ConfigDict = None  # type: ignore[assignment]
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
    category: str  # PluginCategory value
    description: str = ""
    author: str = "projectbrain"
    priority: int = 100
    capabilities: List[str] = Field(default_factory=list)
    requires_env: List[str] = Field(default_factory=list)
    config_schema: Dict[str, Any] = Field(default_factory=dict)
    default_enabled: bool = True
    entrypoint: Optional[str] = None


class PluginManifest(BasePluginModel):
    plugin_id: str
    name: str
    version: str = "1.0.0"
    category: str
    entrypoint: str = "plugin.py"
    class_name: Optional[str] = None
    description: str = ""
    priority: int = 100
    requires_env: List[str] = Field(default_factory=list)
    default_config: Dict[str, Any] = Field(default_factory=dict)
    default_enabled: bool = True


class PluginContext(BasePluginModel):
    project_id: str = "default"
    branch: str = "main"
    version_tag: Optional[str] = None
    workspace_path: str = "."
    config: Dict[str, Any] = Field(default_factory=dict)
    registry: Optional[Any] = None


# ---------------------------------------------------------------------------
# Canonical v3.0 Domain DTOs
# ---------------------------------------------------------------------------
class CanonicalTestStep(BasePluginModel):
    step_index: int
    action: str  # tap | input | assert | uart_send | wait_signal | api_call | power_measure
    target_intent: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = 10000


class CanonicalOracleAssertion(BasePluginModel):
    assertion_type: str  # ui_state | uart_pattern | api_status | log_regex | power_budget_ma
    expected_value: Any
    tolerance: Optional[str] = None
    severity: str = "error"


class CanonicalTestIntent(BasePluginModel):
    intent_id: str
    title: str
    description: str = ""
    priority: str = "P1"  # P0 | P1 | P2 | P3
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
    protocol: str  # uart | at | http | hardfault | kernel | custom
    severity: str = "error"  # critical | error | warning
    pattern: str
    description: str = ""
    fault_domain: str = "Firmware"
    remediation_hint: str = ""
    source_file: str = ""
    version_tag: Optional[str] = None


class StateFlowNode(BasePluginModel):
    node_id: str
    node_kind: str = "STATE_NODE"  # STATE_NODE | SCREEN_NODE
    label: str
    domain: str = "firmware"  # firmware | app | backend
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


class UDCPScriptCacheEntry(BasePluginModel):
    cache_id: str
    project_id: str
    intent_id: str
    platform: str
    build_version: str
    screen_hash: str = ""
    ui_fingerprint: Dict[str, Any] = Field(default_factory=dict)
    udcp_commands: List[Dict[str, Any]] = Field(default_factory=list)
    hit_count: int = 0
    fallback_count: int = 0
    fallback_rate: float = 0.0
    invalidated: bool = False
    invalidation_reason: Optional[str] = None
    self_heal_history: List[Dict[str, Any]] = Field(default_factory=list)
    ttl_seconds: int = 2592000


class TelemetryArtifactRecord(BasePluginModel):
    artifact_id: str
    run_id: str
    step_id: str
    project_id: str
    device_id: str = ""
    build_version: str = ""
    artifact_type: str  # video_h264 | har | pcap | crashpad_dmp | gdb_hardfault | uart_log | power_trace_ma | screenshot_png
    ntp_timestamp_ms: int
    duration_ms: int = 0
    uri: str
    sha256: str = ""
    size_bytes: int = 0
    summary_metrics: Dict[str, Any] = Field(default_factory=dict)
    retention_days: int = 30
    expires_at: Optional[int] = None


# ---------------------------------------------------------------------------
# Abstract Base Plugin Contracts
# ---------------------------------------------------------------------------
class BasePlugin(ABC):
    """Root contract implemented by every Project-Brain v3.0 plugin."""

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


class GraphBackendPlugin(BasePlugin):
    """Contract for GraphStore backend plugins (SQLite, PostgreSQL, Neo4j)."""

    @abstractmethod
    def create_store(self, config: Optional[Dict[str, Any]] = None) -> Any:
        """Return a GraphStore-compatible object."""

    def query_flow_graph(
        self,
        project_id: str,
        branch: str = "main",
        version_tag: Optional[str] = None,
        store: Optional[Any] = None,
    ) -> Dict[str, Any]:
        active = store or self.create_store(self.config)
        sub = active.get_resolved_subgraph(project_id, branch, limit=100000)
        nodes = [
            n
            for n in sub.get("nodes", [])
            if n.get("file_type") in ("state", "screen")
            and (not version_tag or (n.get("metadata") or {}).get("version_tag") == version_tag)
        ]
        node_ids = {n["id"] for n in nodes}
        edges = [
            e
            for e in sub.get("edges", [])
            if e.get("relation") == "transitions_to"
            or (e.get("source") in node_ids and e.get("target") in node_ids)
        ]
        return {
            "project_id": project_id,
            "branch": branch,
            "version_tag": version_tag,
            "nodes": nodes,
            "edges": edges,
        }


class VectorBackendPlugin(BasePlugin):
    """Contract for Vector & Hybrid Search backend plugins (HSG Memory, Qdrant)."""

    @abstractmethod
    async def upsert_vectors(
        self, items: List[Dict[str, Any]], namespace: str = "default"
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def search(
        self,
        query: str,
        vector: Optional[List[float]] = None,
        top_k: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        hybrid_alpha: float = 0.7,
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def delete_by_filter(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        pass


class ETLParserPlugin(BasePlugin):
    """Contract for node-etl canonical parsers (Legacy Testcases, Hardware Datasheets, OpenAPI, C++, AST, Docs)."""

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
        """Return {'graph_delta': ..., 'canonical_intents': [...], 'error_rules': [...], 'flow_nodes': [...], 'flow_edges': [...], 'chunks': [...]}."""


class KnowledgeLinkerPlugin(BasePlugin):
    """Contract for cross-domain knowledge and state-flow linkers."""

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
    """Contract for deterministic Wiki and Office/Report exporters."""

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


class ScriptCachePlugin(BasePlugin):
    """Contract for Layer 1 node-cache Verified UDCP Script Cache plugins."""

    @abstractmethod
    def lookup_script(
        self,
        project_id: str,
        intent_id: str,
        platform: str,
        build_version: str,
        screen_hash: Optional[str] = None,
        include_invalidated: bool = False,
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def store_script(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    def record_execution(
        self,
        cache_id: str,
        success: bool,
        fallback_reason: Optional[str] = None,
        self_heal_patch: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    def invalidate_script(self, cache_id: str, reason: str) -> bool:
        pass

    @abstractmethod
    def list_scripts(
        self,
        project_id: str,
        platform: Optional[str] = None,
        build_version: Optional[str] = None,
        include_invalidated: bool = True,
    ) -> List[Dict[str, Any]]:
        pass


class ArtifactStorePlugin(BasePlugin):
    """Contract for Layer 1 node-artifacts Synchronized Telemetry & Evidence stores."""

    @abstractmethod
    def index_artifact(self, record: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abstractmethod
    def query_artifacts(
        self,
        run_id: Optional[str] = None,
        step_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        build_version: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def query_evidence_window(
        self,
        run_id: str,
        center_ts_ms: int,
        window_ms: int = 5000,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    def purge_expired(self, now_ms: Optional[int] = None) -> Dict[str, Any]:
        pass


class BrainConsumerPlugin(BasePlugin):
    """Contract for Layer 2 cognitive consumers (node-planner, node-oracle, node-rca)."""

    role: str = "planner"  # planner | oracle | rca

    @abstractmethod
    async def execute(
        self,
        request: Dict[str, Any],
        registry: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pass


class WebhookPlugin(BasePlugin):
    """Contract for Layer 0 CI/CD & QA Tracker webhook ingestors."""

    source_name: str = "generic"

    @abstractmethod
    def verify_signature(
        self,
        headers: Dict[str, str],
        raw_body: bytes,
        secret: Optional[str] = None,
    ) -> bool:
        pass

    @abstractmethod
    async def parse_event(
        self, headers: Dict[str, str], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        pass


__all__ = [
    "PluginCategory",
    "BasePluginModel",
    "PluginMetadata",
    "PluginManifest",
    "PluginContext",
    "CanonicalTestStep",
    "CanonicalOracleAssertion",
    "CanonicalTestIntent",
    "ErrorCodeRule",
    "StateFlowNode",
    "StateFlowTransition",
    "UDCPScriptCacheEntry",
    "TelemetryArtifactRecord",
    "BasePlugin",
    "GraphBackendPlugin",
    "VectorBackendPlugin",
    "ETLParserPlugin",
    "KnowledgeLinkerPlugin",
    "DocExporterPlugin",
    "ScriptCachePlugin",
    "ArtifactStorePlugin",
    "BrainConsumerPlugin",
    "WebhookPlugin",
]
