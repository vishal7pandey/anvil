"""Response shapes follow the Confluence Cloud REST v2/v1 docs (hand-written, not recorded: no token yet)."""

import json

import pytest
import respx

from anvil.tools import confluence
from anvil.tools._atlassian import ToolError
from test_tools_jira import atlassian_env  # noqa: F401  (shared fixture, no conftest to avoid collisions)

PAGE = {"id": "42", "title": "Evidence RTEMS-1", "version": {"number": 3}, "body": {"storage": {"value": "<p>x</p>"}}, "_links": {"webui": "/spaces/RTKB/pages/42"}}


@respx.mock
def test_search_uses_v1_cql(atlassian_env):
    route = respx.get(f"{atlassian_env}/wiki/rest/api/search").respond(
        json={"results": [{"content": {"id": "42", "title": "Timers"}, "title": "Timers", "excerpt": "tick", "url": "/spaces/RTKB/pages/42", "resultGlobalContainer": {"title": "RTKB"}}]}
    )
    assert confluence.confluence_search("space = RTKB") == [
        {"id": "42", "title": "Timers", "space": "RTKB", "excerpt": "tick", "url": f"{atlassian_env}/wiki/spaces/RTKB/pages/42"}
    ]
    assert route.calls[0].request.url.params["cql"] == "space = RTKB"


@respx.mock
def test_get_page(atlassian_env):
    respx.get(f"{atlassian_env}/wiki/api/v2/pages/42").respond(json=PAGE)
    assert confluence.confluence_get("42")["body"] == "<p>x</p>"


@respx.mock
def test_write_creates_page_from_markdown(atlassian_env):
    respx.get(f"{atlassian_env}/wiki/api/v2/spaces").respond(json={"results": [{"id": "7"}]})
    respx.get(f"{atlassian_env}/wiki/api/v2/pages").respond(json={"results": []})
    post = respx.post(f"{atlassian_env}/wiki/api/v2/pages").respond(200, json={**PAGE, "version": {"number": 1}})
    out = confluence.confluence_write("RTKB", "Evidence RTEMS-1", "# Fix", parent_id="5")
    assert out == {"id": "42", "title": "Evidence RTEMS-1", "version": 1, "url": f"{atlassian_env}/wiki/spaces/RTKB/pages/42"}
    sent = json.loads(post.calls[0].request.content)
    assert sent["spaceId"] == "7" and sent["parentId"] == "5" and sent["body"] == {"representation": "storage", "value": "<h1>Fix</h1>"}


@respx.mock
def test_write_updates_existing_page_with_next_version(atlassian_env):
    respx.get(f"{atlassian_env}/wiki/api/v2/spaces").respond(json={"results": [{"id": "7"}]})
    respx.get(f"{atlassian_env}/wiki/api/v2/pages").respond(json={"results": [PAGE]})
    put = respx.put(f"{atlassian_env}/wiki/api/v2/pages/42").respond(json={**PAGE, "version": {"number": 4}})
    assert confluence.confluence_write("RTKB", "Evidence RTEMS-1", "<p>raw</p>")["version"] == 4
    sent = json.loads(put.calls[0].request.content)
    assert sent["version"] == {"number": 4} and sent["body"]["value"] == "<p>raw</p>"


@respx.mock
def test_write_unknown_space(atlassian_env):
    respx.get(f"{atlassian_env}/wiki/api/v2/spaces").respond(json={"results": []})
    with pytest.raises(ToolError, match="space 'NOPE' not found"):
        confluence.confluence_write("NOPE", "t", "x")
