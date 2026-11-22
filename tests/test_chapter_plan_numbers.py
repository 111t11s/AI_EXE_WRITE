import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock

from api import novel_writing_api as api
from api.account_api import Account, get_current_account
from api.novel_crud_api import get_database
from main import app


def test_next_chapter_number_endpoint(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_database, lambda: object())
    monkeypatch.setitem(app.dependency_overrides, get_current_account,
                        lambda: Account(id=1, username="writer", display_name="作者"))
    monkeypatch.setattr(api, "require_resource", Mock())
    monkeypatch.setattr(api, "next_free_chapter_number", Mock(return_value=14))

    with TestClient(app) as client:
        response = client.get("/api/v1/writing/projects/2/chapter-plan/next")

    assert response.status_code == 200, response.text
    assert response.json() == {"next_chapter_number": 14}


def test_plan_context_rejects_used_range_with_next_hint(monkeypatch):
    async def listing(session, table, **filters):
        return [{"id": 1}] if filters.get("chapter_number") == 3 else []

    monkeypatch.setattr(api, "listing", listing)
    monkeypatch.setattr(api, "volume_context", AsyncMock(return_value={"project": {"id": 2}}))
    monkeypatch.setattr(api, "previous_chapter", AsyncMock(return_value=None))

    with pytest.raises(api.HTTPException) as error:
        import asyncio
        asyncio.run(api.plan_context(object(), 4, 3, 2, 9))

    assert error.value.status_code == 409
    assert "章号 3 已被使用" in error.value.detail and "下一个可用章号是 9" in error.value.detail
