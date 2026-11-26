"""Fetch popular fiction titles from public novel-platform rankings."""

import html as html_module
import json
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from charset_normalizer import from_bytes

from core.config import get_settings
from core.app_logging import get_logger


logger = get_logger("trends")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
MAX_ITEMS = 40
TITLE_KEYS = ("title", "book_title", "bookTitle", "book_name", "bookName",
              "novel_name", "novelName", "name")
HEAT_KEYS = ("hot", "heat", "score", "num", "hotValue", "hot_value", "index", "view_count")
DEFAULT_NOVEL_SITES = (
    ("番茄小说", "https://fanqienovel.com/rank"),
    ("起点中文网", "https://m.qidian.com/rank/hotsales/"),
)
# 移动版榜单链接文字常带“最新章节在线阅读”等后缀，只保留书名。
TITLE_SUFFIXES = ("最新章节在线阅读", "最新章节列表", "全文免费阅读", "全文阅读", "免费阅读", "无弹窗")
BOOK_LINK_HINTS = ("/book/", "/novel/", "/page/", "/info/", "bookid=", "novelid=", "bid=")

_cache: dict[tuple[str, ...], dict] = {}
_lock = threading.Lock()
# 番茄小说榜单把书名、作者等文本映射到私有区字符，再用自定义字体显示。
# 这里保存字体映射表（键为十进制码位），用于把排行榜标题还原成中文。
_FONT_MAP_PATH = Path(__file__).with_name("fanqie_font.json")
_FONT_MAP = json.loads(_FONT_MAP_PATH.read_text(encoding="utf-8")) if _FONT_MAP_PATH.is_file() else {}

POLITICAL_WORDS = (
    "政治", "时政", "两会", "人大", "政协", "政府", "总书记", "国家主席", "国家领导", "总理", "外交", "国防",
    "军事", "军方", "解放军", "军演", "选举", "议会", "白宫", "总统", "国会", "台湾", "台海", "两岸", "中美",
    "涉政", "政治局", "外交部", "国防部", "制裁", "内阁", "议员", "政党", "政策", "特朗普", "拜登", "习近平",
    "普京", "泽连斯基", "赖清德", "金正恩", "首相", "外长", "议长",
)
_search_cache: dict[tuple[str, ...], tuple[datetime, list[dict]]] = {}


def _decode_font_text(value: str) -> str:
    """还原番茄小说等站点用自定义字体混淆的私有区字符。"""
    if not _FONT_MAP:
        return value
    return "".join(_FONT_MAP.get(str(ord(char)), char) for char in value)


def _clean(value: str) -> str:
    value = unicodedata.normalize("NFKC", html_module.unescape(str(value)))
    value = _decode_font_text(value)
    # Repair the common UTF-8-as-Latin-1 mojibake produced by misconfigured
    # upstream pages before the title reaches the browser.
    if re.search(r"[ÃÂâ€™œžš]|�", value):
        try:
            repaired = value.encode("latin1").decode("utf-8")
            if repaired.count("�") < value.count("�"):
                value = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    # 移除仍无法映射的私有区/替换字符，避免页面出现方框乱码。
    value = "".join(char for char in value if unicodedata.category(char) != "Co")
    return " ".join(value.replace("\ufffd", "").split())


class _NovelAnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            values = dict(attrs)
            self.current = {"href": values.get("href", ""), "title": values.get("title", ""), "text": []}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"].append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.current is not None:
            self.anchors.append(self.current)
            self.current = None


class _SearchResultParser(HTMLParser):
    """提取搜索引擎结果里的 h3 标题与真实落地地址（360 的 data-mdurl）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_heading = False
        self.current = None
        self.results = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag.lower() == "h3":
            self.in_heading = True
            self.current = {"text": [], "href": "", "mdurl": ""}
        elif self.in_heading and tag.lower() == "a" and self.current is not None:
            self.current["href"] = values.get("href", "")
            self.current["mdurl"] = values.get("data-mdurl", "")

    def handle_data(self, data):
        if self.in_heading and self.current is not None:
            self.current["text"].append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "h3" and self.current is not None:
            self.results.append(self.current)
            self.current = None
            self.in_heading = False


SEARCH_RESULT_URL = "https://www.so.com/s?q={query}"
SEARCH_TITLE_MARKERS = ("完整版在线免费阅读", "全文阅读", "免费阅读", "最新章节", "在线阅读", "小说大全")
SEARCH_TITLE_SUFFIX = re.compile(r"[_-](番茄小说|起点中文网|晋江文学城|纵横中文网|17K小说网).*$")


def _search_title(value: str) -> str:
    title = _clean(value)
    for marker in SEARCH_TITLE_MARKERS:
        title = title.split(marker)[0]
    title = SEARCH_TITLE_SUFFIX.sub("", title).strip(" -_|·")
    title = re.sub(r"\s*([:：,，.!！?？])\s*", r"\1", title)
    title = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", title)
    if "排行榜" in title:
        return ""
    return title


def search_novel_sites_via_web(keyword: str, sites: list[tuple[str, str]], timeout: int) -> list[dict]:
    """榜单没有匹配作品时，用搜索引擎按站点检索真实书名。"""
    keyword = keyword.strip()
    if len(keyword) < 2:
        return []
    found, seen = [], set()
    for name, site_url in sites:
        host = urlparse(site_url).hostname or ""
        if not host:
            continue
        try:
            response = httpx.get(
                SEARCH_RESULT_URL.format(query=quote_plus(f"site:{host} {keyword}")),
                headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
                timeout=timeout, follow_redirects=True)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("站内书名检索失败：来源=%s，异常类型=%s", name, type(exc).__name__)
            continue
        parser = _SearchResultParser()
        parser.feed(response.text)
        for item in parser.results:
            target = item["mdurl"] or item["href"]
            parsed = urlparse(target)
            if host not in parsed.netloc or not any(hint in parsed.path for hint in BOOK_LINK_HINTS):
                continue
            title = _search_title(" ".join(item["text"]))
            if not title or keyword not in title or title in seen:
                continue
            seen.add(title)
            found.append({"title": title, "source": name, "heat": None, "url": target})
    return found


def parse_novel_site_html(page: str, source: str, base_url: str) -> list[dict]:
    """Extract book-title links from common HTML ranking pages."""
    parser = _NovelAnchorParser()
    parser.feed(page)
    items, seen = [], set()
    for anchor in parser.anchors:
        href = urljoin(base_url, anchor["href"].strip())
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        path_and_query = (parsed.path + "?" + parsed.query).casefold()
        if not any(hint in path_and_query for hint in BOOK_LINK_HINTS):
            continue
        title = _clean(anchor["title"] or " ".join(anchor["text"]))
        for suffix in TITLE_SUFFIXES:
            if title.endswith(suffix):
                title = title[:-len(suffix)].strip()
                break
        title = title.strip(" -_|·")
        if not title or len(title) < 2 or len(title) > 80:
            continue
        if title.casefold() in {"首页", "排行榜", "分类", "登录", "注册", "目录", "下一页", "上一页"}:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append({"title": title, "source": source, "heat": None, "url": href})
        if len(items) >= MAX_ITEMS:
            break
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


def fetch_novel_site(name: str, url: str, timeout: int) -> list[dict]:
    response = httpx.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
                         timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    if "json" in response.headers.get("content-type", "").casefold():
        return parse_json_source(response.json(), name)
    return parse_novel_site_html(_decode_html_response(response), name, str(response.url))


def _decode_html_response(response: httpx.Response) -> str:
    """Decode ranking HTML using its declared charset, then detect Chinese legacy encodings."""
    raw = response.content
    content_type = response.headers.get("content-type", "")
    declared = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type, re.I)
    if not declared:
        head = raw[:8192].decode("ascii", errors="ignore")
        declared = re.search(r"<meta[^>]+charset\s*=\s*[\"']?([\w.-]+)", head, re.I)
    encodings = [declared.group(1)] if declared else []
    # UTF-8 and GB18030 cover the encodings used by the supported Chinese sites.
    # Charset-normalizer is only a fallback because short Chinese pages can be
    # confidently misclassified as a western single-byte encoding.
    encodings.extend(("utf-8", "gb18030"))
    detected = from_bytes(raw).best()
    if detected and detected.encoding:
        encodings.append(detected.encoding)
    candidates = []
    tried = set()
    for encoding in encodings:
        normalized = encoding.casefold()
        if normalized in tried:
            continue
        tried.add(normalized)
        try:
            candidates.append(raw.decode(encoding))
        except (LookupError, UnicodeDecodeError):
            continue
    if not candidates:
        return raw.decode("utf-8", errors="replace")

    def score(text: str) -> int:
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        replacement = text.count("�")
        controls = sum(unicodedata.category(char) == "Cc" for char in text)
        mojibake = len(re.findall(r"[ÃÂâ][^\s]{0,3}", text))
        return cjk * 3 - replacement * 30 - controls * 5 - mojibake * 10

    return max(candidates, key=score)


def novel_sites(settings) -> list[tuple[str, str]]:
    """Use site ranking URLs from env when supplied; otherwise use platform defaults."""
    configured = getattr(settings, "TREND_NOVEL_SOURCE_URLS", "")
    if not configured.strip():
        return list(DEFAULT_NOVEL_SITES)
    sites = []
    for entry in configured.split(","):
        label, separator, url = entry.strip().partition("|")
        if not separator:
            url, label = label, (urlparse(label).hostname or "小说网站")
        url = url.strip()
        if url.startswith(("http://", "https://")):
            sites.append((label.strip() or (urlparse(url).hostname or "小说网站"), url))
    return sites


def dedupe(items: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for item in items:
        key = item["title"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def contains_political_content(text: str) -> bool:
    value = str(text or "").casefold()
    return any(word.casefold() in value for word in POLITICAL_WORDS)


def filter_political_items(items: list[dict]) -> list[dict]:
    return [item for item in items if not contains_political_content(item.get("title", ""))]


def search_related(brief: str, genre: str = "", limit: int = 20,
                   selected_sources: list[str] | None = None) -> list[dict]:
    """在小说网站的最新榜单中筛选与选题要求相关的作品。"""
    if contains_political_content(brief) or contains_political_content(genre):
        return []
    settings = get_settings()
    sites = _selected_sites(settings, selected_sources)
    key = (brief.strip()[:200], genre.strip(), "|".join(name for name, _ in sites), str(limit))
    now = datetime.now(timezone.utc)
    with _lock:
        cached = _search_cache.get(key)
        if cached and now - cached[0] < timedelta(minutes=settings.TREND_CACHE_MINUTES):
            return filter_political_items(list(cached[1]))[:limit]
    items = []
    tokens = [part.casefold() for part in re.split(r"[，,。;；、\s]+", f"{brief} {genre}") if len(part.strip()) >= 2]
    query_tokens = [part.casefold() for part in re.split(r"[，,。;；、\s]+", brief) if len(part.strip()) >= 2]
    for name, url in sites:
        try:
            items.extend(fetch_novel_site(name, url, settings.TREND_TIMEOUT))
        except Exception as exc:
            logger.warning("小说榜单查询失败：来源=%s，异常类型=%s", name, type(exc).__name__)
    items = dedupe(filter_political_items(items))
    # 只保留真正命中主题词的榜单作品；不能把整张榜单当作搜索结果返回。
    required = query_tokens or tokens
    matched = [row for row in items if any(token in row["title"].casefold() for token in required)]
    if not matched and query_tokens and len(query_tokens) <= 4:
        matched = search_novel_sites_via_web(" ".join(query_tokens[:2]), sites, settings.TREND_TIMEOUT)
    matched.sort(key=lambda row: (-sum(token in row["title"].casefold() for token in tokens),
                                  row["title"]))
    items = matched[:limit]
    with _lock:
        _search_cache[key] = (now, items)
        if len(_search_cache) > 50:
            oldest = min(_search_cache, key=lambda key_: _search_cache[key_][0])
            _search_cache.pop(oldest, None)
    return items


def _selected_sites(settings, selected_sources: list[str] | None) -> list[tuple[str, str]]:
    sites = novel_sites(settings)
    if selected_sources is None:
        return sites
    requested = set(selected_sources)
    return [(name, url) for name, url in sites if name in requested]


def collect_trends(refresh: bool = False, selected_sources: list[str] | None = None) -> dict:
    """返回热点列表；未过期时直接使用缓存，抓取失败时保留旧数据。"""
    settings = get_settings()
    sites = _selected_sites(settings, selected_sources)
    cache_key = tuple(name for name, _ in sites)
    available_sources = [name for name, _ in novel_sites(settings)]
    ttl = timedelta(minutes=settings.TREND_CACHE_MINUTES)
    with _lock:
        now = datetime.now(timezone.utc)
        cached_data = _cache.get(cache_key, {"items": [], "updated_at": None, "errors": [], "sources": []})
        fresh = cached_data["updated_at"] is not None and now - cached_data["updated_at"] < ttl
        if fresh and not refresh and cached_data["items"]:
            return {**cached_data, "available_sources": available_sources,
                    "selected_sources": list(cache_key), "cached": True}

        items, errors, sources = [], [], []
        for loader in _loaders(settings, sites):
            name, fetch = loader
            try:
                rows = fetch()
            except Exception as exc:  # 网络或解析异常：记录来源并继续
                logger.warning("热点抓取失败：来源=%s，异常类型=%s", name, type(exc).__name__)
                errors.append(f"{name}：{type(exc).__name__}")
                continue
            rows = [row for row in rows if row.get("title", "").strip()]
            if rows:
                sources.append(name)
                items.extend(rows)
        items = dedupe(filter_political_items(items))[:MAX_ITEMS]
        if items:
            cached_data = {"items": items, "updated_at": now, "errors": errors, "sources": sources}
        elif cached_data["items"]:
            # 全部失败时继续提供上一次结果，但把本次错误带回页面。
            cached_data = {**cached_data, "items": list(cached_data["items"]), "errors": errors}
        else:
            cached_data = {"items": [], "updated_at": None, "errors": errors, "sources": sources}
        _cache[cache_key] = cached_data
        return {**cached_data, "available_sources": available_sources,
                "selected_sources": list(cache_key), "cached": False}


def _loaders(settings, sites=None):
    timeout = settings.TREND_TIMEOUT
    for name, url in (sites if sites is not None else novel_sites(settings)):
        yield name, lambda name=name, url=url: fetch_novel_site(name, url, timeout)
