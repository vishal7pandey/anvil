import json
import os

import httpx
import pytest
import respx

from anvil.runtime.websearch import TC_BLOCKLIST, WITHHELD, blocked, make_web_search

URL = "http://gw.test/v1/responses"


def response(text: str, cited: list[str], opened: list[str] = ()) -> dict:
    output = [{"type": "web_search_call", "action": {"type": "open_page", "url": u}} for u in opened]
    output.append({"type": "message", "content": [{"type": "output_text", "text": text,
                                                   "annotations": [{"type": "url_citation", "url": u} for u in cited]}]})
    return {"output": output}


def test_blocked_matching():
    bl = TC_BLOCKLIST
    assert blocked("https://gitlab.rtems.org/rtems/rtos/rtems/-/issues/5776", bl)
    assert blocked("https://lists.rtems.org/pipermail/devel/2026-September/1.html", bl)
    assert blocked("https://github.com/rtems/rtems/commit/a62e60b5", bl)
    assert blocked("https://www.github.com/vishal7pandey/rtems/tree/main", bl)
    assert not blocked("https://github.com/rtemsfoo/x", bl)          # prefix must be a path segment owner
    assert not blocked("https://github.com/lwip-tcpip/lwip", bl)
    assert not blocked("https://eur-lex.europa.eu/eli/reg/2024/2847/oj", bl)
    assert not blocked("https://notgitlab.rtems.org.evil.example/x", bl)


@respx.mock
def test_clean_result_returns_text_and_sources():
    respx.post(URL).mock(return_value=httpx.Response(200, json=response("CRA answer", ["https://eur-lex.europa.eu/x"])))
    out = make_web_search(TC_BLOCKLIST, gateway_url="http://gw.test/v1", api_key="k")("CRA?")
    assert out.startswith("CRA answer") and "- https://eur-lex.europa.eu/x" in out


@respx.mock
def test_retry_when_first_answer_touched_blocked_source():
    route = respx.post(URL).mock(side_effect=[
        httpx.Response(200, json=response("leaky", ["https://example.org"], opened=["https://gitlab.rtems.org/i/5776"])),
        httpx.Response(200, json=response("clean", ["https://example.org/doc"])),
    ])
    out = make_web_search(TC_BLOCKLIST, gateway_url="http://gw.test/v1", api_key="k")("mutex bug")
    assert out.startswith("clean") and "leaky" not in out
    second = json.loads(route.calls[1].request.content)
    assert "gitlab.rtems.org" in second["input"]


@respx.mock
def test_withheld_when_every_attempt_is_blocked():
    respx.post(URL).mock(return_value=httpx.Response(200, json=response("the fix is X", ["https://github.com/rtems/rtems/commit/a"])))
    out = make_web_search(TC_BLOCKLIST, gateway_url="http://gw.test/v1", api_key="k")("mutex bug")
    assert out == WITHHELD


@respx.mock
def test_no_blocklist_single_call_and_http_error():
    route = respx.post(URL).mock(return_value=httpx.Response(500))
    out = make_web_search((), gateway_url="http://gw.test/v1", api_key="k")("q")
    assert out.startswith("web_search failed") and route.call_count == 1


@pytest.mark.skipif(os.environ.get("ANVIL_LIVE") != "1", reason="live gateway test; set ANVIL_LIVE=1")
def test_live_gateway():
    out = make_web_search(TC_BLOCKLIST)("EU Cyber Resilience Act Annex I vulnerability handling requirements")
    assert "Sources:" in out and "gitlab.rtems.org" not in out
