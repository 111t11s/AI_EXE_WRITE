import json

from agents import novel_preview


class FakeResponse:
    def __init__(self, text="", payload=None):
        self.text = text
        self.content = text.encode() if text else (b"{}" if payload is not None else b"")
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _encode(text):
    mapping = {}
    for offset, index in novel_preview.CHAPTER_OFFSETS:
        for position, char in enumerate(novel_preview.CHARSET[index]):
            mapping.setdefault(char, chr(offset + position))
    return "".join(mapping.get(char, char) for char in text)


def _state_html(state):
    return "<script>window.__INITIAL_STATE__=" + json.dumps(state, ensure_ascii=False) + ";</script>"


def _reader_state():
    return {"reader": {"chapterData": {
        "content": "<p>" + _encode("这是解码后的正文内容，包含足够的中文。") + "</p>",
    }}}


def test_preview_prefers_plaintext_apis_and_skips_page(monkeypatch):
    info = {"code": 0, "data": {"bookName": "明文作品", "abstract": "明文简介。"}}
    directory = {"code": 0, "data": {"chapterListWithVolume": [[
        {"itemId": "101", "title": "第1章 开端", "isChapterLock": False},
        {"itemId": "102", "title": "第2章 继续", "isChapterLock": False},
        {"itemId": "103", "title": "第3章 转折", "isChapterLock": False},
    ]]}}
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "/api/book/info" in url:
            return FakeResponse(payload=info)
        if "/api/reader/directory/detail" in url:
            return FakeResponse(payload=directory)
        if "/reader/" in url:
            return FakeResponse(_state_html(_reader_state()))
        return FakeResponse("<html></html>")

    monkeypatch.setattr(novel_preview.httpx, "get", fake_get)
    novel_preview._cache.clear()
    books = novel_preview.fetch_previews(
        [{"title": "明文作品", "url": "https://fanqienovel.com/page/123"}], timeout=1)

    assert len(books) == 1
    assert books[0]["title"] == "明文作品"
    assert books[0]["abstract"] == "明文简介。"
    assert [row["title"] for row in books[0]["chapters"]] == ["第1章 开端", "第2章 继续", "第3章 转折"]
    assert not any("/page/" in url for url in calls)


def test_preview_falls_back_to_website_when_plaintext_apis_fail(monkeypatch):
    book_state = {"page": {
        "bookName": "回退作品",
        "abstract": "回退简介。",
        "chapterListWithVolume": [[{"itemId": "201", "title": "第1章 开端", "isChapterLock": False}]],
    }}

    def fake_get(url, **kwargs):
        if "/page/" in url:
            return FakeResponse(_state_html(book_state))
        if "/reader/" in url:
            return FakeResponse(_state_html(_reader_state()))
        return FakeResponse("<html></html>")

    monkeypatch.setattr(novel_preview.httpx, "get", fake_get)
    novel_preview._cache.clear()
    books = novel_preview.fetch_previews(
        [{"title": "回退作品", "url": "https://fanqienovel.com/page/456"}], timeout=1)

    assert len(books) == 1
    assert books[0]["title"] == "回退作品"
    assert books[0]["abstract"] == "回退简介。"
    assert books[0]["chapters"][0]["excerpt"] == "这是解码后的正文内容，包含足够的中文。"


def test_preview_ignores_unsupported_hosts_and_locked_chapters(monkeypatch):
    novel_preview._cache.clear()
    assert novel_preview.fetch_previews(
        [{"title": "外部作品", "url": "https://example.com/page/1"}], timeout=1) == []

    book_state = {"page": {
        "bookName": "锁定作品",
        "abstract": "",
        "chapterListWithVolume": [[{"itemId": "1", "title": "第1章", "isChapterLock": True}]],
    }}

    def fake_get(url, **kwargs):
        return FakeResponse(_state_html(book_state))

    monkeypatch.setattr(novel_preview.httpx, "get", fake_get)
    assert novel_preview.fetch_previews(
        [{"title": "锁定作品", "url": "https://fanqienovel.com/page/2"}], timeout=1) == []
