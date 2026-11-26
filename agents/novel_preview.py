"""读取用户勾选的番茄小说作品的简介和前三章节选。"""

from __future__ import annotations

import html
import json
import random
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from agents.trends import USER_AGENT, _clean
from core.app_logging import get_logger


logger = get_logger("novel_preview")
CHARSET_PATH = Path(__file__).with_name("fanqie_charset.json")
try:
    CHARSET = json.loads(CHARSET_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    CHARSET = []
FANQIE_HOSTS = {"fanqienovel.com", "www.fanqienovel.com"}
BOOK_INFO_API = "https://fanqienovel.com/api/book/info"
DIRECTORY_API = "https://fanqienovel.com/api/reader/directory/detail"
CHAPTER_OFFSETS = ((58344, 0), (58345, 1))
CACHE_MINUTES = 30
MAX_BOOKS = 5
MAX_CHAPTERS = 3
EXCERPT_LIMIT = 700
ABSTRACT_LIMIT = 600
_cache: dict[str, tuple[datetime, dict]] = {}
_lock = threading.Lock()


def _read_state(text: str):
    marker = text.find("window.__INITIAL_STATE__")
    if marker < 0:
        return None
    start = text.find("{", marker)
    if start < 0:
        return None
    raw = re.sub(r"(?<=:)\s*undefined", " null", text[start:])
    try:
        return json.JSONDecoder().raw_decode(raw)[0]
    except ValueError:
        return None


def _decode_chapter(text: str) -> str:
    """还原番茄正文使用的第二套字体映射（与榜单标题映射不同）。"""
    if not CHARSET:
        return text
    output = []
    for char in text:
        code = ord(char)
        for offset, index in CHAPTER_OFFSETS:
            table = CHARSET[index] if index < len(CHARSET) else []
            if offset <= code < offset + len(table):
                output.append(table[code - offset])
                break
        else:
            output.append(char)
    return "".join(output)


def _plain(value: str) -> str:
    text = re.sub(r"</p\s*>", "\n", value or "", flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = "".join(char for char in text if unicodedata.category(char) != "Co")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def book_id_from_url(url: str) -> str | None:
    match = re.search(r"fanqienovel\.com/(?:page|reader)/(\d+)", url or "")
    return match.group(1) if match else None


def _chapter_list(data: dict) -> list[tuple[str, str]]:
    chapters = []
    for volume in data.get("chapterListWithVolume") or []:
        for chapter in volume or []:
            if len(chapters) >= MAX_CHAPTERS:
                return chapters
            if chapter.get("isChapterLock"):
                continue
            chapter_id = str(chapter.get("itemId") or "")
            if chapter_id:
                chapters.append((chapter_id, _clean(chapter.get("title") or "")))
    if not chapters:
        for chapter_id in (data.get("itemIds") or data.get("allItemIds") or [])[:MAX_CHAPTERS]:
            if chapter_id:
                chapters.append((str(chapter_id), ""))
    return chapters


def _api_json(url: str, params: dict, headers: dict, timeout: int):
    """调用番茄明文 JSON 接口；失败或返回空内容时返回 None，由调用方回退网页。"""
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=timeout,
                             follow_redirects=True)
        response.raise_for_status()
        if not response.content:
            return None
        payload = response.json()
    except Exception as exc:
        logger.warning("番茄明文接口不可用：主机=%s，异常类型=%s", urlparse(url).hostname,
                       type(exc).__name__)
        return None
    if payload.get("code") not in (0, None):
        return None
    return payload.get("data") or {}


def _api_book_info(book_id: str, headers: dict, timeout: int) -> dict:
    data = _api_json(BOOK_INFO_API, {"bookId": book_id}, headers, timeout)
    if not data:
        return {}
    return {"title": _clean(data.get("bookName") or ""),
            "abstract": _clean(data.get("abstract") or "")}


def _api_directory(book_id: str, headers: dict, timeout: int) -> dict:
    return _api_json(DIRECTORY_API, {"bookId": book_id}, headers, timeout) or {}


def _page_state(book_id: str, headers: dict, timeout: int) -> dict:
    try:
        response = httpx.get(f"https://fanqienovel.com/page/{book_id}", headers=headers,
                             timeout=timeout, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("参考作品页面读取失败：作品=%s，异常类型=%s", book_id, type(exc).__name__)
        return {}
    state = _read_state(response.text)
    return (state or {}).get("page") or {}


def _reader_excerpt(chapter_id: str, headers: dict, timeout: int) -> tuple[str, str]:
    try:
        reader = httpx.get(
            f"https://fanqienovel.com/reader/{chapter_id}",
            headers={**headers, "cookie": f"novel_web_id={random.randint(10**18, 10**19 - 1)}"},
            timeout=timeout, follow_redirects=True)
        reader.raise_for_status()
    except Exception as exc:
        logger.warning("参考作品章节读取失败：章节=%s，异常类型=%s", chapter_id, type(exc).__name__)
        return "", ""
    state = _read_state(reader.text)
    chapter = ((state or {}).get("reader") or {}).get("chapterData") or {}
    content = chapter.get("content") or ""
    title = chapter.get("title") or ""
    excerpt = _plain(_decode_chapter(content))[:EXCERPT_LIMIT]
    if len(re.findall(r"[\u4e00-\u9fff]", excerpt)) < 10:
        logger.warning("参考作品章节解码异常，已跳过：章节=%s", chapter_id)
        return "", title
    return excerpt.strip(), title


def _fetch_book(url: str, timeout: int) -> dict | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in FANQIE_HOSTS:
        return None
    book_id = book_id_from_url(url)
    if not book_id:
        return None
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}

    # 优先使用明文接口：书名、简介和目录都不需要字体解码。
    info = _api_book_info(book_id, headers, timeout)
    directory = _api_directory(book_id, headers, timeout)
    title = info.get("title") or ""
    abstract = (info.get("abstract") or "")[:ABSTRACT_LIMIT]
    chapters = _chapter_list(directory)

    # 任一关键信息缺失时回退官网页面，避免接口临时失效导致读不到作品。
    if not title or not abstract or not chapters:
        page = _page_state(book_id, headers, timeout)
        if page:
            logger.info("番茄明文接口信息不全，已回退官网页面：作品=%s", book_id)
            title = title or _clean(page.get("bookName") or "")
            abstract = abstract or _clean(page.get("abstract") or "")[:ABSTRACT_LIMIT]
            if not chapters:
                chapters = _chapter_list(page)
    if not title and not abstract and not chapters:
        return None

    entries = []
    for chapter_id, chapter_title in chapters:
        excerpt, reader_title = _reader_excerpt(chapter_id, headers, timeout)
        if not excerpt:
            continue
        entries.append({"title": _clean(reader_title or chapter_title), "excerpt": excerpt})
    if not abstract and not entries:
        return None
    return {"title": title, "abstract": abstract, "chapters": entries, "url": url}


def _cached(url: str, timeout: int) -> dict | None:
    now = datetime.now(timezone.utc)
    with _lock:
        cached = _cache.get(url)
        if cached and now - cached[0] < timedelta(minutes=CACHE_MINUTES):
            return cached[1]
    item = _fetch_book(url, timeout)
    if item:
        with _lock:
            _cache[url] = (now, item)
    return item


def fetch_previews(books, timeout: int = 8) -> list[dict]:
    """并行读取最多 5 部作品的简介与前三章节选；失败的作品跳过。"""
    selected, seen = [], set()
    for book in books or []:
        if isinstance(book, dict):
            url, title = book.get("url"), book.get("title")
        else:
            url, title = getattr(book, "url", None), getattr(book, "title", None)
        if not url or url in seen:
            continue
        seen.add(url)
        selected.append((str(url), str(title or "")))
        if len(selected) >= MAX_BOOKS:
            break
    if not selected:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(_cached, url, timeout): (url, title) for url, title in selected}
        for future in as_completed(futures):
            url, title = futures[future]
            try:
                item = future.result()
            except Exception as exc:
                logger.warning("参考作品读取失败：异常类型=%s", type(exc).__name__)
                continue
            if item:
                results.append(item)
    order = {url: index for index, (url, _) in enumerate(selected)}
    results.sort(key=lambda item: order.get(item.get("url"), len(order)))
    logger.info("参考作品读取完成：作品数=%s，章节数=%s", len(results),
                sum(len(item.get("chapters") or []) for item in results))
    return results
