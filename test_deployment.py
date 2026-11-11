"""Verify remote-client access without hitting a live DB or model."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from core.config import get_settings
from main import app


def test_private_access_and_android_cors(monkeypatch):
    monkeypatch.setattr(get_settings(), "APP_ACCESS_KEY", SecretStr("friends-only-test"))
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/projects").status_code == 401
    assert client.delete("/api/v1/projects/1").status_code == 401
    assert client.get("/api/v1/access", headers={"X-AITXT-Access-Key": "wrong"}).status_code == 401
    response = client.get("/api/v1/access", headers={
        "X-AITXT-Access-Key": "friends-only-test", "Origin": "https://localhost",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://localhost"
    preflight = client.options("/api/v1/projects", headers={
        "Origin": "https://localhost",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-aitxt-access-key",
    })
    assert preflight.status_code == 200
    assert "access-control-allow-origin" not in client.get(
        "/api/v1/access", headers={"Origin": "https://unrelated.test"},
    ).headers


def test_access_fails_closed_without_required_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "APP_ACCESS_KEY", SecretStr(""))
    monkeypatch.setattr(settings, "ACCESS_KEY_REQUIRED", True)
    assert TestClient(app).get("/api/v1/projects").status_code == 503


def test_mcp_forwards_access_key(monkeypatch):
    import asyncio
    import httpx
    from agents.mcp.server import create_server
    from mcp.shared.memory import create_connected_server_and_client_session

    monkeypatch.setattr(get_settings(), "APP_ACCESS_KEY", SecretStr("mcp-test-key"))
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
    assert requests and requests[0].headers["x-aitxt-access-key"] == "mcp-test-key"
