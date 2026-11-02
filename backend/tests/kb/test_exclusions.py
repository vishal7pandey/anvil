import pytest
from anvil.kb.config import (
    TC1_EXCLUDED_COMMIT_PREFIXES,
    TC1_EXCLUDED_ISSUE_IDS,
    TC1_EXCLUDED_MR_IDS,
)
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


@pytest.mark.parametrize(
    "text",
    [
        "Fixes #5776 in score",
        "See issue 5776 for the reproducer",
        "https://gitlab.rtems.org/rtems/rtems/-/issues/5806",
        "Refer to !1493 for priority ceiling logic",
        "Check merge_requests/1526",
        "MR !1448 addresses the dosfs buffer overflow",
        "Fixed in commit a62e60b52501 by author",
        "Backported 97cf8529 arithmetic fix",
    ],
)
def test_content_referencing_denied_items_is_excluded(text):
    guard = ExclusionGuard()
    assert guard.is_content_excluded(text)
    assert not guard.allows_content(text)


def test_safe_content_is_allowed():
    guard = ExclusionGuard()
    assert guard.allows_content("Fixes #6000 in classic API")
    assert guard.allows_content("See MR !9999 or issues/9999")
    assert guard.allows_content("Commit c0ffee123456 passed CI")
    assert guard.allows_content("Rate monotonic scheduling overview")
