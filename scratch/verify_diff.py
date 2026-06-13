import asyncio
import sqlite3
import time
import httpx
from openmemory.ai.mcp import openmemory_diff_project_versions
from openmemory.core.db import db

def setup_test_data():
    db.connect()
    cursor = db.conn.cursor()
    ts = int(time.time())
    
    # 1. Clear old test projects if exist
    cursor.execute("DELETE FROM project_nodes WHERE project_id IN ('test-proj:v1', 'test-proj:v2')")
    cursor.execute("DELETE FROM projects WHERE id IN ('test-proj:v1', 'test-proj:v2')")
    cursor.execute("DELETE FROM memories WHERE user_id IN ('test-proj:v1', 'test-proj:v2')")
    
    # 2. Insert test projects
    cursor.execute("INSERT INTO projects (id, name, description, created_at, updated_at) VALUES ('test-proj:v1', 'Test Project v1', 'Version 1', ?, ?)", (ts, ts))
    cursor.execute("INSERT INTO projects (id, name, description, created_at, updated_at) VALUES ('test-proj:v2', 'Test Project v2', 'Version 2', ?, ?)", (ts, ts))
    
    # 3. Insert test nodes
    # test-proj:v1 nodes
    # file.py (exists in both)
    # class A (exists in v1, changed signature in v2)
    # class B (exists in v1, deleted in v2)
    cursor.execute("""
        INSERT INTO project_nodes (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
        VALUES ('test-proj:v1', 'file:file.py', 'file', 'file.py', 'file.py', 'file.py', 'python', 1, 10, NULL, NULL, ?)
    """, (ts,))
    cursor.execute("""
        INSERT INTO project_nodes (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
        VALUES ('test-proj:v1', 'class:A', 'class', 'A', 'A', 'file.py', 'python', 2, 5, 'Doc A', 'def A()', ?)
    """, (ts,))
    cursor.execute("""
        INSERT INTO project_nodes (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
        VALUES ('test-proj:v1', 'class:B', 'class', 'B', 'B', 'file.py', 'python', 6, 10, 'Doc B', 'def B()', ?)
    """, (ts,))
    
    # test-proj:v2 nodes
    # file.py (exists in both)
    # class A (signature and docstring changed)
    # class C (added in v2)
    cursor.execute("""
        INSERT INTO project_nodes (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
        VALUES ('test-proj:v2', 'file:file.py', 'file', 'file.py', 'file.py', 'file.py', 'python', 1, 15, NULL, NULL, ?)
    """, (ts,))
    cursor.execute("""
        INSERT INTO project_nodes (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
        VALUES ('test-proj:v2', 'class:A', 'class', 'A', 'A', 'file.py', 'python', 2, 8, 'New Doc A', 'def A(x, y)', ?)
    """, (ts,))
    cursor.execute("""
        INSERT INTO project_nodes (project_id, id, kind, name, qualified_name, file_path, language, start_line, end_line, docstring, signature, updated_at)
        VALUES ('test-proj:v2', 'class:C', 'class', 'C', 'C', 'file.py', 'python', 9, 15, 'Doc C', 'def C()', ?)
    """, (ts,))
    
    # 4. Insert memories
    # v1 memory
    cursor.execute("""
        INSERT INTO memories (id, user_id, segment, content, simhash, primary_sector, tags, meta, created_at, updated_at, last_seen_at, salience, decay_lambda, version)
        VALUES ('mem1', 'test-proj:v1', 0, 'Use Outfit font', '123', 'semantic', '["pref"]', '{}', ?, ?, ?, 1.0, 0.02, 1)
    """, (ts, ts, ts))
    # v2 memories (Outfit font stays, but add a new memory: Use dark mode UI)
    cursor.execute("""
        INSERT INTO memories (id, user_id, segment, content, simhash, primary_sector, tags, meta, created_at, updated_at, last_seen_at, salience, decay_lambda, version)
        VALUES ('mem1_v2', 'test-proj:v2', 0, 'Use Outfit font', '123', 'semantic', '["pref"]', '{}', ?, ?, ?, 1.0, 0.02, 1)
    """, (ts, ts, ts))
    cursor.execute("""
        INSERT INTO memories (id, user_id, segment, content, simhash, primary_sector, tags, meta, created_at, updated_at, last_seen_at, salience, decay_lambda, version)
        VALUES ('mem2', 'test-proj:v2', 0, 'Use dark mode UI', '456', 'semantic', '["pref"]', '{}', ?, ?, ?, 1.0, 0.02, 1)
    """, (ts, ts, ts))
    
    db.conn.commit()
    cursor.close()
    print("Test data setup complete!")

async def test_diff_api():
    async with httpx.AsyncClient() as client:
        # Test GET /codegraph/diff
        resp = await client.get("http://localhost:8080/codegraph/diff?base_project_id=test-proj:v1&target_project_id=test-proj:v2")
        print("\nAPI DIFF RESPONSE:")
        print(resp.status_code)
        data = resp.json()
        print(f"Added nodes: {[n['name'] for n in data['added']]}")
        print(f"Deleted nodes: {[n['name'] for n in data['deleted']]}")
        print(f"Modified nodes: {[m['node']['name'] for m in data['modified']]}")
        
async def test_mcp_tool():
    print("\nMCP TOOL RESPONSE:")
    report = await openmemory_diff_project_versions(base_project_id="test-proj:v1", target_project_id="test-proj:v2")
    print(report)

if __name__ == "__main__":
    setup_test_data()
    asyncio.run(test_diff_api())
    asyncio.run(test_mcp_tool())
