"""Pydantic models and data transfer objects for Hybrid Graphify Engine."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


try:
    from pydantic import ConfigDict
    HAS_CONFIG_DICT = True
except ImportError:
    ConfigDict = None
    HAS_CONFIG_DICT = False


class BaseGraphModel(BaseModel):
    """Base model with cross-version Pydantic helper methods."""

    if HAS_CONFIG_DICT:
        model_config = ConfigDict(extra="allow")
    else:
        class Config:
            extra = "allow"

    def to_dict(self) -> Dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump()
        return self.dict()


class GraphNode(BaseGraphModel):
    id: str
    project_id: str
    label: str
    file_type: str = "code"  # "code" | "document" | "config"
    source_file: str
    source_location: Optional[str] = None  # e.g., "src/auth.py:45" or "page:3"
    community: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[str] = None


class GraphEdge(BaseGraphModel):
    project_id: str
    source: str
    target: str
    relation: str  # "calls" | "imports" | "defines" | "inherits" | "rationale_for" | "cites"
    confidence: str = "EXTRACTED"  # "EXTRACTED" | "INFERRED" | "AMBIGUOUS"
    confidence_score: float = 1.0
    source_file: Optional[str] = None
    source_location: Optional[str] = None
    weight: float = 1.0


class GraphHyperedge(BaseGraphModel):
    id: str
    project_id: str
    label: str
    nodes: List[str]
    relation: str
    confidence: str = "EXTRACTED"
    confidence_score: float = 1.0
    source_file: Optional[str] = None


class GraphDelta(BaseGraphModel):
    project_id: str
    branch: str = "main"
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    hyperedges: List[GraphHyperedge] = Field(default_factory=list)
    node_tombstones: List[str] = Field(default_factory=list)
    edge_tombstones: List[Dict[str, Any]] = Field(default_factory=list)  # [{"source": ..., "target": ..., "relation": ...}]
    hyperedge_tombstones: List[str] = Field(default_factory=list)
    synced_files: List[Dict[str, Any]] = Field(default_factory=list)  # [{"file_path": ..., "sha256": ...}]


class BranchDiffSummary(BaseGraphModel):
    new_nodes_count: int = 0
    modified_nodes_count: int = 0
    deleted_nodes_count: int = 0
    new_edges_count: int = 0
    deleted_edges_count: int = 0


class BranchDiffResult(BaseGraphModel):
    summary: BranchDiffSummary
    new_nodes: List[Dict[str, Any]] = Field(default_factory=list)
    modified_nodes: List[Dict[str, Any]] = Field(default_factory=list)
    deleted_node_ids: List[str] = Field(default_factory=list)
    new_edges: List[Dict[str, Any]] = Field(default_factory=list)
    deleted_edges: List[Dict[str, Any]] = Field(default_factory=list)
