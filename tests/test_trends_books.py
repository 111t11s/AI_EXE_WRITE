import asyncio

from api import trends_api
from api.writing_schemas import TrendBook, TrendIdea, TrendReport, TrendRequest


def test_analysis_reads_selected_books_before_prompt(monkeypatch):
    topic = "末日:只有我一人拥有异能"
    previews = [{"title": "末日:只有我一人拥有异能", "abstract": "末日求生简介",
                 "chapters": [{"title": "第1章", "excerpt": "末日降临，主角觉醒异能。"}],
                 "url": "https://fanqienovel.com/page/1"}]
    monkeypatch.setattr(trends_api, "resolve_topics", lambda payload: ([topic], ["番茄小说"]))
    monkeypatch.setattr(trends_api.novel_preview, "fetch_previews", lambda books: previews)
    captured = {}

    async def generate(role, context, request):
        captured["context"] = context
        return TrendReport(overview="两个末日方向", requirement_summary="", ideas=[
            TrendIdea(genre="科幻", title="末日第一人", premise="末日中只有主角拥有异能",
                      discussion_hook="如果你是世界唯一异能者，会先救谁？", reference_topics=[topic]),
            TrendIdea(genre="科幻", title="末日秩序", premise="幸存者重建秩序",
                      discussion_hook="末日里该不该分享物资？", reference_topics=[topic]),
        ])

    monkeypatch.setattr(trends_api, "generate", generate)
    payload = TrendRequest(topics=[topic], books=[TrendBook(title=topic, url="https://fanqienovel.com/page/1",
                                                           source="番茄小说")])
    receipt = asyncio.run(trends_api.analyze(payload))

    assert captured["context"]["books"] == previews
    assert captured["context"]["topics"] == [topic]
    assert len(receipt.ideas) == 2
