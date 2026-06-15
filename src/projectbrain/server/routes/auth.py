import time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from ...core.db import db

router = APIRouter(tags=["auth"])

class AccountRequest(BaseModel):
    username: str
    token: Optional[str] = None
    role: str # 'admin', 'collaborator', 'reader'
    allowed_tools: Optional[str] = "*" # Comma-separated list or '*'

def init_default_admin():
    db.connect()
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM mcp_accounts;")
        count = cursor.fetchone()[0]
        if count == 0:
            ts = int(time.time())
            if db.is_postgres:
                cursor.execute(
                    "INSERT INTO mcp_accounts (username, token, role, allowed_tools, created_at) VALUES (%s, %s, %s, %s, %s)",
                    ("admin", "pb_tok_admin", "admin", "*", ts)
                )
            else:
                cursor.execute(
                    "INSERT INTO mcp_accounts (username, token, role, allowed_tools, created_at) VALUES (?, ?, ?, ?, ?)",
                    ("admin", "pb_tok_admin", "admin", "*", ts)
                )
            db.conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[AUTH] Failed to initialize default admin account: {e}")

# Automatically initialize admin account when route is loaded
try:
    init_default_admin()
except Exception:
    pass

@router.get("/accounts")
async def list_accounts():
    db.connect()
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT username, token, role, allowed_tools, created_at FROM mcp_accounts ORDER BY username ASC;")
        rows = cursor.fetchall()
        accounts = []
        for r in rows:
            if db.is_postgres:
                accounts.append({
                    "username": r[0],
                    "token": r[1],
                    "role": r[2],
                    "allowed_tools": r[3],
                    "created_at": r[4]
                })
            else:
                accounts.append(dict(r))
        cursor.close()
        return {"accounts": accounts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load accounts: {str(e)}")

@router.post("/accounts")
async def create_or_update_account(req: AccountRequest):
    db.connect()
    ts = int(time.time())
    
    # Generate token if not provided
    import secrets
    token = req.token or f"pb_tok_{secrets.token_hex(12)}"
    
    # Validate role
    role = req.role.lower().strip()
    if role not in ["admin", "collaborator", "reader"]:
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'admin', 'collaborator', or 'reader'")

    try:
        cursor = db.conn.cursor()
        if db.is_postgres:
            cursor.execute(
                """
                INSERT INTO mcp_accounts (username, token, role, allowed_tools, created_at) 
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (username) DO UPDATE SET 
                    token = EXCLUDED.token, 
                    role = EXCLUDED.role, 
                    allowed_tools = EXCLUDED.allowed_tools
                """,
                (req.username, token, role, req.allowed_tools or "*", ts)
            )
        else:
            cursor.execute(
                """
                INSERT INTO mcp_accounts (username, token, role, allowed_tools, created_at) 
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (username) DO UPDATE SET 
                    token = excluded.token, 
                    role = excluded.role, 
                    allowed_tools = excluded.allowed_tools
                """,
                (req.username, token, role, req.allowed_tools or "*", ts)
            )
        db.conn.commit()
        cursor.close()
        return {"status": "success", "username": req.username, "token": token}
    except Exception as e:
        if db.conn:
            try: db.conn.rollback()
            except: pass
        raise HTTPException(status_code=500, detail=f"Failed to save account: {str(e)}")

@router.delete("/accounts/{username}")
async def delete_account(username: str):
    db.connect()
    try:
        cursor = db.conn.cursor()
        if db.is_postgres:
            cursor.execute("DELETE FROM mcp_accounts WHERE username = %s", (username,))
        else:
            cursor.execute("DELETE FROM mcp_accounts WHERE username = ?", (username,))
        db.conn.commit()
        cursor.close()
        return {"status": "success", "message": f"Account '{username}' deleted."}
    except Exception as e:
        if db.conn:
            try: db.conn.rollback()
            except: pass
        raise HTTPException(status_code=500, detail=f"Failed to delete account: {str(e)}")

@router.get("/tools")
async def list_available_tools():
    try:
        from ...ai.mcp import mcp_server
        tools = mcp_server._tool_manager.list_tools()
        return {"tools": [t.name for t in tools]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch tools: {str(e)}")

@router.get("/verify")
async def verify_token(request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "").strip()
    if not token:
        token = request.query_params.get("token")
        
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required. Please provide a token.")
        
    db.connect()
    try:
        cursor = db.conn.cursor()
        sql = db.translate_query("SELECT username, role, allowed_tools FROM mcp_accounts WHERE token = ?;")
        cursor.execute(sql, (token,))
        row = cursor.fetchone()
        cursor.close()
        
        if not row:
            raise HTTPException(status_code=401, detail="Invalid token. Access denied.")
            
        if db.is_postgres:
            account = {"username": row[0], "role": row[1], "allowed_tools": row[2]}
        else:
            account = dict(row)
        return {"status": "success", "account": account}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication check failed: {str(e)}")
