"""热点抓取解析、题材引用校验与问答预填。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agents import trends
from agents import writing_agents as agents
from api import novel_assistant_api as assistant
from api import trends_api
from api.writing_schemas import TrendIdea, TrendReport, TrendRequest


BAIDU_PAGE = """
<style>.c-single-text-ellipsis { white-space: nowrap; }</style>
<a href="/s?wd=top" class="title_ab "><div class="c-single-text-ellipsis">  置顶的特别报道  </div></a>
<a href="/s?wd=a" class="title_ab "><div class="c-single-text-ellipsis">  年轻人开始反向消费  </div></a>
<div class="hot-index_ab "> 1,234,567 </div>
<a href="/s?wd=b" class="title_ab "><div class="c-single-text-ellipsis">  某地出现罕见极光  </div></a>
<div class="hot-index_ab "> 987,654 </div>
"""


def idea(reference, hook="能被概括成一句梗，评论区会站队"):
    return TrendIdea(genre="都市", setting="现代都市", power_system="无超凡力量", cheat="无金手指",
                     protagonist="普通打工人", goal="改变命运", tone="轻松", length="长篇",
                     title="参考书名", premise="一句话梗概", core_conflict="核心冲突",
                     discussion_hook=hook,
                     selling_points=["卖点一", "卖点二"], reference_topics=[reference])


@pytest.fixture(autouse=True)
def reset_cache():
    trends._cache.update(items=[], updated_at=None, errors=[], sources=[])
    yield
    trends._cache.update(items=[], updated_at=None, errors=[], sources=[])


def test_baidu_parser_drops_featured_item_and_pairs_heat():
    items = trends.parse_baidu(BAIDU_PAGE)
    assert [item["title"] for item in items] == ["年轻人开始反向消费", "某地出现罕见极光"]
    assert [item["heat"] for item in items] == [1234567, 987654]
    assert items[0]["url"].startswith("https://www.baidu.com/s?wd=")


def test_json_source_parser_accepts_common_field_names():
    payload = {"data": [{"word": "热点新闻一", "hot": 12345, "url": "https://example.com/1"},
                        {"title": "热点新闻二", "score": 99}]}
    items = trends.parse_json_source(payload, "example.com")
    assert [(item["title"], item["heat"], item["source"]) for item in items] == [
        ("热点新闻一", 12345, "example.com"), ("热点新闻二", 99, "example.com")]


def test_default_hot_list_keeps_only_ai_digital_entertainment_and_drops_politics(monkeypatch):
    monkeypatch.setattr(trends, "get_settings", lambda: SimpleNamespace(
        TREND_CACHE_MINUTES=30, TREND_TIMEOUT=5, TREND_SOURCE_URLS=""))
    monkeypatch.setattr(trends, "fetch_baidu", lambda timeout: [
        {"title": "AI 助手进入日常办公", "source": "百度热搜", "heat": 1, "url": None},
        {"title": "新款手机影像功能发布", "source": "百度热搜", "heat": 2, "url": None},
        {"title": "新剧官宣开播引发讨论", "source": "百度热搜", "heat": 3, "url": None},
        {"title": "两会讨论 AI 手机政策", "source": "百度热搜", "heat": 4, "url": None},
        {"title": "年轻人开始反向消费", "source": "百度热搜", "heat": 5, "url": None},
    ])

    result = trends.collect_trends()
    assert [row["title"] for row in result["items"]] == [
        "AI 助手进入日常办公", "新款手机影像功能发布", "新剧官宣开播引发讨论"]


def test_trend_defaults_target_requested_categories_and_forbid_politics():
    request = TrendRequest()
    assert request.genre_preference == "AI、数码、娱乐"
    assert "严禁涉及政治" in request.instruction


def test_manual_hot_topics_and_brief_reject_political_content():
    request = TrendRequest(topics=["AI 助手上线", "两会讨论 AI 政策", "新剧官宣开播"])
    topics, _ = trends_api.resolve_topics(request)
    assert topics == ["AI 助手上线", "新剧官宣开播"]
    with pytest.raises(HTTPException) as error:
        trends_api.resolve_topics(TrendRequest(brief="围绕选举和总统写故事"))
    assert error.value.status_code == 422


def test_collect_trends_caches_and_refreshes(monkeypatch):
    calls = []
    monkeypatch.setattr(trends, "get_settings", lambda: SimpleNamespace(
        TREND_CACHE_MINUTES=30, TREND_TIMEOUT=5, TREND_SOURCE_URLS=""))
    monkeypatch.setattr(trends, "fetch_baidu", lambda timeout: calls.append(timeout) or [
        {"title": "AI 助手进入日常办公", "source": "百度热搜", "heat": 1, "url": None}])

    first = trends.collect_trends()
    assert first["cached"] is False and first["sources"] == ["百度热搜"]
    second = trends.collect_trends()
    assert second["cached"] is True and len(calls) == 1
    refreshed = trends.collect_trends(refresh=True)
    assert refreshed["cached"] is False and len(calls) == 2


def test_failed_refresh_keeps_the_previous_items(monkeypatch):
    monkeypatch.setattr(trends, "get_settings", lambda: SimpleNamespace(
        TREND_CACHE_MINUTES=0, TREND_TIMEOUT=5, TREND_SOURCE_URLS=""))
    state = {"fail": False}

    def fetch(timeout):
        if state["fail"]:
            raise RuntimeError("blocked")
        return [{"title": "AI 助手进入日常办公", "source": "百度热搜", "heat": 5, "url": None}]

    monkeypatch.setattr(trends, "fetch_baidu", fetch)
    assert trends.collect_trends()["items"][0]["title"] == "AI 助手进入日常办公"
    state["fail"] = True
    failed = trends.collect_trends(refresh=True)
    assert failed["items"][0]["title"] == "AI 助手进入日常办公"
    assert failed["errors"] and "百度热搜" in failed["errors"][0]


def test_report_must_cite_the_provided_topics():
    topics = ["年轻人开始反向消费", "某地出现罕见极光"]
    valid = TrendReport(overview="概览", ideas=[idea("年轻人开始反向消费"), idea("「某地出现罕见极光」")])
    cleaned = trends_api.clean_report(valid, topics)
    assert [row.reference_topics for row in cleaned.ideas] == [["年轻人开始反向消费"], ["「某地出现罕见极光」"]]

    paraphrased = TrendReport(overview="概览", ideas=[
        idea("年轻人开始反向消费"), idea("年轻人全都开始反向消费了")])
    assert trends_api.clean_report(paraphrased, topics) is None

    incomplete = TrendReport(overview="概览", ideas=[
        idea("年轻人开始反向消费"), idea("某地出现罕见极光", hook="")])
    assert trends_api.clean_report(incomplete, topics) is None


def test_report_drops_ideas_that_introduce_political_content():
    safe = idea("AI 助手进入日常办公")
    political = idea("AI 助手进入日常办公").model_copy(update={"premise": "总统竞选团队使用 AI 争夺选票"})
    report = TrendReport(overview="概览", ideas=[safe, political])
    assert trends_api.clean_report(report, ["AI 助手进入日常办公"]) is None

    report = TrendReport(overview="大模型提升创作效率，但也带来新挑战。", ideas=[safe, safe])
    political_overview = report.model_copy(update={"overview": "两会讨论大模型政策"})
    assert trends_api.clean_report(political_overview, ["AI 助手进入日常办公"]) is None


def test_analysis_retries_once_when_a_recommendation_is_incomplete(monkeypatch):
    calls = []

    async def fake_generate(role, context, payload):
        calls.append(1)
        if len(calls) == 1:
            return TrendReport(overview="概览", ideas=[idea("热点甲"), idea("热点乙", hook="")])
        return TrendReport(overview="概览", ideas=[idea("热点甲"), idea("热点乙")])

    monkeypatch.setattr(trends_api, "generate", fake_generate)
    receipt = asyncio.run(trends_api.analyze(TrendRequest(topics=["热点甲", "热点乙"], idea_count=2)))
    assert len(calls) == 2
    assert [row.reference_topics for row in receipt.ideas] == [["热点甲"], ["热点乙"]]


def test_analysis_retries_once_after_a_format_failure(monkeypatch):
    calls = []

    async def fake_generate(role, context, payload):
        calls.append(1)
        if len(calls) == 1:
            raise HTTPException(502, "模型返回的题材格式不符合要求：第 1 条推荐的基调不符合要求，请重试生成")
        return TrendReport(overview="概览", ideas=[idea("热点甲"), idea("热点乙")])

    monkeypatch.setattr(trends_api, "generate", fake_generate)
    receipt = asyncio.run(trends_api.analyze(TrendRequest(topics=["热点甲", "热点乙"], idea_count=2)))
    assert len(calls) == 2 and len(receipt.ideas) == 2


def test_analysis_uses_selected_topics_and_keeps_receipt(monkeypatch):
    captured = {}

    async def fake_generate(role, context, payload):
        captured.update(role=role, context=context, payload=payload)
        return TrendReport(overview="概览", ideas=[idea("热点甲"), idea("热点乙")])

    monkeypatch.setattr(trends_api, "generate", fake_generate)
    receipt = asyncio.run(trends_api.analyze(TrendRequest(topics=["热点甲", "热点乙"], idea_count=2)))
    assert captured["role"] is not None and captured["context"]["topics"] == ["热点甲", "热点乙"]
    assert receipt.topics == ["热点甲", "热点乙"]
    assert [row.reference_topics for row in receipt.ideas] == [["热点甲"], ["热点乙"]]
    assert receipt.ideas[0].discussion_hook == "能被概括成一句梗，评论区会站队"
    assert receipt.generated_at


def test_analysis_accepts_one_selected_hot_topic(monkeypatch):
    captured = {}

    async def fake_generate(role, context, payload):
        captured["topics"] = context["topics"]
        return TrendReport(overview="围绕热点延展出的不同故事方向。", ideas=[
            idea("热点衍生方向甲"), idea("热点衍生方向乙")])

    monkeypatch.setattr(trends_api, "generate", fake_generate)
    receipt = asyncio.run(trends_api.analyze(TrendRequest(topics=["单条有效热点"], idea_count=2)))
    assert captured["topics"] == ["单条有效热点"]
    assert receipt.topics == ["单条有效热点"]
    assert len(receipt.ideas) == 2


def test_analysis_without_topics_is_rejected(monkeypatch):
    monkeypatch.setattr(trends, "collect_trends", lambda refresh=False: {
        "items": [], "sources": [], "errors": ["百度热搜：HTTPError"], "updated_at": None})
    with pytest.raises(HTTPException) as error:
        asyncio.run(trends_api.analyze(TrendRequest()))
    assert error.value.status_code == 409


def test_trend_output_tolerates_prose_extras_and_single_value_lists():
    payload = {
        "overview": "概览",
        "ignored_note": "多余字段",
        "ideas": [
            {"genre": "都市", "setting": "现代都市", "power_system": "无超凡力量", "cheat": "无金手指",
             "protagonist": "打工人", "goal": "守住小店", "tone": "轻松", "length": "长篇",
             "title": "参考书名", "premise": "一句话梗概", "core_conflict": "核心冲突",
             "discussion_hook": "能被概括成一句梗", "selling_points": "卖点一；卖点二",
             "reference_topics": "热点甲\n热点乙", "risks": None, "unknown_field": "忽略"},
            {"genre": "都市", "setting": "现代都市", "power_system": "无超凡力量", "cheat": "无金手指",
             "protagonist": "打工人", "goal": "守住小店", "tone": "轻松", "length": "长篇",
             "title": "第二本", "premise": "梗概", "core_conflict": "冲突",
             "discussion_hook": "梗", "selling_points": ["卖点"], "reference_topics": ["热点甲"]},
        ],
    }
    raw = "以下是推荐结果：\n" + json.dumps(payload, ensure_ascii=False) + "\n以上。"
    parsed = agents._parse_output(agents.TREND_ANALYST, raw)
    assert parsed.ideas[0].selling_points == ["卖点一", "卖点二"]
    assert parsed.ideas[0].reference_topics == ["热点甲", "热点乙"]
    assert parsed.ideas[0].risks == ""
    assert parsed.ideas[0].discussion_hook == "能被概括成一句梗"


def test_trend_validation_reports_the_failing_field():
    from api.novel_writing_api import trend_problem

    assert trend_problem(["ideas.0.discussion_hook"]) == "第 1 条推荐的话题点不符合要求"
    assert trend_problem(["ideas.1.reference_topics"]) == "第 2 条推荐的参考热点不符合要求"
    assert trend_problem(["overview"]) == "总览不符合要求"
    assert trend_problem([]) == "缺少必要内容"


def test_trend_report_accepts_a_bare_list_or_missing_overview():
    row = {"genre": "都市", "setting": "现代都市", "power_system": "无超凡力量", "cheat": "无金手指",
           "protagonist": "打工人", "goal": "守住小店", "tone": "轻松", "length": "长篇",
           "title": "参考书名", "premise": "梗概", "core_conflict": "冲突", "discussion_hook": "梗",
           "selling_points": ["卖点"], "reference_topics": ["热点甲"]}
    as_list = TrendReport.model_validate([row, row])
    assert len(as_list.ideas) == 2 and as_list.overview
    without_overview = TrendReport.model_validate({"ideas": [row, row]})
    assert len(without_overview.ideas) == 2 and without_overview.overview
    renamed = TrendReport.model_validate({"recommendations": [row, row]})
    assert len(renamed.ideas) == 2


def test_search_results_parse_h3_titles():
    page = """
    <h3 class="vr-title"><a href="/link?url=x">《嫁金钗》开播热度破万，真假千金设定引争议</a></h3>
    <h3>大家还在搜</h3>
    <h3>嫁金钗 小说 热点_相关小说</h3>
    """
    rows = trends.parse_search_results(page, "搜狗搜索")
    assert [row["title"] for row in rows] == ["《嫁金钗》开播热度破万，真假千金设定引争议", "嫁金钗小说热点"]
    assert rows[0]["source"] == "搜狗搜索"
    assert trends.parse_search_results("<h3>嫁金钗_百度百科</h3>", "搜狗搜索") == []


def test_brief_queries_use_the_topic_words():
    queries = trends.brief_queries("嫁金钗，穿越男主，三观正，以实力为尊", "古装")
    assert queries[0] == "嫁金钗 小说 热点"
    assert "古装 小说 热点" in queries


def test_brief_search_is_preferred_over_the_general_hot_list(monkeypatch):
    monkeypatch.setattr(trends, "search_related", lambda brief, genre="": [
        {"title": "相关热点一", "source": "搜狗搜索"},
        {"title": "相关热点二", "source": "搜狗搜索"}])
    monkeypatch.setattr(trends, "collect_trends", lambda refresh=False: {
        "items": [{"title": "无关热搜"}], "sources": ["百度热搜"], "errors": [], "updated_at": None})
    topics, sources = trends_api.resolve_topics(TrendRequest(brief="嫁金钗，穿越男主"))
    assert topics == ["相关热点一", "相关热点二"]
    assert sources == ["搜狗搜索"]


def test_brief_search_falls_back_to_the_hot_list(monkeypatch):
    monkeypatch.setattr(trends, "search_related", lambda brief, genre="": [])
    monkeypatch.setattr(trends, "collect_trends", lambda refresh=False: {
        "items": [{"title": "通用热搜"}], "sources": ["百度热搜"], "errors": [], "updated_at": None})
    topics, sources = trends_api.resolve_topics(TrendRequest(brief="嫁金钗"))
    assert topics == ["通用热搜"] and sources == ["百度热搜"]


def test_brief_is_sent_with_auto_topics_and_summarized(monkeypatch):
    captured = {}

    async def fake_generate(role, context, payload):
        captured.update(context=context)
        return TrendReport(overview="概览", requirement_summary="要求：古装宅斗、三观正、实力为尊",
                           ideas=[idea("热点甲"), idea("热点乙")])

    monkeypatch.setattr(trends_api, "generate", fake_generate)
    monkeypatch.setattr(trends, "search_related", lambda brief, genre="": [
        {"title": "热点甲", "source": "搜狗搜索"}, {"title": "热点乙", "source": "搜狗搜索"},
        {"title": "无关条目", "source": "搜狗搜索"}])
    receipt = asyncio.run(trends_api.analyze(TrendRequest(brief="嫁金钗，穿越男主，三观正，以实力为尊", idea_count=2)))
    assert captured["context"]["brief"].startswith("嫁金钗")
    assert receipt.requirement_summary.startswith("要求：")
    assert len(receipt.topics) == 3


def test_related_titles_are_ranked_first_for_a_brief():
    topics = ["某地出现罕见极光", "年轻人开始反向消费", "AI 助手进入日常办公"]
    assert trends_api.rank_topics(topics, "年轻人反向消费")[0] == "年轻人开始反向消费"
    assert trends_api.rank_topics(topics, "") == topics


def test_brief_mode_accepts_ideas_without_related_topics():
    base = idea("热点甲").model_copy(update={"reference_topics": []})
    report = TrendReport(overview="概览", requirement_summary="要求：古装宅斗", ideas=[base, base])
    cleaned = trends_api.clean_report(report, ["与要求无关的热点"], require_reference=False)
    assert [row.reference_topics for row in cleaned.ideas] == [[], []]
    # 没有选题要求时仍必须引用热点。
    assert trends_api.clean_report(report, ["与要求无关的热点"]) is None


def test_search_endpoint_returns_related_items(monkeypatch):
    monkeypatch.setattr(trends, "search_related", lambda q, genre="", **_: [
        {"title": "《嫁金钗》开播热度破万", "source": "搜狗搜索", "heat": None, "url": None}])
    payload = trends_api.search_trends(object(), q="嫁金钗", genre="古装")
    assert payload["query"] == "嫁金钗"
    assert payload["sources"] == ["搜狗搜索"]
    assert payload["items"][0]["title"] == "《嫁金钗》开播热度破万"


def test_prefill_sets_slots_and_rejects_unknown_keys():
    user = SimpleNamespace(id=1)
    session = assistant.create_session(user)
    session_id = session["session_id"]
    values = {"genre": "都市", "setting": "现代都市", "power_system": "无超凡力量", "cheat": "无金手指",
              "protagonist": "外卖员", "goal": "守住小店", "tone": "轻松", "length": "长篇"}
    reply = assistant.prefill_session(session_id, assistant.PrefillRequest(values=values), user)
    assert reply["values"] == values
    assert reply["question"] is None and "确认" in reply["message"]

    with pytest.raises(HTTPException) as error:
        assistant.prefill_session(session_id, assistant.PrefillRequest(values={"unknown": "x"}), user)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        assistant.prefill_session(session_id, assistant.PrefillRequest(values=values), SimpleNamespace(id=2))
    assert error.value.status_code == 404

def test_fanqie_font_obfuscated_titles_are_decoded():
    from agents.trends import _clean

    assert _clean("伪\ue4c2\ue47c\ue4c4穿古\ue50c,\ue4c7朝\ue410武皆素材") == "伪人作者穿古代,满朝文武皆素材"
    assert _clean("测\ue000试") == "测试"

