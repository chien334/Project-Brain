"""Layer 2 Brain Consumer & Layer 0 Webhook Plugins for Project-Brain v3.0.

Implements:
- ImpactTestDAGPlannerPlugin (brain.planner)
- OracleKnowledgeJudgePlugin (brain.oracle)
- RCATriageMemoryPlugin (brain.rca)
- WebhookEventIngestPlugin (webhook.event_ingest)
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os
import re
import sqlite3
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from pydantic import Field

try:
    from projectbrain.plugins.base import (
        BasePlugin,
        BasePluginModel,
        BrainConsumerPlugin,
        CanonicalOracleAssertion,
        CanonicalTestIntent,
        CanonicalTestStep,
        ErrorCodeRule,
        GraphBackendPlugin,
        PluginCategory,
        PluginContext,
        PluginMetadata,
        ScriptCachePlugin,
        StateFlowNode,
        StateFlowTransition,
        VectorBackendPlugin,
        WebhookPlugin,
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
            return self.model_dump() if hasattr(self, "model_dump") else self.dict()

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

    class GraphBackendPlugin(BasePlugin):
        @abstractmethod
        def create_store(self, config: Optional[Dict[str, Any]] = None) -> Any:
            pass

    class VectorBackendPlugin(BasePlugin):
        @abstractmethod
        async def upsert_vectors(self, items: List[Dict[str, Any]], namespace: str = "default") -> Dict[str, Any]:
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

    class ScriptCachePlugin(BasePlugin):
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

    class BrainConsumerPlugin(BasePlugin):
        role: str = "planner"

        @abstractmethod
        async def execute(
            self,
            request: Dict[str, Any],
            registry: Optional[Any] = None,
            context: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            pass

    class WebhookPlugin(BasePlugin):
        source_name: str = "generic"

        @abstractmethod
        def verify_signature(self, headers: Dict[str, str], raw_body: bytes, secret: Optional[str] = None) -> bool:
            pass

        @abstractmethod
        async def parse_event(self, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
            pass


# ---------------------------------------------------------------------------
# Shared Helpers & DTOs
# ---------------------------------------------------------------------------

_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_PRIORITY_SCORE = {"P0": 100, "P1": 70, "P2": 40, "P3": 20}

_FIRMWARE_ARTIFACT_EXTS = (".bin", ".elf", ".hex", ".uf2", ".dfu")
_FIRMWARE_SOURCE_EXTS = (".c", ".h", ".cpp", ".cc", ".hpp", ".ld", ".s", ".asm")
_APP_ARTIFACT_EXTS = (".apk", ".ipa", ".appx", ".aab")
_BUILD_ARTIFACT_EXTS = _FIRMWARE_ARTIFACT_EXTS + _APP_ARTIFACT_EXTS

_CORE_HUB_KEYWORDS = (
    "auth",
    "login",
    "payment",
    "pay",
    "checkout",
    "boot",
    "firmware",
    "kernel",
    "hardfault",
    "ble",
    "ota",
    "security",
    "crypto",
    "core",
    "safety",
)

_NON_TICKET_PREFIXES = {
    "HTTP",
    "HTTPS",
    "UTF",
    "SHA",
    "MD",
    "TLS",
    "SSL",
    "ARM",
    "BLE",
    "USB",
    "UART",
    "SPI",
    "I2C",
    "GPIO",
    "ISO",
    "RFC",
    "CVE",
    "ERR",
    "AT",
    "RX",
    "TX",
}


class WebhookNormalizedEvent(BasePluginModel):
    """Canonical normalized Layer 0 webhook event payload."""

    status: str = "ok"
    source: str
    event_type: str
    project_id: str = "default"
    branch: Optional[str] = None
    base_branch: Optional[str] = None
    version_tag: Optional[str] = None
    changed_files: List[str] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    ticket_refs: List[str] = Field(default_factory=list)
    srs_diff: str = ""
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)


def _safe_resolve_plugin(
    registry: Optional[Any],
    category: str,
    preferred_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[Any]:
    """Resolve a plugin from PluginRegistry without failing if category is absent."""
    if registry is None or not hasattr(registry, "resolve"):
        return None
    try:
        return registry.resolve(category, preferred_id=preferred_id, project_id=project_id)
    except Exception:
        return None


def _resolve_graph_store_config(
    registry: Optional[Any],
    graph_plugin: Any,
    project_id: str,
) -> Optional[Dict[str, Any]]:
    """Resolve graph backend effective config from registry without leaking brain consumer config."""
    if registry is not None and hasattr(registry, "get_effective_config"):
        meta = getattr(graph_plugin, "metadata", None)
        pid = getattr(meta, "plugin_id", None)
        if pid:
            try:
                cfg = registry.get_effective_config(pid, project_id=project_id)
                return cfg or None
            except Exception:
                return None
    return None


def _strip_flow_prefix(node_id: str) -> str:
    s = str(node_id or "").strip()
    return s[5:] if s.startswith("flow:") else s


def _flow_id_variants(node_id: str) -> Set[str]:
    s = str(node_id or "").strip()
    if not s:
        return set()
    if s.startswith("flow:"):
        bare = s[5:]
        return {s, bare} if bare else {s}
    return {s, f"flow:{s}"}


_SIGNAL_TIMEOUT_SUFFIX_RE = re.compile(
    r"\s*(?:\btimeout\s*=?\s*|\bwithin\s+)\d+\s*ms\b", re.IGNORECASE
)


def _normalize_expected_signal(sig: Any) -> str:
    raw = str(sig or "").strip()
    cleaned = _SIGNAL_TIMEOUT_SUFFIX_RE.sub("", raw).strip()
    return cleaned or raw


_MAX_REGEX_PATTERN_LEN = 256
_MAX_REGEX_LINE_LEN = 4096


def _is_safe_regex(pattern: str) -> bool:
    """Reject overly long, nested-quantifier, or multi-wildcard ReDoS-prone regex patterns."""
    if not pattern or len(pattern) > _MAX_REGEX_PATTERN_LEN:
        return False
    if re.search(r"\\[1-9]", pattern):
        return False

    stack: List[Dict[str, bool]] = []
    in_char_class = False
    unbounded_dot_runs = 0
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if in_char_class:
            if ch == "]":
                in_char_class = False
            i += 1
            continue
        if ch == "[":
            in_char_class = True
            i += 1
            continue
        if ch == "(":
            stack.append({"has_quant": False, "has_alt": False})
            i += 1
            continue
        if ch == ")":
            closed = stack.pop() if stack else None
            i += 1
            if i < n and pattern[i] in ("*", "+", "{"):
                if closed is not None and (closed["has_quant"] or closed["has_alt"]):
                    return False
                if stack:
                    stack[-1]["has_quant"] = True
            elif i < n and pattern[i] == "?":
                if (
                    closed is not None
                    and closed["has_quant"]
                    and i + 1 < n
                    and pattern[i + 1] in ("*", "+", "{")
                ):
                    return False
            if closed is not None and closed["has_quant"] and stack:
                stack[-1]["has_quant"] = True
            continue
        if ch == "|":
            if stack:
                stack[-1]["has_alt"] = True
            i += 1
            continue
        if ch in ("*", "+"):
            if i > 0 and pattern[i - 1] == "." and (i < 2 or pattern[i - 2] != "\\"):
                unbounded_dot_runs += 1
                if unbounded_dot_runs > 1:
                    return False
            for frame in stack:
                frame["has_quant"] = True
            i += 1
            continue
        if ch == "{":
            close_brace = pattern.find("}", i + 1)
            if close_brace != -1 and re.match(
                r"^\{\s*\d+\s*(?:,\s*\d*\s*)?\}$", pattern[i : close_brace + 1]
            ):
                for frame in stack:
                    frame["has_quant"] = True
                i = close_brace + 1
                continue
        i += 1
    return True


def _tokenize_text(value: Any) -> Set[str]:
    """Extract normalized lowercase identifier tokens from strings, lists, or dicts."""
    tokens: Set[str] = set()
    if value is None:
        return tokens
    if isinstance(value, dict):
        for k, v in value.items():
            tokens.update(_tokenize_text(k))
            tokens.update(_tokenize_text(v))
        return tokens
    if isinstance(value, (list, tuple, set)):
        for item in value:
            tokens.update(_tokenize_text(item))
        return tokens
    text = str(value)
    for raw in re.findall(r"[A-Za-z0-9_.\-/]+", text):
        low = raw.lower()
        tokens.add(low)
        if "/" in low:
            base = low.rsplit("/", 1)[-1]
            tokens.add(base)
            if "." in base:
                tokens.add(base.rsplit(".", 1)[0])
        elif "." in low:
            tokens.add(low.rsplit(".", 1)[0])
        for sub in re.split(r"[_\-./]+", low):
            if len(sub) >= 2:
                tokens.add(sub)
    return tokens


def _parse_int_hex(val: Any) -> Optional[int]:
    """Parse integer or hex string like '0x00008200' into int."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    s = str(val).strip()
    if not s:
        return None
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        return None


def _extract_ticket_refs(text: str, extra: Optional[Iterable[str]] = None) -> List[str]:
    """Extract deduplicated Jira/Linear/GitHub issue references in order."""
    seen: Set[str] = set()
    refs: List[str] = []
    if extra:
        for item in extra:
            val = str(item).strip()
            if val and val not in seen:
                seen.add(val)
                refs.append(val)
    for match in re.findall(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b", text or ""):
        prefix = match.split("-", 1)[0]
        if prefix in _NON_TICKET_PREFIXES:
            continue
        if match not in seen:
            seen.add(match)
            refs.append(match)
    return refs


def _normalize_artifact_item(item: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize a string path or dict into a standard artifact descriptor."""
    if isinstance(item, dict):
        name = str(item.get("name") or item.get("filename") or item.get("path") or item.get("uri") or "")
        ext = Path(name).suffix.lower() if name else str(item.get("extension") or "").lower()
        return {
            "name": name,
            "extension": ext,
            "artifact_type": item.get("artifact_type") or _infer_artifact_type(ext),
            "url": item.get("url") or item.get("uri") or "",
            "sha256": item.get("sha256") or "",
        }
    name = str(item)
    ext = Path(name).suffix.lower()
    return {
        "name": name,
        "extension": ext,
        "artifact_type": _infer_artifact_type(ext),
        "url": name if "://" in name else "",
        "sha256": "",
    }


def _infer_artifact_type(ext: str) -> str:
    ext = ext.lower()
    if ext in _FIRMWARE_ARTIFACT_EXTS:
        return "firmware_image"
    if ext in (".apk", ".aab"):
        return "android_package"
    if ext == ".ipa":
        return "ios_package"
    if ext == ".appx":
        return "desktop_package"
    return "artifact"


def _maybe_await(result: Any) -> Any:
    """Helper marker used inside async methods when calling potentially async dependencies."""
    return inspect.isawaitable(result)


# ---------------------------------------------------------------------------
# 1. ImpactTestDAGPlannerPlugin (brain.planner)
# ---------------------------------------------------------------------------


class ImpactTestDAGPlannerPlugin(BrainConsumerPlugin):
    """Maps SRS/code diffs across Graph & Flow blast radius into a topological test DAG."""

    role: str = "planner"
    metadata: PluginMetadata = PluginMetadata(
        plugin_id="brain.planner",
        name="Impact Test DAG Planner",
        version="1.0.0",
        category=PluginCategory.BRAIN_CONSUMER.value,
        priority=100,
        capabilities=[
            "blast_radius",
            "precondition_injection",
            "dag_toposort",
            "script_cache_binding",
        ],
        description=(
            "Maps srs_diff and code_diff_files across Graph/Flow blast radius, "
            "injects flash_firmware/install_app/precondition nodes, binds UDCP script cache, "
            "and emits topologically sorted execution waves."
        ),
    )

    async def execute(
        self,
        request: Dict[str, Any],
        registry: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = context or {}
        project_id = str(request.get("project_id") or ctx.get("project_id") or "default")
        branch = str(request.get("branch") or ctx.get("branch") or "main")
        base_branch = str(request.get("base_branch") or ctx.get("base_branch") or "main")
        version_tag = request.get("version_tag") or ctx.get("version_tag")
        max_depth = int(request.get("max_depth", 4))

        srs_diff = request.get("srs_diff", "")
        code_diff_files = [str(f) for f in (request.get("code_diff_files") or [])]
        raw_artifacts = request.get("artifacts") or []
        artifacts = [_normalize_artifact_item(a) for a in raw_artifacts]
        target_devices = [str(d) for d in (request.get("target_devices") or [])]
        default_platform = str(
            request.get("platform")
            or (target_devices[0] if target_devices else "")
            or "default"
        )
        build_version = str(request.get("build_version") or version_tag or "latest")

        # 1. Resolve graph & cache plugins
        graph_data = self._resolve_graph_data(
            request, ctx, registry, project_id, branch, version_tag
        )
        cache_plugin = (
            request.get("cache_plugin")
            or ctx.get("cache_plugin")
            or _safe_resolve_plugin(
                registry, PluginCategory.SCRIPT_CACHE.value, project_id=project_id
            )
        )

        nodes = list(graph_data.get("nodes") or [])
        edges = list(graph_data.get("edges") or [])
        node_by_id: Dict[str, Dict[str, Any]] = {
            str(n["id"]): n for n in nodes if "id" in n
        }

        # 2. Seed matching & BFS blast-radius traversal
        diff_tokens = _tokenize_text(code_diff_files) | _tokenize_text(srs_diff)
        diff_files_lower = {f.lower() for f in code_diff_files}
        diff_basenames_lower = {Path(f).name.lower() for f in code_diff_files}

        seed_ids: Set[str] = set()
        for nid, node in node_by_id.items():
            meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
            if version_tag and meta.get("version_tag") and meta.get("version_tag") != version_tag:
                continue
            src_file = str(node.get("source_file") or "").lower()
            if src_file and (
                src_file in diff_files_lower
                or Path(src_file).name in diff_basenames_lower
                or any(src_file.endswith(f"/{b}") or f.endswith(f"/{src_file}") for f in diff_files_lower for b in [Path(f).name])
            ):
                seed_ids.add(nid)
                continue

            node_tokens = (
                _tokenize_text(nid)
                | _tokenize_text(_strip_flow_prefix(nid))
                | _tokenize_text(node.get("label"))
                | _tokenize_text(node.get("name"))
                | _tokenize_text(meta.get("symbol"))
                | _tokenize_text(meta.get("srs_ref"))
                | _tokenize_text(meta.get("requirement_id"))
                | _tokenize_text(meta.get("linked_requirements"))
                | _tokenize_text(meta.get("screen_id"))
                | _tokenize_text(meta.get("state_id"))
                | _tokenize_text(meta.get("node_id"))
            )
            if diff_tokens & node_tokens:
                seed_ids.add(nid)

        adj: Dict[str, Set[str]] = {}
        for edge in edges:
            src = str(edge.get("source") or "")
            dst = str(edge.get("target") or "")
            if not src or not dst:
                continue
            adj.setdefault(src, set()).add(dst)
            adj.setdefault(dst, set()).add(src)
            if str(edge.get("relation") or "").lower() == "transitions_to":
                emeta = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
                edge_tokens = (
                    _tokenize_text(emeta.get("expected_signals"))
                    | _tokenize_text(emeta.get("guard_condition"))
                    | _tokenize_text(emeta.get("trigger_action"))
                )
                if diff_tokens & edge_tokens:
                    seed_ids.add(src)
                    seed_ids.add(dst)

        # Bridge flow:foo <-> foo aliases if one side is in node_by_id/adj
        for nid in list(node_by_id.keys()) + list(adj.keys()):
            for var in _flow_id_variants(nid):
                if var != nid and (var in node_by_id or var in adj):
                    adj.setdefault(nid, set()).add(var)
                    adj.setdefault(var, set()).add(nid)

        hop_distance: Dict[str, int] = {sid: 0 for sid in seed_ids}
        queue: deque[Tuple[str, int]] = deque((sid, 0) for sid in sorted(seed_ids))
        while queue:
            curr, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for nxt in sorted(adj.get(curr, ())):
                if nxt not in hop_distance:
                    hop_distance[nxt] = depth + 1
                    queue.append((nxt, depth + 1))

        impacted_ids = set(hop_distance.keys())

        # Determine if core hubs or firmware sources were touched
        all_surface_tokens = diff_tokens | _tokenize_text([a["name"] for a in artifacts])
        for nid in impacted_ids:
            node = node_by_id.get(nid, {})
            all_surface_tokens |= _tokenize_text(node.get("label")) | _tokenize_text(
                node.get("metadata")
            )
        for edge in edges:
            if str(edge.get("relation") or "").lower() == "transitions_to":
                src = str(edge.get("source") or "")
                dst = str(edge.get("target") or "")
                if src in impacted_ids or dst in impacted_ids:
                    emeta = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
                    all_surface_tokens |= _tokenize_text(emeta.get("expected_signals"))

        has_firmware_artifact = any(
            a["extension"] in _FIRMWARE_ARTIFACT_EXTS for a in artifacts
        )
        has_firmware_source = any(
            Path(f).suffix.lower() in _FIRMWARE_SOURCE_EXTS or "firmware" in f.lower()
            for f in code_diff_files
        )
        has_app_artifact = any(
            a["extension"] in _APP_ARTIFACT_EXTS for a in artifacts
        )

        core_hub_touched = (
            has_firmware_artifact
            or has_firmware_source
            or any(kw in all_surface_tokens for kw in _CORE_HUB_KEYWORDS)
            or any(
                bool((node_by_id.get(nid, {}).get("metadata") or {}).get("is_core_hub"))
                for nid in impacted_ids
            )
        )

        # 3. Collect candidate CanonicalTestIntents and prioritize
        candidate_intents = self._collect_candidate_intents(request, ctx, nodes)
        selected_intents: List[Tuple[int, int, float, Dict[str, Any]]] = []

        for intent in candidate_intents:
            intent_id = str(intent["intent_id"])
            priority = str(intent.get("priority") or "P1").upper()
            if priority not in _PRIORITY_RANK:
                priority = "P1"

            # Direct match or blast-radius match
            intent_tokens = (
                _tokenize_text(intent_id)
                | _tokenize_text(intent.get("title"))
                | _tokenize_text(intent.get("description"))
                | _tokenize_text(intent.get("linked_requirements"))
                | _tokenize_text(intent.get("tags"))
                | _tokenize_text(intent.get("source_file"))
            )
            linked_reqs = [str(r) for r in (intent.get("linked_requirements") or [])]
            related_nodes = [str(r) for r in (intent.get("related_nodes") or [])]

            candidate_node_ids: Set[str] = set()
            for raw_id in [intent_id] + linked_reqs + related_nodes:
                candidate_node_ids.update(_flow_id_variants(raw_id))
            matched_hops = [hop_distance[nid] for nid in candidate_node_ids if nid in hop_distance]
            direct_token_hit = bool(diff_tokens & intent_tokens)
            in_blast_radius = bool(matched_hops) or direct_token_hit

            best_hop = min(matched_hops) if matched_hops else (0 if direct_token_hit else max_depth + 1)

            should_select = False
            if in_blast_radius:
                should_select = True
            elif priority == "P0" and (core_hub_touched or not diff_tokens):
                should_select = True
            elif not diff_tokens and not impacted_ids and not candidate_intents:
                should_select = True

            if should_select:
                score = float(_PRIORITY_SCORE[priority]) + max(0, (max_depth + 1 - best_hop)) * 10.0
                if direct_token_hit:
                    score += 15.0
                intent_copy = dict(intent)
                intent_copy["impact_score"] = score
                intent_copy["hop_distance"] = best_hop
                selected_intents.append((_PRIORITY_RANK[priority], best_hop, -score, intent_copy))

        selected_intents.sort(key=lambda item: (item[0], item[1], item[2], str(item[3]["intent_id"])))
        final_intents = [item[3] for item in selected_intents]

        # 4. Build DAG nodes & automatic precondition injection
        dag_nodes: List[Dict[str, Any]] = []
        dag_edges: List[Dict[str, str]] = []
        precondition_nodes_by_id: Dict[str, Dict[str, Any]] = {}
        edge_set: Set[Tuple[str, str, str]] = set()

        def _add_precond_node(
            pid: str, action: str, label: str, metadata: Optional[Dict[str, Any]] = None
        ) -> None:
            if pid not in precondition_nodes_by_id:
                precondition_nodes_by_id[pid] = {
                    "id": pid,
                    "node_type": "precondition",
                    "action": action,
                    "label": label,
                    "metadata": metadata or {},
                }

        def _add_edge(src: str, dst: str, relation: str = "precedes") -> None:
            key = (src, dst, relation)
            if src != dst and key not in edge_set:
                edge_set.add(key)
                dag_edges.append({"source": src, "target": dst, "relation": relation})

        if has_firmware_artifact or has_firmware_source:
            fw_artifacts = [
                a["name"] for a in artifacts if a["extension"] in _FIRMWARE_ARTIFACT_EXTS
            ]
            _add_precond_node(
                "precond_flash_firmware",
                "flash_firmware",
                "Flash Firmware Image",
                {"artifacts": fw_artifacts, "triggered_by_source": has_firmware_source},
            )

        if has_app_artifact:
            app_artifacts = [
                a["name"] for a in artifacts if a["extension"] in _APP_ARTIFACT_EXTS
            ]
            _add_precond_node(
                "precond_install_app",
                "install_app",
                "Install Application Package",
                {"artifacts": app_artifacts},
            )

        # 5. Bind ScriptCachePlugin + per-intent preconditions
        cache_hits = 0
        intent_dag_nodes: List[Dict[str, Any]] = []
        for intent in final_intents:
            iid = str(intent["intent_id"])
            platforms = [str(p).lower() for p in (intent.get("target_platforms") or [])]
            tags = [str(t).lower() for t in (intent.get("tags") or [])]
            intent_platform = platforms[0] if platforms else default_platform
            screen_hash = intent.get("screen_hash")

            cache_binding = await self._lookup_cache_binding(
                cache_plugin=cache_plugin,
                project_id=project_id,
                intent_id=iid,
                platform=intent_platform,
                build_version=build_version,
                screen_hash=screen_hash,
            )
            if cache_binding["cache_hit"]:
                cache_hits += 1

            intent_node = {
                "id": iid,
                "node_type": "test_intent",
                "intent_id": iid,
                "title": intent.get("title", iid),
                "priority": intent.get("priority", "P1"),
                "target_platforms": intent.get("target_platforms") or [],
                "impact_score": intent.get("impact_score", 0.0),
                "hop_distance": intent.get("hop_distance", 0),
                "cache_binding": cache_binding,
                "steps": intent.get("steps") or [],
            }
            intent_dag_nodes.append(intent_node)

            # Link flash_firmware to firmware/hardware/device/unrestricted intents
            if "precond_flash_firmware" in precondition_nodes_by_id:
                is_pure_web_backend = bool(platforms) and all(
                    p in ("backend", "api", "web") for p in platforms
                )
                if not is_pure_web_backend or "firmware" in tags or "hardware" in tags:
                    _add_edge("precond_flash_firmware", iid, "requires_precondition")

            # Link install_app to mobile/app/unrestricted intents
            if "precond_install_app" in precondition_nodes_by_id:
                is_pure_firmware = bool(platforms) and all(
                    p in ("firmware", "mcu", "rtos", "baremetal") for p in platforms
                )
                if not is_pure_firmware or any(p in ("android", "ios", "app", "mobile") for p in platforms + tags):
                    _add_edge("precond_install_app", iid, "requires_precondition")

            # Extract intent-declared preconditions (power_cycle, seed_db, pair_ble, etc.)
            for pre in intent.get("preconditions") or []:
                if isinstance(pre, dict):
                    action = str(pre.get("action") or pre.get("type") or pre.get("name") or "setup")
                    pre_meta = dict(pre)
                else:
                    action = str(pre)
                    pre_meta = {"action": action}
                slug = re.sub(r"[^a-z0-9_]+", "_", action.lower()).strip("_") or "setup"
                pid = f"precond_{slug}"
                _add_precond_node(pid, action, action.replace("_", " ").title(), pre_meta)
                _add_edge(pid, iid, "requires_precondition")

        # Optional explicit intent-to-intent dependencies if supplied
        selected_id_set = {n["id"] for n in intent_dag_nodes}
        for intent in final_intents:
            iid = str(intent["intent_id"])
            for dep in intent.get("depends_on") or []:
                dep_id = str(dep)
                if dep_id in selected_id_set:
                    _add_edge(dep_id, iid, "depends_on")

        precondition_nodes = [
            precondition_nodes_by_id[k] for k in sorted(precondition_nodes_by_id.keys())
        ]
        dag_nodes = precondition_nodes + intent_dag_nodes

        # 6. Kahn's topological sort & wave emission
        acyclic, execution_waves, topo_order = self._kahn_topological_waves(
            dag_nodes, dag_edges
        )

        total_intents = len(intent_dag_nodes)
        cache_hit_ratio = round(cache_hits / total_intents, 4) if total_intents > 0 else 0.0

        return {
            "status": "ok" if acyclic else "error",
            "project_id": project_id,
            "branch": branch,
            "base_branch": base_branch,
            "version_tag": version_tag,
            "acyclic": acyclic,
            "blast_radius": {
                "seed_nodes": sorted(seed_ids),
                "impacted_nodes": sorted(impacted_ids),
                "max_depth": max_depth,
                "core_hub_touched": core_hub_touched,
            },
            "dag": {
                "nodes": dag_nodes,
                "edges": dag_edges,
                "execution_waves": execution_waves,
                "topological_order": topo_order,
            },
            "stats": {
                "total_intents": total_intents,
                "precondition_nodes": len(precondition_nodes),
                "total_nodes": len(dag_nodes),
                "total_edges": len(dag_edges),
                "cache_hits": cache_hits,
                "cache_hit_ratio": cache_hit_ratio,
            },
        }

    def _resolve_graph_data(
        self,
        request: Dict[str, Any],
        ctx: Dict[str, Any],
        registry: Optional[Any],
        project_id: str,
        branch: str,
        version_tag: Optional[str],
    ) -> Dict[str, Any]:
        direct = request.get("graph_data") or ctx.get("graph_data")
        if isinstance(direct, dict):
            return direct
        graph_plugin = (
            request.get("graph_plugin")
            or ctx.get("graph_plugin")
            or _safe_resolve_plugin(
                registry, PluginCategory.GRAPH_BACKEND.value, project_id=project_id
            )
        )
        if graph_plugin is None:
            return {"nodes": [], "edges": []}
        store: Optional[Any] = None
        try:
            store_cfg = _resolve_graph_store_config(registry, graph_plugin, project_id)
            store = graph_plugin.create_store(store_cfg)
            sub = store.get_resolved_subgraph(project_id, branch, limit=100000)
            nodes = list(sub.get("nodes") or [])
            edges_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
            for edge in sub.get("edges") or []:
                ekey = (
                    str(edge.get("source") or ""),
                    str(edge.get("target") or ""),
                    str(edge.get("relation") or ""),
                )
                edges_by_key[ekey] = edge
            if hasattr(graph_plugin, "query_flow_graph"):
                flow = graph_plugin.query_flow_graph(
                    project_id, branch=branch, version_tag=version_tag, store=store
                )
                seen_n = {str(n.get("id")) for n in nodes}
                for fn in flow.get("nodes") or []:
                    if str(fn.get("id")) not in seen_n:
                        nodes.append(fn)
                        seen_n.add(str(fn.get("id")))
                for fe in flow.get("edges") or []:
                    ekey = (
                        str(fe.get("source") or ""),
                        str(fe.get("target") or ""),
                        str(fe.get("relation") or ""),
                    )
                    existing_edge = edges_by_key.get(ekey)
                    if existing_edge is None or (
                        not existing_edge.get("metadata") and fe.get("metadata")
                    ):
                        edges_by_key[ekey] = fe
            return {"nodes": nodes, "edges": list(edges_by_key.values())}
        except Exception:
            return {"nodes": [], "edges": []}
        finally:
            if store is not None and hasattr(store, "close"):
                try:
                    store.close()
                except Exception:
                    pass

    def _collect_candidate_intents(
        self,
        request: Dict[str, Any],
        ctx: Dict[str, Any],
        graph_nodes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        intents_by_id: Dict[str, Dict[str, Any]] = {}

        def _ingest_intent(raw: Any) -> None:
            if isinstance(raw, CanonicalTestIntent):
                d = raw.to_dict()
            elif hasattr(raw, "to_dict"):
                d = raw.to_dict()
            elif isinstance(raw, dict):
                d = dict(raw)
            else:
                return
            iid = str(d.get("intent_id") or d.get("id") or "")
            if not iid:
                return
            d["intent_id"] = iid
            intents_by_id[iid] = d

        for raw in request.get("intents") or ctx.get("intents") or []:
            _ingest_intent(raw)

        for node in graph_nodes:
            ftype = str(node.get("file_type") or node.get("kind") or "").lower()
            if ftype in ("test_intent", "intent", "testcase"):
                meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
                merged = dict(meta)
                merged.setdefault("intent_id", meta.get("intent_id") or node.get("id"))
                merged.setdefault("title", node.get("label") or node.get("name") or merged["intent_id"])
                merged.setdefault("source_file", node.get("source_file"))
                _ingest_intent(merged)

        return list(intents_by_id.values())

    async def _lookup_cache_binding(
        self,
        cache_plugin: Optional[Any],
        project_id: str,
        intent_id: str,
        platform: str,
        build_version: str,
        screen_hash: Optional[str],
    ) -> Dict[str, Any]:
        default_binding = {
            "cache_hit": False,
            "cache_id": None,
            "udcp_commands": [],
            "platform": platform,
            "build_version": build_version,
        }
        if cache_plugin is None or not hasattr(cache_plugin, "lookup_script"):
            return default_binding
        try:
            res = cache_plugin.lookup_script(
                project_id=project_id,
                intent_id=intent_id,
                platform=platform,
                build_version=build_version,
                screen_hash=screen_hash,
            )
            if inspect.isawaitable(res):
                res = await res
            if res and not res.get("invalidated", False):
                return {
                    "cache_hit": True,
                    "cache_id": res.get("cache_id"),
                    "udcp_commands": list(res.get("udcp_commands") or []),
                    "platform": res.get("platform") or platform,
                    "build_version": res.get("build_version") or build_version,
                }
        except Exception:
            pass
        return default_binding

    @staticmethod
    def _kahn_topological_waves(
        nodes: List[Dict[str, Any]], edges: List[Dict[str, str]]
    ) -> Tuple[bool, List[List[str]], List[str]]:
        node_ids = [str(n["id"]) for n in nodes]
        in_degree: Dict[str, int] = {nid: 0 for nid in node_ids}
        out_adj: Dict[str, List[str]] = {nid: [] for nid in node_ids}

        for edge in edges:
            src = str(edge["source"])
            dst = str(edge["target"])
            if src in in_degree and dst in in_degree:
                out_adj[src].append(dst)
                in_degree[dst] += 1

        current_wave = sorted(nid for nid, deg in in_degree.items() if deg == 0)
        waves: List[List[str]] = []
        topo_order: List[str] = []

        while current_wave:
            waves.append(current_wave)
            topo_order.extend(current_wave)
            next_wave_candidates: Set[str] = set()
            for nid in current_wave:
                for nxt in out_adj[nid]:
                    in_degree[nxt] -= 1
                    if in_degree[nxt] == 0:
                        next_wave_candidates.add(nxt)
            current_wave = sorted(next_wave_candidates)

        acyclic = len(topo_order) == len(node_ids)
        return acyclic, waves, topo_order


# ---------------------------------------------------------------------------
# 2. OracleKnowledgeJudgePlugin (brain.oracle)
# ---------------------------------------------------------------------------


class OracleKnowledgeJudgePlugin(BrainConsumerPlugin):
    """Evaluates telemetry summaries against SRS criteria, flow transitions, and error rules."""

    role: str = "oracle"
    metadata: PluginMetadata = PluginMetadata(
        plugin_id="brain.oracle",
        name="Oracle Knowledge Judge",
        version="1.0.0",
        category=PluginCategory.BRAIN_CONSUMER.value,
        priority=100,
        capabilities=[
            "srs_criteria_eval",
            "transition_verification",
            "error_rule_matching",
            "hardfault_detection",
            "power_budget_check",
        ],
        description=(
            "Evaluates telemetry_summary against SRS acceptance criteria, "
            "TRANSITION_EDGE expected signals, UART/AT ErrorCodeRules, ARM HardFault/CFSR, "
            "power budget mA limits, and HTTP 5xx/4xx responses."
        ),
    )

    _BUILTIN_UART_ERROR_PATTERNS: Tuple[Tuple[str, str, str, str], ...] = (
        ("HARDFAULT", "hardfault", "critical", r"\b(?:HardFault|MemManage|BusFault|UsageFault)\b"),
        ("WDT_RESET", "uart", "critical", r"\b(?:UART_WDT_RESET|WDT_RESET|WATCHDOG_RESET)\b"),
        ("AT_ERROR", "at", "error", r"(?:\bAT\+ERR=\S+|\bAT_ERR_[A-Z0-9_]+)"),
        ("UART_ERROR", "uart", "error", r"\b(?:UART_ERR_[A-Z0-9_]+|E_BLE_[A-Z0-9_]+|ERR_[A-Z0-9_]{3,})\b"),
        ("KERNEL_PANIC", "kernel", "critical", r"\b(?:KERNEL_PANIC|PANIC!|FATAL_ERROR)\b"),
    )

    async def execute(
        self,
        request: Dict[str, Any],
        registry: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = context or {}
        project_id = str(request.get("project_id") or ctx.get("project_id") or "default")
        branch = str(request.get("branch") or ctx.get("branch") or "main")
        version_tag = request.get("version_tag") or ctx.get("version_tag")
        test_intent_id = request.get("test_intent_id")
        screen_id = request.get("screen_id")
        symbols = [str(s) for s in (request.get("symbols") or [])]

        telemetry = dict(request.get("telemetry_summary") or {})

        # Retrieve rules, transitions, SRS criteria, and power budget
        knowledge = await self._gather_knowledge(
            request, ctx, registry, project_id, branch, version_tag, test_intent_id, screen_id, symbols
        )
        rules = knowledge["error_rules"]
        expected_transitions = knowledge["expected_transitions"]
        srs_criteria = knowledge["srs_criteria"]
        budget_ma = knowledge["power_budget_ma"]

        uart_lines = [str(x) for x in (telemetry.get("uart_lines") or [])]
        if telemetry.get("uart_log"):
            uart_lines.extend(str(telemetry["uart_log"]).splitlines())
        log_lines = [str(x) for x in (telemetry.get("logs") or [])]
        all_lines = uart_lines + log_lines
        all_log_text = "\n".join(all_lines)

        observed_signals = {str(s) for s in (telemetry.get("observed_signals") or [])}
        observed_state = telemetry.get("observed_state")
        observed_states_raw = {str(s) for s in (telemetry.get("observed_states") or [])}
        if observed_state:
            observed_states_raw.add(str(observed_state))
        observed_states: Set[str] = set()
        for st in observed_states_raw:
            observed_states.update(_flow_id_variants(st))

        http_responses = list(telemetry.get("http_responses") or [])

        violated_rules: List[Dict[str, Any]] = []
        matched_criteria: List[Dict[str, Any]] = []
        unmet_criteria: List[Dict[str, Any]] = []
        verified_evidence_count = 0

        # 1. ARM HardFault / CFSR / Crash detection
        if telemetry.get("crash_detected") is True:
            violated_rules.append(
                {
                    "rule_id": "builtin_crash_detected",
                    "code": "CRASH_DETECTED",
                    "protocol": "hardfault",
                    "severity": "critical",
                    "detail": "Telemetry reported crash_detected=True",
                }
            )

        cfsr_val = _parse_int_hex(telemetry.get("hardfault_cfsr"))
        if cfsr_val is not None and cfsr_val != 0:
            violated_rules.append(
                {
                    "rule_id": "builtin_arm_cfsr",
                    "code": "ARM_HARDFAULT_CFSR",
                    "protocol": "hardfault",
                    "severity": "critical",
                    "detail": f"Non-zero ARM CFSR register: {telemetry.get('hardfault_cfsr')} ({hex(cfsr_val)})",
                }
            )

        hfsr_val = _parse_int_hex(telemetry.get("hardfault_hfsr"))
        if hfsr_val is not None and hfsr_val != 0:
            violated_rules.append(
                {
                    "rule_id": "builtin_arm_hfsr",
                    "code": "ARM_HARDFAULT_HFSR",
                    "protocol": "hardfault",
                    "severity": "critical",
                    "detail": f"Non-zero ARM HFSR register: {telemetry.get('hardfault_hfsr')} ({hex(hfsr_val)})",
                }
            )

        # Check CFSR=0x... in UART text
        for m in re.finditer(r"\bCFSR\s*=\s*(0x[0-9a-fA-F]+|\d+)", all_log_text, re.IGNORECASE):
            parsed = _parse_int_hex(m.group(1))
            if parsed and parsed != 0:
                violated_rules.append(
                    {
                        "rule_id": "builtin_log_cfsr",
                        "code": "ARM_HARDFAULT_CFSR",
                        "protocol": "hardfault",
                        "severity": "critical",
                        "detail": f"Detected non-zero CFSR in log: {m.group(0)}",
                    }
                )

        # 2. Match ErrorCodeRules & builtin UART/AT/HardFault signatures
        seen_rule_hits: Set[Tuple[str, str]] = set()
        for rule in rules:
            code = str(rule.get("code") or "")
            pattern = str(rule.get("pattern") or code)
            protocol = str(rule.get("protocol") or "uart").lower()
            severity = str(rule.get("severity") or "error").lower()
            rule_id = str(rule.get("rule_id") or code)

            haystack = all_log_text
            if protocol == "http":
                haystack = json.dumps(http_responses) + "\n" + all_log_text

            matched_line = self._match_pattern(pattern, haystack)
            if not matched_line and code and code != pattern:
                matched_line = self._match_pattern(re.escape(code), haystack)

            if matched_line is not None:
                key = (rule_id, code)
                if key not in seen_rule_hits:
                    seen_rule_hits.add(key)
                    violated_rules.append(
                        {
                            "rule_id": rule_id,
                            "code": code,
                            "protocol": protocol,
                            "severity": severity,
                            "detail": matched_line,
                            "remediation_hint": rule.get("remediation_hint") or "",
                        }
                    )

        for b_code, b_proto, b_sev, b_pat in self._BUILTIN_UART_ERROR_PATTERNS:
            matched_line = self._match_pattern(b_pat, all_log_text)
            if matched_line is not None:
                key = (f"builtin_{b_code}", b_code)
                if key not in seen_rule_hits and not any(
                    v.get("code") == b_code or b_code in str(v.get("detail", ""))
                    for v in violated_rules
                ):
                    seen_rule_hits.add(key)
                    violated_rules.append(
                        {
                            "rule_id": f"builtin_{b_code.lower()}",
                            "code": b_code,
                            "protocol": b_proto,
                            "severity": b_sev,
                            "detail": matched_line,
                        }
                    )

        if uart_lines:
            verified_evidence_count += 1

        # 3. Power budget mA check
        peak_ma = telemetry.get("power_ma_peak")
        if peak_ma is None and telemetry.get("power_trace_ma"):
            trace = [float(x) for x in telemetry["power_trace_ma"]]
            peak_ma = max(trace) if trace else None
        if peak_ma is not None and budget_ma is not None:
            peak_f = float(peak_ma)
            budget_f = float(budget_ma)
            if peak_f > budget_f:
                violated_rules.append(
                    {
                        "rule_id": "power_budget_ma",
                        "code": "POWER_BUDGET_EXCEEDED",
                        "protocol": "power",
                        "severity": "error",
                        "detail": f"Peak current {peak_f:.2f}mA exceeded budget {budget_f:.2f}mA",
                    }
                )
            else:
                matched_criteria.append(
                    {
                        "criterion_id": "power_budget_ma",
                        "description": f"Peak current {peak_f:.2f}mA <= {budget_f:.2f}mA",
                    }
                )
                verified_evidence_count += 1

        # 4. HTTP 5xx / unexpected 4xx check
        for idx, resp in enumerate(http_responses):
            if isinstance(resp, int):
                status = resp
                resp_obj: Dict[str, Any] = {"status": status}
            elif isinstance(resp, dict):
                status = int(resp.get("status") or resp.get("status_code") or 0)
                resp_obj = resp
            else:
                continue

            expected_statuses = resp_obj.get("expected_statuses")
            if expected_statuses is None and resp_obj.get("expected_status") is not None:
                expected_statuses = [int(resp_obj["expected_status"])]

            if status >= 500:
                violated_rules.append(
                    {
                        "rule_id": f"http_5xx_{idx}",
                        "code": f"HTTP_{status}",
                        "protocol": "http",
                        "severity": "critical",
                        "detail": f"HTTP {status} on {resp_obj.get('path', 'endpoint')}",
                    }
                )
            elif 400 <= status < 500:
                if expected_statuses is not None and status in [int(s) for s in expected_statuses]:
                    matched_criteria.append(
                        {
                            "criterion_id": f"http_expected_{status}_{idx}",
                            "description": f"Expected HTTP {status} received",
                        }
                    )
                    verified_evidence_count += 1
                elif not resp_obj.get("allow_4xx", False):
                    violated_rules.append(
                        {
                            "rule_id": f"http_4xx_{idx}",
                            "code": f"HTTP_{status}",
                            "protocol": "http",
                            "severity": "error",
                            "detail": f"Unexpected HTTP {status} on {resp_obj.get('path', 'endpoint')}",
                        }
                    )
            elif 200 <= status < 400:
                verified_evidence_count += 1

        # 5. StateFlow TRANSITION_EDGE verification
        transitions_summary: List[Dict[str, Any]] = []
        for tr in expected_transitions:
            target_state = str(tr.get("target_state") or "")
            expected_sigs = [
                _normalize_expected_signal(s)
                for s in (tr.get("expected_signals") or [])
                if _normalize_expected_signal(s)
            ]
            tr_record = {
                "source_state": tr.get("source_state"),
                "target_state": target_state,
                "expected_signals": expected_sigs,
                "status": "unverified",
            }

            if observed_states or observed_signals or uart_lines:
                state_ok = (not target_state) or bool(_flow_id_variants(target_state) & observed_states)
                missing_sigs = [
                    sig
                    for sig in expected_sigs
                    if sig not in observed_signals and sig not in all_log_text
                ]
                if not observed_states and target_state and not expected_sigs:
                    tr_record["status"] = "unverified"
                elif state_ok and not missing_sigs:
                    tr_record["status"] = "matched"
                    matched_criteria.append(
                        {
                            "criterion_id": f"transition_{tr.get('source_state', '')}_{target_state}",
                            "description": f"Transition to {target_state} with signals {expected_sigs} verified",
                        }
                    )
                    verified_evidence_count += 1
                else:
                    tr_record["status"] = "failed"
                    reason_parts = []
                    if not state_ok:
                        reason_parts.append(
                            f"expected state '{target_state}', observed {sorted(observed_states_raw)}"
                        )
                    if missing_sigs:
                        reason_parts.append(f"missing signals {missing_sigs}")
                    unmet_criteria.append(
                        {
                            "criterion_id": f"transition_{tr.get('source_state', '')}_{target_state}",
                            "description": "; ".join(reason_parts),
                        }
                    )
            transitions_summary.append(tr_record)

        # 6. SRS acceptance criteria evaluation
        for idx, crit in enumerate(srs_criteria):
            if isinstance(crit, str):
                crit_obj: Dict[str, Any] = {"criterion_id": f"srs_{idx}", "text": crit}
            elif isinstance(crit, dict):
                crit_obj = dict(crit)
            else:
                continue

            cid = str(crit_obj.get("criterion_id") or crit_obj.get("id") or f"srs_{idx}")
            req_signal = crit_obj.get("expected_signal") or crit_obj.get("signal")
            req_state = crit_obj.get("expected_state")
            req_pattern = crit_obj.get("pattern")
            text = str(crit_obj.get("text") or crit_obj.get("description") or cid)

            if req_signal:
                if str(req_signal) in observed_signals or str(req_signal) in all_log_text:
                    matched_criteria.append({"criterion_id": cid, "description": text})
                    verified_evidence_count += 1
                elif telemetry:
                    unmet_criteria.append(
                        {
                            "criterion_id": cid,
                            "description": f"Missing expected signal '{req_signal}' ({text})",
                        }
                    )
            elif req_state:
                if _flow_id_variants(str(req_state)) & observed_states:
                    matched_criteria.append({"criterion_id": cid, "description": text})
                    verified_evidence_count += 1
                elif observed_states_raw:
                    unmet_criteria.append(
                        {
                            "criterion_id": cid,
                            "description": f"Expected state '{req_state}', got {sorted(observed_states_raw)}",
                        }
                    )
            elif req_pattern:
                if self._match_pattern(str(req_pattern), all_log_text) is not None:
                    matched_criteria.append({"criterion_id": cid, "description": text})
                    verified_evidence_count += 1
                elif all_lines:
                    unmet_criteria.append(
                        {
                            "criterion_id": cid,
                            "description": f"Pattern '{req_pattern}' not found in logs",
                        }
                    )
            else:
                # Informational SRS criterion with no structured predicate
                if verified_evidence_count > 0 and not violated_rules:
                    matched_criteria.append({"criterion_id": cid, "description": text})

        # 7. Final verdict calculation
        if violated_rules or unmet_criteria:
            verdict = "FAIL"
            confidence = min(0.99, 0.85 + 0.03 * (len(violated_rules) + len(unmet_criteria)))
            top_reasons = [
                v.get("detail") or v.get("code") for v in violated_rules[:3]
            ] + [u.get("description") for u in unmet_criteria[:2]]
            explanation = "FAIL: " + "; ".join(str(r) for r in top_reasons if r)
        elif verified_evidence_count > 0 or (
            telemetry
            and telemetry.get("crash_detected") is False
            and (uart_lines or http_responses or observed_states or peak_ma is not None)
        ):
            verdict = "PASS"
            confidence = min(0.98, 0.86 + 0.02 * len(matched_criteria))
            explanation = (
                f"PASS: 0 rule violations; {len(matched_criteria)} criteria/transitions verified."
            )
        else:
            verdict = "INCONCLUSIVE"
            confidence = 0.4
            explanation = (
                "INCONCLUSIVE: Insufficient telemetry signals or unverifiable state transitions."
            )

        return {
            "status": "ok",
            "project_id": project_id,
            "branch": branch,
            "version_tag": version_tag,
            "test_intent_id": test_intent_id,
            "verdict": verdict,
            "violated_rules": violated_rules,
            "matched_criteria": matched_criteria,
            "unmet_criteria": unmet_criteria,
            "expected_transitions": transitions_summary,
            "confidence": round(confidence, 4),
            "explanation": explanation,
        }

    async def _gather_knowledge(
        self,
        request: Dict[str, Any],
        ctx: Dict[str, Any],
        registry: Optional[Any],
        project_id: str,
        branch: str,
        version_tag: Optional[str],
        test_intent_id: Optional[str],
        screen_id: Optional[str],
        symbols: List[str],
    ) -> Dict[str, Any]:
        error_rules: List[Dict[str, Any]] = []
        for r in request.get("error_rules") or ctx.get("error_rules") or []:
            if isinstance(r, ErrorCodeRule) or hasattr(r, "to_dict"):
                error_rules.append(r.to_dict())
            elif isinstance(r, dict):
                error_rules.append(dict(r))

        expected_transitions: List[Dict[str, Any]] = []
        for t in request.get("expected_transitions") or ctx.get("expected_transitions") or []:
            if isinstance(t, StateFlowTransition) or hasattr(t, "to_dict"):
                expected_transitions.append(t.to_dict())
            elif isinstance(t, dict):
                expected_transitions.append(dict(t))

        srs_criteria: List[Any] = list(
            request.get("srs_criteria")
            or request.get("acceptance_criteria")
            or ctx.get("srs_criteria")
            or []
        )

        telemetry = request.get("telemetry_summary") or {}
        power_budget_ma = (
            telemetry.get("max_current_ma")
            or request.get("power_budget_ma")
            or ctx.get("power_budget_ma")
        )

        # Pull from GraphBackendPlugin if registered
        graph_plugin = (
            request.get("graph_plugin")
            or ctx.get("graph_plugin")
            or _safe_resolve_plugin(
                registry, PluginCategory.GRAPH_BACKEND.value, project_id=project_id
            )
        )
        if graph_plugin is not None:
            store: Optional[Any] = None
            try:
                store_cfg = _resolve_graph_store_config(registry, graph_plugin, project_id)
                store = graph_plugin.create_store(store_cfg)
                sub = store.get_resolved_subgraph(project_id, branch, limit=100000)
                flow_node_ids: Set[str] = set()
                for node in sub.get("nodes") or []:
                    ftype = str(node.get("file_type") or "").lower()
                    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
                    if version_tag and meta.get("version_tag") and meta.get("version_tag") != version_tag:
                        continue
                    if ftype in ("state", "screen") and node.get("id") is not None:
                        flow_node_ids.add(str(node["id"]))
                    if ftype in ("hardware_rule", "error_rule"):
                        rule_dict = dict(meta)
                        rule_dict.setdefault("rule_id", node.get("id"))
                        rule_dict.setdefault("code", meta.get("code") or node.get("label"))
                        rule_dict.setdefault("pattern", meta.get("pattern") or rule_dict["code"])
                        error_rules.append(rule_dict)
                        if power_budget_ma is None and meta.get("max_current_ma") is not None:
                            power_budget_ma = meta.get("max_current_ma")
                    elif ftype in ("srs", "requirement", "acceptance_criteria"):
                        srs_criteria.append(
                            {
                                "criterion_id": node.get("id"),
                                "text": node.get("label") or meta.get("description") or node.get("id"),
                                **meta,
                            }
                        )

                flow_edges_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
                for edge in sub.get("edges") or []:
                    rel = str(edge.get("relation") or "").lower()
                    src_id = str(edge.get("source") or "")
                    tgt_id = str(edge.get("target") or "")
                    if rel == "transitions_to" or (
                        src_id in flow_node_ids and tgt_id in flow_node_ids
                    ):
                        flow_edges_by_key[(src_id, tgt_id, rel)] = edge

                if hasattr(graph_plugin, "query_flow_graph"):
                    flow = graph_plugin.query_flow_graph(
                        project_id, branch=branch, version_tag=version_tag, store=store
                    )
                    for edge in flow.get("edges") or []:
                        ekey = (
                            str(edge.get("source") or ""),
                            str(edge.get("target") or ""),
                            str(edge.get("relation") or "").lower(),
                        )
                        existing_edge = flow_edges_by_key.get(ekey)
                        if existing_edge is None or (
                            not existing_edge.get("metadata") and edge.get("metadata")
                        ):
                            flow_edges_by_key[ekey] = edge

                screen_variants = _flow_id_variants(str(screen_id)) if screen_id else set()
                for edge in flow_edges_by_key.values():
                    meta = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
                    if version_tag and meta.get("version_tag") and meta.get("version_tag") != version_tag:
                        continue
                    src_raw = str(edge.get("source") or "")
                    tgt_raw = str(edge.get("target") or "")
                    meta_src = str(meta.get("source_state") or "")
                    meta_tgt = str(meta.get("target_state") or "")
                    if screen_variants:
                        edge_variants = (
                            _flow_id_variants(src_raw)
                            | _flow_id_variants(tgt_raw)
                            | _flow_id_variants(meta_src)
                            | _flow_id_variants(meta_tgt)
                        )
                        if not (screen_variants & edge_variants):
                            continue
                    tr_entry = dict(meta)
                    tr_entry["source_state"] = meta_src or _strip_flow_prefix(src_raw)
                    tr_entry["target_state"] = meta_tgt or _strip_flow_prefix(tgt_raw)
                    tr_entry["expected_signals"] = [
                        _normalize_expected_signal(s)
                        for s in (meta.get("expected_signals") or [])
                        if _normalize_expected_signal(s)
                    ]
                    expected_transitions.append(tr_entry)
            except Exception:
                pass
            finally:
                if store is not None and hasattr(store, "close"):
                    try:
                        store.close()
                    except Exception:
                        pass

        # Pull SRS chunks from VectorBackendPlugin if needed
        if not srs_criteria:
            vector_plugin = (
                request.get("vector_plugin")
                or ctx.get("vector_plugin")
                or _safe_resolve_plugin(
                    registry, PluginCategory.VECTOR_BACKEND.value, project_id=project_id
                )
            )
            if vector_plugin is not None and hasattr(vector_plugin, "search"):
                try:
                    query_str = " ".join(
                        p for p in [test_intent_id or "", screen_id or ""] + symbols if p
                    ).strip() or "acceptance criteria"
                    hits = await vector_plugin.search(
                        query=query_str,
                        top_k=5,
                        filters={"project_id": project_id},
                    )
                    for h in hits or []:
                        srs_criteria.append(
                            {
                                "criterion_id": str(h.get("id") or "vec_srs"),
                                "text": str(h.get("content") or h.get("text") or ""),
                            }
                        )
                except Exception:
                    pass

        return {
            "error_rules": error_rules,
            "expected_transitions": expected_transitions,
            "srs_criteria": srs_criteria,
            "power_budget_ma": power_budget_ma,
        }

    @staticmethod
    def _substring_match(pattern: str, text: str) -> Optional[str]:
        idx = text.lower().find(pattern.lower())
        if idx == -1:
            return None
        start = text.rfind("\n", 0, idx) + 1
        end = text.find("\n", idx + len(pattern))
        if end == -1:
            end = len(text)
        return text[start:end].strip() or pattern

    @classmethod
    def _match_pattern(cls, pattern: str, text: str) -> Optional[str]:
        if not pattern or not text:
            return None
        if len(pattern) > _MAX_REGEX_PATTERN_LEN or not _is_safe_regex(pattern):
            return cls._substring_match(pattern, text)
        try:
            if "\\n" in pattern or "\n" in pattern:
                compiled_ml = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
                if len(text) <= _MAX_REGEX_LINE_LEN * 4:
                    m = compiled_ml.search(text)
                    if m:
                        start = text.rfind("\n", 0, m.start()) + 1
                        end = text.find("\n", m.end())
                        if end == -1:
                            end = len(text)
                        return text[start:end].strip()
                return cls._substring_match(pattern, text)

            compiled = re.compile(pattern, re.IGNORECASE)
            for line in text.splitlines():
                if not line:
                    continue
                if len(line) > _MAX_REGEX_LINE_LEN:
                    m = compiled.search(line[:_MAX_REGEX_LINE_LEN])
                    if m or pattern.lower() in line.lower():
                        return line.strip()
                else:
                    if compiled.search(line):
                        return line.strip()
        except re.error:
            return cls._substring_match(pattern, text)
        return None


# ---------------------------------------------------------------------------
# 3. RCATriageMemoryPlugin (brain.rca)
# ---------------------------------------------------------------------------


class RCATriageMemoryPlugin(BrainConsumerPlugin):
    """Normalizes failures, deduplicates incidents in SQL, classifies fault domain, and triggers ScriptCache feedback."""

    role: str = "rca"
    metadata: PluginMetadata = PluginMetadata(
        plugin_id="brain.rca",
        name="RCA Triage Memory",
        version="1.0.0",
        category=PluginCategory.BRAIN_CONSUMER.value,
        priority=100,
        capabilities=[
            "signature_normalization",
            "incident_deduplication",
            "ticket_linking",
            "fault_domain_classification",
            "self_heal_feedback",
        ],
        description=(
            "Normalizes stacktraces/UART panics into deterministic SHA256 signatures, "
            "deduplicates incidents in v3_rca_incidents via ANSI ON CONFLICT SQL, "
            "classifies fault domains, and feeds FlakyScript invalidations back to ScriptCachePlugin."
        ),
    )

    _DDL_STATEMENTS: Tuple[str, ...] = (
        """
        CREATE TABLE IF NOT EXISTS v3_rca_incidents (
            incident_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            signature_hash TEXT NOT NULL,
            fault_domain TEXT NOT NULL,
            normalized_signature TEXT NOT NULL,
            ticket_refs_json TEXT DEFAULT '[]',
            occurrence_count INTEGER DEFAULT 1,
            last_run_id TEXT,
            self_heal_hint TEXT,
            metadata_json TEXT DEFAULT '{}',
            first_seen_at BIGINT NOT NULL,
            last_seen_at BIGINT NOT NULL,
            UNIQUE(project_id, signature_hash)
        )
        """.strip(),
        """
        CREATE INDEX IF NOT EXISTS idx_v3_rca_project_hash
        ON v3_rca_incidents(project_id, signature_hash)
        """.strip(),
    )

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._conn: Optional[sqlite3.Connection] = None
        self._bound_db_path: Optional[str] = None
        self._tables_ensured: bool = False

    def on_load(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().on_load(config)
        new_path = self.config.get("db_path")
        if new_path != self._bound_db_path:
            self._close_conn()
            self._tables_ensured = False

    def shutdown(self) -> None:
        self._close_conn()
        self._tables_ensured = False
        super().shutdown()

    def _close_conn(self) -> None:
        if self._conn is not None and self.config.get("conn") is None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._bound_db_path = None

    def _get_sqlite_conn(self) -> sqlite3.Connection:
        ext_conn = self.config.get("conn")
        if ext_conn is not None:
            return ext_conn
        db_path = (
            self.config.get("db_path")
            or os.environ.get("PB_RCA_DB_PATH")
            or os.environ.get("PB_DB_PATH")
            or os.environ.get("OM_DB_PATH")
            or ":memory:"
        )
        if self._conn is not None and self._bound_db_path == db_path:
            return self._conn
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._bound_db_path = str(db_path)
        self._tables_ensured = False
        return conn

    def ensure_tables(self) -> None:
        """Idempotently create v3_rca_incidents table and index."""
        external_db = self.config.get("db")
        if external_db is not None:
            for stmt in self._DDL_STATEMENTS:
                external_db.execute(stmt)
            if hasattr(external_db, "commit"):
                external_db.commit()
            self._tables_ensured = True
            return

        conn = self._get_sqlite_conn()
        for stmt in self._DDL_STATEMENTS:
            conn.execute(stmt)
        conn.commit()
        self._tables_ensured = True

    @staticmethod
    def normalize_signature(stacktrace_or_log: str) -> str:
        """Strip volatile timestamps, PIDs/TIDs, registers, and hex addresses while keeping fault symbols."""
        if not stacktrace_or_log:
            return ""

        lines: List[str] = []
        for raw_line in stacktrace_or_log.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Strip ISO-8601 / syslog / bracketed timestamps
            line = re.sub(
                r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b",
                "<TS>",
                line,
            )
            line = re.sub(r"^\[\s*\d+(?:\.\d+)?\s*\]\s*", "", line)
            line = re.sub(r"\[\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\]", "<TS>", line)

            # Strip PIDs / TIDs / thread IDs
            line = re.sub(r"\b(?:pid|tid)\s*[=:]\s*\d+\b", "pid=<PID>", line, flags=re.IGNORECASE)
            line = re.sub(r"\[(?:pid|tid):?\s*\d+\]", "[pid=<PID>]", line, flags=re.IGNORECASE)
            line = re.sub(r"\bThread-\d+\b", "Thread-<ID>", line)

            # Strip volatile CPU register dumps (R0=0x..., PC=0x..., LR=0x..., SP=0x..., PSR=0x...)
            # while preserving fault status registers like CFSR=0x..., HFSR=0x..., ERR=0x...
            line = re.sub(
                r"\b(?:R\d{1,2}|PC|LR|SP|PSP|MSP|XPSR|PSR|RAX|RBX|RCX|RDX|RSI|RDI|RBP|RSP|RIP)\s*[=:]\s*(?:0x[0-9a-fA-F]+|\d+)\b",
                "<REG>",
                line,
                flags=re.IGNORECASE,
            )

            # Preserve symbolic fault codes (CFSR=0x..., HFSR=0x..., ERR=0x...) via placeholder token
            preserved: List[Tuple[str, str]] = []

            def _keep_fault_hex(match: re.Match) -> str:
                token = f"__FAULT_CODE_{len(preserved)}__"
                preserved.append((token, match.group(0)))
                return token

            line = re.sub(
                r"\b(?:CFSR|HFSR|MMFAR|BFAR|DFSR|AFSR|ERR(?:OR)?|CODE)\s*[=:]\s*0x[0-9a-fA-F]+\b",
                _keep_fault_hex,
                line,
                flags=re.IGNORECASE,
            )

            # Strip remaining hex memory addresses (e.g. 0x7ffd1234abcd, 0x20001040)
            line = re.sub(r"\+0x[0-9a-fA-F]+\b", "+<OFF>", line)
            line = re.sub(r"\b0x[0-9a-fA-F]{4,}\b", "<ADDR>", line)

            for token, original in preserved:
                line = line.replace(token, original)

            # Normalize repeated whitespace
            line = re.sub(r"\s+", " ", line).strip()
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def classify_fault_domain(
        stacktrace_or_log: str,
        failure_artifacts: Optional[List[Any]] = None,
        screen_hash: Optional[str] = None,
    ) -> str:
        """Classify failure into Firmware | Hardware/Power | App | Backend | FlakyScript."""
        text = (stacktrace_or_log or "").lower()
        artifact_text = " ".join(
            str(a.get("artifact_type") or a.get("name") or a) if isinstance(a, dict) else str(a)
            for a in (failure_artifacts or [])
        ).lower()
        combined = f"{text}\n{artifact_text}"

        # 1. Hardware / Power
        if any(
            kw in combined
            for kw in (
                "brownout",
                "overcurrent",
                "undervoltage",
                "overvoltage",
                "power_budget",
                "power_trace_ma",
                "power_ma",
                "voltage drop",
                "bod_reset",
                "thermal_shutdown",
                "pmic",
            )
        ):
            return "Hardware/Power"

        # 2. Firmware
        if any(
            kw in combined
            for kw in (
                "hardfault",
                "cfsr",
                "hfsr",
                "uart_wdt_reset",
                "wdt_reset",
                "uart",
                "at+err",
                "irqhandler",
                "isr",
                "gdb_hardfault",
                "freertos",
                "firmware",
                "hal_",
                ".c:",
                ".cpp:",
            )
        ):
            return "Firmware"

        # 3. Backend
        if any(
            kw in combined
            for kw in (
                "http 500",
                "http 502",
                "http 503",
                "http 504",
                "internal server error",
                "fastapi",
                "uvicorn",
                "sqlalchemy",
                "psycopg2",
                "postgresql",
                "traceback (most recent call last)",
                "gateway timeout",
            )
        ):
            return "Backend"

        # 4. App
        if any(
            kw in combined
            for kw in (
                "crashpad",
                "nullpointerexception",
                "nsinvalidargumentexception",
                "sigsegv",
                "anr",
                "android",
                "ios",
                "qobject",
                "qml",
                "qt ",
                ".apk",
                ".ipa",
                "app_crash",
            )
        ):
            return "App"

        # 5. FlakyScript (selector/UI mismatch without crash)
        if screen_hash or any(
            kw in combined
            for kw in (
                "element not found",
                "elementnotfound",
                "no such element",
                "selector not found",
                "locator timeout",
                "screen_hash",
                "ui_fingerprint",
                "stale element",
                "widget not found",
                "flaky",
            )
        ):
            return "FlakyScript"

        return "Firmware"

    @staticmethod
    def _build_self_heal_hint(fault_domain: str, normalized_signature: str) -> str:
        if fault_domain == "FlakyScript":
            return (
                "Invalidate stale UDCP cache entry and re-synthesize UI selectors "
                "using updated screen_hash/ui_fingerprint."
            )
        if fault_domain == "Firmware":
            return (
                "Inspect fault address/CFSR and recent firmware commit diff; "
                "symbolize PC/LR against ELF debug symbols."
            )
        if fault_domain == "Hardware/Power":
            return (
                "Inspect synchronized power_trace_ma and rail voltage telemetry around NTP failure window."
            )
        if fault_domain == "App":
            return (
                "Symbolize Crashpad minidump / mobile stacktrace and verify UI state transitions."
            )
        return (
            "Inspect backend HTTP/HAR traces and server exception stacktrace for regression."
        )

    async def execute(
        self,
        request: Dict[str, Any],
        registry: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = context or {}
        if not self._tables_ensured:
            self.ensure_tables()

        project_id = str(request.get("project_id") or ctx.get("project_id") or "default")
        run_id = str(request.get("run_id") or "")
        raw_log = str(request.get("stacktrace_or_log") or "")
        failure_artifacts = list(request.get("failure_artifacts") or [])
        test_intent_id = request.get("test_intent_id")
        screen_hash = request.get("screen_hash")
        cache_id = request.get("cache_id")

        normalized_sig = self.normalize_signature(raw_log)
        signature_hash = hashlib.sha256(normalized_sig.encode("utf-8")).hexdigest()
        fault_domain = self.classify_fault_domain(raw_log, failure_artifacts, screen_hash=screen_hash)
        self_heal_hint = self._build_self_heal_hint(fault_domain, normalized_sig)
        new_tickets = _extract_ticket_refs(raw_log, extra=request.get("ticket_refs"))

        now_ms = int(time.time() * 1000)
        metadata = {
            "test_intent_id": test_intent_id,
            "screen_hash": screen_hash,
            "cache_id": cache_id,
            "failure_artifacts": failure_artifacts,
        }

        row = self._upsert_incident(
            project_id=project_id,
            signature_hash=signature_hash,
            fault_domain=fault_domain,
            normalized_signature=normalized_sig,
            new_tickets=new_tickets,
            run_id=run_id,
            self_heal_hint=self_heal_hint,
            metadata=metadata,
            now_ms=now_ms,
        )

        cache_feedback = await self._apply_cache_feedback(
            fault_domain=fault_domain,
            cache_id=cache_id,
            signature_hash=signature_hash,
            self_heal_hint=self_heal_hint,
            request=request,
            ctx=ctx,
            registry=registry,
            project_id=project_id,
        )

        occurrence_count = int(row["occurrence_count"])
        ticket_refs = json.loads(row.get("ticket_refs_json") or "[]")

        return {
            "status": "ok",
            "incident_id": row["incident_id"],
            "project_id": project_id,
            "signature_hash": signature_hash,
            "normalized_signature": normalized_sig,
            "fault_domain": fault_domain,
            "is_duplicate": occurrence_count > 1,
            "occurrence_count": occurrence_count,
            "ticket_refs": ticket_refs,
            "self_heal_hint": self_heal_hint,
            "cache_feedback": cache_feedback,
        }

    def _upsert_incident(
        self,
        *,
        project_id: str,
        signature_hash: str,
        fault_domain: str,
        normalized_signature: str,
        new_tickets: List[str],
        run_id: str,
        self_heal_hint: str,
        metadata: Dict[str, Any],
        now_ms: int,
    ) -> Dict[str, Any]:
        select_sql = (
            "SELECT * FROM v3_rca_incidents WHERE project_id = ? AND signature_hash = ?"
        )
        upsert_sql = """
        INSERT INTO v3_rca_incidents (
            incident_id, project_id, signature_hash, fault_domain,
            normalized_signature, ticket_refs_json, occurrence_count,
            last_run_id, self_heal_hint, metadata_json,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
        ON CONFLICT (project_id, signature_hash) DO UPDATE SET
            fault_domain = excluded.fault_domain,
            normalized_signature = excluded.normalized_signature,
            ticket_refs_json = excluded.ticket_refs_json,
            occurrence_count = v3_rca_incidents.occurrence_count + 1,
            last_run_id = excluded.last_run_id,
            self_heal_hint = excluded.self_heal_hint,
            metadata_json = excluded.metadata_json,
            last_seen_at = excluded.last_seen_at
        """.strip()

        external_db = self.config.get("db")
        if external_db is not None:
            existing = external_db.fetchone(select_sql, (project_id, signature_hash))
            merged_tickets = _extract_ticket_refs(
                "",
                extra=(json.loads(existing["ticket_refs_json"]) if existing else []) + new_tickets,
            )
            incident_id = existing["incident_id"] if existing else f"rca_{uuid.uuid4().hex[:16]}"
            external_db.execute(
                upsert_sql,
                (
                    incident_id,
                    project_id,
                    signature_hash,
                    fault_domain,
                    normalized_signature,
                    json.dumps(merged_tickets),
                    run_id,
                    self_heal_hint,
                    json.dumps(metadata),
                    now_ms,
                    now_ms,
                ),
            )
            if hasattr(external_db, "commit"):
                external_db.commit()
            return dict(external_db.fetchone(select_sql, (project_id, signature_hash)))

        conn = self._get_sqlite_conn()
        existing_row = conn.execute(select_sql, (project_id, signature_hash)).fetchone()
        existing_tickets = (
            json.loads(existing_row["ticket_refs_json"]) if existing_row else []
        )
        merged_tickets = _extract_ticket_refs("", extra=existing_tickets + new_tickets)
        incident_id = existing_row["incident_id"] if existing_row else f"rca_{uuid.uuid4().hex[:16]}"

        conn.execute(
            upsert_sql,
            (
                incident_id,
                project_id,
                signature_hash,
                fault_domain,
                normalized_signature,
                json.dumps(merged_tickets),
                run_id,
                self_heal_hint,
                json.dumps(metadata),
                now_ms,
                now_ms,
            ),
        )
        conn.commit()
        updated = conn.execute(select_sql, (project_id, signature_hash)).fetchone()
        return dict(updated)

    async def _apply_cache_feedback(
        self,
        *,
        fault_domain: str,
        cache_id: Optional[str],
        signature_hash: str,
        self_heal_hint: str,
        request: Dict[str, Any],
        ctx: Dict[str, Any],
        registry: Optional[Any],
        project_id: str,
    ) -> Dict[str, Any]:
        if fault_domain != "FlakyScript" or not cache_id:
            return {"applied": False}

        cache_plugin = (
            request.get("cache_plugin")
            or ctx.get("cache_plugin")
            or _safe_resolve_plugin(
                registry, PluginCategory.SCRIPT_CACHE.value, project_id=project_id
            )
        )
        if cache_plugin is None:
            return {"applied": False, "reason": "no_script_cache_plugin"}

        feedback: Dict[str, Any] = {"applied": True, "cache_id": cache_id}
        reason = f"FlakyScript RCA {signature_hash[:12]}: {self_heal_hint}"
        self_heal_patch = request.get("self_heal_patch")

        try:
            if hasattr(cache_plugin, "record_execution"):
                rec = cache_plugin.record_execution(
                    cache_id,
                    success=False,
                    fallback_reason=reason,
                    self_heal_patch=self_heal_patch,
                )
                if inspect.isawaitable(rec):
                    rec = await rec
                feedback["execution_record"] = rec

            if request.get("invalidate_on_flaky", True) and not (
                isinstance(self_heal_patch, dict) and self_heal_patch.get("verified")
            ):
                if hasattr(cache_plugin, "invalidate_script"):
                    inv = cache_plugin.invalidate_script(cache_id, reason)
                    if inspect.isawaitable(inv):
                        inv = await inv
                    feedback["invalidated"] = bool(inv)
        except Exception as exc:
            feedback["error"] = str(exc)

        return feedback


# ---------------------------------------------------------------------------
# 4. WebhookEventIngestPlugin (webhook.event_ingest)
# ---------------------------------------------------------------------------


class WebhookEventIngestPlugin(WebhookPlugin):
    """Unified Layer 0 webhook verifier & normalizer for GitLab, GitHub, TestRail, Jira, and Notion."""

    source_name: str = "multi"
    metadata: PluginMetadata = PluginMetadata(
        plugin_id="webhook.event_ingest",
        name="Unified Webhook Event Ingestor",
        version="1.0.0",
        category=PluginCategory.WEBHOOK.value,
        priority=100,
        capabilities=["gitlab", "github", "testrail", "jira", "notion", "hmac_sha256"],
        description=(
            "Verifies HMAC-SHA256 or token signatures and normalizes GitLab, GitHub, "
            "TestRail, Jira, and Notion webhook events into WebhookNormalizedEvent."
        ),
    )

    def verify_signature(
        self,
        headers: Dict[str, str],
        raw_body: bytes,
        secret: Optional[str] = None,
        allow_unsigned: Optional[bool] = None,
    ) -> bool:
        norm_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        active_secret = (
            secret
            if secret is not None
            else (self.config.get("webhook_secret") or self.config.get("secret"))
        )
        eff_allow_unsigned = (
            bool(self.config.get("allow_unsigned", False))
            if allow_unsigned is None
            else bool(allow_unsigned)
        )
        if not active_secret:
            return eff_allow_unsigned

        # GitLab shared token header
        gitlab_token = norm_headers.get("x-gitlab-token")
        if gitlab_token is not None:
            return hmac.compare_digest(gitlab_token, str(active_secret))

        # Generic token header
        webhook_token = norm_headers.get("x-webhook-token")
        if webhook_token is not None:
            return hmac.compare_digest(webhook_token, str(active_secret))

        # HMAC-SHA256 headers (GitHub X-Hub-Signature-256 or Project-Brain X-PB-Signature)
        sig_256 = norm_headers.get("x-hub-signature-256") or norm_headers.get("x-pb-signature")
        if sig_256 is not None:
            provided = sig_256.split("=", 1)[1] if sig_256.lower().startswith("sha256=") else sig_256
            expected = hmac.new(
                str(active_secret).encode("utf-8"), raw_body or b"", hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(provided.lower(), expected.lower())

        # Legacy GitHub HMAC-SHA1
        sig_1 = norm_headers.get("x-hub-signature")
        if sig_1 is not None:
            provided = sig_1.split("=", 1)[1] if sig_1.lower().startswith("sha1=") else sig_1
            expected = hmac.new(
                str(active_secret).encode("utf-8"), raw_body or b"", hashlib.sha1
            ).hexdigest()
            return hmac.compare_digest(provided.lower(), expected.lower())

        return False

    async def parse_event(
        self, headers: Dict[str, str], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        norm_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        data = dict(payload or {})
        source = self._detect_source(norm_headers, data)

        if source == "gitlab":
            event = self._normalize_gitlab(norm_headers, data)
        elif source == "github":
            event = self._normalize_github(norm_headers, data)
        elif source == "testrail":
            event = self._normalize_testrail(norm_headers, data)
        elif source == "jira":
            event = self._normalize_jira(norm_headers, data)
        elif source == "notion":
            event = self._normalize_notion(norm_headers, data)
        else:
            event = self._normalize_generic(norm_headers, data, source)

        return event.to_dict()

    @staticmethod
    def _detect_source(headers: Dict[str, str], payload: Dict[str, Any]) -> str:
        explicit = str(payload.get("source") or headers.get("x-pb-source") or "").lower()
        if explicit in ("gitlab", "github", "testrail", "jira", "notion"):
            return explicit
        if "x-gitlab-event" in headers or "x-gitlab-token" in headers or "object_attributes" in payload:
            return "gitlab"
        if "x-github-event" in headers or "x-hub-signature-256" in headers or "pull_request" in payload:
            return "github"
        if "x-testrail-event" in headers or any(k in payload for k in ("suite_id", "case_id", "cases", "plan_id")):
            return "testrail"
        if "webhookEvent" in payload or ("issue" in payload and isinstance(payload["issue"], dict) and "fields" in payload["issue"]):
            return "jira"
        if "notion" in headers.get("user-agent", "").lower() or (
            isinstance(payload.get("entity"), dict) and payload.get("type", "").startswith("page.")
        ) or ("page" in payload and "workspace_id" in payload):
            return "notion"
        return explicit or "generic"

    @staticmethod
    def _extract_build_artifacts(
        candidates: List[Any], changed_files: List[str]
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in candidates:
            norm = _normalize_artifact_item(item)
            if norm["name"] and norm["name"] not in seen:
                seen.add(norm["name"])
                artifacts.append(norm)
        for path in changed_files:
            ext = Path(path).suffix.lower()
            if ext in _BUILD_ARTIFACT_EXTS and path not in seen:
                seen.add(path)
                artifacts.append(_normalize_artifact_item(path))
        return artifacts

    @staticmethod
    def _dedup_list(items: Iterable[str]) -> List[str]:
        seen: Set[str] = set()
        out: List[str] = []
        for item in items:
            s = str(item).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _normalize_gitlab(
        self, headers: Dict[str, str], data: Dict[str, Any]
    ) -> WebhookNormalizedEvent:
        obj_attrs = data.get("object_attributes") if isinstance(data.get("object_attributes"), dict) else {}
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        event_type = str(
            data.get("object_kind")
            or headers.get("x-gitlab-event")
            or "merge_request"
        ).lower().replace(" ", "_")

        ref = str(data.get("ref") or "")
        branch = obj_attrs.get("source_branch") or (
            ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else None
        )
        base_branch = obj_attrs.get("target_branch")
        version_tag = data.get("version_tag") or obj_attrs.get("tag") or (
            ref.replace("refs/tags/", "") if ref.startswith("refs/tags/") else None
        )

        files: List[str] = list(data.get("changed_files") or [])
        for ch in data.get("changes") or []:
            if isinstance(ch, dict):
                for key in ("new_path", "old_path", "path"):
                    if ch.get(key):
                        files.append(str(ch[key]))
        for commit in data.get("commits") or []:
            if isinstance(commit, dict):
                for key in ("added", "modified", "removed"):
                    files.extend(str(f) for f in (commit.get(key) or []))
        changed_files = self._dedup_list(files)

        raw_artifacts: List[Any] = list(data.get("artifacts") or [])
        for build in data.get("builds") or []:
            if isinstance(build, dict) and build.get("artifacts_file"):
                af = build["artifacts_file"]
                if isinstance(af, dict) and af.get("filename"):
                    raw_artifacts.append(af["filename"])
                elif isinstance(af, str):
                    raw_artifacts.append(af)
        artifacts = self._extract_build_artifacts(raw_artifacts, changed_files)

        title = str(obj_attrs.get("title") or data.get("title") or "")
        desc = str(obj_attrs.get("description") or "")
        srs_diff = "\n".join(p for p in (title, desc) if p)
        tickets = _extract_ticket_refs(srs_diff)

        return WebhookNormalizedEvent(
            source="gitlab",
            event_type=event_type,
            project_id=str(
                data.get("project_id")
                or project.get("path_with_namespace")
                or project.get("name")
                or "default"
            ),
            branch=branch,
            base_branch=base_branch,
            version_tag=version_tag,
            changed_files=changed_files,
            artifacts=artifacts,
            ticket_refs=tickets,
            srs_diff=srs_diff,
            raw_metadata={
                "mr_iid": obj_attrs.get("iid"),
                "url": obj_attrs.get("url") or project.get("web_url"),
                "sha": obj_attrs.get("last_commit", {}).get("id")
                if isinstance(obj_attrs.get("last_commit"), dict)
                else data.get("after"),
            },
        )

    def _normalize_github(
        self, headers: Dict[str, str], data: Dict[str, Any]
    ) -> WebhookNormalizedEvent:
        pr = data.get("pull_request") if isinstance(data.get("pull_request"), dict) else {}
        repo = data.get("repository") if isinstance(data.get("repository"), dict) else {}
        release = data.get("release") if isinstance(data.get("release"), dict) else {}
        event_type = str(
            headers.get("x-github-event")
            or ("pull_request" if pr else "push")
        ).lower()

        ref = str(data.get("ref") or "")
        head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
        branch = head.get("ref") or (
            ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else None
        )
        base_branch = base.get("ref")
        version_tag = (
            data.get("version_tag")
            or release.get("tag_name")
            or (ref.replace("refs/tags/", "") if ref.startswith("refs/tags/") else None)
        )

        files: List[str] = list(data.get("changed_files") or [])
        for f in pr.get("files") or data.get("files") or []:
            if isinstance(f, dict) and f.get("filename"):
                files.append(str(f["filename"]))
            elif isinstance(f, str):
                files.append(f)
        for commit in data.get("commits") or []:
            if isinstance(commit, dict):
                for key in ("added", "modified", "removed"):
                    files.extend(str(x) for x in (commit.get(key) or []))
        changed_files = self._dedup_list(files)

        raw_artifacts: List[Any] = list(data.get("artifacts") or [])
        for asset in release.get("assets") or []:
            if isinstance(asset, dict):
                raw_artifacts.append(
                    {
                        "name": asset.get("name") or "",
                        "url": asset.get("browser_download_url") or asset.get("url") or "",
                    }
                )
        artifacts = self._extract_build_artifacts(raw_artifacts, changed_files)

        title = str(pr.get("title") or release.get("name") or "")
        body = str(pr.get("body") or release.get("body") or "")
        srs_diff = "\n".join(p for p in (title, body) if p)
        tickets = _extract_ticket_refs(srs_diff)

        return WebhookNormalizedEvent(
            source="github",
            event_type=event_type,
            project_id=str(
                data.get("project_id")
                or repo.get("full_name")
                or repo.get("name")
                or "default"
            ),
            branch=branch,
            base_branch=base_branch,
            version_tag=version_tag,
            changed_files=changed_files,
            artifacts=artifacts,
            ticket_refs=tickets,
            srs_diff=srs_diff,
            raw_metadata={
                "pr_number": pr.get("number") or data.get("number"),
                "url": pr.get("html_url") or repo.get("html_url"),
                "sha": head.get("sha") or data.get("after"),
            },
        )

    def _normalize_testrail(
        self, headers: Dict[str, str], data: Dict[str, Any]
    ) -> WebhookNormalizedEvent:
        cases = data.get("cases") if isinstance(data.get("cases"), list) else []
        if not cases and data.get("case_id"):
            cases = [data]

        diff_parts: List[str] = []
        for c in cases:
            if isinstance(c, dict):
                diff_parts.append(
                    " ".join(
                        str(c.get(k) or "")
                        for k in ("case_id", "id", "title", "custom_preconds", "custom_expected")
                    ).strip()
                )
        srs_diff = "\n".join(p for p in diff_parts if p) or str(data.get("title") or "")
        tickets = _extract_ticket_refs(srs_diff + " " + str(data.get("refs") or ""))

        return WebhookNormalizedEvent(
            source="testrail",
            event_type=str(data.get("event_type") or headers.get("x-testrail-event") or "testcase_updated"),
            project_id=str(data.get("project_id") or data.get("project_name") or "default"),
            branch=data.get("branch"),
            version_tag=data.get("version_tag") or data.get("milestone"),
            changed_files=self._dedup_list(data.get("changed_files") or []),
            artifacts=self._extract_build_artifacts(data.get("artifacts") or [], []),
            ticket_refs=tickets,
            srs_diff=srs_diff,
            raw_metadata={
                "suite_id": data.get("suite_id"),
                "plan_id": data.get("plan_id"),
                "case_count": len(cases),
            },
        )

    def _normalize_jira(
        self, headers: Dict[str, str], data: Dict[str, Any]
    ) -> WebhookNormalizedEvent:
        issue = data.get("issue") if isinstance(data.get("issue"), dict) else {}
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        project = fields.get("project") if isinstance(fields.get("project"), dict) else {}
        fix_versions = fields.get("fixVersions") if isinstance(fields.get("fixVersions"), list) else []
        version_tag = data.get("version_tag") or (
            fix_versions[0].get("name") if fix_versions and isinstance(fix_versions[0], dict) else None
        )

        issue_key = str(issue.get("key") or "")
        summary = str(fields.get("summary") or "")
        description = str(fields.get("description") or "")
        srs_diff = "\n".join(p for p in (issue_key, summary, description) if p)
        tickets = _extract_ticket_refs(srs_diff, extra=[issue_key] if issue_key else None)

        return WebhookNormalizedEvent(
            source="jira",
            event_type=str(data.get("webhookEvent") or "jira:issue_updated"),
            project_id=str(data.get("project_id") or project.get("key") or "default"),
            branch=data.get("branch"),
            version_tag=version_tag,
            changed_files=self._dedup_list(data.get("changed_files") or []),
            artifacts=self._extract_build_artifacts(data.get("artifacts") or [], []),
            ticket_refs=tickets,
            srs_diff=srs_diff,
            raw_metadata={
                "issue_key": issue_key,
                "status": (fields.get("status") or {}).get("name")
                if isinstance(fields.get("status"), dict)
                else None,
            },
        )

    def _normalize_notion(
        self, headers: Dict[str, str], data: Dict[str, Any]
    ) -> WebhookNormalizedEvent:
        page = data.get("page") if isinstance(data.get("page"), dict) else {}
        entity = data.get("entity") if isinstance(data.get("entity"), dict) else {}
        title = str(data.get("title") or page.get("title") or "")
        content_diff = str(data.get("content_diff") or data.get("markdown") or "")
        srs_diff = "\n".join(p for p in (title, content_diff) if p)

        return WebhookNormalizedEvent(
            source="notion",
            event_type=str(data.get("type") or data.get("event_type") or "page.content_updated"),
            project_id=str(data.get("project_id") or data.get("workspace_id") or "default"),
            branch=data.get("branch"),
            version_tag=data.get("version_tag"),
            changed_files=self._dedup_list(data.get("changed_files") or []),
            artifacts=self._extract_build_artifacts(data.get("artifacts") or [], []),
            ticket_refs=_extract_ticket_refs(srs_diff),
            srs_diff=srs_diff,
            raw_metadata={
                "page_id": page.get("id") or entity.get("id") or data.get("page_id"),
                "workspace_id": data.get("workspace_id"),
            },
        )

    def _normalize_generic(
        self, headers: Dict[str, str], data: Dict[str, Any], source: str
    ) -> WebhookNormalizedEvent:
        changed_files = self._dedup_list(data.get("changed_files") or [])
        artifacts = self._extract_build_artifacts(data.get("artifacts") or [], changed_files)
        srs_diff = str(data.get("srs_diff") or data.get("description") or "")
        return WebhookNormalizedEvent(
            source=source,
            event_type=str(data.get("event_type") or "generic"),
            project_id=str(data.get("project_id") or "default"),
            branch=data.get("branch"),
            base_branch=data.get("base_branch"),
            version_tag=data.get("version_tag"),
            changed_files=changed_files,
            artifacts=artifacts,
            ticket_refs=_extract_ticket_refs(srs_diff, extra=data.get("ticket_refs")),
            srs_diff=srs_diff,
            raw_metadata={},
        )


# ---------------------------------------------------------------------------
# Plugin Discovery / Registration Entrypoints
# ---------------------------------------------------------------------------


def get_plugins(config: Optional[Dict[str, Any]] = None) -> List[BasePlugin]:
    """Instantiate and return all built-in Brain Consumer & Webhook plugins."""
    return [
        ImpactTestDAGPlannerPlugin(config=config),
        OracleKnowledgeJudgePlugin(config=config),
        RCATriageMemoryPlugin(config=config),
        WebhookEventIngestPlugin(config=config),
    ]


def register_plugins(
    registry: Any, config: Optional[Dict[str, Any]] = None
) -> List[str]:
    """Register all built-in Brain Consumer & Webhook plugins into PluginRegistry."""
    registered: List[str] = []
    for plugin in get_plugins(config=config):
        registry.register(plugin)
        registered.append(plugin.metadata.plugin_id)
    return registered


__all__ = [
    "ImpactTestDAGPlannerPlugin",
    "OracleKnowledgeJudgePlugin",
    "RCATriageMemoryPlugin",
    "WebhookEventIngestPlugin",
    "WebhookNormalizedEvent",
    "get_plugins",
    "register_plugins",
]
