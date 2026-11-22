"""抓取公开热搜，供「热点选题」分析使用。

只访问服务端配置的地址（默认百度热搜），不接受用户提交的 URL；抓取失败时
保留上一次缓存，并把错误信息交给页面。解析函数独立于网络，便于离线测试。
"""

import html as html_module
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from core.config import get_settings
from core.logging import get_logger


logger = get_logger("trends")
BAIDU_URL = "https://top.baidu.com/board?tab=realtime"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
MAX_ITEMS = 40
TITLE_KEYS = ("title", "name", "word", "hotword", "query", "keyword")
HEAT_KEYS = ("hot", "heat", "score", "num", "hotValue", "hot_value", "index", "view_count")

_cache = {"items": [], "updated_at": None, "errors": [], "sources": []}
_lock = threading.Lock()

SEARCH_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Mobile Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
SEARCH_ENGINES = (
    ("搜狗搜索", "https://www.sogou.com/web", "query"),
    ("360搜索", "https://www.so.com/s", "q"),
)
SEARCH_SKIP = {"大家还在搜", "相关搜索", "其他人还在搜", "热搜榜", "热点资讯"}
SKIP_TITLE_WORDS = ("百科", "辞典", "词典", "读音", "笔顺", "小说简介", "人物介绍", "剧情介绍")
HOT_WORDS = ("热", "爆", "争议", "话题", "官宣", "开播", "收视", "讨论", "评价", "关注", "刷屏", "破万")
_search_cache: dict[tuple[str, str], tuple[datetime, list[dict]]] = {}


def _clean(value: str) -> str:
    return " ".join(html_module.unescape(value).split())


def parse_baidu(page: str) -> list[dict]:
    """从百度热搜页面提取标题与热搜指数；页面结构变化时返回能解析到的部分。"""
    titles = [_clean(match) for match in re.findall(
        r'c-single-text-ellipsis"\s*>\s*([^<]+?)\s*</div>', page)]
    heats = [int(match.replace(",", "")) for match in re.findall(
        r'class="hot-index[^"]*"[^>]*>\s*([\d,]+)', page)]
    # 首个条目可能是置顶推荐，没有热搜指数；按数量差对齐。
    if titles and len(titles) == len(heats) + 1:
        titles = titles[1:]
    items = []
    for index, title in enumerate(titles):
        if not title:
            continue
        items.append({
            "title": title,
            "source": "百度热搜",
            "heat": heats[index] if index < len(heats) else None,
            "url": "https://www.baidu.com/s?wd=" + quote(title),
        })
    return items


def parse_json_source(payload, source: str) -> list[dict]:
    """从第三方热搜 JSON 中找出带标题的条目，兼容常见的字段命名。"""
    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            title = next((node[key] for key in TITLE_KEYS
                          if isinstance(node.get(key), str) and len(_clean(node[key])) >= 4), None)
            if title:
                heat = next((node[key] for key in HEAT_KEYS
                             if isinstance(node.get(key), (int, float))), None)
                url = next((node[key] for key in ("url", "link", "href")
                            if isinstance(node.get(key), str) and node[key].startswith("http")), None)
                found.append({"title": _clean(title), "source": source,
                              "heat": int(heat) if heat is not None else None, "url": url})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


def fetch_baidu(timeout: int) -> list[dict]:
    response = httpx.get(BAIDU_URL, headers={"User-Agent": USER_AGENT},
                        timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return parse_baidu(response.text)


def fetch_json_source(url: str, timeout: int) -> list[dict]:
    host = httpx.URL(url).host or "自定义来源"
    response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout,
                        follow_redirects=True)
    response.raise_for_status()
    return parse_json_source(response.json(), host)


def dedupe(items: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for item in items:
        key = item["title"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def brief_anchor(brief: str) -> str:
    """取选题要求里最像主题词的短句，作为搜索关键词。"""
    for part in re.split(r"[，,。;；、\s]+", brief):
        part = part.strip()
        if 2 <= len(part) <= 12:
            return part
    return " ".join(brief.split())[:12]


def brief_queries(brief: str, genre: str = "") -> list[str]:
    anchor = brief_anchor(brief)
    queries = []
    if anchor:
        queries.append(f"{anchor} 小说 热点")
        queries.append(f"{anchor} 热议")
    if genre and genre.strip() and genre.strip() != anchor:
        queries.append(f"{genre.strip()} 小说 热点")
    return queries[:3]


def clean_search_title(text: str) -> str:
    """去掉搜索结果标题里的站点后缀、中文之间的空格与常见噪声。"""
    text = re.sub(r"(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])\s+"
                  r"(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])", "", text)
    # 站点后缀可能叠了两层，例如“_娱乐频道_中华网”。
    for _ in range(2):
        text = re.sub(r"\s*[_|]\s*[^_|]{1,12}$", "", text).strip()
    text = re.sub(r"\s+(?=[：:，,。.！!？?；;])", "", text)
    text = re.sub(r"\s+-\s+[^-]{1,12}$", "", text).strip()
    return text


def parse_search_results(page: str, source: str) -> list[dict]:
    rows = []
    for block in re.findall(r"<h3[^>]*>(.*?)</h3>", page, re.S):
        text = _clean(re.sub(r"<[^>]+>", " ", block))
        for label in ("媒体原创", "热点资讯", "广告"):
            text = text.replace(label, "").strip()
        text = clean_search_title(text)
        if not text or text in SEARCH_SKIP or len(text) < 6 or len(text) > 80:
            continue
        if any(word in text for word in SKIP_TITLE_WORDS):
            continue
        rows.append({"title": text, "source": source, "heat": None, "url": None})
    return rows


def search_related(brief: str, genre: str = "", limit: int = 20) -> list[dict]:
    """按选题要求检索相关新闻/热点，优先返回带热度、争议等词的条目。"""
    key = (brief.strip()[:200], genre.strip())
    settings = get_settings()
    now = datetime.now(timezone.utc)
    with _lock:
        cached = _search_cache.get(key)
        if cached and now - cached[0] < timedelta(minutes=settings.TREND_CACHE_MINUTES):
            return list(cached[1])[:limit]
    items = []
    for name, url, param in SEARCH_ENGINES:
        for query in brief_queries(brief, genre):
            try:
                response = httpx.get(url, params={param: query}, headers=SEARCH_HEADERS,
                                    timeout=settings.TREND_TIMEOUT, follow_redirects=True)
                response.raise_for_status()
            except Exception as exc:
                logger.warning("相关热点搜索失败：来源=%s，异常类型=%s", name, type(exc).__name__)
                continue
            items.extend(parse_search_results(response.text, name))
        if len(items) >= 6:
            break
    items = dedupe(items)
    # 含“热度、争议、开播”等词的条目更像热点，排到前面；其余保持搜索顺序。
    items.sort(key=lambda row: not any(word in row["title"] for word in HOT_WORDS))
    items = items[:limit]
    with _lock:
        _search_cache[key] = (now, items)
        if len(_search_cache) > 50:
            oldest = min(_search_cache, key=lambda key_: _search_cache[key_][0])
            _search_cache.pop(oldest, None)
    return items


def collect_trends(refresh: bool = False) -> dict:
    """返回热点列表；未过期时直接使用缓存，抓取失败时保留旧数据。"""
    settings = get_settings()
    ttl = timedelta(minutes=settings.TREND_CACHE_MINUTES)
    with _lock:
        now = datetime.now(timezone.utc)
        fresh = _cache["updated_at"] is not None and now - _cache["updated_at"] < ttl
        if fresh and not refresh and _cache["items"]:
            return {**_cache, "cached": True}

        items, errors, sources = [], [], []
        for loader in _loaders(settings):
            name, fetch = loader
            try:
                rows = fetch()
            except Exception as exc:  # 网络或解析异常：记录来源并继续
                logger.warning("热点抓取失败：来源=%s，异常类型=%s", name, type(exc).__name__)
                errors.append(f"{name}：{type(exc).__name__}")
                continue
            if rows:
                sources.append(name)
                items.extend(rows)
        items = dedupe(items)[:MAX_ITEMS]
        if items:
            _cache.update(items=items, updated_at=now, errors=errors, sources=sources)
        elif _cache["items"]:
            # 全部失败时继续提供上一次结果，但把本次错误带回页面。
            _cache.update(errors=errors)
        else:
            _cache.update(items=[], errors=errors, sources=sources)
        return {**_cache, "cached": False}


def _loaders(settings):
    timeout = settings.TREND_TIMEOUT
    yield "百度热搜", lambda: fetch_baidu(timeout)
    for url in (part.strip() for part in settings.TREND_SOURCE_URLS.split(",")):
        if url.startswith(("http://", "https://")):
            yield url, lambda url=url: fetch_json_source(url, timeout)
