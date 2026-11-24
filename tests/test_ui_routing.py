"""Verification suite for server-side clean multi-route Web App & SPA routing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from projectbrain.core.config import env
from projectbrain.core.db import db
from projectbrain.server.api import create_app
from projectbrain.server.routes import auth as auth_routes


CANONICAL_APP_ROUTES = [
    "/app/overview",
    "/app/workspace/projects",
    "/app/workspace/sources",
    "/app/workspace/memories",
    "/app/workspace/search?project=chien334/Project-Brain&branch=main&q=tenant",
    "/app/graph/explorer?project=chien334/Project-Brain&symbol=ProjectDatabaseManager",
    "/app/graph/diff",
    "/app/kb",
    "/app/copilot",
    "/app/platform/plugins",
    "/app/platform/qa",
    "/app/platform/mcp",
    "/app/settings/ai",
    "/app/settings/profile",
    "/app/settings/iam",
]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """FastAPI TestClient isolated from any persistent workspace DB."""
    orig_db_url = env.database_url
    orig_db_path = env.db_path
    orig_conn = db.conn
    orig_is_postgres = db.is_postgres
    orig_require_rest_env = os.environ.get("PB_REQUIRE_REST_AUTH")

    temp_db_path = tmp_path / "ui_routing_test.db"
    monkeypatch.setenv("PB_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("OM_DB_PATH", str(temp_db_path))
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "false")
    monkeypatch.setattr(env, "database_url", f"sqlite:///{temp_db_path}")
    monkeypatch.setattr(env, "db_path", str(temp_db_path))

    db.conn = None
    db.is_postgres = False
    auth_routes._ensured_conn = None
    auth_routes._ensured_db_url = None
    db.connect()
    auth_routes.init_auth_system()

    try:
        yield TestClient(create_app())
    finally:
        temp_conn = db.conn
        if temp_conn is not None and temp_conn is not orig_conn:
            try:
                temp_conn.close()
            except Exception:
                pass
        env.database_url = orig_db_url
        env.db_path = orig_db_path
        if orig_require_rest_env is None:
            os.environ.pop("PB_REQUIRE_REST_AUTH", None)
        else:
            os.environ["PB_REQUIRE_REST_AUTH"] = orig_require_rest_env
        db.conn = orig_conn
        db.is_postgres = orig_is_postgres
        auth_routes._ensured_conn = None
        auth_routes._ensured_db_url = None


def _assert_html_response(response) -> None:
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "").lower()
    assert len(response.text.strip()) > 100


def test_root_and_app_root_redirects(client: TestClient) -> None:
    res_no_redirect = client.get("/", follow_redirects=False)
    assert res_no_redirect.status_code == 307
    assert res_no_redirect.headers.get("location") == "/app/overview"

    res_followed = client.get("/", follow_redirects=True)
    _assert_html_response(res_followed)

    res_query = client.get("/?project=demo/app&branch=dev", follow_redirects=False)
    assert res_query.status_code == 307
    assert res_query.headers.get("location") == "/app/overview?project=demo/app&branch=dev"

    res_app_root = client.get("/app", follow_redirects=False)
    assert res_app_root.status_code == 307
    assert res_app_root.headers.get("location") == "/app/overview"

    res_app_root_query = client.get("/app?project=demo/app&branch=dev", follow_redirects=False)
    assert res_app_root_query.status_code == 307
    assert res_app_root_query.headers.get("location") == "/app/overview?project=demo/app&branch=dev"


def test_auth_spa_routes_return_html(client: TestClient) -> None:
    for path in ("/login", "/register"):
        res = client.get(path)
        _assert_html_response(res)


@pytest.mark.parametrize("route", CANONICAL_APP_ROUTES)
def test_canonical_app_routes_return_html(client: TestClient, route: str) -> None:
    res = client.get(route)
    _assert_html_response(res)


def test_static_and_legacy_dashboard_assets_and_spa_fallback(client: TestClient) -> None:
    res_static_css = client.get("/static/style.css")
    assert res_static_css.status_code == 200
    assert "css" in res_static_css.headers.get("content-type", "").lower()
    assert len(res_static_css.text.strip()) > 100

    res_static_js = client.get("/static/app.js")
    assert res_static_js.status_code == 200
    assert "javascript" in res_static_js.headers.get("content-type", "").lower()
    assert len(res_static_js.text.strip()) > 100

    res_dash_index = client.get("/dashboard/")
    _assert_html_response(res_dash_index)

    res_dash_css = client.get("/dashboard/style.css")
    assert res_dash_css.status_code == 200
    assert "css" in res_dash_css.headers.get("content-type", "").lower()
    assert len(res_dash_css.text.strip()) > 100

    res_dash_js = client.get("/dashboard/app.js")
    assert res_dash_js.status_code == 200
    assert "javascript" in res_dash_js.headers.get("content-type", "").lower()
    assert len(res_dash_js.text.strip()) > 100

    res_dash_spa = client.get("/dashboard/graph/explorer")
    _assert_html_response(res_dash_spa)


def test_spa_path_traversal_falls_back_to_index_html(client: TestClient) -> None:
    index_html = (Path(__file__).resolve().parents[1] / "src" / "projectbrain" / "server" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    for traversal_path in ("/app/%2e%2e/api.py", "/dashboard/%2e%2e/api.py"):
        res = client.get(traversal_path)
        _assert_html_response(res)
        assert res.text == index_html


def test_ui_routes_exempt_when_rest_auth_required(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PB_REQUIRE_REST_AUTH", "true")

    res_root = client.get("/", follow_redirects=False)
    assert res_root.status_code == 307
    assert res_root.headers.get("location") == "/app/overview"

    for html_route in ("/login", "/register", "/app/overview", "/app/graph/explorer", "/dashboard/", "/dashboard/graph/explorer"):
        res = client.get(html_route)
        _assert_html_response(res)

    res_css = client.get("/static/style.css")
    assert res_css.status_code == 200
    assert "css" in res_css.headers.get("content-type", "").lower()

    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
    assert "javascript" in res_js.headers.get("content-type", "").lower()

    res_protected = client.get("/memory/list")
    assert res_protected.status_code == 401
