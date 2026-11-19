"""Comprehensive Unit, Integration, REST & MCP Verification Suite for:
1. Multi-Level Base Branch Code Delta (SqliteOverlayGraphStore, HybridResolver, /graphify/*, PB_ROOT_BRANCH)
2. GraphifyExtractor Git-Diff Auto-Delta (base_branch...HEAD + uncommitted dirty workspace)
3. HSG Vector Memory Ancestor Overlay Inheritance, Tombstones & Git-Diff Incremental Sync
4. Document Versioning (v1.0 -> v1.1) & Pre-OCR / Pre-Embedding Delta Engine + REST/MCP + DB.translate_query
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient
from PIL import Image
import pypdf

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for p in (str(REPO_ROOT), str(SRC_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from projectbrain.ai.mcp import (
    projectbrain_diff_document_versions,
    projectbrain_list_document_versions,
    sync_codebase_memories,
)
from projectbrain.core.config import env
from projectbrain.core.db import (
    DB,
    db,
    get_memory_branch_lineage,
    record_memory_tombstone,
    register_memory_branch,
)
from projectbrain.graphify_engine.doc_versioning import (
    derive_doc_key,
    ingest_document_versioned,
)
from projectbrain.graphify_engine.extractor import GraphifyExtractor
from projectbrain.graphify_engine.hybrid_resolver import HybridResolver
from projectbrain.graphify_engine.models import GraphDelta, GraphEdge, GraphNode
from projectbrain.graphify_engine.store import SqliteOverlayGraphStore, get_graph_store
from projectbrain.memory import hsg as hsg_mod
from projectbrain.memory.hsg import (
    add_hsg_memory,
    hsg_query,
    invalidate_query_cache,
)
from projectbrain.server.api import create_app
from projectbrain.utils import doc_parser


# =====================================================================
# Strictly Isolated Per-Test Database & Environment Fixture
# =====================================================================


@pytest.fixture(autouse=True)
def isolated_branch_doc_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate every test to fresh SQLite DBs in tmp_path and restore all globals cleanly."""
    orig_db_url = env.database_url
    orig_db_path = env.db_path
    orig_emb_kind = env.emb_kind
    orig_min_score = env.min_score
    orig_root_branch = env.root_branch
    orig_conn = db.conn
    orig_is_postgres = db.is_postgres

    temp_db_path = tmp_path / "branch_doc_test.db"
    temp_graph_db_path = tmp_path / "branch_doc_graph.db"

    monkeypatch.setenv("PB_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("OM_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("PB_GRAPH_DB_PATH", str(temp_graph_db_path))
    monkeypatch.setenv("OM_GRAPH_DB_PATH", str(temp_graph_db_path))
    monkeypatch.delenv("PB_ROOT_BRANCH", raising=False)
    monkeypatch.delenv("OM_ROOT_BRANCH", raising=False)
    monkeypatch.delenv("PB_REQUIRE_REST_AUTH", raising=False)
    monkeypatch.delenv("PB_READ_ONLY", raising=False)
    monkeypatch.delenv("OM_READ_ONLY", raising=False)
    monkeypatch.delenv("PB_MODE", raising=False)

    monkeypatch.setattr(env, "database_url", f"sqlite:///{temp_db_path}")
    monkeypatch.setattr(env, "db_path", str(temp_db_path))
    monkeypatch.setattr(env, "emb_kind", "synthetic")
    monkeypatch.setattr(env, "min_score", 0.0)
    monkeypatch.setattr(env, "root_branch", "main")

    # Detach existing connection without closing it so other test modules remain unaffected
    db.conn = None
    db.is_postgres = False
    db.connect()
    invalidate_query_cache()

    applied_migrations = {
        row["name"] for row in db.fetchall("SELECT name FROM _migrations")
    }
    assert "008_memory_branch_overlay.sql" in applied_migrations
    assert "009_document_versioning.sql" in applied_migrations

    try:
        yield {
            "db_path": temp_db_path,
            "graph_db_path": temp_graph_db_path,
        }
    finally:
        temp_conn = db.conn
        if temp_conn is not None and temp_conn is not orig_conn:
            try:
                temp_conn.close()
            except Exception:
                pass
        env.database_url = orig_db_url
        env.db_path = orig_db_path
        env.emb_kind = orig_emb_kind
        env.min_score = orig_min_score
        env.root_branch = orig_root_branch
        db.conn = orig_conn
        db.is_postgres = orig_is_postgres
        invalidate_query_cache()


# =====================================================================
# Helpers
# =====================================================================


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return proc.stdout.strip()


def _init_git_repo(repo: Path, initial_branch: str = "develop") -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@projectbrain.local")
    _run_git(repo, "config", "user.name", "ProjectBrain Test")
    _run_git(repo, "checkout", "-B", initial_branch)
    return repo


def _make_single_page_image_pdf(color: Tuple[int, int, int], seed: int) -> bytes:
    """Create a deterministic single-page image-only PDF with unique pixel content."""
    img = Image.new("RGB", (64, 64), color=color)
    # Paint a deterministic pattern unique to `seed` so raw image hashes differ strictly
    for x in range(8):
        for y in range(8):
            r = (color[0] + seed * 17 + x * 13) % 256
            g = (color[1] + seed * 29 + y * 19) % 256
            b = (color[2] + seed * 43 + (x + y) * 7) % 256
            img.putpixel((x, y), (r, g, b))
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def _combine_pdf_pages(single_page_pdfs: List[bytes]) -> bytes:
    """Merge single-page PDF byte payloads into a multi-page PDF via pypdf."""
    writer = pypdf.PdfWriter()
    for page_bytes in single_page_pdfs:
        reader = pypdf.PdfReader(io.BytesIO(page_bytes))
        writer.add_page(reader.pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# =====================================================================
# Pillar 1: Multi-Level Base Branch Code Delta
# =====================================================================


def test_dynamic_root_branch_config_and_hybrid_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    graph_db = tmp_path / "dynamic_root_graph.db"

    # Configure PB_ROOT_BRANCH=develop via env var & env.root_branch
    monkeypatch.setenv("PB_ROOT_BRANCH", "develop")
    monkeypatch.setattr(env, "root_branch", "develop")

    store_env = get_graph_store(db_path=str(graph_db))
    try:
        assert store_env.root_branch == "develop"
        assert store_env.is_root_branch("develop") is True
        assert store_env.is_root_branch("main") is False
    finally:
        store_env.close()

    # Also verify explicit root_branch argument
    store = get_graph_store(db_path=str(graph_db), root_branch="develop")
    try:
        assert store.is_root_branch("develop") is True
        assert store.is_root_branch("main") is False

        # Sync canonical root ("develop") and a child feature branch ("feat/dyn")
        store.sync_branch_delta(
            project_id="proj_dyn",
            branch_name="develop",
            delta=GraphDelta(
                project_id="proj_dyn",
                branch="develop",
                nodes=[
                    GraphNode(
                        id="n_dev_root",
                        project_id="proj_dyn",
                        label="DevRoot",
                        source_file="src/dev_root.py",
                    )
                ],
            ),
            partial=False,
        )
        store.sync_branch_delta(
            project_id="proj_dyn",
            branch_name="feat/dyn",
            delta=GraphDelta(
                project_id="proj_dyn",
                branch="feat/dyn",
                nodes=[
                    GraphNode(
                        id="n_feat_dyn",
                        project_id="proj_dyn",
                        label="FeatDyn",
                        source_file="src/feat_dyn.py",
                    )
                ],
            ),
            partial=True,
            synced_files=["src/feat_dyn.py"],
            base_branch="develop",
        )

        resolver = HybridResolver(store=store)
        g_root = resolver.resolve_hybrid_graph("proj_dyn", branch="develop")
        assert g_root.nodes["n_dev_root"]["tier"] == "root"

        g_feat = resolver.resolve_hybrid_graph("proj_dyn", branch="feat/dyn")
        assert g_feat.nodes["n_dev_root"]["tier"] == "branch"
        assert g_feat.nodes["n_feat_dyn"]["tier"] == "branch"
    finally:
        store.close()


def test_multilevel_branch_overlay_diff_promote_and_validation(tmp_path: Path):
    graph_db = tmp_path / "multilevel_graph.db"
    with SqliteOverlayGraphStore(db_path=str(graph_db), root_branch="main") as store:
        # Level 1: main (canonical root)
        store.sync_branch_delta(
            project_id="proj",
            branch_name="main",
            delta=GraphDelta(
                project_id="proj",
                branch="main",
                nodes=[
                    GraphNode(
                        id="n_root",
                        project_id="proj",
                        label="RootService",
                        source_file="src/root.py",
                    ),
                    GraphNode(
                        id="n_old",
                        project_id="proj",
                        label="OldV1",
                        source_file="src/old.py",
                    ),
                ],
                edges=[
                    GraphEdge(
                        project_id="proj",
                        source="n_root",
                        target="n_old",
                        relation="calls",
                        source_file="src/root.py",
                    )
                ],
            ),
            partial=False,
        )

        # Level 2: develop (overrides n_old, adds n_dev)
        store.sync_branch_delta(
            project_id="proj",
            branch_name="develop",
            delta=GraphDelta(
                project_id="proj",
                branch="develop",
                nodes=[
                    GraphNode(
                        id="n_old",
                        project_id="proj",
                        label="OldV2FromDevelop",
                        source_file="src/old.py",
                    ),
                    GraphNode(
                        id="n_dev",
                        project_id="proj",
                        label="DevOnlyHelper",
                        source_file="src/dev_helper.py",
                    ),
                ],
            ),
            partial=True,
            synced_files=["src/old.py", "src/dev_helper.py"],
            base_branch="main",
        )

        # Level 3: feat/x based on develop (adds n_feat, deletes src/dev_helper.py -> tombstones n_dev)
        store.sync_branch_delta(
            project_id="proj",
            branch_name="feat/x",
            delta=GraphDelta(
                project_id="proj",
                branch="feat/x",
                nodes=[
                    GraphNode(
                        id="n_feat",
                        project_id="proj",
                        label="FeatXHandler",
                        source_file="src/feat_x.py",
                    )
                ],
            ),
            partial=True,
            synced_files=["src/feat_x.py"],
            deleted_files=["src/dev_helper.py"],
            base_branch="develop",
        )

        # Verify lineage and O(Delta) overlay storage on feat/x (does NOT duplicate n_root or n_old)
        assert store.get_branch_lineage("proj", "feat/x") == ["main", "develop", "feat/x"]
        feat_overlay = store._get_branch_overlay("proj", "feat/x")
        assert set(feat_overlay["node_upserts"].keys()) == {"n_feat"}
        assert "n_root" not in feat_overlay["node_upserts"]
        assert "n_old" not in feat_overlay["node_upserts"]
        assert "n_dev" in feat_overlay["node_tombstones"]

        # Verify 3-level resolved subgraph on feat/x
        resolved_feat = store.get_resolved_subgraph("proj", "feat/x")
        feat_nodes_by_id = {n["id"]: n for n in resolved_feat["nodes"]}
        assert set(feat_nodes_by_id.keys()) == {"n_root", "n_old", "n_feat"}
        assert feat_nodes_by_id["n_old"]["label"] == "OldV2FromDevelop"
        assert "n_dev" not in feat_nodes_by_id

        # Verify diff_branches("proj", "develop", "feat/x") reports exact delta vs develop
        diff_dev_feat = store.diff_branches("proj", "develop", "feat/x")
        assert [n["id"] for n in diff_dev_feat.new_nodes] == ["n_feat"]
        assert diff_dev_feat.deleted_node_ids == ["n_dev"]
        assert diff_dev_feat.modified_nodes == []
        assert diff_dev_feat.summary.new_nodes_count == 1
        assert diff_dev_feat.summary.deleted_nodes_count == 1

        # Add child branch subfeat -> feat/x to test reparenting upon promotion
        store.sync_branch_delta(
            project_id="proj",
            branch_name="subfeat",
            delta=GraphDelta(
                project_id="proj",
                branch="subfeat",
                nodes=[
                    GraphNode(
                        id="n_sub",
                        project_id="proj",
                        label="SubFeatNode",
                        source_file="src/subfeat.py",
                    )
                ],
            ),
            partial=True,
            synced_files=["src/subfeat.py"],
            base_branch="feat/x",
        )
        assert store.get_branch_lineage("proj", "subfeat") == [
            "main",
            "develop",
            "feat/x",
            "subfeat",
        ]

        # Promote feat/x into develop: merges feat/x -> develop, drops feat/x, reparents subfeat -> develop
        promo = store.promote_branch_to_root("proj", "feat/x", target_branch="develop")
        assert promo["status"] == "promoted"
        assert promo["target_branch"] == "develop"

        branch_names = {b["branch_name"] for b in store.list_branches("proj")}
        assert "feat/x" not in branch_names
        assert "develop" in branch_names
        assert "subfeat" in branch_names

        # develop now includes n_feat and no longer has n_dev
        resolved_dev_after = store.get_resolved_subgraph("proj", "develop")
        dev_after_ids = {n["id"] for n in resolved_dev_after["nodes"]}
        assert dev_after_ids == {"n_root", "n_old", "n_feat"}

        # subfeat is reparented cleanly onto develop
        assert store.get_branch_lineage("proj", "subfeat") == ["main", "develop", "subfeat"]
        resolved_sub_after = store.get_resolved_subgraph("proj", "subfeat")
        assert {n["id"] for n in resolved_sub_after["nodes"]} == {
            "n_root",
            "n_old",
            "n_feat",
            "n_sub",
        }

        # Cycle detection raises ValueError (develop -> subfeat where subfeat -> develop)
        with pytest.raises(ValueError, match="Cycle detected"):
            store.ensure_branch_schema("proj", "develop", base_branch="subfeat")

        # Max depth > 8 raises ValueError (8 non-root levels allowed, 9th rejected)
        parent = "main"
        for i in range(1, 9):
            br = f"chain_{i}"
            store.ensure_branch_schema("proj_depth", br, base_branch=parent)
            parent = br
        with pytest.raises(ValueError, match="exceeds maximum"):
            store.ensure_branch_schema("proj_depth", "chain_9", base_branch=parent)


def test_graphify_rest_multilevel_sync_diff_promote():
    app = create_app()
    client = TestClient(app)

    # Sync root main
    r_root = client.post(
        "/graphify/sync",
        json={
            "project_id": "proj_api",
            "branch": "main",
            "partial": False,
            "nodes": [
                {
                    "id": "api_root",
                    "project_id": "proj_api",
                    "label": "ApiRoot",
                    "source_file": "api/root.py",
                }
            ],
        },
    )
    assert r_root.status_code == 200, r_root.text

    # Sync develop off main
    r_dev = client.post(
        "/graphify/sync",
        json={
            "project_id": "proj_api",
            "branch": "develop",
            "base_branch": "main",
            "partial": True,
            "synced_files": [{"file_path": "api/dev.py", "sha256": ""}],
            "nodes": [
                {
                    "id": "api_dev",
                    "project_id": "proj_api",
                    "label": "ApiDev",
                    "source_file": "api/dev.py",
                }
            ],
        },
    )
    assert r_dev.status_code == 200, r_dev.text

    # Sync feat/api off develop
    r_feat = client.post(
        "/graphify/sync",
        json={
            "project_id": "proj_api",
            "branch": "feat/api",
            "base_branch": "develop",
            "partial": True,
            "synced_files": [{"file_path": "api/feat.py", "sha256": ""}],
            "nodes": [
                {
                    "id": "api_feat",
                    "project_id": "proj_api",
                    "label": "ApiFeat",
                    "source_file": "api/feat.py",
                }
            ],
        },
    )
    assert r_feat.status_code == 200, r_feat.text

    # Diff develop vs feat/api via REST
    r_diff = client.get(
        "/graphify/diff",
        params={
            "project_id": "proj_api",
            "base_branch": "develop",
            "target_branch": "feat/api",
        },
    )
    assert r_diff.status_code == 200, r_diff.text
    diff_body = r_diff.json()
    assert [n["id"] for n in diff_body["new_nodes"]] == ["api_feat"]

    # Promote feat/api -> develop via REST
    r_promo = client.post(
        "/graphify/promote",
        json={
            "project_id": "proj_api",
            "branch": "feat/api",
            "target_branch": "develop",
        },
    )
    assert r_promo.status_code == 200, r_promo.text
    assert r_promo.json()["status"] == "promoted"
    assert r_promo.json()["target_branch"] == "develop"

    r_sub = client.get(
        "/graphify/subgraph",
        params={"project_id": "proj_api", "branch": "develop"},
    )
    assert r_sub.status_code == 200
    assert {n["id"] for n in r_sub.json()["nodes"]} == {"api_root", "api_dev", "api_feat"}


# =====================================================================
# Pillar 2: GraphifyExtractor Git-Diff Auto-Delta
# =====================================================================


def test_graphify_extractor_git_diff_auto_delta_and_dirty_workspace(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "git_delta_repo", initial_branch="develop")

    # Initial commit on develop
    (repo / "keep.py").write_text(
        "def keep_fn():\n    return 'keep'\n", encoding="utf-8"
    )
    (repo / "mod.py").write_text(
        "def mod_fn():\n    return 1\n", encoding="utf-8"
    )
    (repo / "del.py").write_text(
        "def del_fn():\n    return 'gone'\n", encoding="utf-8"
    )
    (repo / "ren.py").write_text(
        "def ren_fn():\n    return 'renamed_payload_line_1'\n", encoding="utf-8"
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "initial develop")

    # Branch off to feat/auto
    _run_git(repo, "checkout", "-b", "feat/auto")

    # Modify mod.py, delete del.py, rename ren.py -> ren_new.py
    (repo / "mod.py").write_text(
        "def mod_fn():\n    return 2\n\ndef mod_added():\n    return 3\n",
        encoding="utf-8",
    )
    (repo / "del.py").unlink()
    _run_git(repo, "mv", "ren.py", "ren_new.py")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", "feat/auto changes")

    # Uncommitted dirty file in workspace
    (repo / "dirty.py").write_text(
        "def dirty_fn():\n    return 'uncommitted'\n", encoding="utf-8"
    )

    delta = GraphifyExtractor().extract_workspace(
        workspace_path=str(repo),
        project_id="p_git",
        branch="feat/auto",
        base_branch="develop",
        use_cache=False,
    )

    assert delta.base_branch == "develop"

    synced_paths = {
        (sf["file_path"] if isinstance(sf, dict) else str(sf)).replace("\\", "/")
        for sf in delta.synced_files
    }
    node_files = {n.source_file.replace("\\", "/") for n in delta.nodes}
    deleted_paths = {p.replace("\\", "/") for p in delta.deleted_files}

    assert "keep.py" not in synced_paths
    assert "keep.py" not in node_files
    assert {"mod.py", "ren_new.py", "dirty.py"}.issubset(synced_paths)
    assert {"mod.py", "ren_new.py", "dirty.py"}.issubset(node_files)
    assert "del.py" in deleted_paths
    assert "ren.py" in deleted_paths


# =====================================================================
# Pillar 3: HSG Memory Ancestor Overlay, Tombstones & Git-Diff Sync
# =====================================================================


def test_memory_branch_registration_lineage_cycle_depth_and_isolation():
    register_memory_branch("proj_m", "develop", "main")
    register_memory_branch("proj_m", "feat/x", "develop")

    assert get_memory_branch_lineage("proj_m", "feat/x") == ["feat/x", "develop", "main"]

    # Unregistered branch remains strictly isolated (len == 1)
    assert get_memory_branch_lineage("proj_m", "unlinked") == ["unlinked"]

    # Cycle detection raises ValueError
    with pytest.raises(ValueError, match="Cycle detected"):
        register_memory_branch("proj_m", "main", "feat/x")

    # Depth > 8 raises ValueError
    parent = "main"
    for i in range(1, 9):
        br = f"b{i}"
        register_memory_branch("proj_depth_m", br, parent)
        parent = br
    with pytest.raises(ValueError, match="exceeds maximum"):
        register_memory_branch("proj_depth_m", "b9", parent)


@pytest.mark.asyncio
async def test_hsg_3level_inheritance_shadowing_tombstones_and_isolation():
    register_memory_branch("proj_hsg", "develop", "main")
    register_memory_branch("proj_hsg", "feat/x", "develop")

    # Root main stored under bare "proj_hsg" (canonical bare-project fallback)
    await add_hsg_memory(
        "Root architecture baseline token_root_baseline for core router",
        metadata={"file_path": "src/core.py"},
        user_id="proj_hsg",
    )

    # Develop memories: src/auth.py (will be shadowed), spec_auth (will be shadowed),
    # src/old_del.py (will be tombstoned), and src/dev_keep.py (should be inherited)
    await add_hsg_memory(
        "Develop auth v1 token_dev_auth_old password hashing logic",
        metadata={"file_path": "src/auth.py"},
        user_id="proj_hsg:develop",
    )
    await add_hsg_memory(
        "Develop spec auth v1 token_dev_spec_old OAuth specification",
        metadata={"doc_key": "spec_auth"},
        user_id="proj_hsg:develop",
    )
    await add_hsg_memory(
        "Develop deprecated module token_dev_old_del legacy adapter",
        metadata={"file_path": "src/old_del.py"},
        user_id="proj_hsg:develop",
    )
    await add_hsg_memory(
        "Develop retained helper token_dev_keep shared telemetry utility",
        metadata={"file_path": "src/dev_keep.py"},
        user_id="proj_hsg:develop",
    )

    # feat/x overrides src/auth.py and doc_key="spec_auth" -> auto-tombstones & shadows develop
    await add_hsg_memory(
        "Feature X auth v2 token_feat_auth_new passkey authentication logic",
        metadata={"file_path": "src/auth.py"},
        user_id="proj_hsg:feat/x",
    )
    await add_hsg_memory(
        "Feature X spec auth v2 token_feat_spec_new WebAuthn specification",
        metadata={"doc_key": "spec_auth"},
        user_id="proj_hsg:feat/x",
    )

    # Explicitly delete src/old_del.py via tombstone on feat/x
    record_memory_tombstone("proj_hsg", "feat/x", "file", "src/old_del.py")

    # Unregistered branch chunk for isolation verification
    await add_hsg_memory(
        "Unlinked branch exclusive token_unlinked_only sandbox note",
        metadata={"file_path": "src/sandbox.py"},
        user_id="proj_hsg:unlinked",
    )

    invalidate_query_cache()

    # 1. Full 3-level query on proj_hsg:feat/x
    res_feat = await hsg_query(
        "architecture auth spec helper module",
        k=20,
        f={"user_id": "proj_hsg:feat/x"},
    )
    feat_contents = "\n".join(r["content"] for r in res_feat)
    assert "token_root_baseline" in feat_contents
    assert "token_dev_keep" in feat_contents
    assert "token_feat_auth_new" in feat_contents
    assert "token_feat_spec_new" in feat_contents
    assert "token_dev_auth_old" not in feat_contents
    assert "token_dev_spec_old" not in feat_contents
    assert "token_dev_old_del" not in feat_contents
    assert "token_unlinked_only" not in feat_contents

    # 2. inherit_base=False returns ONLY feat/x memories
    res_no_inherit = await hsg_query(
        "architecture auth spec helper",
        k=20,
        f={"user_id": "proj_hsg:feat/x", "inherit_base": False},
    )
    assert len(res_no_inherit) >= 2
    assert all(r["user_id"] == "proj_hsg:feat/x" for r in res_no_inherit)
    no_inherit_contents = "\n".join(r["content"] for r in res_no_inherit)
    assert "token_root_baseline" not in no_inherit_contents
    assert "token_dev_keep" not in no_inherit_contents

    # 3. Unregistered branch proj_hsg:unlinked sees ONLY unlinked chunks
    res_unlinked = await hsg_query(
        "sandbox architecture helper",
        k=20,
        f={"user_id": "proj_hsg:unlinked"},
    )
    assert len(res_unlinked) == 1
    assert res_unlinked[0]["user_id"] == "proj_hsg:unlinked"
    assert "token_unlinked_only" in res_unlinked[0]["content"]


@pytest.mark.asyncio
async def test_sync_codebase_memories_git_diff_zero_reembed_for_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = _init_git_repo(tmp_path / "mem_git_repo", initial_branch="develop")

    (repo / "unchanged.py").write_text(
        "def stable_service():\n    return 'token_unchanged_base_file'\n",
        encoding="utf-8",
    )
    (repo / "to_modify.py").write_text(
        "def mutable_service():\n    return 'token_modify_v1_old'\n",
        encoding="utf-8",
    )
    (repo / "to_delete.py").write_text(
        "def doomed_service():\n    return 'token_deleted_from_base'\n",
        encoding="utf-8",
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "base develop files")

    # Initial full memory sync on develop
    base_res = await sync_codebase_memories("grepo:develop", str(repo))
    assert base_res["total_ingested"] == 3
    assert base_res["total_errors"] == 0

    # Branch off to feat/delta: modify to_modify.py, delete to_delete.py, add added.py
    _run_git(repo, "checkout", "-b", "feat/delta")
    (repo / "to_modify.py").write_text(
        "def mutable_service():\n    return 'token_modify_v2_new'\n",
        encoding="utf-8",
    )
    (repo / "to_delete.py").unlink()
    (repo / "added.py").write_text(
        "def brand_new_service():\n    return 'token_added_on_feat'\n",
        encoding="utf-8",
    )
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", "feat/delta changes")

    # Spy on embed_multi_sector and mcp.mem.add to prove unchanged.py triggers 0 embeddings
    import projectbrain.ai.mcp as mcp_mod

    embed_calls: List[str] = []
    orig_embed_multi = hsg_mod.embed_multi_sector

    async def spy_embed_multi(mid: str, txt: str, secs: List[str], chunks: Any = None):
        embed_calls.append(txt)
        return await orig_embed_multi(mid, txt, secs, chunks)

    add_calls: List[str] = []
    orig_mem_add = mcp_mod.mem.add

    async def spy_mem_add(content: str, user_id: Optional[str] = None, **kwargs: Any):
        meta = kwargs.get("meta") or {}
        add_calls.append(meta.get("file_path", ""))
        return await orig_mem_add(content, user_id=user_id, **kwargs)

    monkeypatch.setattr(hsg_mod, "embed_multi_sector", spy_embed_multi)
    monkeypatch.setattr(mcp_mod.mem, "add", spy_mem_add)

    delta_res = await sync_codebase_memories(
        "grepo:feat/delta", str(repo), base_branch="develop"
    )
    assert delta_res["total_ingested"] == 2
    assert delta_res["total_errors"] == 0
    assert set(add_calls) == {"to_modify.py", "added.py"}
    assert len(embed_calls) == 2
    assert all("unchanged.py" not in txt for txt in embed_calls)

    invalidate_query_cache()
    queried = await hsg_query(
        "service stable mutable brand new doomed",
        k=20,
        f={"user_id": "grepo:feat/delta"},
    )
    joined = "\n".join(r["content"] for r in queried)
    assert "token_unchanged_base_file" in joined
    assert "token_modify_v2_new" in joined
    assert "token_added_on_feat" in joined
    assert "token_modify_v1_old" not in joined
    assert "token_deleted_from_base" not in joined


# =====================================================================
# Pillar 4: Document Versioning & Pre-OCR / Pre-Embedding Delta Engine
# =====================================================================


def test_derive_doc_key_normalizes_version_suffixes():
    assert (
        derive_doc_key("SRS_Payment_v1.0.pdf")
        == derive_doc_key("SRS_Payment_v1.1.pdf")
        == "srs_payment"
    )
    assert derive_doc_key("Architecture-ver2.3.1.md") == "architecture"
    assert derive_doc_key("ignored.pdf", explicit_doc_key="Custom Spec Key") == "custom_spec_key"


@pytest.mark.asyncio
async def test_markdown_versioned_delta_reuse_supersede_and_zero_reembed(
    monkeypatch: pytest.MonkeyPatch,
):
    md_v1 = "\n\n".join(
        [
            "# Section 1 Overview\nOverview alpha_sec1 payment gateway architecture.",
            "# Section 2 Ledger\nLedger beta_sec2_v1_old double entry posting rules.",
            "# Section 3 Settlement\nSettlement gamma_sec3 daily batch reconciliation.",
            "# Section 4 Compliance\nCompliance delta_sec4 PCI audit logging policy.",
        ]
    )
    # v1.1: modify Section 2 AND reorder/shift downstream sections (Section 4 before Section 3)
    md_v1_1 = "\n\n".join(
        [
            "# Section 1 Overview\nOverview alpha_sec1 payment gateway architecture.",
            "# Section 2 Ledger\nLedger beta_sec2_v1_1_new real-time streaming posting rules.",
            "# Section 4 Compliance\nCompliance delta_sec4 PCI audit logging policy.",
            "# Section 3 Settlement\nSettlement gamma_sec3 daily batch reconciliation.",
        ]
    )

    embed_spy_calls: List[str] = []
    orig_add_hsg = hsg_mod.add_hsg_memory

    async def spy_add_hsg(content: str, tags: Optional[str] = None, metadata: Any = None, user_id: Optional[str] = None):
        embed_spy_calls.append(content)
        return await orig_add_hsg(content, tags=tags, metadata=metadata, user_id=user_id)

    monkeypatch.setattr(hsg_mod, "add_hsg_memory", spy_add_hsg)

    res_v1 = await ingest_document_versioned(
        filename="SRS_Payment_v1.0.md",
        file_bytes=md_v1.encode("utf-8"),
        project_id="proj_md",
        branch="main",
        doc_version="v1.0",
    )
    assert res_v1["doc_key"] == "srs_payment"
    assert res_v1["total_units"] == 4
    assert res_v1["reused_units"] == 0
    assert res_v1["new_units"] == 4
    assert len(embed_spy_calls) == 4

    embed_spy_calls.clear()

    res_v1_1 = await ingest_document_versioned(
        filename="SRS_Payment_v1.1.md",
        file_bytes=md_v1_1.encode("utf-8"),
        project_id="proj_md",
        branch="main",
        doc_version="v1.1",
    )
    assert res_v1_1["doc_key"] == "srs_payment"
    assert res_v1_1["parent_version"] == "v1.0"
    assert res_v1_1["total_units"] == 4
    assert res_v1_1["reused_units"] == 3
    assert res_v1_1["new_units"] == 1
    assert len(embed_spy_calls) == 1
    assert "beta_sec2_v1_1_new" in embed_spy_calls[0]

    # Verify v1.0 document_versions row is superseded and v1.1 is active
    v1_row = db.fetchone(
        "SELECT status FROM document_versions WHERE project_id=? AND branch=? AND doc_key=? AND version=?",
        ("proj_md", "main", "srs_payment", "v1.0"),
    )
    v1_1_row = db.fetchone(
        "SELECT status FROM document_versions WHERE project_id=? AND branch=? AND doc_key=? AND version=?",
        ("proj_md", "main", "srs_payment", "v1.1"),
    )
    assert v1_row["status"] == "superseded"
    assert v1_1_row["status"] == "active"

    # Verify hsg_query returns 0 stale v1.0 chunks and returns v1.1 chunks cleanly
    hits = await hsg_query(
        "Ledger posting rules overview settlement compliance",
        k=20,
        f={"user_id": "proj_md"},
    )
    hit_versions = {h["metadata"].get("doc_version") for h in hits}
    hit_text = "\n".join(h["content"] for h in hits)
    assert "v1.0" not in hit_versions
    assert "v1.1" in hit_versions
    assert "beta_sec2_v1_old" not in hit_text
    assert "beta_sec2_v1_1_new" in hit_text
    assert "alpha_sec1" in hit_text
    assert "gamma_sec3" in hit_text
    assert "delta_sec4" in hit_text


@pytest.mark.asyncio
async def test_pdf_4page_pre_ocr_and_pre_embedding_delta_reuse(
    monkeypatch: pytest.MonkeyPatch,
):
    # Construct 4 distinct image-only PDF pages in memory
    p0 = _make_single_page_image_pdf((20, 40, 60), seed=1)
    p1_v1 = _make_single_page_image_pdf((80, 100, 120), seed=2)
    p1_v1_1 = _make_single_page_image_pdf((180, 60, 40), seed=99)
    p2 = _make_single_page_image_pdf((40, 140, 80), seed=3)
    p3 = _make_single_page_image_pdf((120, 40, 160), seed=4)

    pdf_v1 = _combine_pdf_pages([p0, p1_v1, p2, p3])
    # v1.1: 1 modified page (p1_v1_1) + shifted downstream pages (p3 before p2)
    pdf_v1_1 = _combine_pdf_pages([p0, p1_v1_1, p3, p2])

    ocr_calls: List[int] = []
    ocr_tag = {"ver": "v1_0"}

    async def spy_ocr_full_pdf_page(file_bytes: bytes, page_index: int) -> str:
        ocr_calls.append(page_index)
        subsystems = ["ingress_gateway", "billing_engine", "ledger_store", "audit_sink"]
        sub = subsystems[page_index % len(subsystems)]
        return (
            f"Scanned architecture diagram {ocr_tag['ver']} page_{page_index + 1} "
            f"subsystem_{sub} unique_marker_{ocr_tag['ver']}_{page_index}"
        )

    embed_calls: List[str] = []
    orig_add_hsg = hsg_mod.add_hsg_memory

    async def spy_add_hsg(content: str, tags: Optional[str] = None, metadata: Any = None, user_id: Optional[str] = None):
        embed_calls.append(content)
        return await orig_add_hsg(content, tags=tags, metadata=metadata, user_id=user_id)

    monkeypatch.setattr(doc_parser, "_ocr_full_pdf_page", spy_ocr_full_pdf_page)
    monkeypatch.setattr(hsg_mod, "add_hsg_memory", spy_add_hsg)

    # Ingest Arch_v1.0.pdf -> 4 OCR calls, 4 embed calls
    res_v1 = await ingest_document_versioned(
        filename="Arch_v1.0.pdf",
        file_bytes=pdf_v1,
        project_id="proj_pdf",
        branch="main",
        doc_version="v1.0",
    )
    assert res_v1["doc_key"] == "arch"
    assert res_v1["total_units"] == 4
    assert res_v1["reused_units"] == 0
    assert res_v1["new_units"] == 4
    assert len(ocr_calls) == 4
    assert len(embed_calls) == 4

    ocr_calls.clear()
    embed_calls.clear()
    ocr_tag["ver"] = "v1_1"

    # Ingest Arch_v1.1.pdf -> 3 reused (0 OCR, 0 embed), 1 changed (1 OCR, 1 embed)
    res_v1_1 = await ingest_document_versioned(
        filename="Arch_v1.1.pdf",
        file_bytes=pdf_v1_1,
        project_id="proj_pdf",
        branch="main",
        doc_version="v1.1",
    )
    assert res_v1_1["doc_key"] == "arch"
    assert res_v1_1["total_units"] == 4
    assert res_v1_1["reused_units"] == 3
    assert res_v1_1["new_units"] == 1
    assert len(ocr_calls) == 1
    assert ocr_calls == [1]
    assert len(embed_calls) == 1
    assert "unique_marker_v1_1_1" in embed_calls[0]


@pytest.mark.asyncio
async def test_cross_branch_document_isolation_develop_vs_feat(
    monkeypatch: pytest.MonkeyPatch,
):
    md_v1 = "\n\n".join(
        [
            "# Intro\nIntro shared_sec_1 platform overview.",
            "# Auth\nAuth token_branch_v1_only session cookie flow.",
            "# Billing\nBilling shared_sec_3 invoice scheduling.",
            "# Ops\nOps shared_sec_4 monitoring runbook.",
        ]
    )
    md_v1_1 = "\n\n".join(
        [
            "# Intro\nIntro shared_sec_1 platform overview.",
            "# Auth\nAuth token_branch_v1_1_feat_only JWT rotation flow.",
            "# Billing\nBilling shared_sec_3 invoice scheduling.",
            "# Ops\nOps shared_sec_4 monitoring runbook.",
        ]
    )

    res_dev = await ingest_document_versioned(
        filename="Platform_Spec_v1.0.md",
        file_bytes=md_v1.encode("utf-8"),
        project_id="proj_xbr",
        branch="develop",
        doc_version="v1.0",
    )
    assert res_dev["total_units"] == 4
    assert res_dev["new_units"] == 4

    embed_spy: List[str] = []
    orig_add_hsg = hsg_mod.add_hsg_memory

    async def spy_add_hsg(content: str, tags: Optional[str] = None, metadata: Any = None, user_id: Optional[str] = None):
        embed_spy.append(content)
        return await orig_add_hsg(content, tags=tags, metadata=metadata, user_id=user_id)

    monkeypatch.setattr(hsg_mod, "add_hsg_memory", spy_add_hsg)

    res_feat = await ingest_document_versioned(
        filename="Platform_Spec_v1.1.md",
        file_bytes=md_v1_1.encode("utf-8"),
        project_id="proj_xbr",
        branch="feat/x",
        base_branch="develop",
        doc_version="v1.1",
    )
    assert res_feat["total_units"] == 4
    assert res_feat["reused_units"] == 3
    assert res_feat["new_units"] == 1
    assert len(embed_spy) == 1
    assert "token_branch_v1_1_feat_only" in embed_spy[0]

    # develop still sees pure v1.0
    dev_hits = await hsg_query(
        "Auth flow intro billing ops",
        k=20,
        f={"user_id": "proj_xbr:develop"},
    )
    dev_text = "\n".join(h["content"] for h in dev_hits)
    assert "token_branch_v1_only" in dev_text
    assert "token_branch_v1_1_feat_only" not in dev_text

    # feat/x sees pure v1.1 with zero v1.0 leak
    feat_hits = await hsg_query(
        "Auth flow intro billing ops",
        k=20,
        f={"user_id": "proj_xbr:feat/x"},
    )
    feat_text = "\n".join(h["content"] for h in feat_hits)
    feat_versions = {h["metadata"].get("doc_version") for h in feat_hits}
    assert "token_branch_v1_1_feat_only" in feat_text
    assert "token_branch_v1_only" not in feat_text
    assert feat_versions == {"v1.1"}


@pytest.mark.asyncio
async def test_document_versioning_rest_and_mcp_endpoints():
    app = create_app()
    client = TestClient(app)

    md_v1 = "# Overview\nInitial overview content.\n\n# API\nEndpoint v1 schema."
    md_v2 = "# Overview\nInitial overview content.\n\n# API\nEndpoint v2 schema with pagination."
    md_feat = "# Overview\nInitial overview content.\n\n# API\nEndpoint feat schema with cursor."

    # 1. POST /sources/upload v1 on develop
    r1 = client.post(
        "/sources/upload",
        data={"project_id": "proj_rest", "branch": "develop"},
        files={"file": ("Spec_API_v1.0.md", io.BytesIO(md_v1.encode("utf-8")), "text/markdown")},
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["doc_key"] == "spec_api"
    assert b1["doc_version"] == "v1"
    assert b1["reused_units"] == 0
    assert b1["new_units"] == 2

    # 2. POST /sources/upload v2 on develop (same-branch update)
    r2 = client.post(
        "/sources/upload",
        data={"project_id": "proj_rest", "branch": "develop"},
        files={"file": ("Spec_API_v1.1.md", io.BytesIO(md_v2.encode("utf-8")), "text/markdown")},
    )
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["doc_key"] == "spec_api"
    assert b2["doc_version"] == "v2"
    assert b2["reused_units"] == 1
    assert b2["new_units"] == 1

    # 3. POST /sources/upload on feat/rest with base_branch=develop (cross-branch)
    r3 = client.post(
        "/sources/upload",
        data={
            "project_id": "proj_rest",
            "branch": "feat/rest",
            "base_branch": "develop",
            "doc_version": "v3-feat",
        },
        files={"file": ("Spec_API_v1.2.md", io.BytesIO(md_feat.encode("utf-8")), "text/markdown")},
    )
    assert r3.status_code == 200, r3.text
    b3 = r3.json()
    assert b3["doc_key"] == "spec_api"
    assert b3["doc_version"] == "v3-feat"
    assert b3["reused_units"] == 1
    assert b3["new_units"] == 1

    # 4. GET /sources/documents
    r_docs = client.get("/sources/documents", params={"project_id": "proj_rest", "branch": "develop"})
    assert r_docs.status_code == 200, r_docs.text
    docs_list = r_docs.json()["documents"]
    assert len(docs_list) == 1
    assert docs_list[0]["doc_key"] == "spec_api"
    assert docs_list[0]["version"] == "v2"

    # 5. GET /sources/documents/{doc_key}/versions
    r_vers = client.get(
        "/sources/documents/spec_api/versions",
        params={"project_id": "proj_rest", "branch": "develop"},
    )
    assert r_vers.status_code == 200, r_vers.text
    vers = r_vers.json()["versions"]
    assert {v["version"] for v in vers} == {"v1", "v2"}

    # 6. GET /sources/documents/{doc_key}/diff (same-branch v1 -> v2)
    r_diff_same = client.get(
        "/sources/documents/spec_api/diff",
        params={
            "project_id": "proj_rest",
            "branch": "develop",
            "from_version": "v1",
            "to_version": "v2",
        },
    )
    assert r_diff_same.status_code == 200, r_diff_same.text
    diff_same = r_diff_same.json()
    assert diff_same["summary"]["unchanged"] == 1
    assert diff_same["summary"]["modified"] == 1

    # 7. GET /sources/documents/{doc_key}/diff (cross-branch develop:v2 -> feat/rest:v3-feat)
    r_diff_xbr = client.get(
        "/sources/documents/spec_api/diff",
        params={
            "project_id": "proj_rest",
            "from_branch": "develop",
            "to_branch": "feat/rest",
            "from_version": "v2",
            "to_version": "v3-feat",
        },
    )
    assert r_diff_xbr.status_code == 200, r_diff_xbr.text
    diff_xbr = r_diff_xbr.json()
    assert diff_xbr["from_branch"] == "develop"
    assert diff_xbr["to_branch"] == "feat/rest"
    assert diff_xbr["summary"]["unchanged"] == 1
    assert diff_xbr["summary"]["modified"] == 1

    # 8. 404 on nonexistent version
    r_404 = client.get(
        "/sources/documents/spec_api/diff",
        params={
            "project_id": "proj_rest",
            "branch": "develop",
            "from_version": "v1",
            "to_version": "v999",
        },
    )
    assert r_404.status_code == 404

    # 9. MCP tools: projectbrain_list_document_versions & projectbrain_diff_document_versions
    mcp_list_raw = await projectbrain_list_document_versions(
        doc_key="spec_api", project_id="proj_rest", branch="develop"
    )
    mcp_list = json.loads(mcp_list_raw)
    assert mcp_list["doc_key"] == "spec_api"
    assert {v["version"] for v in mcp_list["versions"]} == {"v1", "v2"}

    mcp_diff_raw = await projectbrain_diff_document_versions(
        doc_key="spec_api",
        from_version="v2",
        to_version="v3-feat",
        project_id="proj_rest",
        from_branch="develop",
        to_branch="feat/rest",
    )
    mcp_diff = json.loads(mcp_diff_raw)
    assert mcp_diff["from_branch"] == "develop"
    assert mcp_diff["to_branch"] == "feat/rest"
    assert mcp_diff["summary"]["unchanged"] == 1
    assert mcp_diff["summary"]["modified"] == 1


def test_db_translate_query_postgres_upsert_for_overlay_and_doc_versioning_tables():
    pg_db = DB()
    pg_db.is_postgres = True

    cases = [
        (
            "INSERT OR REPLACE INTO memory_branch_links(project_id, branch, base_branch, updated_at) VALUES (?,?,?,?)",
            "ON CONFLICT (project_id, branch) DO UPDATE SET base_branch=EXCLUDED.base_branch, updated_at=EXCLUDED.updated_at",
        ),
        (
            "INSERT OR REPLACE INTO memory_overlay_tombstones(project_id, branch, target_type, target_key, created_at) VALUES (?,?,?,?,?)",
            "ON CONFLICT (project_id, branch, target_type, target_key) DO UPDATE SET created_at=EXCLUDED.created_at",
        ),
        (
            "INSERT OR REPLACE INTO document_versions(id, project_id, branch, doc_key, version, parent_version, base_branch, filename, file_sha256, total_units, reused_units, new_units, deleted_units, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            "ON CONFLICT (project_id, branch, doc_key, version) DO UPDATE SET",
        ),
        (
            "INSERT OR REPLACE INTO document_units(id, project_id, branch, doc_key, version, unit_index, unit_type, unit_title, unit_sha256, markdown_text, memory_ids_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            "ON CONFLICT (id) DO UPDATE SET",
        ),
    ]

    for sqlite_sql, expected_conflict in cases:
        translated = pg_db.translate_query(sqlite_sql)
        assert "INSERT OR REPLACE" not in translated
        assert "?" not in translated
        assert "%s" in translated
        assert expected_conflict in translated
