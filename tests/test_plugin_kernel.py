"""Comprehensive verification & unit test suite for Project-Brain v3.0 Plugin Microkernel & Storage Plugins (TASK-14A)."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

from projectbrain.graphify_engine.models import GraphDelta, GraphEdge, GraphNode
from projectbrain.plugins import (
    BasePlugin,
    GraphBackendPlugin,
    PluginCategory,
    PluginMetadata,
    PluginRegistry,
    get_default_registry,
    reset_default_registry,
)
from projectbrain.plugins.builtins import get_all_builtin_plugins
from projectbrain.plugins.builtins.storage_plugins import (
    HSGMemoryPlugin,
    Neo4jGraphPlugin,
    Neo4jGraphStore,
    PostgresSchemaGraphPlugin,
    QdrantVectorPlugin,
    SqliteOverlayGraphPlugin,
    build_sparse_bm25_vector,
)


# ---------------------------------------------------------------------------
# 1. Built-in Discovery Across All 9 Categories (25 Plugins Total)
# ---------------------------------------------------------------------------
def test_discover_all_25_builtins_across_all_categories() -> None:
    all_instances = get_all_builtin_plugins()
    assert len(all_instances) == 25

    reg = PluginRegistry()
    loaded_count = reg.discover_builtins()
    assert loaded_count == 25

    expected_by_category = {
        PluginCategory.GRAPH_BACKEND.value: {
            "graph.sqlite_overlay",
            "graph.postgres_schema",
            "graph.neo4j",
        },
        PluginCategory.VECTOR_BACKEND.value: {
            "vector.hsg_memory",
            "vector.qdrant",
        },
        PluginCategory.ETL_PARSER.value: {
            "etl.graphify_ast",
            "etl.multimodal_doc",
            "etl.legacy_testcase",
            "etl.hardware_datasheet",
            "etl.openapi31",
            "etl.cpp_embedded",
        },
        PluginCategory.KNOWLEDGE_LINKER.value: {
            "linker.code_doc",
            "linker.state_flow",
        },
        PluginCategory.DOC_EXPORTER.value: {
            "exporter.wiki",
            "exporter.docx",
            "exporter.xlsx",
            "exporter.pptx",
        },
        PluginCategory.SCRIPT_CACHE.value: {
            "cache.sqlite_pg",
            "cache.redis",
        },
        PluginCategory.ARTIFACT_STORE.value: {
            "artifacts.local_sqlite",
            "artifacts.minio_clickhouse",
        },
        PluginCategory.BRAIN_CONSUMER.value: {
            "brain.planner",
            "brain.oracle",
            "brain.rca",
        },
        PluginCategory.WEBHOOK.value: {
            "webhook.event_ingest",
        },
    }

    listed = reg.list_plugins()
    assert len(listed) == 25

    for category, expected_ids in expected_by_category.items():
        cat_plugins = reg.list_plugins(category=category)
        assert {p["plugin_id"] for p in cat_plugins} == expected_ids

    # Re-running discover_builtins with override=True cleanly replaces all 25
    reloaded_count = reg.discover_builtins(override=True)
    assert reloaded_count == 25
    assert len(reg.list_plugins()) == 25


def test_default_registry_singleton_lifecycle() -> None:
    reset_default_registry()
    try:
        reg1 = get_default_registry()
        reg2 = get_default_registry()
        assert reg1 is reg2
        assert len(reg1.list_plugins()) == 25
    finally:
        reset_default_registry()


# ---------------------------------------------------------------------------
# 2. External Directory Discovery, Manifests, requires_env & Hot-Reload
# ---------------------------------------------------------------------------
def test_directory_discovery_manifest_and_reload_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins_dir = tmp_path / "ext_plugins"
    pkg_dir = plugins_dir / "custom_exporter"
    pkg_dir.mkdir(parents=True)

    (pkg_dir / "plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "exporter.custom_json",
                "name": "Custom JSON Exporter",
                "version": "1.0.0",
                "category": "doc_exporter",
                "entrypoint": "plugin.py",
                "class_name": "CustomJsonExporterPlugin",
                "priority": 150,
                "default_config": {"indent": 2},
                "default_enabled": True,
            }
        ),
        encoding="utf-8",
    )

    plugin_py = pkg_dir / "plugin.py"
    plugin_py.write_text(
        "from projectbrain.plugins.base import DocExporterPlugin, PluginMetadata\n"
        "SHUTDOWN_CALLED = []\n"
        "class CustomJsonExporterPlugin(DocExporterPlugin):\n"
        "    metadata = PluginMetadata(\n"
        "        plugin_id='exporter.custom_json',\n"
        "        name='Custom JSON Exporter',\n"
        "        version='1.0.0',\n"
        "        category='doc_exporter',\n"
        "        priority=150,\n"
        "    )\n"
        "    export_format = 'json_v1'\n"
        "    def shutdown(self) -> None:\n"
        "        SHUTDOWN_CALLED.append('v1')\n"
        "        super().shutdown()\n"
        "    def export(self, graph_data, output_path, linker_index=None, context=None):\n"
        "        return {'version': 'v1', 'indent': self.config.get('indent'), 'output_path': output_path}\n",
        encoding="utf-8",
    )

    # Standalone *.py plugin using register_plugins(registry) + requires_env
    standalone_py = plugins_dir / "env_gated_webhook.py"
    standalone_py.write_text(
        "from projectbrain.plugins.base import WebhookPlugin, PluginMetadata\n"
        "class EnvGatedWebhook(WebhookPlugin):\n"
        "    metadata = PluginMetadata(\n"
        "        plugin_id='webhook.env_gated',\n"
        "        name='Env Gated Webhook',\n"
        "        category='webhook',\n"
        "        priority=120,\n"
        "        requires_env=['PB_TEST_WEBHOOK_SECRET'],\n"
        "    )\n"
        "    def verify_signature(self, headers, raw_body, secret=None) -> bool:\n"
        "        return True\n"
        "    async def parse_event(self, headers, payload):\n"
        "        return {'status': 'ok'}\n"
        "def register_plugins(registry):\n"
        "    registry.register(EnvGatedWebhook())\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("PB_TEST_WEBHOOK_SECRET", raising=False)
    reg = PluginRegistry()
    loaded = reg.discover_directory(plugins_dir)
    assert set(loaded) == {"exporter.custom_json", "webhook.env_gated"}

    exp_v1 = reg.resolve(PluginCategory.DOC_EXPORTER)
    assert exp_v1.metadata.plugin_id == "exporter.custom_json"
    res_v1 = exp_v1.export({}, str(tmp_path / "out.json"))
    assert res_v1 == {"version": "v1", "indent": 2, "output_path": str(tmp_path / "out.json")}

    # Gated plugin is disabled because PB_TEST_WEBHOOK_SECRET is unset
    assert reg.is_enabled("webhook.env_gated") is False

    # Modify plugin.py on disk + set env var, then hot-reload directory
    monkeypatch.setenv("PB_TEST_WEBHOOK_SECRET", "s3cr3t")
    plugin_py.write_text(
        "from projectbrain.plugins.base import DocExporterPlugin, PluginMetadata\n"
        "class CustomJsonExporterPlugin(DocExporterPlugin):\n"
        "    metadata = PluginMetadata(\n"
        "        plugin_id='exporter.custom_json',\n"
        "        name='Custom JSON Exporter',\n"
        "        version='2.0.0',\n"
        "        category='doc_exporter',\n"
        "        priority=150,\n"
        "    )\n"
        "    export_format = 'json_v2'\n"
        "    def export(self, graph_data, output_path, linker_index=None, context=None):\n"
        "        return {'version': 'v2', 'indent': self.config.get('indent'), 'output_path': output_path}\n",
        encoding="utf-8",
    )

    reloaded = reg.reload_directory(plugins_dir)
    assert set(reloaded) == {"exporter.custom_json", "webhook.env_gated"}
    exp_v2 = reg.resolve(PluginCategory.DOC_EXPORTER)
    assert exp_v2.export_format == "json_v2"
    assert exp_v2.export({}, str(tmp_path / "out2.json"))["version"] == "v2"
    assert reg.is_enabled("webhook.env_gated") is True


# ---------------------------------------------------------------------------
# 3. Entrypoint Discovery Mock (group="projectbrain.plugins")
# ---------------------------------------------------------------------------
def test_discover_entrypoints_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EpPluginA(BasePlugin):
        metadata = PluginMetadata(
            plugin_id="custom.ep_class",
            name="EP Class Plugin",
            category=PluginCategory.WEBHOOK.value,
            priority=80,
        )

    class _EpPluginB(BasePlugin):
        metadata = PluginMetadata(
            plugin_id="custom.ep_instance",
            name="EP Instance Plugin",
            category=PluginCategory.WEBHOOK.value,
            priority=90,
        )

    class _EpPluginC(BasePlugin):
        metadata = PluginMetadata(
            plugin_id="custom.ep_callable",
            name="EP Callable Plugin",
            category=PluginCategory.WEBHOOK.value,
            priority=95,
        )

    class _FakeEP:
        def __init__(self, name: str, group: str, obj: Any, fail: bool = False) -> None:
            self.name = name
            self.group = group
            self._obj = obj
            self._fail = fail

        def load(self) -> Any:
            if self._fail:
                raise RuntimeError("simulated broken entrypoint")
            return self._obj

    class _FakeEntryPoints:
        def __init__(self, eps: List[_FakeEP]) -> None:
            self._eps = eps

        def select(self, *, group: str) -> List[_FakeEP]:
            return [ep for ep in self._eps if ep.group == group]

    def _register_callable(registry: PluginRegistry) -> None:
        registry.register(_EpPluginC())

    fake_eps = _FakeEntryPoints(
        [
            _FakeEP("ep_a", "projectbrain.plugins", _EpPluginA),
            _FakeEP("ep_b", "projectbrain.plugins", _EpPluginB()),
            _FakeEP("ep_c", "projectbrain.plugins", _register_callable),
            _FakeEP("ep_bad", "projectbrain.plugins", None, fail=True),
            _FakeEP("ep_other", "other.group", _EpPluginA),
        ]
    )
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: fake_eps)

    reg = PluginRegistry()
    loaded = reg.discover_entrypoints("projectbrain.plugins")
    assert set(loaded) == {"custom.ep_class", "custom.ep_instance", "custom.ep_callable"}
    assert reg.resolve(PluginCategory.WEBHOOK).metadata.plugin_id == "custom.ep_callable"


# ---------------------------------------------------------------------------
# 4. Per-Project Overrides, DB Persistence, Config Files & Health Report
# ---------------------------------------------------------------------------
def test_per_project_overrides_persistence_and_health_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "registry.db"
    reg = PluginRegistry(db_path=db_file)
    reg.discover_builtins()

    # Default graph backend resolution -> graph.sqlite_overlay (priority=100)
    assert reg.resolve(PluginCategory.GRAPH_BACKEND).metadata.plugin_id == "graph.sqlite_overlay"

    # Project A disables sqlite_overlay and boosts neo4j priority above postgres_schema
    reg.disable("graph.sqlite_overlay", project_id="proj-a")
    reg.configure(
        "graph.neo4j",
        {"mode": "memory", "uri": "http://neo4j.internal:7474"},
        project_id="proj-a",
        priority=150,
    )

    # Project A resolves neo4j; Project B / global still resolves sqlite_overlay
    assert reg.resolve(PluginCategory.GRAPH_BACKEND, project_id="proj-a").metadata.plugin_id == "graph.neo4j"
    assert reg.resolve(PluginCategory.GRAPH_BACKEND, project_id="proj-b").metadata.plugin_id == "graph.sqlite_overlay"
    assert reg.resolve(PluginCategory.GRAPH_BACKEND).metadata.plugin_id == "graph.sqlite_overlay"
    assert reg.get_effective_config("graph.neo4j", project_id="proj-a")["uri"] == "http://neo4j.internal:7474"
    assert "uri" not in reg.get_effective_config("graph.neo4j", project_id="proj-b")

    # Preferred ID wins when enabled, falls back when disabled for project
    assert (
        reg.resolve(PluginCategory.GRAPH_BACKEND, preferred_id="graph.postgres_schema", project_id="proj-a")
        .metadata.plugin_id
        == "graph.postgres_schema"
    )
    assert (
        reg.resolve(PluginCategory.GRAPH_BACKEND, preferred_id="graph.sqlite_overlay", project_id="proj-a")
        .metadata.plugin_id
        == "graph.neo4j"
    )

    # Re-open registry from same SQLite DB to verify persistence
    reg.close()
    reg_reloaded = PluginRegistry(db_path=db_file)
    reg_reloaded.discover_builtins()
    assert reg_reloaded.is_enabled("graph.sqlite_overlay", project_id="proj-a") is False
    assert (
        reg_reloaded.resolve(PluginCategory.GRAPH_BACKEND, project_id="proj-a").metadata.plugin_id
        == "graph.neo4j"
    )

    # Disable all vector backends for proj-c -> LookupError
    reg_reloaded.disable("vector.hsg_memory", project_id="proj-c")
    reg_reloaded.disable("vector.qdrant", project_id="proj-c")
    with pytest.raises(LookupError):
        reg_reloaded.resolve(PluginCategory.VECTOR_BACKEND, project_id="proj-c")

    # Env var override check
    monkeypatch.setenv("PB_DISABLED_PLUGINS", "vector.hsg_memory")
    assert reg_reloaded.resolve(PluginCategory.VECTOR_BACKEND).metadata.plugin_id == "vector.qdrant"
    monkeypatch.delenv("PB_DISABLED_PLUGINS", raising=False)

    # Health report reflects degraded when an enabled plugin reports unhealthy
    class _FlakyPlugin(BasePlugin):
        metadata = PluginMetadata(
            plugin_id="custom.flaky",
            name="Flaky Plugin",
            category=PluginCategory.SOURCE_CONNECTOR.value,
        )

        def health_check(self) -> Dict[str, Any]:
            return {"plugin_id": self.metadata.plugin_id, "category": self.metadata.category, "status": "unhealthy"}

    reg_reloaded.register(_FlakyPlugin())
    hr_global = reg_reloaded.health_report()
    assert hr_global["status"] == "degraded"
    assert hr_global["total_plugins"] == 26
    assert hr_global["plugins"]["custom.flaky"]["status"] == "unhealthy"

    # Disabling the flaky plugin for proj-clean keeps proj-clean healthy
    reg_reloaded.disable("custom.flaky", project_id="proj-clean")
    hr_clean = reg_reloaded.health_report(project_id="proj-clean")
    assert hr_clean["status"] == "healthy"
    reg_reloaded.close()


def test_load_config_file_toml(tmp_path: Path) -> None:
    cfg_path = tmp_path / "projectbrain.toml"
    cfg_path.write_text(
        '[plugins]\n'
        'disabled = ["graph.postgres_schema"]\n'
        '\n'
        '[plugins."graph.neo4j"]\n'
        'priority = 120\n'
        'mode = "memory"\n'
        '\n'
        '[plugins.projects.proj_x."graph.sqlite_overlay"]\n'
        'enabled = false\n',
        encoding="utf-8",
    )
    reg = PluginRegistry()
    reg.discover_builtins()
    reg.load_config_file(cfg_path)

    assert reg.is_enabled("graph.postgres_schema") is False
    assert reg.is_enabled("graph.sqlite_overlay", project_id="proj_x") is False
    assert reg.resolve(PluginCategory.GRAPH_BACKEND, project_id="proj_x").metadata.plugin_id == "graph.neo4j"


# ---------------------------------------------------------------------------
# 5. SqliteOverlayGraphPlugin & PostgresSchemaGraphPlugin
# ---------------------------------------------------------------------------
def test_sqlite_overlay_graph_plugin_and_flow_query(tmp_path: Path) -> None:
    plugin = SqliteOverlayGraphPlugin(config={"db_path": str(tmp_path / "graph_overlay.db")})
    plugin.initialize()
    store = plugin.create_store()
    store.ensure_root_schema()

    delta_main = GraphDelta(
        project_id="proj-iot",
        branch="main",
        nodes=[
            GraphNode(
                id="state:boot",
                project_id="proj-iot",
                label="BOOT",
                file_type="state",
                source_file="fw/boot.md",
                metadata={"version_tag": "fw-v1.0.0"},
            ),
            GraphNode(
                id="state:ready",
                project_id="proj-iot",
                label="READY",
                file_type="state",
                source_file="fw/boot.md",
                metadata={"version_tag": "fw-v1.0.0"},
            ),
            GraphNode(
                id="screen:home_v2",
                project_id="proj-iot",
                label="HOME_V2",
                file_type="screen",
                source_file="app/screens.md",
                metadata={"version_tag": "app-v2.0.0"},
            ),
            GraphNode(
                id="code:init",
                project_id="proj-iot",
                label="init_hw",
                file_type="code",
                source_file="fw/init.c",
            ),
        ],
        edges=[
            GraphEdge(
                project_id="proj-iot",
                source="state:boot",
                target="state:ready",
                relation="transitions_to",
                source_file="fw/boot.md",
            )
        ],
    )
    store.sync_branch_delta("proj-iot", "main", delta_main)

    flow_v1 = plugin.query_flow_graph("proj-iot", branch="main", version_tag="fw-v1.0.0", store=store)
    assert {n["id"] for n in flow_v1["nodes"]} == {"state:boot", "state:ready"}
    assert len(flow_v1["edges"]) == 1
    assert flow_v1["edges"][0]["relation"] == "transitions_to"

    # Branch overlay delta
    store.ensure_branch_schema("proj-iot", "feat/ble")
    store.sync_branch_delta(
        "proj-iot",
        "feat/ble",
        GraphDelta(
            project_id="proj-iot",
            branch="feat/ble",
            nodes=[
                GraphNode(
                    id="state:ble_pair",
                    project_id="proj-iot",
                    label="BLE_PAIR",
                    file_type="state",
                    source_file="fw/ble.md",
                    metadata={"version_tag": "fw-v1.0.0"},
                )
            ],
            edges=[
                GraphEdge(
                    project_id="proj-iot",
                    source="state:ready",
                    target="state:ble_pair",
                    relation="transitions_to",
                    source_file="fw/ble.md",
                )
            ],
        ),
        partial=True,
    )
    diff = store.diff_branch_vs_root("proj-iot", "feat/ble")
    assert diff.summary.new_nodes_count == 1
    assert diff.summary.new_edges_count == 1
    flow_feat = plugin.query_flow_graph("proj-iot", branch="feat/ble", version_tag="fw-v1.0.0", store=store)
    assert {n["id"] for n in flow_feat["nodes"]} == {"state:boot", "state:ready", "state:ble_pair"}

    pg_plugin = PostgresSchemaGraphPlugin()
    assert pg_plugin.metadata.plugin_id == "graph.postgres_schema"
    assert pg_plugin.metadata.priority == 90


# ---------------------------------------------------------------------------
# 6. Neo4jGraphPlugin (mode="memory" + mock HTTP Cypher transport)
# ---------------------------------------------------------------------------
def test_neo4j_graph_plugin_memory_mode() -> None:
    plugin = Neo4jGraphPlugin(config={"mode": "memory"})
    store = plugin.create_store()
    assert isinstance(store, Neo4jGraphStore)

    store.sync_branch_delta(
        "proj-neo",
        "main",
        GraphDelta(
            project_id="proj-neo",
            branch="main",
            nodes=[
                GraphNode(
                    id="state:idle",
                    project_id="proj-neo",
                    label="IDLE",
                    file_type="state",
                    source_file="flow.md",
                    metadata={"version_tag": "fw-v1.2.0"},
                ),
                GraphNode(
                    id="state:active",
                    project_id="proj-neo",
                    label="ACTIVE",
                    file_type="state",
                    source_file="flow.md",
                    metadata={"version_tag": "fw-v1.2.0"},
                ),
            ],
            edges=[
                GraphEdge(
                    project_id="proj-neo",
                    source="state:idle",
                    target="state:active",
                    relation="transitions_to",
                    source_file="flow.md",
                )
            ],
        ),
    )

    flow = plugin.query_flow_graph("proj-neo", branch="main", version_tag="fw-v1.2.0", store=store)
    assert flow["cypher_executed"] is False
    assert {n["id"] for n in flow["nodes"]} == {"state:idle", "state:active"}
    assert len(flow["edges"]) == 1

    # Branch, diff, promote, stats
    store.ensure_branch_schema("proj-neo", "feat/x")
    store.sync_branch_delta(
        "proj-neo",
        "feat/x",
        GraphDelta(
            project_id="proj-neo",
            branch="feat/x",
            nodes=[
                GraphNode(
                    id="screen:settings",
                    project_id="proj-neo",
                    label="SETTINGS",
                    file_type="screen",
                    source_file="ui.md",
                    metadata={"version_tag": "fw-v1.2.0"},
                )
            ],
        ),
        partial=True,
    )
    assert store.get_stats("proj-neo", "feat/x")["node_count"] == 3
    promo = store.promote_branch_to_root("proj-neo", "feat/x")
    assert promo["status"] == "promoted"
    assert store.get_stats("proj-neo", "main")["node_count"] == 3
    assert "proj-neo" in store.list_projects()
    vis = store.to_legacy_visjs("proj-neo", "main")
    assert len(vis["nodes"]) == 3


def test_neo4j_graph_plugin_mock_http_cypher_transport() -> None:
    captured_requests: List[Dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append({"url": str(request.url), "body": body})
        stmt = body["statements"][0]["statement"]
        if "RETURN s AS node" in stmt:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "columns": ["node", "transitions", "target"],
                            "data": [
                                {
                                    "row": [
                                        {"id": "state:boot", "label": "BOOT", "version_tag": "fw-v2.0.0"},
                                        [{"relation": "transitions_to", "trigger": "BOOT_OK"}],
                                        {"id": "state:paired", "label": "PAIRED", "version_tag": "fw-v2.0.0"},
                                    ]
                                }
                            ],
                        }
                    ],
                    "errors": [],
                },
            )
        return httpx.Response(200, json={"results": [], "errors": []})

    transport = httpx.MockTransport(handler)
    plugin = Neo4jGraphPlugin(
        config={
            "mode": "http",
            "uri": "http://neo4j.mock:7474",
            "database": "neo4j",
            "username": "neo4j",
            "password": "secret",
            "transport": transport,
        }
    )
    store = plugin.create_store()
    try:
        store.ensure_root_schema()
        store.sync_branch_delta(
            "proj-http",
            "main",
            GraphDelta(
                project_id="proj-http",
                branch="main",
                nodes=[
                    GraphNode(
                        id="state:boot",
                        project_id="proj-http",
                        label="BOOT",
                        file_type="state",
                        source_file="flow.md",
                        metadata={"version_tag": "fw-v2.0.0"},
                    ),
                    GraphNode(
                        id="state:paired",
                        project_id="proj-http",
                        label="PAIRED",
                        file_type="state",
                        source_file="flow.md",
                        metadata={"version_tag": "fw-v2.0.0"},
                    ),
                ],
                edges=[
                    GraphEdge(
                        project_id="proj-http",
                        source="state:boot",
                        target="state:paired",
                        relation="transitions_to",
                    )
                ],
            ),
        )
        flow = plugin.query_flow_graph("proj-http", branch="main", version_tag="fw-v2.0.0", store=store)
        assert flow["cypher_executed"] is True
        assert {n["id"] for n in flow["nodes"]} == {"state:boot", "state:paired"}
        assert flow["edges"] == [{"relation": "transitions_to", "trigger": "BOOT_OK"}]
        assert len(captured_requests) == 4  # constraint + nodes + edges + flow query
        assert all(r["url"] == "http://neo4j.mock:7474/db/neo4j/tx/commit" for r in captured_requests)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 7. HSGMemoryPlugin (Offline Memory Mode + Live Delegation Smoke)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hsg_memory_plugin_memory_mode() -> None:
    plugin = HSGMemoryPlugin(config={"mode": "memory"})
    up = await plugin.upsert_vectors(
        [
            {
                "id": "mem-1",
                "text": "UART timeout recovery AT+RST watchdog reset",
                "project_id": "proj-1",
                "branch": "main",
                "version_tag": "fw-v1.0",
            },
            {
                "id": "mem-2",
                "text": "Android BLE pairing screen button tap",
                "project_id": "proj-1",
                "branch": "feat/ui",
                "version_tag": "app-v2.0",
            },
        ],
        namespace="docs",
    )
    assert up["upserted"] == 2

    hits = await plugin.search(
        "UART watchdog AT+RST",
        top_k=5,
        filters={"project_id": "proj-1", "branch": "main", "version_tag": "fw-v1.0"},
    )
    assert len(hits) == 1
    assert hits[0]["id"] == "mem-1"
    assert hits[0]["score"] > 0.0

    deleted = await plugin.delete_by_filter({"project_id": "proj-1", "branch": "main"})
    assert deleted["deleted"] == 1
    assert await plugin.search("UART", filters={"project_id": "proj-1", "branch": "main"}) == []


# ---------------------------------------------------------------------------
# 8. QdrantVectorPlugin (Hybrid BM25 + Dense Memory Mode & Mock HTTP REST)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_qdrant_vector_plugin_hybrid_bm25_dense_memory_mode() -> None:
    sparse = build_sparse_bm25_vector("AT+BLE_PAIR CFSR_0x8200")
    assert len(sparse["indices"]) == 3
    assert len(sparse["values"]) == 3

    plugin = QdrantVectorPlugin(config={"mode": "memory"})
    up = await plugin.upsert_vectors(
        [
            {
                "id": "q-1",
                "text": "Firmware AT+BLE_PAIR command failed with CFSR_0x8200 bus fault",
                "project_id": "proj-q",
                "branch": "main",
                "version_tag": "fw-v1.2",
            },
            {
                "id": "q-2",
                "text": "General Bluetooth wireless pairing guide for mobile users",
                "project_id": "proj-q",
                "branch": "main",
                "version_tag": "fw-v1.2",
            },
            {
                "id": "q-3",
                "text": "AT+BLE_PAIR legacy v1.0 behavior",
                "project_id": "proj-q",
                "branch": "main",
                "version_tag": "fw-v1.0",
            },
        ]
    )
    assert up["mode"] == "memory"
    assert up["upserted"] == 3

    # Hybrid BM25 + Dense filtered by version_tag="fw-v1.2"
    hits = await plugin.search(
        "AT+BLE_PAIR CFSR_0x8200",
        top_k=5,
        filters={"project_id": "proj-q", "version_tag": "fw-v1.2"},
        hybrid_alpha=0.5,
    )
    assert [h["id"] for h in hits] == ["q-1", "q-2"]
    assert hits[0]["sparse_score"] > hits[1]["sparse_score"]
    assert hits[0]["score"] > hits[1]["score"]

    deleted = await plugin.delete_by_filter({"version_tag": "fw-v1.0"})
    assert deleted["deleted"] == 1


@pytest.mark.asyncio
async def test_qdrant_vector_plugin_mock_http_rest() -> None:
    calls: List[Dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "api_key": request.headers.get("api-key"),
                "payload": payload,
            }
        )
        if request.url.path.endswith("/points/query"):
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": [
                            {
                                "id": "pt-42",
                                "score": 0.94,
                                "payload": {
                                    "text": "HardFault CFSR 0x8200",
                                    "project_id": "proj-rest",
                                    "version_tag": "fw-v3.0",
                                },
                            }
                        ]
                    }
                },
            )
        return httpx.Response(200, json={"result": {"status": "completed"}, "status": "ok"})

    transport = httpx.MockTransport(handler)
    plugin = QdrantVectorPlugin(
        config={
            "mode": "http",
            "url": "http://qdrant.mock:6333",
            "collection": "pb_test_col",
            "api_key": "qdrant-key-123",
            "transport": transport,
        }
    )

    up = await plugin.upsert_vectors(
        [
            {
                "id": "pt-42",
                "text": "HardFault CFSR 0x8200",
                "project_id": "proj-rest",
                "version_tag": "fw-v3.0",
            }
        ]
    )
    assert up["mode"] == "http"
    assert up["upserted"] == 1

    results = await plugin.search(
        "HardFault CFSR",
        top_k=3,
        filters={"project_id": "proj-rest", "version_tag": "fw-v3.0"},
    )
    assert len(results) == 1
    assert results[0]["id"] == "pt-42"
    assert results[0]["score"] == pytest.approx(0.94)

    del_res = await plugin.delete_by_filter({"project_id": "proj-rest"})
    assert del_res["mode"] == "http"

    assert [c["method"] for c in calls] == ["PUT", "POST", "POST"]
    assert all(c["api_key"] == "qdrant-key-123" for c in calls)
    query_payload = calls[1]["payload"]
    assert query_payload["query"] == {"fusion": "rrf"}
    assert [p["using"] for p in query_payload["prefetch"]] == ["dense", "bm25"]
    assert query_payload["filter"] == {
        "must": [
            {"key": "project_id", "match": {"value": "proj-rest"}},
            {"key": "version_tag", "match": {"value": "fw-v3.0"}},
        ]
    }
