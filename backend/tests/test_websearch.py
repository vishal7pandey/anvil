import json
import os

import httpx
import pytest
import respx

from anvil.runtime.websearch import (NOT_ALLOWED, TC_BLOCKLIST, WITHHELD, blocked, env_allowlist,
                                     make_web_search, matches)

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
def test_sends_run_id_as_litellm_spend_log_metadata():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=response("answer", [])))

    make_web_search(
        gateway_url="http://gw.test/v1",
        api_key="k",
        run_id="run-123",
    )("question")

    payload = json.loads(route.calls[0].request.content)
    assert payload["litellm_metadata"]["spend_logs_metadata"] == {
        "run_id": "run-123",
        "tool": "web_search",
    }


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


ALLOW = ("docs.rtems.org", "pubs.opengroup.org", "nongnu.org/lwip")


def test_allowlist_matching_is_host_and_path_segment_exact():
    assert matches("https://docs.rtems.org/branches/master/c-user/index.html", ALLOW)
    assert matches("https://www.nongnu.org/lwip/2_1_x/index.html", ALLOW)
    assert not matches("https://nongnu.org/lwipfake/x", ALLOW)
    assert not matches("https://docs.rtems.org.evil.example/x", ALLOW)
    assert not matches("https://evil.example/docs.rtems.org", ALLOW)


@respx.mock
def test_allowlisted_result_passes_and_the_query_names_the_sources():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=response(
        "posix answer", ["https://pubs.opengroup.org/onlinepubs/9799919799/x.html"],
        opened=["https://docs.rtems.org/a"])))
    out = make_web_search(TC_BLOCKLIST, allowlist=ALLOW, gateway_url="http://gw.test/v1", api_key="k")("q")
    assert out.startswith("posix answer") and route.call_count == 1
    assert "docs.rtems.org" in json.loads(route.calls[0].request.content)["input"]


@respx.mock
def test_a_page_outside_the_allowlist_withholds_the_whole_result():
    """A planted page that was only *opened* (not cited) still taints the summary."""
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=response(
        "run rm -rf", ["https://docs.rtems.org/a"], opened=["https://planted.example/howto"])))
    out = make_web_search((), allowlist=ALLOW, gateway_url="http://gw.test/v1", api_key="k")("q")
    assert out == NOT_ALLOWED.format(allowed=", ".join(ALLOW)) and "rm -rf" not in out
    assert route.call_count == 2
    assert "ONLY" in json.loads(route.calls[1].request.content)["input"]


@respx.mock
def test_retry_recovers_when_the_second_answer_stays_on_the_list():
    respx.post(URL).mock(side_effect=[
        httpx.Response(200, json=response("off-list", ["https://blog.example/x"])),
        httpx.Response(200, json=response("on-list", ["https://docs.rtems.org/b"])),
    ])
    out = make_web_search((), allowlist=ALLOW, gateway_url="http://gw.test/v1", api_key="k")("q")
    assert out.startswith("on-list") and "off-list" not in out


@respx.mock
def test_blocklist_still_wins_over_the_allowlist():
    respx.post(URL).mock(return_value=httpx.Response(200, json=response(
        "the fix", ["https://github.com/rtems/rtems/commit/a"])))
    out = make_web_search(TC_BLOCKLIST, allowlist=("github.com",), gateway_url="http://gw.test/v1", api_key="k")("q")
    assert out == WITHHELD


def test_env_allowlist(monkeypatch):
    monkeypatch.setenv("ANVIL_WEB_ALLOWLIST", " docs.rtems.org, ,man7.org ")
    assert env_allowlist() == ("docs.rtems.org", "man7.org")
    monkeypatch.delenv("ANVIL_WEB_ALLOWLIST")
    assert env_allowlist() == ()


def test_cases_declare_trusted_sources_and_never_allow_a_blocked_one():
    from anvil.cases import load_cases
    cases = load_cases()
    assert cases and all(c.web_allow for c in cases)
    for c in cases:
        assert not any(blocked(f"https://{a}/", TC_BLOCKLIST) for a in c.web_allow), c.id


@pytest.mark.skipif(os.environ.get("ANVIL_LIVE") != "1", reason="live gateway test; set ANVIL_LIVE=1")
def test_live_gateway():
    out = make_web_search(TC_BLOCKLIST)("EU Cyber Resilience Act Annex I vulnerability handling requirements")
    assert "Sources:" in out and "gitlab.rtems.org" not in out
