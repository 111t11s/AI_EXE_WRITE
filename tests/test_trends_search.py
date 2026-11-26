from agents import trends


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def test_default_novel_sources_are_fanqie_and_qidian():
    assert [name for name, _ in trends.DEFAULT_NOVEL_SITES] == ["番茄小说", "起点中文网"]


def test_qidian_mobile_title_suffix_is_removed():
    page = '<a href="/book/123/" title="">神宠供应商最新章节在线阅读</a>'
    items = trends.parse_novel_site_html(page, "起点中文网", "https://m.qidian.com/rank/hotsales/")
    assert [item["title"] for item in items] == ["神宠供应商"]


def test_search_does_not_return_unmatched_ranking_items(monkeypatch):
    trends._search_cache.clear()
    monkeypatch.setattr(trends, "fetch_novel_site", lambda *args: [
        {"title": "帝王娇宠,不做太子妃做皇后", "source": "番茄小说", "heat": None, "url": None},
        {"title": "惹金枝", "source": "番茄小说", "heat": None, "url": None},
    ])
    monkeypatch.setattr(trends, "search_novel_sites_via_web", lambda *args: [])
    assert trends.search_related("末日", "", selected_sources=["番茄小说"]) == []


def test_search_falls_back_to_site_book_search(monkeypatch):
    trends._search_cache.clear()
    monkeypatch.setattr(trends, "fetch_novel_site", lambda *args: [
        {"title": "帝王娇宠,不做太子妃做皇后", "source": "番茄小说", "heat": None, "url": None},
    ])
    monkeypatch.setattr(trends, "search_novel_sites_via_web", lambda *args: [
        {"title": "末日:只有我一人拥有异能", "source": "番茄小说", "heat": None, "url": None},
    ])
    items = trends.search_related("末日", "", selected_sources=["番茄小说"])
    assert [row["title"] for row in items] == ["末日:只有我一人拥有异能"]


def test_web_search_parses_landing_urls_and_cleans_titles(monkeypatch):
    page = ('<h3 class="res-title"><a data-mdurl="https://fanqienovel.com/page/7531" '
            'href="https://www.so.com/link?m=x">末日 :只有我一人拥有异能完整版在线免费阅读_番茄小说</a></h3>'
            '<h3 class="res-title"><a data-mdurl="https://fanqienovel.com/keyword/1" '
            'href="https://www.so.com/link?m=y">末日系统类小说排行榜-番茄小说</a></h3>')
    monkeypatch.setattr(trends.httpx, "get", lambda *args, **kwargs: FakeResponse(page))
    items = trends.search_novel_sites_via_web("末日", [("番茄小说", "https://fanqienovel.com/rank")], 5)
    assert [row["title"] for row in items] == ["末日:只有我一人拥有异能"]
