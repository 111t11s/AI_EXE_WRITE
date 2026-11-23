"""模型配置选择：.env 档案、接口清单与生成请求的参数传递。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import model_api, novel_writing_api
from api.writing_schemas import WritingInput
from core import models


def settings(**overrides):
    values = dict(API_KEY="main-key", PROXY_URL="https://main.test/v1",
                  DEEPSEEK_API_KEY="deep-key", DEEPSEEK_PROXY_URL="https://api.deepseek.com",
                  DEEPSEEK_MODEL_NAME="deepseek-flash", REQUEST_TIMEOUT=40)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_model_error_diagnostics_hide_keys():
    exc = SimpleNamespace(body={"error": {"message": "invalid api key sk-abcdef123456 for model gpt-6-sol"}})
    reason = models.safe_error_reason(exc)
    assert "invalid api key" in reason and "sk-abcdef" not in reason
    assert models.endpoint_host("https://www.example.com/v1") == "www.example.com"
    assert models.endpoint_host("") == "-"
    assert models.safe_error_reason(SimpleNamespace(body=None, message="")) == ""
    edge = models.safe_error_reason(SimpleNamespace(
        body="<html><body><h1>403 Forbidden</h1><p>Cloudflare Ray ID: abc</p></body></html>"))
    assert "403 Forbidden" in edge and "<" not in edge


def test_default_selection_keeps_the_existing_connection(monkeypatch):
    monkeypatch.setattr(models, "get_settings", lambda: settings())
    assert models.resolve_profile(None) is None
    assert models.resolve_profile("") is None
    assert models.resolve_profile("default") is None


def test_gemini_flash_is_default_and_pro_shares_its_credentials(monkeypatch):
    configured = settings(GEMINI_API_KEY="gem-key", GEMINI_PROXY_URL="https://gemini.test/v1",
                         GEMINI_3_8_FLASH="gemini-3.8-flash", GEMINI_3_1_PRO="gemini-3.1-pro-preview")
    monkeypatch.setattr(models, "get_settings", lambda: configured)

    default = models.resolve_profile(None)
    assert default.connection() == {"model": "gemini-3.8-flash", "api_key": "gem-key",
                                    "base_url": "https://gemini.test/v1", "timeout": 40}
    assert default.public() == {"id": "default", "label": "Gemini 3.8 Flash", "model": "gemini-3.8-flash"}
    pro = models.resolve_profile("gemini-pro")
    assert pro.connection() == {"model": "gemini-3.1-pro-preview", "api_key": "gem-key",
                                "base_url": "https://gemini.test/v1", "timeout": 40}


def test_selected_profile_carries_its_own_credentials(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "gpt-test")
    monkeypatch.setattr(models, "get_settings", lambda: settings())
    profile = models.resolve_profile("deepseek")
    assert profile.connection() == {"model": "deepseek-flash", "api_key": "deep-key",
                                    "base_url": "https://api.deepseek.com", "timeout": 40}
    assert profile.public() == {"id": "deepseek", "label": "DeepSeek", "model": "deepseek-flash"}


def test_incomplete_or_unknown_profiles_are_rejected(monkeypatch):
    monkeypatch.setattr(models, "get_settings", lambda: settings(DEEPSEEK_API_KEY=""))
    with pytest.raises(models.ModelProfileError):
        models.resolve_profile("deepseek")
    with pytest.raises(models.ModelProfileError):
        models.resolve_profile("unknown")


def test_model_endpoint_lists_available_profiles_without_secrets(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "gpt-test")
    monkeypatch.setattr(models, "get_settings", lambda: settings())
    payload = model_api.list_models(object())
    assert payload["items"] == [
        {"id": "default", "label": "主模型", "model": "gpt-test"},
        {"id": "deepseek", "label": "DeepSeek", "model": "deepseek-flash"},
    ]
    assert payload["default"] == "default"
    assert all("api_key" not in item and "base_url" not in item for item in payload["items"])

    monkeypatch.setattr(models, "get_settings", lambda: settings(
        GEMINI_API_KEY="gem-key", GEMINI_PROXY_URL="https://gemini.test/v1",
        GEMINI_3_8_FLASH="gemini-3.8-flash", GEMINI_3_1_PRO="gemini-3.1-pro-preview"))
    payload = model_api.list_models(object())
    assert [item["id"] for item in payload["items"]] == ["default", "gemini-pro", "deepseek"]
    assert payload["items"][0]["label"] == "Gemini 3.8 Flash"
    assert payload["items"][1]["label"] == "Gemini 3.1 Pro Preview"
    assert all("api_key" not in item and "base_url" not in item for item in payload["items"])

    monkeypatch.setattr(models, "get_settings", lambda: settings(DEEPSEEK_API_KEY=""))
    assert [item["id"] for item in model_api.list_models(object())["items"]] == ["default"]


def test_writing_generation_passes_the_selected_profile(monkeypatch):
    captured = {}

    def fake_generate(role, context, profile=None):
        captured.update(role=role, context=context, profile=profile)
        return {"ok": True}

    monkeypatch.setattr(novel_writing_api.agents, "generate", fake_generate)
    monkeypatch.setattr(novel_writing_api, "resolve_profile", lambda model: ("profile", model))
    role = SimpleNamespace(title="测试任务")
    result = asyncio.run(novel_writing_api.generate(
        role, {"project": {}}, WritingInput(model="deepseek", instruction="加强冲突")))
    assert result == {"ok": True}
    assert captured["profile"] == ("profile", "deepseek")
    # 模型配置只用于选择调用参数，不进入发给模型的资料。
    assert captured["context"]["request"] == {"instruction": "加强冲突"}


def test_unknown_profile_fails_before_the_model_call(monkeypatch):
    def reject(model):
        raise models.ModelProfileError("所选模型不存在，请重新选择")

    monkeypatch.setattr(novel_writing_api, "resolve_profile", reject)
    monkeypatch.setattr(novel_writing_api.agents, "generate",
                        lambda *args, **kwargs: pytest.fail("模型不应被调用"))
    with pytest.raises(HTTPException) as error:
        asyncio.run(novel_writing_api.generate(SimpleNamespace(title="测试任务"), {}, WritingInput(model="nope")))
    assert error.value.status_code == 422


def test_batch_draft_uses_the_same_profile_for_body_and_progress(monkeypatch):
    from api.writing_schemas import BatchDraftRequest, CharacterProgressReceipt, DraftReceipt

    seen = {}

    async def fake_draft(chapter_id, payload, user, engine):
        seen["draft"] = payload.model
        return DraftReceipt(chapter_id=chapter_id, word_count=10)

    async def fake_progress(chapter_id, payload, user, engine):
        seen["progress"] = payload.model
        return CharacterProgressReceipt(chapter_id=chapter_id, updated_count=2)

    monkeypatch.setattr(novel_writing_api, "validate_batch_chapters",
                        lambda *args: [{"id": 7, "chapter_number": 1, "title": "第一章"}])
    monkeypatch.setattr(novel_writing_api, "write_draft", fake_draft)
    monkeypatch.setattr(novel_writing_api, "analyze_character_progress", fake_progress)
    receipt = asyncio.run(novel_writing_api.write_batch_drafts(
        1, BatchDraftRequest(chapter_ids=[7], model="deepseek"), SimpleNamespace(id=1), object()))
    assert seen == {"draft": "deepseek", "progress": "deepseek"}
    assert receipt.completed_count == 1
