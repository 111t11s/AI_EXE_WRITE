"""Verify remote-client access without hitting a live DB or model."""

import json
import re

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.novel_crud_api import get_database
from api.account_api import Account, get_current_account
from core.config import get_settings
from main import app


@pytest.mark.parametrize("legacy_key", ["", "legacy-key"])
def test_library_requires_account_even_with_legacy_access_settings(monkeypatch, legacy_key):
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.all.return_value = []
    monkeypatch.setitem(app.dependency_overrides, get_database, lambda: engine)
    try:
        with monkeypatch.context() as env:
            env.setenv("ACCESS_KEY_REQUIRED", "true")
            env.setenv("APP_ACCESS_KEY", legacy_key)
            get_settings.cache_clear()
            with TestClient(app) as client:
                assert client.get("/healthz").status_code == 200
                page = client.get("/")
                assert page.status_code == 200
                assert 'id="access-dialog"' not in page.text
                projects = client.get("/api/v1/projects")
                assert projects.status_code == 401
                with monkeypatch.context() as auth:
                    auth.setitem(app.dependency_overrides, get_current_account,
                                 lambda: Account(id=1, username="writer", display_name="作者"))
                    projects = client.get("/api/v1/projects")
                    assert projects.status_code == 200 and projects.json()["items"] == []
    finally:
        get_settings.cache_clear()


def test_legacy_android_probe_and_cors():
    with TestClient(app) as client:
        response = client.get("/api/v1/access", headers={"Origin": "https://localhost"})
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == "https://localhost"
        preflight = client.options("/api/v1/projects", headers={
            "Origin": "https://localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        })
        assert preflight.status_code == 200
        assert "access-control-allow-origin" not in client.get(
            "/api/v1/access", headers={"Origin": "https://unrelated.test"},
        ).headers


def test_frontend_revalidates_cached_modules_after_updates():
    with TestClient(app) as client:
        for path in ("/", "/bootstrap.js", "/workbench.js", "/api-client.js", "/generation-stream.js", "/workbench.css", "/runtime-config.js"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
            cached = client.get(path, headers={"If-None-Match": response.headers["etag"]})
            assert cached.status_code == 304
            assert cached.headers["cache-control"] == "no-cache"


def test_frontend_versions_the_entire_module_graph():
    with TestClient(app) as client:
        html = client.get("/").text
        mapping = re.search(r'<script type="importmap">(.*?)</script>', html, re.S)
        assert mapping is not None
        imports = json.loads(mapping[1])["imports"]
        assert set(imports) == {"/workbench.js", "/api-client.js", "/generation-stream.js"}
        versions = set()
        for original, versioned in imports.items():
            assert versioned.startswith(original + "?v=")
            versions.add(versioned.split("?v=", 1)[1])
            assert client.get(versioned).status_code == 200
        assert len(versions) == 1
        assert 'type="importmap"' in html[:html.index('src="/bootstrap.js')]
        assert "import('./workbench.js')" in client.get("/bootstrap.js").text


def test_mcp_lists_projects_without_access_key():
    import asyncio
    import httpx
    from agents.mcp.server import create_server
    from mcp.shared.memory import create_connected_server_and_client_session

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [], "offset": 0, "limit": 20, "has_more": False})

    async def run():
        server = create_server("http://test/api/v1", transport=httpx.MockTransport(respond))
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("list_novel_project", {})
            assert not result.isError

    asyncio.run(run())
    assert requests and "x-aitxt-access-key" not in requests[0].headers
