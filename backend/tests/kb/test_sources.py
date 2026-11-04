import httpx
import respx
from anvil.kb.config import KBSettings
from anvil.kb.sources import (
    CONFLUENCE_SOURCE,
    documents_from_docs_dir,
    fetch_closed_gitlab_issues,
    fetch_confluence_pages,
)


def _issue(iid: int, title: str) -> dict:
    return {
        "iid": iid,
        "title": title,
        "description": f"Body for issue {iid}.",
        "web_url": f"https://gitlab.example/rtems/rtems/-/issues/{iid}",
        "labels": ["bug"],
        "closed_at": "2026-10-01T00:00:00Z",
    }


@respx.mock
def test_closed_issues_are_fetched_and_tc1_is_excluded():
    respx.route(method="GET", path__regex=r"/api/v4/projects/.*/issues").mock(
        return_value=httpx.Response(
            200, json=[_issue(5776, "TC1"), _issue(6000, "Safe")]
        )
    )
    settings = KBSettings(
        gitlab_url="https://gitlab.example",
        gitlab_project="rtems/rtems",
        gitlab_token=None,
    )
    with httpx.Client() as client:
        documents = fetch_closed_gitlab_issues(settings, client=client)

    assert [document.external_id for document in documents] == ["6000"]
    assert documents[0].source == "gitlab-issue"
    assert documents[0].title == "Safe"
    assert "Body for issue 6000." in documents[0].text


@respx.mock
def test_issue_pagination_stops_on_short_page():
    route = respx.route(method="GET", path__regex=r"/api/v4/projects/.*/issues").mock(
        side_effect=[
            httpx.Response(
                200, json=[_issue(6000 + i, f"Issue {i}") for i in range(100)]
            ),
            httpx.Response(200, json=[_issue(7000, "Last")]),
        ]
    )
    settings = KBSettings(
        gitlab_url="https://gitlab.example", gitlab_project="rtems/rtems"
    )
    with httpx.Client() as client:
        documents = fetch_closed_gitlab_issues(settings, client=client)

    assert len(documents) == 101
    assert route.calls.call_count == 2


def test_docs_dir_loader_skips_hidden_build_and_binary(tmp_path):
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "intro.rst").write_text(
        "RTEMS Intro\n===========\n\nHello.", encoding="utf-8"
    )
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generated.md").write_text("generated", encoding="utf-8")
    (tmp_path / ".hidden.md").write_text("hidden", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")

    documents = list(
        documents_from_docs_dir(tmp_path, "https://github.com/vishal7pandey/rtems-docs")
    )

    assert [document.external_id for document in documents] == ["user/intro.rst"]
    assert documents[0].title == "RTEMS Intro"
    assert documents[0].url.endswith("/blob/main/user/intro.rst")


def test_title_skips_spdx_preamble(tmp_path):
    (tmp_path / "book.md").write_text(
        "% SPDX-License-Identifier: CC-BY-SA-4.0\n\n# RTEMS Documentation\n\nBody.",
        encoding="utf-8",
    )
    documents = list(documents_from_docs_dir(tmp_path))
    assert documents[0].title == "RTEMS Documentation"


def test_confluence_pages_fetched_and_parsed():
    mock_search_results = [
        {"id": "28868609", "title": "Payload Telemetry Flow", "url": "/wiki/spaces/RTKB/pages/28868609"},
        {"id": "att12345", "title": "Attachment (skip)", "url": "/wiki/attachment"},
    ]

    def mock_search(cql: str, limit: int = 10):
        assert "space = RTKB" in cql
        return mock_search_results

    def mock_get(page_id: str):
        if page_id == "28868609":
            return {
                "id": "28868609",
                "title": "Payload Telemetry Flow",
                "version": 1,
                "body": "<h2>1. Overview</h2><p>The science payload instrument acquires telemetry.</p>",
                "url": "https://example.atlassian.net/wiki/spaces/RTKB/pages/28868609",
            }
        raise ValueError(f"Unknown page {page_id}")

    docs = fetch_confluence_pages(
        space_key="RTKB",
        search_fn=mock_search,
        get_fn=mock_get,
    )

    assert len(docs) == 1
    doc = docs[0]
    assert doc.source == CONFLUENCE_SOURCE
    assert doc.external_id == "28868609"
    assert doc.title == "Payload Telemetry Flow"
    assert doc.url == "https://example.atlassian.net/wiki/spaces/RTKB/pages/28868609"
    assert "The science payload instrument acquires telemetry." in doc.text
    assert doc.metadata == {"space": "RTKB", "version": 1}


def test_confluence_pages_guard_exclusion():
    mock_search_results = [
        {"id": "28868609", "title": "Safe Page"},
        {"id": "28868610", "title": "Leaky Page mentioning #5776"},
    ]

    def mock_search(cql: str, limit: int = 10):
        return mock_search_results

    def mock_get(page_id: str):
        if page_id == "28868609":
            return {"id": "28868609", "title": "Safe Page", "version": 1, "body": "<p>Safe content</p>"}
        return {"id": "28868610", "title": "Leaky Page", "version": 1, "body": "<p>Fixes issue #5776</p>"}

    docs = fetch_confluence_pages(
        space_key="RTKB",
        search_fn=mock_search,
        get_fn=mock_get,
    )

    assert len(docs) == 1
    assert docs[0].external_id == "28868609"


def test_confluence_pages_populates_created_at():
    mock_search_results = [
        {"id": "28868609", "title": "Payload Telemetry Flow", "url": "/wiki/spaces/RTKB/pages/28868609"},
    ]

    def mock_get(page_id: str):
        return {
            "id": "28868609",
            "title": "Payload Telemetry Flow",
            "version": 1,
            "body": "<p>Content</p>",
            "url": "https://example.atlassian.net/wiki/spaces/RTKB/pages/28868609",
            "createdAt": "2026-08-15T10:00:00.000Z",
        }

    docs = fetch_confluence_pages(
        space_key="RTKB",
        search_fn=lambda cql, limit: mock_search_results,
        get_fn=mock_get,
    )
    assert len(docs) == 1
    assert docs[0].metadata.get("created_at") == "2026-08-15T10:00:00.000Z"


def test_docs_dir_populates_created_at_from_git(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)

    doc_file = repo / "manual.rst"
    doc_file.write_text("# Manual\n\nContent here.", encoding="utf-8")
    subprocess.run(["git", "add", "manual.rst"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add manual"], cwd=repo, check=True)

    docs = list(documents_from_docs_dir(repo))
    assert len(docs) == 1
    assert "created_at" in docs[0].metadata
    from datetime import datetime
    dt = datetime.fromisoformat(docs[0].metadata["created_at"])
    assert dt is not None

