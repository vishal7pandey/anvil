"""ANVIL-90: a case's [build] section lands in the agent's task; no section, no change."""

import pytest

from anvil.cases import CaseError, get_case, load_case
from anvil.runtime.executor import _task_message

CHECKOUT = {"path": "/workspace/rtems", "branch": "anvil/rtems-1-fix",
            "base_ref": "tc1/5776-base", "base_sha": "abc123"}


def test_5776_declares_and_injects_the_recipe():
    run = {"jira_key": "RTEMS-1", "repo": "vishal7pandey/rtems"}
    task = _task_message(run, "fix the ticket", CHECKOUT)
    assert "build and test commands for this code base" in task
    assert "./waf configure" in task and "erc32-sis" in task
    case = get_case("rtems-5776")
    assert case.build["configure"].startswith("cat > config.ini")
    assert case.build["test"].startswith("rtems-test")


def test_a_case_without_build_changes_nothing(tmp_path):
    # rtems-5806 has no [build]: the task must be byte-for-byte the pre-ANVIL-90 text.
    run = {"jira_key": "RTEMS-2", "repo": "vishal7pandey/rtems"}
    task = _task_message(run, "fix the ticket", CHECKOUT)
    assert "build and test commands" not in task


def test_bad_build_table_is_rejected(tmp_path):
    bad = tmp_path / "x.toml"
    bad.write_text(
        'id = "x"\nrepo = "o/r"\nas_of = "2026-01-01"\n'
        '[base]\nbranch = "b"\n'
        '[ticket]\nproject = "P"\nsummary = "s"\nbody = "t"\n'
        '[upstream]\nfix_commits = ["abc"]\n'
        '[build]\nconfigure = 1\n', encoding="utf-8")
    with pytest.raises(CaseError):
        load_case(bad)
