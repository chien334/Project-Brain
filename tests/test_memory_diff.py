import pytest
from fastapi.testclient import TestClient
from projectbrain.server.api import create_app
from projectbrain.core.db import db, q
import json

app = create_app()
client = TestClient(app)

def test_memory_diff():
    # 1. Clean up potential old test data
    db.connect()
    db.execute("DELETE FROM memories WHERE user_id IN (?, ?)", ("test-diff-base", "test-diff-target"))
    db.commit()

    # 2. Insert test memories for base
    # Memory 1 (identical in both): WarehouseService.cs sec 1
    m1_base = {
        "id": "mem-1", "user_id": "test-diff-base", "content": "public class WarehouseService { }",
        "primary_sector": "procedural", "meta": json.dumps({"file_path": "WarehouseService.cs", "section_index": 1})
    }
    # Memory 2 (deleted in target): WarehouseService.cs sec 2
    m2_base = {
        "id": "mem-2", "user_id": "test-diff-base", "content": "public void OldMethod() { }",
        "primary_sector": "procedural", "meta": json.dumps({"file_path": "WarehouseService.cs", "section_index": 2})
    }
    # Memory 3 (modified in target): WarehouseService.cs sec 3
    m3_base = {
        "id": "mem-3", "user_id": "test-diff-base", "content": "public void ModifiedMethod() { int a = 1; }",
        "primary_sector": "procedural", "meta": json.dumps({"file_path": "WarehouseService.cs", "section_index": 3})
    }
    # Memory 4 (no metadata, deleted in target)
    m4_base = {
        "id": "mem-4", "user_id": "test-diff-base", "content": "some raw doc text",
        "primary_sector": "semantic", "meta": "{}"
    }

    # Insert base memories
    for m in [m1_base, m2_base, m3_base, m4_base]:
        q.ins_mem(**m)

    # 3. Insert test memories for target
    # Memory 1 (identical)
    m1_target = {
        "id": "mem-1-t", "user_id": "test-diff-target", "content": "public class WarehouseService { }",
        "primary_sector": "procedural", "meta": json.dumps({"file_path": "WarehouseService.cs", "section_index": 1})
    }
    # Memory 3 (modified)
    m3_target = {
        "id": "mem-3-t", "user_id": "test-diff-target", "content": "public void ModifiedMethod() { int a = 2; }", # modified value
        "primary_sector": "procedural", "meta": json.dumps({"file_path": "WarehouseService.cs", "section_index": 3})
    }
    # Memory 5 (added in target): ProductService.cs sec 1
    m5_target = {
        "id": "mem-5-t", "user_id": "test-diff-target", "content": "public class ProductService { }",
        "primary_sector": "procedural", "meta": json.dumps({"file_path": "ProductService.cs", "section_index": 1})
    }
    # Memory 6 (no metadata, added in target)
    m6_target = {
        "id": "mem-6-t", "user_id": "test-diff-target", "content": "new raw doc text",
        "primary_sector": "semantic", "meta": "{}"
    }

    # Insert target memories
    for m in [m1_target, m3_target, m5_target, m6_target]:
        q.ins_mem(**m)

    try:
        # 4. Call /memory/diff endpoint
        response = client.get("/memory/diff?base_project_id=test-diff-base&target_project_id=test-diff-target")
        assert response.status_code == 200
        res_data = response.json()

        # 5. Assertions
        assert res_data["base_project_id"] == "test-diff-base"
        assert res_data["target_project_id"] == "test-diff-target"

        # Check added
        added_ids = [m["id"] for m in res_data["added"]]
        assert "mem-5-t" in added_ids
        assert "mem-6-t" in added_ids
        assert len(added_ids) == 2

        # Check deleted
        deleted_ids = [m["id"] for m in res_data["deleted"]]
        assert "mem-2" in deleted_ids
        assert "mem-4" in deleted_ids
        assert len(deleted_ids) == 2

        # Check modified
        assert len(res_data["modified"]) == 1
        mod = res_data["modified"][0]
        assert mod["file_path"] == "WarehouseService.cs"
        assert mod["section_index"] == 3
        assert mod["base"]["content"] == "public void ModifiedMethod() { int a = 1; }"
        assert mod["target"]["content"] == "public void ModifiedMethod() { int a = 2; }"

    finally:
        # 6. Clean up
        db.execute("DELETE FROM memories WHERE user_id IN (?, ?)", ("test-diff-base", "test-diff-target"))
        db.commit()
