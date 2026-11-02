import httpx
import respx
from anvil.kb.config import KBSettings
from anvil.kb.sources import documents_from_docs_dir, fetch_closed_gitlab_issues


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
