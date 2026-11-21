"""Unit tests for Layer 1 Verified UDCP Script Cache & Synchronized Telemetry Artifact Plugins."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import httpx
import pytest

from projectbrain.plugins.builtins.cache_and_artifact_plugins import (
    MS_PER_DAY,
    SUPPORTED_ARTIFACT_TYPES,
    LocalSqliteArtifactStorePlugin,
    MinioClickHouseArtifactStorePlugin,
    RedisScriptCachePlugin,
    SqlitePgScriptCachePlugin,
    TelemetryArtifactStorePlugin,
    UDCPScriptCacheEntry,
    TelemetryArtifactRecord,
    VerifiedUDCPScriptCachePlugin,
    get_plugins,
    register_plugins,
)


class _ManualClock:
    def __init__(self, initial_ms: int = 1_700_000_000_000):
        self.now_ms = initial_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance_ms(self, delta_ms: int) -> int:
        self.now_ms += delta_ms
        return self.now_ms

    def advance_seconds(self, delta_sec: int) -> int:
        return self.advance_ms(delta_sec * 1000)

    def advance_days(self, days: int) -> int:
        return self.advance_ms(days * MS_PER_DAY)


class _MockRegistry:
    def __init__(self):
        self.plugins: Dict[str, Any] = {}

    def register(self, plugin: Any) -> None:
        self.plugins[plugin.metadata.plugin_id] = plugin


class _MockRedisClient:
    def __init__(self):
        self.kv: Dict[str, str] = {}
        self.ttls: Dict[str, Optional[int]] = {}

    def get(self, key: str) -> Optional[str]:
        return self.kv.get(key)

    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        self.kv[key] = value
        self.ttls[key] = ex
        return True

    def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self.kv:
                del self.kv[k]
                count += 1
        return count

    def keys(self, pattern: str = "*") -> List[str]:
        return [k for k in self.kv if fnmatch.fnmatch(k, pattern)]


# ---------------------------------------------------------------------------
# Registration & Alias Tests
# ---------------------------------------------------------------------------
def test_plugin_exports_aliases_and_registration():
    assert VerifiedUDCPScriptCachePlugin is SqlitePgScriptCachePlugin
    assert TelemetryArtifactStorePlugin is LocalSqliteArtifactStorePlugin

    plugins = get_plugins()
    ids = [p.metadata.plugin_id for p in plugins]
    assert ids == [
        "cache.sqlite_pg",
        "cache.redis",
        "artifacts.local_sqlite",
        "artifacts.minio_clickhouse",
    ]

    reg = _MockRegistry()
    registered = register_plugins(reg)
    assert set(registered) == set(ids)
    for plugin in plugins:
        plugin.initialize()
        hc = plugin.health_check()
        assert hc["status"] == "healthy"
        assert hc["initialized"] is True
        plugin.shutdown()


# ---------------------------------------------------------------------------
# 1. SqlitePgScriptCachePlugin Tests
# ---------------------------------------------------------------------------
def test_sqlite_pg_store_lookup_and_upsert(tmp_path: Path):
    db_file = tmp_path / "script_cache.db"
    plugin = SqlitePgScriptCachePlugin(db_path=db_file)
    plugin.initialize()

    entry = UDCPScriptCacheEntry(
        cache_id="",
        project_id="proj-alpha",
        intent_id="TC-LOGIN-01",
        platform="android",
        build_version="3.0.1",
        screen_hash="scr_abc123",
        ui_fingerprint={"activity": "LoginActivity", "btn_id": "btn_submit"},
        udcp_commands=[{"action": "tap", "target": "btn_submit"}],
    )
    stored = plugin.store_script(entry)
    assert stored["cache_id"]
    assert len(stored["cache_id"]) == 24
    assert stored["invalidated"] is False

    # Lookup exact with screen_hash
    found = plugin.lookup_script(
        project_id="proj-alpha",
        intent_id="TC-LOGIN-01",
        platform="android",
        build_version="3.0.1",
        screen_hash="scr_abc123",
    )
    assert found is not None
    assert found["cache_id"] == stored["cache_id"]
    assert found["udcp_commands"] == [{"action": "tap", "target": "btn_submit"}]

    # Lookup omitting screen_hash using ui_fingerprint tolerance
    found_fp = plugin.lookup_script(
        project_id="proj-alpha",
        intent_id="TC-LOGIN-01",
        platform="android",
        build_version="3.0.1",
        ui_fingerprint={"activity": "LoginActivity", "btn_id": "btn_submit"},
    )
    assert found_fp is not None
    assert found_fp["cache_id"] == stored["cache_id"]

    # Upsert via ANSI ON CONFLICT(cache_id) DO UPDATE
    updated_commands = [{"action": "tap", "target": "btn_login_v2"}]
    plugin.store_script({**stored, "udcp_commands": updated_commands})
    scripts = plugin.list_scripts("proj-alpha")
    assert len(scripts) == 1
    assert scripts[0]["udcp_commands"] == updated_commands

    plugin.shutdown()


def test_sqlite_pg_auto_invalidation_and_self_heal(tmp_path: Path):
    clock = _ManualClock()
    plugin = VerifiedUDCPScriptCachePlugin(
        config={"db_path": str(tmp_path / "cache_heal.db"), "fallback_threshold": 0.25, "min_samples": 4},
        clock=clock,
    )
    stored = plugin.store_script(
        {
            "project_id": "proj-1",
            "intent_id": "TC-PAIR-BLE",
            "platform": "ios",
            "build_version": "2.4.0",
            "screen_hash": "hash_v1",
            "udcp_commands": [{"action": "tap", "target": "pair_btn"}],
        }
    )
    cid = stored["cache_id"]

    # 2 hits + 1 fallback = 3 samples (< min_samples=4) -> remains valid
    plugin.record_execution(cid, success=True)
    plugin.record_execution(cid, success=True)
    res3 = plugin.record_execution(cid, success=False, fallback_reason="timeout_once")
    assert res3["hit_count"] == 2
    assert res3["fallback_count"] == 1
    assert res3["invalidated"] is False

    # 4th sample is a fallback -> 2/4 = 0.50 > 0.25 -> auto-invalidated!
    clock.advance_seconds(10)
    res4 = plugin.record_execution(cid, success=False, fallback_reason="selector_changed")
    assert res4["hit_count"] == 2
    assert res4["fallback_count"] == 2
    assert pytest.approx(res4["fallback_rate"]) == 0.50
    assert res4["invalidated"] is True
    assert "0.50 > 0.25" in res4["invalidation_reason"]
    assert "selector_changed" in res4["invalidation_reason"]

    # Excluded from default lookup, visible when include_invalidated=True
    assert plugin.lookup_script("proj-1", "TC-PAIR-BLE", "ios", "2.4.0", "hash_v1") is None
    assert plugin.lookup_script("proj-1", "TC-PAIR-BLE", "ios", "2.4.0", "hash_v1", include_invalidated=True) is not None

    # Unverified self-heal patch logs history but keeps invalidated=True
    clock.advance_seconds(5)
    unverified = plugin.record_execution(
        cid,
        success=False,
        fallback_reason="still_broken",
        self_heal_patch={"udcp_commands": [{"action": "tap", "target": "pair_btn_draft"}], "verified": False},
    )
    assert unverified["invalidated"] is True
    assert len(unverified["self_heal_history"]) == 1
    assert unverified["self_heal_history"][0]["verified"] is False

    # Verified self-heal patch re-enables script and updates commands + screen_hash
    clock.advance_seconds(5)
    healed = plugin.record_execution(
        cid,
        success=True,
        fallback_reason="rca_repaired",
        self_heal_patch={
            "udcp_commands": [{"action": "tap", "target": "pair_btn_v2"}],
            "screen_hash": "hash_v2",
            "ui_fingerprint": {"button": "pair_btn_v2"},
            "verified": True,
        },
    )
    assert healed["invalidated"] is False
    assert healed["invalidation_reason"] is None
    assert healed["screen_hash"] == "hash_v2"
    assert healed["udcp_commands"] == [{"action": "tap", "target": "pair_btn_v2"}]
    assert len(healed["self_heal_history"]) == 2
    assert healed["self_heal_history"][1]["verified"] is True

    # Lookup with new screen_hash succeeds
    reloaded = plugin.lookup_script("proj-1", "TC-PAIR-BLE", "ios", "2.4.0", "hash_v2")
    assert reloaded is not None
    assert reloaded["cache_id"] == cid

    # Manual invalidate & list_scripts filtering
    assert plugin.invalidate_script(cid, "manual_deprecation") is True
    assert plugin.invalidate_script("missing_id", "none") is False
    assert plugin.list_scripts("proj-1", include_invalidated=False) == []
    assert len(plugin.list_scripts("proj-1", include_invalidated=True)) == 1
    plugin.shutdown()


def test_ansi_on_conflict_sql_with_db_wrapper(tmp_path: Path):
    import sqlite3

    class _CoreDBSim:
        def __init__(self, path: Path):
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.is_postgres = False
            self.executed_sql: List[str] = []

        def execute(self, sql: str, params: tuple = ()):
            assert "INSERT OR REPLACE" not in sql.upper()
            self.executed_sql.append(sql)
            return self.conn.execute(sql, params)

        def fetchall(self, sql: str, params: tuple = ()):
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

        def fetchone(self, sql: str, params: tuple = ()):
            r = self.conn.execute(sql, params).fetchone()
            return dict(r) if r else None

        def commit(self):
            self.conn.commit()

    sim_db = _CoreDBSim(tmp_path / "sim_core.db")
    cache = SqlitePgScriptCachePlugin(db=sim_db)
    art = LocalSqliteArtifactStorePlugin(db=sim_db)

    stored = cache.store_script(
        {
            "project_id": "p-ansi",
            "intent_id": "TC-ANSI",
            "platform": "web",
            "build_version": "1.0",
            "udcp_commands": [{"action": "tap"}],
        }
    )
    assert any("ON CONFLICT(cache_id) DO UPDATE" in s for s in sim_db.executed_sql)
    assert cache.lookup_script("p-ansi", "TC-ANSI", "web", "1.0")["cache_id"] == stored["cache_id"]

    indexed = art.index_artifact(
        {
            "run_id": "r-ansi",
            "step_id": "s-1",
            "artifact_type": "har",
            "ntp_timestamp_ms": 123456,
            "uri": "/tmp/net.har",
        }
    )
    assert any("ON CONFLICT(artifact_id) DO UPDATE" in s for s in sim_db.executed_sql)
    assert art.query_artifacts(run_id="r-ansi")[0]["artifact_id"] == indexed["artifact_id"]
    sim_db.conn.close()


# ---------------------------------------------------------------------------
# 2. RedisScriptCachePlugin Tests
# ---------------------------------------------------------------------------
def test_redis_script_cache_ttl_and_mock_client():
    clock = _ManualClock()
    plugin = RedisScriptCachePlugin(
        config={"ttl_seconds": 60, "fallback_threshold": 0.25, "min_samples": 4},
        clock=clock,
    )

    stored = plugin.store_script(
        {
            "project_id": "proj-r",
            "intent_id": "TC-BOOT",
            "platform": "embedded_qt",
            "build_version": "fw-1.0.0",
            "screen_hash": "qt_home",
            "udcp_commands": [{"action": "wait_signal", "signal": "BOOT_OK"}],
        }
    )
    cid = stored["cache_id"]
    assert plugin.lookup_script("proj-r", "TC-BOOT", "embedded_qt", "fw-1.0.0", "qt_home") is not None

    # Test auto-invalidation & heal on Redis plugin
    for ok in (True, True, False, False):
        res = plugin.record_execution(cid, success=ok, fallback_reason="qt_timeout" if not ok else None)
    assert res["invalidated"] is True

    healed = plugin.record_execution(
        cid,
        success=True,
        self_heal_patch={"udcp_commands": [{"action": "wait_signal", "signal": "BOOT_READY"}], "verified": True},
    )
    assert healed["invalidated"] is False

    # Advance clock past TTL (60s) -> entry expires cleanly
    clock.advance_seconds(61)
    assert plugin.lookup_script("proj-r", "TC-BOOT", "embedded_qt", "fw-1.0.0", "qt_home") is None
    assert plugin.list_scripts("proj-r") == []

    # Verify injected Redis client receives pb:scache:{project_id}:{intent_id}:{platform}:{build_version}
    mock_redis = _MockRedisClient()
    client_plugin = RedisScriptCachePlugin(redis_client=mock_redis, config={"ttl_seconds": 120})
    client_plugin.store_script(
        {
            "project_id": "p2",
            "intent_id": "TC-1",
            "platform": "web",
            "build_version": "1.2",
            "udcp_commands": [{"action": "tap"}],
        }
    )
    assert "pb:scache:p2:TC-1:web:1.2" in mock_redis.kv
    assert mock_redis.ttls["pb:scache:p2:TC-1:web:1.2"] == 120


# ---------------------------------------------------------------------------
# 3. LocalSqliteArtifactStorePlugin Tests
# ---------------------------------------------------------------------------
def test_local_sqlite_artifact_types_window_and_ttl_purge(tmp_path: Path):
    clock = _ManualClock(initial_ms=1_700_000_000_000)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    outside_file = tmp_path / "do_not_delete.txt"
    outside_file.write_text("keep", encoding="utf-8")

    plugin = TelemetryArtifactStorePlugin(
        config={
            "db_path": str(tmp_path / "telemetry.db"),
            "artifact_root": str(artifact_dir),
            "default_retention_days": 30,
        },
        clock=clock,
    )
    plugin.initialize()

    base_ts = 1_700_000_100_000
    # Video started at base_ts - 10_000 ms
    offsets = {
        "video_h264": -10_000,
        "uart_log": -2_000,
        "power_trace_ma": -500,
        "har": 0,
        "pcap": 400,
        "screenshot_png": 900,
        "crashpad_dmp": 1_200,
        "gdb_hardfault": 1_500,
    }
    assert set(offsets.keys()) == set(SUPPORTED_ARTIFACT_TYPES)

    for idx, (atype, delta) in enumerate(offsets.items()):
        fpath = artifact_dir / f"{atype}_{idx}.dat"
        fpath.write_text(f"data-{atype}", encoding="utf-8")
        rec = TelemetryArtifactRecord(
            artifact_id="",
            run_id="run-100",
            step_id=f"step-{idx}",
            project_id="proj-hw",
            device_id="dev-nrf52",
            build_version="fw-3.0.0",
            artifact_type=atype,
            ntp_timestamp_ms=base_ts + delta,
            duration_ms=500,
            uri=str(fpath),
            size_bytes=len(f"data-{atype}"),
            summary_metrics={"idx": idx},
            retention_days=30 if atype != "gdb_hardfault" else 90,
        )
        plugin.index_artifact(rec)

    # Query all 8 types
    all_run = plugin.query_artifacts(run_id="run-100", limit=50)
    assert len(all_run) == 8
    assert len(plugin.query_artifacts(run_id="run-100", artifact_type="uart_log")) == 1

    # Query evidence window centered at base_ts with +/- 2500 ms
    # video_h264 is at -10000 ms (outside window), but still sets video_start_ms!
    window = plugin.query_evidence_window(run_id="run-100", center_ts_ms=base_ts, window_ms=2500)
    assert window["video_start_ms"] == base_ts - 10_000
    assert window["summary"]["total_artifacts"] == 7
    assert window["summary"]["has_video"] is True

    by_type = {item["artifact_type"]: item for item in window["timeline"]}
    assert by_type["uart_log"]["delta_from_center_ms"] == -2000
    assert by_type["uart_log"]["video_offset_ms"] == 8000
    assert by_type["har"]["delta_from_center_ms"] == 0
    assert by_type["har"]["video_offset_ms"] == 10_000
    assert by_type["gdb_hardfault"]["delta_from_center_ms"] == 1500
    assert by_type["gdb_hardfault"]["video_offset_ms"] == 11_500

    # Advance 31 days -> 30d artifacts purge, 90d gdb_hardfault survives
    purge_31 = plugin.purge_expired(now_ms=clock.advance_days(31))
    assert purge_31["deleted_count"] == 7
    assert len(purge_31["deleted_files"]) == 7
    remaining = plugin.query_artifacts(run_id="run-100")
    assert len(remaining) == 1
    assert remaining[0]["artifact_type"] == "gdb_hardfault"
    assert outside_file.exists()

    # Advance another 60 days (total 91d) -> 90d gdb_hardfault purges too
    purge_91 = plugin.purge_expired(now_ms=clock.advance_days(60))
    assert purge_91["deleted_count"] == 1
    assert plugin.query_artifacts(run_id="run-100") == []
    plugin.shutdown()


# ---------------------------------------------------------------------------
# 4. MinioClickHouseArtifactStorePlugin Tests
# ---------------------------------------------------------------------------
def test_minio_clickhouse_mock_http_and_offline_fallback():
    clock = _ManualClock(initial_ms=1_700_000_000_000)
    stored_rows: List[Dict[str, Any]] = []
    minio_puts: List[str] = []
    ch_queries: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            minio_puts.append(str(request.url))
            return httpx.Response(200, text="ok")

        parsed_q = parse_qs(request.url.query.decode("utf-8"))
        sql = parsed_q.get("query", [""])[0]
        ch_queries.append(sql)

        if sql.startswith("INSERT INTO"):
            for line in request.content.decode("utf-8").strip().splitlines():
                stored_rows.append(json.loads(line))
            return httpx.Response(200, text="")

        if "SELECT min(ntp_timestamp_ms)" in sql:
            run_id = parsed_q.get("param_run_id", [""])[0]
            vids = [
                r["ntp_timestamp_ms"]
                for r in stored_rows
                if r["run_id"] == run_id and r["artifact_type"] == "video_h264"
            ]
            return httpx.Response(200, json={"data": [{"min_ts": min(vids) if vids else None}]})

        if sql.startswith("SELECT * FROM"):
            run_id = parsed_q.get("param_run_id", [None])[0]
            start_ts = parsed_q.get("param_start_ts", [None])[0]
            end_ts = parsed_q.get("param_end_ts", [None])[0]
            rows = [
                r
                for r in stored_rows
                if (run_id is None or r["run_id"] == run_id)
                and (start_ts is None or int(r["ntp_timestamp_ms"]) >= int(start_ts))
                and (end_ts is None or int(r["ntp_timestamp_ms"]) <= int(end_ts))
            ]
            rows.sort(key=lambda r: int(r["ntp_timestamp_ms"]))
            return httpx.Response(200, json={"data": rows})

        if sql.startswith("ALTER TABLE"):
            cutoff = int(parsed_q.get("param_cutoff", ["0"])[0])
            stored_rows[:] = [r for r in stored_rows if int(r["expires_at"]) > cutoff]
            return httpx.Response(200, text="")

        return httpx.Response(404, text="unknown")

    transport = httpx.MockTransport(handler)
    plugin = MinioClickHouseArtifactStorePlugin(
        config={"bucket": "qa-bucket", "clickhouse_table": "telemetry_artifacts"},
        transport=transport,
        clock=clock,
    )

    vid = plugin.index_artifact(
        {
            "run_id": "run-ch-1",
            "step_id": "step-0",
            "project_id": "proj-ch",
            "artifact_type": "video_h264",
            "ntp_timestamp_ms": 1_000_000,
            "uri": "/tmp/video.h264",
            "payload_bytes": b"h264-bytes",
        }
    )
    assert vid["uri"].startswith("s3://qa-bucket/run-ch-1/step-0/")
    assert len(minio_puts) == 1

    plugin.index_artifact(
        {
            "run_id": "run-ch-1",
            "step_id": "step-1",
            "project_id": "proj-ch",
            "artifact_type": "crashpad_dmp",
            "ntp_timestamp_ms": 1_004_500,
            "uri": "s3://qa-bucket/run-ch-1/step-1/crash.dmp",
            "summary_metrics": {"signal": "SIGSEGV"},
        }
    )

    queried = plugin.query_artifacts(run_id="run-ch-1")
    assert len(queried) == 2
    assert queried[1]["summary_metrics"] == {"signal": "SIGSEGV"}

    win = plugin.query_evidence_window(run_id="run-ch-1", center_ts_ms=1_004_000, window_ms=2000)
    assert win["video_start_ms"] == 1_000_000
    assert win["summary"]["total_artifacts"] == 1
    assert win["timeline"][0]["delta_from_center_ms"] == 500
    assert win["timeline"][0]["video_offset_ms"] == 4500

    purged = plugin.purge_expired(now_ms=clock.advance_days(31))
    assert purged["deleted_count"] == 2
    assert any(q.startswith("ALTER TABLE") for q in ch_queries)
    plugin.shutdown()

    # Offline fallback when ClickHouse is unreachable
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    offline_plugin = MinioClickHouseArtifactStorePlugin(
        config={"offline_fallback": True},
        transport=httpx.MockTransport(failing_handler),
        clock=clock,
    )
    offline_plugin.index_artifact(
        {
            "run_id": "run-off",
            "step_id": "step-1",
            "artifact_type": "uart_log",
            "ntp_timestamp_ms": 500_000,
            "uri": "uart.log",
        }
    )
    assert len(offline_plugin.query_artifacts(run_id="run-off")) == 1
    off_win = offline_plugin.query_evidence_window("run-off", center_ts_ms=500_100, window_ms=1000)
    assert off_win["summary"]["total_artifacts"] == 1
    assert off_win["timeline"][0]["delta_from_center_ms"] == -100
    assert off_win["timeline"][0]["video_offset_ms"] is None
    assert offline_plugin.purge_expired(now_ms=clock.advance_days(31))["deleted_count"] == 1
    offline_plugin.shutdown()
