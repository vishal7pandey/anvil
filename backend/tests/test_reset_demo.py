from unittest.mock import MagicMock, call, patch
import pytest

from scripts.reset_demo import (
    BASELINE_TICKETS,
    reset_confluence_rtkb,
    reset_containers,
    reset_github_mirrors,
    reset_jira,
)


def test_reset_jira_transitions_and_updates_labels():
    with patch("anvil.tools.jira.jira_get") as mock_get, \
         patch("anvil.tools.jira.jira_transition") as mock_trans, \
         patch("anvil.tools._atlassian.request") as mock_req:

        # RTEMS-1 is In Progress with anvil label
        mock_get.side_effect = lambda key: {
            "key": key,
            "status": "In Progress" if key == "RTEMS-1" else "To Do",
            "labels": ["posix", "tc1", "anvil", "anvil-in-progress"] if key == "RTEMS-1" else BASELINE_TICKETS[key]["labels"],
        }

        reset_jira(dry_run=False)

        # RTEMS-1 should be transitioned to To Do
        mock_trans.assert_called_once_with("RTEMS-1", "To Do")

        # RTEMS-1 should have its labels reset to baseline
        mock_req.assert_called_once_with(
            "PUT",
            "/rest/api/3/issue/RTEMS-1",
            json={"fields": {"labels": ["posix", "tc1"], "assignee": None}},
        )


def test_reset_jira_dry_run_does_not_mutate():
    with patch("anvil.tools.jira.jira_get") as mock_get, \
         patch("anvil.tools.jira.jira_transition") as mock_trans, \
         patch("anvil.tools._atlassian.request") as mock_req:

        mock_get.return_value = {"key": "RTEMS-1", "status": "Done", "labels": ["posix", "tc1", "anvil-done"]}

        reset_jira(dry_run=True)

        mock_trans.assert_not_called()
        mock_req.assert_not_called()


def test_reset_github_mirrors():
    with patch("anvil.tools.github._gh") as mock_gh:
        def fake_gh(method, path, **kwargs):
            if "/pulls" in path and method == "GET":
                return [
                    {"number": 42, "head": {"ref": "anvil/tc1-patch"}},
                    {"number": 99, "head": {"ref": "main"}},
                ]
            if "/branches" in path and method == "GET":
                return [
                    {"name": "anvil/tc1-patch"},
                    {"name": "tc1/5776-base"},
                ]
            return {}

        mock_gh.side_effect = fake_gh

        reset_github_mirrors(dry_run=False)

        # Check that PR #42 was closed on each mirror
        assert any(
            call("PATCH", "/repos/vishal7pandey/rtems/pulls/42", json={"state": "closed"}) in mock_gh.mock_calls
            for _ in [1]
        )
        # Check that branch anvil/tc1-patch was deleted
        assert any(
            call("DELETE", "/repos/vishal7pandey/rtems/git/refs/heads/anvil/tc1-patch") in mock_gh.mock_calls
            for _ in [1]
        )


def test_reset_confluence_rtkb_protects_synthetic_and_deletes_agent_pages():
    with patch("anvil.tools.confluence.confluence_search") as mock_search, \
         patch("anvil.tools._atlassian.request") as mock_req, \
         patch("subprocess.run") as mock_subproc:

        mock_search.return_value = [
            {"id": "28541041", "title": "RTEMS Engineering KB Home"},
            {"id": "28672001", "title": "ANVIL-13 evidence: Host tools"},
            {"id": "28868609", "title": "[synthetic] Architecture Note: Payload Telemetry Flow"},
            {"id": "99990001", "title": "CRA Compliance Audit Report for RefDev"},
            {"id": "99990002", "title": "Incident Analysis: Telemetry packet drops and TM_GUIDANCE"},
        ]
        mock_subproc.return_value = MagicMock(returncode=0, stdout="Ingested ok", stderr="")

        # 1. Reset without keeping TC4 run 1 -> deletes both agent pages
        reset_confluence_rtkb(keep_tc4_run1=False, dry_run=False)

        mock_req.assert_has_calls([
            call("DELETE", "/wiki/api/v2/pages/99990001"),
            call("DELETE", "/wiki/api/v2/pages/99990002"),
        ], any_order=True)

        # 2. Reset WITH keeping TC4 run 1 -> preserves 99990002
        mock_req.reset_mock()
        reset_confluence_rtkb(keep_tc4_run1=True, dry_run=False)

        mock_req.assert_called_once_with("DELETE", "/wiki/api/v2/pages/99990001")


def test_reset_containers():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="anvil-sbx-12345\nanvil-sandbox-leon3\npostgres\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]

        reset_containers(dry_run=False)

        # anvil-sbx-12345 and anvil-sandbox-leon3 should be rm -f'd
        mock_run.assert_has_calls([
            call(["docker", "rm", "-f", "anvil-sbx-12345"], capture_output=True, text=True, timeout=30),
            call(["docker", "rm", "-f", "anvil-sandbox-leon3"], capture_output=True, text=True, timeout=30),
        ], any_order=True)

