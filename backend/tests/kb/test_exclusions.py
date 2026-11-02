import pytest
from anvil.kb.config import TC1_EXCLUDED_ISSUE_IDS
from anvil.kb.exclusions import ExclusionGuard


def test_all_tc1_issues_are_denied():
    guard = ExclusionGuard()
    for issue_id in TC1_EXCLUDED_ISSUE_IDS:
        assert guard.is_excluded("gitlab-issue", issue_id)
        assert not guard.allows("gitlab-issue", issue_id)


@pytest.mark.parametrize("external_id", ["5776", "#5776", " 5776 "])
def test_issue_id_forms_are_normalised(external_id):
    assert ExclusionGuard().is_excluded("gitlab-issue", external_id)


def test_non_tc1_issue_is_allowed():
    guard = ExclusionGuard()
    assert guard.allows("gitlab-issue", "6000")


def test_other_sources_are_never_excluded_by_issue_id():
    guard = ExclusionGuard()
    assert guard.allows("rtems-docs", "5776")
    assert guard.allows("confluence", "5776")
