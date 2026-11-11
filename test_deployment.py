"""Verify remote-client access without hitting a live DB or model."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.novel_crud_api import get_database
from core.config import get_settings
from main import app


@pytest.mark.parametrize("legacy_key", ["", "legacy-key"])
def test_library_works_without_access_key_even_with_old_settings(monkeypatch, legacy_key):
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
                assert projects.status_code == 200
                assert projects.json()["items"] == []
    finally:
        get_settings.cache_clear()


def test_legacy_android_probe_and_cors():
    with TestClient(app) as client:
        response = client.get("/api/v1/access", headers={"Origin": "https://localhost"})
        assert response.status_code == 200
        assert response.json() == {"authorized": True}
        assert response.headers["access-control-allow-origin"] == "https://localhost"
        preflight = client.options("/api/v1/projects", headers={
            "Origin": "https://localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        assert preflight.status_code == 200
        assert "access-control-allow-origin" not in client.get(
            "/api/v1/access", headers={"Origin": "https://unrelated.test"},
        ).headers


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
