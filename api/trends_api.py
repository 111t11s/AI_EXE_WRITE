"""热点选题：读取公开热搜，并让模型推荐可直接开写的题材。"""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from agents import trends as trend_source
from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from api import generation_jobs
from api.account_api import CurrentAccount
from api.generation_stream import generation_response
from api.novel_writing_api import generate
from api.writing_schemas import TrendReceipt, TrendRequest, WritingInput


router = APIRouter(prefix="/trends", tags=["热点选题"])
MAX_TOPICS = 40


def normalize_topics(values) -> list[str]:
    seen, topics = set(), []
    for value in values:
        title = " ".join(str(value).split())
        if len(title) < 2 or title.casefold() in seen:
            continue
        seen.add(title.casefold())
        topics.append(title[:200])
        if len(topics) >= MAX_TOPICS:
            break
    return topics


def rank_topics(topics: list[str], brief: str) -> list[str]:
    """按与选题要求的字面重合度排序，让相关热点排在前面（保持稳定顺序）。"""
    if not brief:
        return topics
    tokens = {brief[index:index + 2] for index in range(len(brief) - 1) if not brief[index:index + 2].isspace()}
    if not tokens:
        return topics

    def score(title: str) -> int:
        return sum(1 for token in tokens if token in title)

    return sorted(topics, key=score, reverse=True)


def resolve_topics(payload: TrendRequest) -> tuple[list[str], list[str]]:
    """优先使用用户粘贴的热点；有选题要求时先按它检索相关热点，再退回热搜榜单。"""
    if payload.topics:
        return normalize_topics(payload.topics), []
    brief = payload.brief.strip()
    if brief:
        searched = trend_source.search_related(brief, payload.genre_preference)
        if len(searched) >= 2:
            sources = list(dict.fromkeys(row["source"] for row in searched))
            return normalize_topics([row["title"] for row in searched]), sources
    data = trend_source.collect_trends()
    topics = normalize_topics([row["title"] for row in data["items"]])
    return rank_topics(topics, brief), list(data.get("sources", []))


def matches_topic(reference: str, topics: list[str]) -> bool:
    # 允许模型用引号包裹标题，只要引用的是同一条热点即可。
    value = " ".join(reference.split()).strip('"“”「」『』\'` ')
    if not value:
        return False
    return any(value in topic or topic in value for topic in topics)


ESSENTIAL_FIELDS = ("genre", "title", "premise", "discussion_hook")


def clean_report(report, topics: list[str], require_reference: bool = True):
    """丢弃缺字段的半成品；非选题要求模式下还要求确实引用热点。

    返回 None 表示可用方案不足两条，调用方会重试一次。
    """
    ideas = []
    for idea in report.ideas:
        if any(not str(getattr(idea, field) or "").strip() for field in ESSENTIAL_FIELDS):
            continue
        references = [value for value in idea.reference_topics if matches_topic(value, topics)]
        if require_reference and not references:
            continue
        ideas.append(idea.model_copy(update={"reference_topics": references}))
    if len(ideas) < 2:
        return None
    return report.model_copy(update={"ideas": ideas})


def retryable(exc: HTTPException) -> bool:
    detail = str(exc.detail)
    return exc.status_code == 502 and ("不符合要求" in detail or "截断" in detail)


async def analyze(payload: TrendRequest) -> TrendReceipt:
    topics, sources = await run_in_threadpool(resolve_topics, payload)
    if not topics:
        raise HTTPException(409, "暂时没有可用的热点，请刷新热搜、查询相关热搜，或在补充要求中粘贴热点标题")
    emit_generation_event("status", message=f"已准备 {len(topics)} 条热点，正在提炼题材方向…")
    context = {
        "topics": topics,
        "brief": payload.brief.strip(),
        "preferences": {"genre_preference": payload.genre_preference,
                        "audience": payload.audience, "idea_count": payload.idea_count},
    }
    request = WritingInput(instruction=payload.instruction, model=payload.model)
    require_reference = not payload.brief.strip()
    for attempt in (1, 2):
        try:
            report = await generate(agents.TREND_ANALYST, context, request)
        except HTTPException as exc:
            if attempt == 1 and retryable(exc):
                emit_generation_event("status", message="模型输出未通过校验，正在重新生成…")
                continue
            raise
        cleaned = clean_report(report, topics, require_reference)
        if cleaned is not None:
            emit_generation_event("status", message="题材方案已通过热点引用校验。")
            return TrendReceipt(**cleaned.model_dump(), generated_at=datetime.now(timezone.utc).isoformat(),
                                topics=topics, sources=sources)
        if attempt == 1:
            emit_generation_event("status", message="部分推荐缺少必要字段，正在重新生成…")
    raise HTTPException(502, "模型返回的推荐缺少必要字段或热点依据，请重试生成")


@router.get("", summary="查询最近热点（缓存时间可配置）")
def list_trends(user: CurrentAccount, refresh: bool = False) -> dict:
    return trend_source.collect_trends(refresh=refresh)


@router.get("/search", summary="按输入内容查询相关热搜")
def search_trends(user: CurrentAccount,
                  q: str = Query(min_length=2, max_length=200),
                  genre: str = Query(default="", max_length=40)) -> dict:
    items = trend_source.search_related(q, genre)
    sources = list(dict.fromkeys(row["source"] for row in items))
    return {"items": items, "sources": sources, "query": q}


@router.post("/analyze", response_model=TrendReceipt, summary="根据最近热点推荐小说题材")
async def analyze_trends(payload: TrendRequest, user: CurrentAccount):
    return await analyze(payload)


@router.post("/analyze/tasks", status_code=202, summary="后台分析热点并推荐题材")
async def start_trends(payload: TrendRequest, user: CurrentAccount):
    signature = hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True,
                                          ensure_ascii=False).encode()).hexdigest()[:16]
    return generation_jobs.store.start(
        account_id=user.id, key=f"trends:{signature}", kind="trends", title="热点选题分析",
        operation=lambda: analyze(payload)).summary()


@router.post("/analyze/stream", summary="实时分析热点并推荐题材")
async def stream_trends(payload: TrendRequest, user: CurrentAccount):
    return generation_response(lambda: analyze(payload), message="正在读取热点，准备分析…")
