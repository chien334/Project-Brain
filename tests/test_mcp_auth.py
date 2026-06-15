import pytest
import json
import re
import httpx
from fastapi.testclient import TestClient
from projectbrain.server.api import create_app
from projectbrain.core.db import db

app = create_app()

def setup_module(module):
    db.connect()
    # Clean up test accounts
    cursor = db.conn.cursor()
    if db.is_postgres:
        cursor.execute("DELETE FROM mcp_accounts WHERE username LIKE %s", ("test_%%",))
    else:
        cursor.execute("DELETE FROM mcp_accounts WHERE username LIKE ?", ("test_%",))
    db.conn.commit()
    cursor.close()

def teardown_module(module):
    db.connect()
    cursor = db.conn.cursor()
    if db.is_postgres:
        cursor.execute("DELETE FROM mcp_accounts WHERE username LIKE %s", ("test_%%",))
    else:
        cursor.execute("DELETE FROM mcp_accounts WHERE username LIKE ?", ("test_%",))
    db.conn.commit()
    cursor.close()

def test_rest_api_accounts_crud():
    client = TestClient(app)
    # 1. Create a collaborator account
    res = client.post("/auth/accounts", json={
        "username": "test_collaborator",
        "token": "pb_tok_test_collab",
        "role": "collaborator",
        "allowed_tools": "*"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["username"] == "test_collaborator"
    assert data["token"] == "pb_tok_test_collab"

    # 2. List accounts and verify test_collaborator exists
    res = client.get("/auth/accounts")
    assert res.status_code == 200
    accounts = res.json()["accounts"]
    usernames = [a["username"] for a in accounts]
    assert "test_collaborator" in usernames
    assert "admin" in usernames

    # 3. Create a reader account with custom restricted tools list
    res = client.post("/auth/accounts", json={
        "username": "test_reader",
        "token": "pb_tok_test_reader",
        "role": "reader",
        "allowed_tools": "projectbrain_query,projectbrain_read"
    })
    assert res.status_code == 200

    # 4. Delete collaborator account
    res = client.delete("/auth/accounts/test_collaborator")
    assert res.status_code == 200
    assert "deleted" in res.json()["message"]

    # 5. List again, collaborator should be gone, reader should be there
    res = client.get("/auth/accounts")
    accounts = res.json()["accounts"]
    usernames = [a["username"] for a in accounts]
    assert "test_collaborator" not in usernames
    assert "test_reader" in usernames

@pytest.mark.asyncio
async def test_mcp_auth_middleware_verification():
    # Use httpx.AsyncClient to prevent event loop blocking on infinite SSE streams
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        # Verify GET /auth/tools returns tools
        res_tools = await ac.get("/auth/tools")
        assert res_tools.status_code == 200
        tools_data = res_tools.json()
        assert "tools" in tools_data
        assert "projectbrain_query" in tools_data["tools"]

        # Setup test_reader again to make sure it's present
        await ac.post("/auth/accounts", json={
            "username": "test_reader",
            "token": "pb_tok_test_reader",
            "role": "reader",
            "allowed_tools": "projectbrain_query"
        })

        # Case 1: No auth headers -> should return 403
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "projectbrain_query"
            },
            "id": 1
        }
        res = await ac.post("/mcp/messages/?session_id=dummy", json=payload)
        assert res.status_code == 403
        assert "Authentication required" in res.json()["error"]["message"]

        # Case 2: Invalid token -> should return 403
        res = await ac.post("/mcp/messages/?session_id=dummy", json=payload, headers={"Authorization": "Bearer pb_tok_invalid"})
        assert res.status_code == 403
        assert "Permission denied: Invalid access token" in res.json()["error"]["message"]

        # Case 3: Valid token, reader role calling unauthorized tool (not in allowed list)
        payload_store = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "projectbrain_store"
            },
            "id": 2
        }
        res = await ac.post("/mcp/messages/?session_id=dummy", json=payload_store, headers={"Authorization": "Bearer pb_tok_test_reader"})
        assert res.status_code == 403
        assert "is not authorized to call tool" in res.json()["error"]["message"]

        # Case 4: Valid token, reader role calling write tool that IS in allowed list but blocked because of write keyword
        await ac.post("/auth/accounts", json={
            "username": "test_reader",
            "token": "pb_tok_test_reader",
            "role": "reader",
            "allowed_tools": "projectbrain_store"
        })
        res = await ac.post("/mcp/messages/?session_id=dummy", json=payload_store, headers={"Authorization": "Bearer pb_tok_test_reader"})
        assert res.status_code == 403
        assert "Read-only accounts cannot execute modifying tool" in res.json()["error"]["message"]

        # Case 5: Verify SSE GET connection validation
        # 5a. GET /mcp/sse without token should return 403
        res = await ac.get("/mcp/sse")
        assert res.status_code == 403

        # 5b. GET /mcp/sse with invalid token should return 403
        res = await ac.get("/mcp/sse?token=pb_tok_invalid")
        assert res.status_code == 403

        # Case 6: Verify tools/list filtering using SSEFilter directly
        from projectbrain.server.api import SSEFilter

        # 6a. Collaborator account with allowed_tools="projectbrain_query" -> should only return projectbrain_query
        sse_filter_collab = SSEFilter("collaborator", "projectbrain_query")
        
        mock_raw_chunk = b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"result\":{\"tools\":[{\"name\":\"projectbrain_query\",\"description\":\"Query facts\"},{\"name\":\"projectbrain_store\",\"description\":\"Store fact\"}]},\"id\":10}\n\n"
        
        filtered_chunk_collab = sse_filter_collab.process_chunk(mock_raw_chunk)
        filtered_text_collab = filtered_chunk_collab.decode("utf-8")
        assert "projectbrain_query" in filtered_text_collab
        assert "projectbrain_store" not in filtered_text_collab

        # 6b. Reader account with allowed_tools="*" -> should filter out all write tools
        sse_filter_reader = SSEFilter("reader", "*")
        
        filtered_chunk_reader = sse_filter_reader.process_chunk(mock_raw_chunk)
        filtered_text_reader = filtered_chunk_reader.decode("utf-8")
        assert "projectbrain_query" in filtered_text_reader
        assert "projectbrain_store" not in filtered_text_reader

@pytest.mark.asyncio
async def test_project_registration_and_deletion():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        # Create project
        res = await ac.post("/codegraph/projects", json={
            "id": "test/test-project",
            "name": "Test Project",
            "description": "A description for testing"
        })
        assert res.status_code == 200
        assert res.json()["status"] == "success"

        # Verify it is in list
        res_list = await ac.get("/codegraph/projects")
        assert res_list.status_code == 200
        project_ids = [p["id"] for p in res_list.json()["projects"]]
        assert "test/test-project" in project_ids

        # Delete project
        res_del = await ac.delete("/codegraph/projects/test/test-project")
        assert res_del.status_code == 200
        assert res_del.json()["status"] == "success"

        # Verify it is gone
        res_list2 = await ac.get("/codegraph/projects")
        assert res_list2.status_code == 200
        project_ids2 = [p["id"] for p in res_list2.json()["projects"]]
        assert "test/test-project" not in project_ids2
