from agents.llm_streaming import IncompleteGenerationError, collect_chat_text


class HtmlResponse:
    headers = {"content-type": "text/html; charset=utf-8"}


class HtmlStream:
    response = HtmlResponse()


def test_html_gateway_response_is_reported_as_configuration_error():
    try:
        collect_chat_text(HtmlStream())
    except IncompleteGenerationError as error:
        assert "PROXY_URL" in str(error)
    else:
        raise AssertionError("网页响应应被识别为配置错误")
