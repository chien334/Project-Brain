"""Comprehensive Test Suite for the Full Identity & Access Management (IAM) Auth System.

Covers:
- Public GET /auth/config policy flags
- Self-service POST /auth/register (PBKDF2 password hashing, safe collaborator default,
  pb_sess_ token + HttpOnly pb_session cookie, duplicate username/email & weak password rejection)
- POST /auth/login (Username+Password, Email+Password, API Token pb_tok_admin) & GET /auth/verify
  (Bearer header & pb_session cookie)
- Brute-force lockout (5 failed attempts -> 401, 6th -> 429 locked_until, Admin unlock)
- Self-service profile & password lifecycle (GET/PUT /auth/me, POST /auth/change-password)
- Session management (GET /auth/sessions, POST /auth/sessions/revoke-others,
  DELETE /auth/sessions/{id}, POST /auth/logout)
- Personal API Keys (POST/GET/DELETE /auth/api-keys, privilege ceiling, project scope
  enforcement under PB_REQUIRE_REST_AUTH=true, immediate revocation)
- Admin governance & audit trail (PATCH /auth/accounts/{u}/status, sole admin protection,
  rotate-token, reset-password, GET/PUT /auth/settings, GET /auth/audit-logs filtering & RBAC)
- Static Workbench Asset Integrity (GET /dashboard/, /dashboard/style.css, /dashboard/app.js)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.server.api import create_app
from projectbrain.server.routes import auth as auth_routes


@pytest.fixture(autouse=True)
def isolated_auth_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate every test to a fresh temporary SQLite DB and clean auth settings."""
    orig_db_url = env.database_url
    orig_db_path = env.db_path
    orig_conn = db.conn
    orig_is_postgres = db.is_postgres

    orig_allow_reg = getattr(env, "allow_registration", True)
    orig_default_role = getattr(env, "default_signup_role", "collaborator")
    orig_ttl_hours = getattr(env, "session_ttl_hours", 24)
    orig_require_rest = getattr(env, "require_rest_auth", False)
    orig_lockout_max = getattr(env, "auth_lockout_max_attempts", 5)
    orig_lockout_sec = getattr(env, "auth_lockout_seconds", 300)

    env_keys_to_restore = (
        "PB_REQUIRE_REST_AUTH",
        "PB_ALLOW_REGISTRATION",
        "PB_DEFAULT_SIGNUP_ROLE",
        "PB_SESSION_TTL_HOURS",
    )
    orig_os_env = {k: os.environ.get(k) for k in env_keys_to_restore}

    temp_db_path = tmp_path / "full_auth_test.db"
    monkeypatch.setenv("PB_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("OM_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")
    monkeypatch.setenv("PB_ALLOW_REGISTRATION", "true")
    monkeypatch.setenv("PB_DEFAULT_SIGNUP_ROLE", "collaborator")
    monkeypatch.setenv("PB_SESSION_TTL_HOURS", "24")

    monkeypatch.setattr(env, "database_url", f"sqlite:///{temp_db_path}")
    monkeypatch.setattr(env, "db_path", str(temp_db_path))
    monkeypatch.setattr(env, "allow_registration", True)
    monkeypatch.setattr(env, "default_signup_role", "collaborator")
    monkeypatch.setattr(env, "session_ttl_hours", 24)
    monkeypatch.setattr(env, "require_rest_auth", False)
    monkeypatch.setattr(env, "auth_lockout_max_attempts", 5)
    monkeypatch.setattr(env, "auth_lockout_seconds", 300)

    # Detach prior global DB connection without closing it so earlier/later modules stay safe
    db.conn = None
    db.is_postgres = False
    auth_routes._ensured_conn = None
    auth_routes._ensured_db_url = None
    db.connect()
    auth_routes.init_auth_system()

    try:
        yield temp_db_path
    finally:
        temp_conn = db.conn
        if temp_conn is not None and temp_conn is not orig_conn:
            try:
                temp_conn.close()
            except Exception:
                pass
        env.database_url = orig_db_url
        env.db_path = orig_db_path
        env.allow_registration = orig_allow_reg
        env.default_signup_role = orig_default_role
        env.session_ttl_hours = orig_ttl_hours
        env.require_rest_auth = orig_require_rest
        env.auth_lockout_max_attempts = orig_lockout_max
        env.auth_lockout_seconds = orig_lockout_sec

        for key, prev_val in orig_os_env.items():
            if prev_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev_val

        db.conn = orig_conn
        db.is_postgres = orig_is_postgres
        auth_routes._ensured_conn = None
        auth_routes._ensured_db_url = None


@pytest.fixture
def client(isolated_auth_env) -> TestClient:
    """FastAPI TestClient bound to the isolated auth database."""
    return TestClient(create_app())


# ============================================================================
# 1. Public Config & Self-Service Registration
# ============================================================================


def test_public_auth_config_returns_policy_flags(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    res = client.get("/auth/config")
    assert res.status_code == 200
    data = res.json()
    assert data["allow_registration"] is True
    assert data["default_signup_role"] == "collaborator"
    assert data["require_rest_auth"] is False
    assert data["session_ttl_hours"] == 24
    assert data["password_login_enabled"] is True
    assert data["token_login_enabled"] is True

    # Must remain publicly accessible even when PB_REQUIRE_REST_AUTH=true
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")
    res_locked = client.get("/auth/config")
    assert res_locked.status_code == 200
    assert res_locked.json()["require_rest_auth"] is True


def test_self_service_register_success_cookie_hash_and_validations(client: TestClient):
    # Weak password (<8 chars) rejected
    res_weak = client.post(
        "/auth/register",
        json={"username": "weakuser", "email": "weak@example.com", "password": "short"},
    )
    assert res_weak.status_code == 400
    assert "8 characters" in res_weak.json()["detail"]

    # Invalid username rejected
    res_bad_user = client.post(
        "/auth/register",
        json={"username": "ab", "email": "ab@example.com", "password": "ValidPassword123!"},
    )
    assert res_bad_user.status_code == 400

    # Successful self-registration (even if caller passes extra role='admin', role must stay safe default)
    res = client.post(
        "/auth/register",
        json={
            "username": "alice_dev",
            "email": "Alice.Dev@Example.com",
            "display_name": "Alice Dev",
            "password": "StrongPassword123!",
            "role": "admin",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "success"
    assert body["session_token"].startswith("pb_sess_")
    assert body["account"]["username"] == "alice_dev"
    assert body["account"]["role"] == "collaborator"
    assert body["account"]["role"] != "admin"
    assert body["account"]["has_password"] is True
    assert body["account"]["status"] == "active"

    # HttpOnly pb_session cookie must be set
    set_cookie_hdr = res.headers.get("set-cookie", "")
    assert "pb_session=pb_sess_" in set_cookie_hdr
    assert "httponly" in set_cookie_hdr.lower()

    # Password stored as PBKDF2-HMAC-SHA256 hash, never plaintext
    row = db.fetchone("SELECT password_hash, role FROM mcp_accounts WHERE username = ?", ("alice_dev",))
    assert row is not None
    assert row["role"] == "collaborator"
    assert row["password_hash"].startswith("pbkdf2_sha256$")
    assert "StrongPassword123!" not in row["password_hash"]

    # Duplicate username rejected (case-insensitive)
    res_dup_user = client.post(
        "/auth/register",
        json={"username": "ALICE_DEV", "email": "other@example.com", "password": "StrongPassword123!"},
    )
    assert res_dup_user.status_code == 409

    # Duplicate email rejected (case-insensitive)
    res_dup_email = client.post(
        "/auth/register",
        json={"username": "alice_other", "email": "alice.dev@example.com", "password": "StrongPassword123!"},
    )
    assert res_dup_email.status_code == 409


# ============================================================================
# 2. Login Modes & Token Verification (Bearer + Cookie)
# ============================================================================


def test_login_all_modes_and_verify_bearer_and_cookie(client: TestClient):
    reg = client.post(
        "/auth/register",
        json={
            "username": "bob_login",
            "email": "Bob.Login@Example.com",
            "display_name": "Bob Login",
            "password": "BobSecretPass99!",
        },
    )
    assert reg.status_code == 200

    # Mode A1: Username + Password
    res_user = client.post(
        "/auth/login",
        json={"identifier": "bob_login", "password": "BobSecretPass99!"},
    )
    assert res_user.status_code == 200
    tok_user = res_user.json()["session_token"]
    assert tok_user.startswith("pb_sess_")
    assert res_user.json()["account"]["username"] == "bob_login"
    assert "httponly" in res_user.headers.get("set-cookie", "").lower()

    # Mode A2: Email + Password (case-insensitive)
    res_email = client.post(
        "/auth/login",
        json={"identifier": "bob.login@example.com", "password": "BobSecretPass99!"},
    )
    assert res_email.status_code == 200
    tok_email = res_email.json()["session_token"]
    assert tok_email.startswith("pb_sess_")
    assert res_email.json()["account"]["username"] == "bob_login"

    # Mode B: API Token login (pb_tok_admin)
    res_tok = client.post("/auth/login", json={"token": "pb_tok_admin"})
    assert res_tok.status_code == 200
    tok_admin_sess = res_tok.json()["session_token"]
    assert tok_admin_sess.startswith("pb_sess_")
    assert res_tok.json()["account"]["username"] == "admin"
    assert res_tok.json()["account"]["role"] == "admin"

    # Invalid token & missing fields rejected
    assert client.post("/auth/login", json={"token": "pb_tok_nonexistent"}).status_code == 401
    assert client.post("/auth/login", json={"identifier": "bob_login"}).status_code == 400

    # Verify via Authorization Bearer header (clear cookies on separate client)
    bare_client = TestClient(create_app())
    assert bare_client.get("/auth/verify").status_code == 401

    res_verify_bearer = bare_client.get(
        "/auth/verify",
        headers={"Authorization": f"Bearer {tok_user}"},
    )
    assert res_verify_bearer.status_code == 200
    assert res_verify_bearer.json()["account"]["username"] == "bob_login"
    assert res_verify_bearer.json()["account"]["auth_method"] == "session"

    # Verify via pb_session cookie
    cookie_client = TestClient(create_app())
    cookie_client.cookies.set("pb_session", tok_email)
    res_verify_cookie = cookie_client.get("/auth/verify")
    assert res_verify_cookie.status_code == 200
    assert res_verify_cookie.json()["account"]["username"] == "bob_login"
    assert res_verify_cookie.json()["account"]["auth_method"] == "session"

    # Verify direct primary token via Bearer
    res_verify_primary = bare_client.get(
        "/auth/verify",
        headers={"Authorization": "Bearer pb_tok_admin"},
    )
    assert res_verify_primary.status_code == 200
    assert res_verify_primary.json()["account"]["username"] == "admin"
    assert res_verify_primary.json()["account"]["auth_method"] == "account_token"


# ============================================================================
# 3. Brute-Force Lockout & Admin Unlock
# ============================================================================


def test_bruteforce_lockout_and_admin_unlock(client: TestClient):
    res_reg = client.post(
        "/auth/register",
        json={"username": "lock_user", "email": "lock@example.com", "password": "CorrectPassword123!"},
    )
    assert res_reg.status_code == 200

    # 5 failed password attempts return 401
    for attempt in range(1, 6):
        res_fail = client.post(
            "/auth/login",
            json={"identifier": "lock_user", "password": f"WrongPass_{attempt}"},
        )
        assert res_fail.status_code == 401

    acc_row = db.fetchone(
        "SELECT failed_login_count, locked_until FROM mcp_accounts WHERE username = ?",
        ("lock_user",),
    )
    assert acc_row is not None
    assert int(acc_row["failed_login_count"]) == 5
    assert int(acc_row["locked_until"]) > int(time.time())

    # 6th attempt even with CORRECT password returns 429 with locked_until / Retry-After
    res_locked = client.post(
        "/auth/login",
        json={"identifier": "lock_user", "password": "CorrectPassword123!"},
    )
    assert res_locked.status_code == 429
    assert "Retry-After" in res_locked.headers
    assert "locked" in res_locked.json()["detail"].lower()

    # Admin unlocks via PATCH /auth/accounts/{u}/status -> active
    res_unlock = client.patch(
        "/auth/accounts/lock_user/status",
        json={"status": "active"},
        headers={"Authorization": "Bearer pb_tok_admin"},
    )
    assert res_unlock.status_code == 200
    assert res_unlock.json()["account_status"] == "active"

    acc_after = db.fetchone(
        "SELECT failed_login_count, locked_until FROM mcp_accounts WHERE username = ?",
        ("lock_user",),
    )
    assert int(acc_after["failed_login_count"]) == 0
    assert int(acc_after["locked_until"]) == 0

    # Login succeeds again
    res_ok = client.post(
        "/auth/login",
        json={"identifier": "lock_user", "password": "CorrectPassword123!"},
    )
    assert res_ok.status_code == 200


# ============================================================================
# 4. Self-Service Profile & Password Lifecycle
# ============================================================================


def test_self_service_profile_and_change_password_lifecycle(client: TestClient):
    bare = TestClient(create_app())
    assert bare.get("/auth/me").status_code == 401

    reg = bare.post(
        "/auth/register",
        json={
            "username": "carol_user",
            "email": "carol@example.com",
            "display_name": "Carol",
            "password": "CarolOldPassword1!",
        },
    )
    assert reg.status_code == 200
    sess1 = reg.json()["session_token"]

    # Create second session for carol
    login2 = bare.post(
        "/auth/login",
        json={"identifier": "carol_user", "password": "CarolOldPassword1!"},
    )
    assert login2.status_code == 200
    sess2 = login2.json()["session_token"]

    # Also register another user to verify duplicate email check on PUT /auth/me
    bare.post(
        "/auth/register",
        json={"username": "other_user", "email": "taken@example.com", "password": "OtherPassword1!"},
    )

    # GET /auth/me
    me_res = bare.get("/auth/me", headers={"Authorization": f"Bearer {sess1}"})
    assert me_res.status_code == 200
    assert me_res.json()["account"]["username"] == "carol_user"
    assert me_res.json()["account"]["display_name"] == "Carol"

    # PUT /auth/me duplicate email rejected
    dup_res = bare.put(
        "/auth/me",
        json={"email": "taken@example.com"},
        headers={"Authorization": f"Bearer {sess1}"},
    )
    assert dup_res.status_code == 409

    # PUT /auth/me updates display_name & email
    upd_res = bare.put(
        "/auth/me",
        json={"display_name": "Carol Updated", "email": "carol.new@example.com"},
        headers={"Authorization": f"Bearer {sess1}"},
    )
    assert upd_res.status_code == 200
    assert upd_res.json()["account"]["display_name"] == "Carol Updated"
    assert upd_res.json()["account"]["email"] == "carol.new@example.com"

    # POST /auth/change-password requires current_password when password already exists
    res_no_cur = bare.post(
        "/auth/change-password",
        json={"new_password": "CarolNewPassword2!"},
        headers={"Authorization": f"Bearer {sess1}"},
    )
    assert res_no_cur.status_code == 401

    res_wrong_cur = bare.post(
        "/auth/change-password",
        json={"current_password": "WrongOldPassword!", "new_password": "CarolNewPassword2!"},
        headers={"Authorization": f"Bearer {sess1}"},
    )
    assert res_wrong_cur.status_code == 401

    # Weak new password rejected
    res_weak_new = bare.post(
        "/auth/change-password",
        json={"current_password": "CarolOldPassword1!", "new_password": "short"},
        headers={"Authorization": f"Bearer {sess1}"},
    )
    assert res_weak_new.status_code == 400

    # Valid change-password revokes other sessions (sess2) while keeping current (sess1)
    res_chg = bare.post(
        "/auth/change-password",
        json={
            "current_password": "CarolOldPassword1!",
            "new_password": "CarolNewPassword2!",
            "revoke_other_sessions": True,
        },
        headers={"Authorization": f"Bearer {sess1}"},
    )
    assert res_chg.status_code == 200
    assert res_chg.json()["has_password"] is True

    # Current session remains valid; other session is revoked
    assert bare.get("/auth/me", headers={"Authorization": f"Bearer {sess1}"}).status_code == 200
    assert bare.get("/auth/verify", headers={"Authorization": f"Bearer {sess2}"}).status_code == 401

    # Old password fails; new password works
    assert bare.post(
        "/auth/login",
        json={"identifier": "carol_user", "password": "CarolOldPassword1!"},
    ).status_code == 401
    assert bare.post(
        "/auth/login",
        json={"identifier": "carol_user", "password": "CarolNewPassword2!"},
    ).status_code == 200


# ============================================================================
# 5. Session Management & Logout
# ============================================================================


def test_session_listing_revocation_and_logout(client: TestClient):
    app = create_app()
    c1 = TestClient(app)

    reg = c1.post(
        "/auth/register",
        json={"username": "dave_sess", "email": "dave@example.com", "password": "DavePassword123!"},
    )
    assert reg.status_code == 200
    s1 = reg.json()["session_token"]

    s2 = c1.post(
        "/auth/login",
        json={"identifier": "dave_sess", "password": "DavePassword123!"},
    ).json()["session_token"]
    s3 = c1.post(
        "/auth/login",
        json={"identifier": "dave_sess", "password": "DavePassword123!"},
    ).json()["session_token"]

    # Another user cannot revoke Dave's session
    eve_reg = c1.post(
        "/auth/register",
        json={"username": "eve_other", "email": "eve@example.com", "password": "EvePassword123!"},
    )
    eve_sess = eve_reg.json()["session_token"]

    # List sessions from s1
    list_res = c1.get("/auth/sessions", headers={"Authorization": f"Bearer {s1}"})
    assert list_res.status_code == 200
    sessions = list_res.json()["sessions"]
    assert len(sessions) == 3
    current_sessions = [s for s in sessions if s["is_current"]]
    other_sessions = [s for s in sessions if not s["is_current"]]
    assert len(current_sessions) == 1
    assert len(other_sessions) == 2

    target_other_id = other_sessions[0]["session_id"]

    # Cross-user revocation denied with 403
    res_cross = c1.delete(
        f"/auth/sessions/{target_other_id}",
        headers={"Authorization": f"Bearer {eve_sess}"},
    )
    assert res_cross.status_code == 403

    # Delete specific session
    res_del = c1.delete(
        f"/auth/sessions/{target_other_id}",
        headers={"Authorization": f"Bearer {s1}"},
    )
    assert res_del.status_code == 200

    # Verify remaining sessions count is 2
    sessions_after_del = c1.get("/auth/sessions", headers={"Authorization": f"Bearer {s1}"}).json()["sessions"]
    assert len(sessions_after_del) == 2

    # Revoke-others revokes the remaining non-current session
    res_rev_others = c1.post(
        "/auth/sessions/revoke-others",
        headers={"Authorization": f"Bearer {s1}"},
    )
    assert res_rev_others.status_code == 200
    assert res_rev_others.json()["revoked_count"] == 1

    # Both s2 and s3 are now invalid; s1 is still valid
    assert c1.get("/auth/verify", headers={"Authorization": f"Bearer {s2}"}).status_code == 401
    assert c1.get("/auth/verify", headers={"Authorization": f"Bearer {s3}"}).status_code == 401
    assert c1.get("/auth/verify", headers={"Authorization": f"Bearer {s1}"}).status_code == 200

    # Logout invalidates s1 and clears cookie
    res_logout = c1.post("/auth/logout", headers={"Authorization": f"Bearer {s1}"})
    assert res_logout.status_code == 200
    assert c1.get("/auth/verify", headers={"Authorization": f"Bearer {s1}"}).status_code == 401
    assert c1.get("/auth/me", headers={"Authorization": f"Bearer {s1}"}).status_code == 401


# ============================================================================
# 6. Personal API Keys: Privilege Ceiling, Project Scope & Revocation
# ============================================================================


def test_personal_api_keys_ceiling_project_scope_and_revocation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    # Admin creates a reader account and a collaborator account
    assert client.post(
        "/auth/accounts",
        json={
            "username": "reader_u",
            "token": "pb_tok_reader_u",
            "role": "reader",
            "password": "ReaderPass123!",
        },
        headers={"Authorization": "Bearer pb_tok_admin"},
    ).status_code == 200

    assert client.post(
        "/auth/accounts",
        json={
            "username": "collab_u",
            "token": "pb_tok_collab_u",
            "role": "collaborator",
            "password": "CollabPass123!",
        },
        headers={"Authorization": "Bearer pb_tok_admin"},
    ).status_code == 200

    # Privilege ceiling: reader cannot mint collaborator or admin key
    res_r_collab = client.post(
        "/auth/api-keys",
        json={"name": "escalate-collab", "role_scope": "collaborator"},
        headers={"Authorization": "Bearer pb_tok_reader_u"},
    )
    assert res_r_collab.status_code == 403

    res_r_admin = client.post(
        "/auth/api-keys",
        json={"name": "escalate-admin", "role_scope": "admin"},
        headers={"Authorization": "Bearer pb_tok_reader_u"},
    )
    assert res_r_admin.status_code == 403

    # Privilege ceiling: collaborator cannot mint admin key
    res_c_admin = client.post(
        "/auth/api-keys",
        json={"name": "collab-escalate-admin", "role_scope": "admin"},
        headers={"Authorization": "Bearer pb_tok_collab_u"},
    )
    assert res_c_admin.status_code == 403

    # Collaborator CAN mint a scoped key for proj-a
    res_pat = client.post(
        "/auth/api-keys",
        json={
            "name": "ci-proj-a",
            "role_scope": "reader",
            "allowed_projects": "proj-a",
            "expires_in_days": 7,
        },
        headers={"Authorization": "Bearer pb_tok_collab_u"},
    )
    assert res_pat.status_code == 200, res_pat.text
    raw_pat = res_pat.json()["api_key"]
    key_meta = res_pat.json()["key"]
    key_id = key_meta["key_id"]
    assert raw_pat.startswith("pb_pat_")
    assert key_meta["role_scope"] == "reader"
    assert key_meta["allowed_projects"] == "proj-a"

    # List keys: caller sees masked prefix, non-admin all_users=true blocked with 403
    list_res = client.get("/auth/api-keys", headers={"Authorization": "Bearer pb_tok_collab_u"})
    assert list_res.status_code == 200
    keys = list_res.json()["api_keys"]
    assert len(keys) == 1
    assert keys[0]["key_id"] == key_id
    assert keys[0]["key_prefix"].startswith("pb_pat_")
    assert "token_hash" not in keys[0]

    assert (
        client.get(
            "/auth/api-keys?all_users=true",
            headers={"Authorization": "Bearer pb_tok_collab_u"},
        ).status_code
        == 403
    )

    # Project scope enforcement under PB_REQUIRE_REST_AUTH=true
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")

    # Anonymous rejected with 401
    assert client.get("/memory/stats?user_id=proj-a").status_code == 401

    # PAT scoped to proj-a allows proj-a
    res_proj_a = client.get(
        "/memory/stats?user_id=proj-a",
        headers={"Authorization": f"Bearer {raw_pat}"},
    )
    assert res_proj_a.status_code == 200, res_proj_a.text
    assert res_proj_a.json()["user_id"] == "proj-a"

    # PAT scoped to proj-a blocks proj-b with 403
    res_proj_b = client.get(
        "/memory/stats?user_id=proj-b",
        headers={"Authorization": f"Bearer {raw_pat}"},
    )
    assert res_proj_b.status_code == 403
    assert "not authorized for project 'proj-b'" in res_proj_b.json()["detail"]

    # Revoke PAT -> immediately rejected with 401 everywhere
    res_revoke = client.delete(
        f"/auth/api-keys/{key_id}",
        headers={"Authorization": "Bearer pb_tok_collab_u"},
    )
    assert res_revoke.status_code == 200

    assert client.get("/auth/verify", headers={"Authorization": f"Bearer {raw_pat}"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {raw_pat}"}).status_code == 401
    assert (
        client.get(
            "/memory/stats?user_id=proj-a",
            headers={"Authorization": f"Bearer {raw_pat}"},
        ).status_code
        == 401
    )


# ============================================================================
# 7. Admin Governance, Settings, & Audit Trail
# ============================================================================


def test_admin_governance_settings_and_audit_logs(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    # Create target user via self-registration
    reg = client.post(
        "/auth/register",
        json={"username": "gov_user", "email": "gov@example.com", "password": "GovPassword123!"},
    )
    assert reg.status_code == 200
    gov_sess = reg.json()["session_token"]

    # Non-admin blocked from /auth/audit-logs & /auth/settings with 403
    assert client.get("/auth/audit-logs", headers={"Authorization": f"Bearer {gov_sess}"}).status_code == 403
    assert client.get("/auth/settings", headers={"Authorization": f"Bearer {gov_sess}"}).status_code == 403
    assert (
        client.put(
            "/auth/settings",
            json={"allow_registration": False},
            headers={"Authorization": f"Bearer {gov_sess}"},
        ).status_code
        == 403
    )

    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")
    assert client.get("/auth/audit-logs", headers={"Authorization": f"Bearer {gov_sess}"}).status_code == 403
    assert client.get("/auth/settings", headers={"Authorization": f"Bearer {gov_sess}"}).status_code == 403

    admin_hdr = {"Authorization": "Bearer pb_tok_admin"}

    # Fetch gov_user's primary token before suspending
    accounts_list_pre = client.get("/auth/accounts", headers=admin_hdr).json()["accounts"]
    gov_primary_tok = next(a for a in accounts_list_pre if a["username"] == "gov_user")["token"]

    # Suspending sole active admin is rejected with 400
    res_sole_admin = client.patch(
        "/auth/accounts/admin/status",
        json={"status": "suspended"},
        headers=admin_hdr,
    )
    assert res_sole_admin.status_code == 400
    assert "last active admin" in res_sole_admin.json()["detail"].lower()

    # Suspend gov_user -> revokes sessions and blocks login/verify
    res_susp = client.patch(
        "/auth/accounts/gov_user/status",
        json={"status": "suspended"},
        headers=admin_hdr,
    )
    assert res_susp.status_code == 200
    assert res_susp.json()["account_status"] == "suspended"

    # Existing session revoked in DB and rejected with 401; primary token rejected with 403 (suspended)
    sess_row = db.fetchone("SELECT revoked_at FROM auth_sessions WHERE username = ?", ("gov_user",))
    assert sess_row is not None and sess_row["revoked_at"] is not None
    assert client.get("/auth/verify", headers={"Authorization": f"Bearer {gov_sess}"}).status_code == 401
    res_susp_verify = client.get("/auth/verify", headers={"Authorization": f"Bearer {gov_primary_tok}"})
    assert res_susp_verify.status_code == 403
    assert "suspended" in res_susp_verify.json()["detail"].lower()
    assert (
        client.post(
            "/auth/login",
            json={"identifier": "gov_user", "password": "GovPassword123!"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/auth/login",
            json={"token": gov_primary_tok},
        ).status_code
        == 403
    )

    # Reactivate gov_user
    res_react = client.patch(
        "/auth/accounts/gov_user/status",
        json={"status": "active"},
        headers=admin_hdr,
    )
    assert res_react.status_code == 200
    login_after_react = client.post(
        "/auth/login",
        json={"identifier": "gov_user", "password": "GovPassword123!"},
    )
    assert login_after_react.status_code == 200
    gov_sess_2 = login_after_react.json()["session_token"]

    # Admin rotates primary token
    accounts_list = client.get("/auth/accounts", headers=admin_hdr).json()["accounts"]
    gov_acc_before = next(a for a in accounts_list if a["username"] == "gov_user")
    old_primary_tok = gov_acc_before["token"]

    res_rot = client.post("/auth/accounts/gov_user/rotate-token", headers=admin_hdr)
    assert res_rot.status_code == 200
    new_primary_tok = res_rot.json()["token"]
    assert new_primary_tok.startswith("pb_tok_")
    assert new_primary_tok != old_primary_tok
    assert client.get("/auth/verify", headers={"Authorization": f"Bearer {old_primary_tok}"}).status_code == 401
    assert client.get("/auth/verify", headers={"Authorization": f"Bearer {new_primary_tok}"}).status_code == 200

    # Admin resets password -> revokes existing sessions, old password fails, new password works
    res_reset = client.post(
        "/auth/accounts/gov_user/reset-password",
        json={"new_password": "AdminResetPass99!"},
        headers=admin_hdr,
    )
    assert res_reset.status_code == 200
    assert client.get("/auth/verify", headers={"Authorization": f"Bearer {gov_sess_2}"}).status_code == 401
    assert (
        client.post(
            "/auth/login",
            json={"identifier": "gov_user", "password": "GovPassword123!"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login",
            json={"identifier": "gov_user", "password": "AdminResetPass99!"},
        ).status_code
        == 200
    )

    # Admin settings: allow_registration=False blocks /auth/register with 403
    res_get_settings = client.get("/auth/settings", headers=admin_hdr)
    assert res_get_settings.status_code == 200
    assert res_get_settings.json()["allow_registration"] is True

    res_put_settings = client.put(
        "/auth/settings",
        json={"allow_registration": False, "default_signup_role": "reader"},
        headers=admin_hdr,
    )
    assert res_put_settings.status_code == 200
    assert res_put_settings.json()["allow_registration"] is False
    assert res_put_settings.json()["default_signup_role"] == "reader"

    res_reg_blocked = client.post(
        "/auth/register",
        json={"username": "blocked_reg", "email": "blocked@example.com", "password": "ValidPassword123!"},
    )
    assert res_reg_blocked.status_code == 403

    # Re-enable registration -> new signup receives configured 'reader' role
    assert (
        client.put(
            "/auth/settings",
            json={"allow_registration": True},
            headers=admin_hdr,
        ).status_code
        == 200
    )
    res_reg_reader = client.post(
        "/auth/register",
        json={"username": "new_reader", "email": "new_reader@example.com", "password": "ValidPassword123!"},
    )
    assert res_reg_reader.status_code == 200
    assert res_reg_reader.json()["account"]["role"] == "reader"

    # Audit logs filtering by event_type and username
    res_logs_status = client.get(
        "/auth/audit-logs?event_type=account_status_changed&username=gov_user",
        headers=admin_hdr,
    )
    assert res_logs_status.status_code == 200
    logs = res_logs_status.json()["logs"]
    assert len(logs) >= 2
    assert all(l["event_type"] == "account_status_changed" for l in logs)
    assert all(l["actor_username"] == "gov_user" or l["target_username"] == "gov_user" for l in logs)

    res_logs_reset = client.get(
        "/auth/audit-logs?event_type=account_password_reset&username=gov_user",
        headers=admin_hdr,
    )
    assert res_logs_reset.status_code == 200
    assert len(res_logs_reset.json()["logs"]) == 1


# ============================================================================
# 8. Static Workbench Asset Integrity
# ============================================================================


def test_static_workbench_assets_integrity(client: TestClient):
    res_index = client.get("/dashboard/")
    assert res_index.status_code == 200
    assert "text/html" in res_index.headers.get("content-type", "").lower()
    assert len(res_index.text.strip()) > 100

    res_css = client.get("/dashboard/style.css")
    assert res_css.status_code == 200
    assert "css" in res_css.headers.get("content-type", "").lower()
    assert len(res_css.text.strip()) > 100

    res_js = client.get("/dashboard/app.js")
    assert res_js.status_code == 200
    assert "javascript" in res_js.headers.get("content-type", "").lower()
    assert len(res_js.text.strip()) > 100
