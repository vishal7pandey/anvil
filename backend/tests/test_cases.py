import subprocess
from pathlib import Path

import pytest

from anvil.cases import CaseError, get_case, load_case, load_cases, withheld_union
from anvil.cases.base import build_base
from anvil.tools.git_push import PushSettings

SPEC = """
id = "{id}"
repo = "owner/proj"
as_of = "2026-01-01"
[upstream]
issues = ["{issue}"]
mrs = ["{mr}"]
fix_commits = ["{fix}"]
[withheld]
issues = ["900"]
[base]
branch = "case/{id}-base"
[ticket]
project = "BUG"
summary = "It crashes"
body = "Steps: run it."
labels = ["anvil", "x"]
[scoring]
target_files = ["src/a.c"]
"""


def _write(tmp: Path, id="c1", issue="1", mr="2", fix="abc123", extra="") -> Path:
    path = tmp / f"{id}.toml"
    path.write_text(SPEC.format(id=id, issue=issue, mr=mr, fix=fix) + extra, encoding="utf-8")
    return path


def test_spec_loads_and_upstream_is_always_withheld(tmp_path):
    case = load_case(_write(tmp_path))
    assert case.fix_commits == ("abc123",) and case.base_branch == "case/c1-base"
    assert case.withheld_issues == ("1", "900") and case.withheld_mrs == ("2",)
    assert case.withheld_commits == ("abc123",)  # the fix itself, without repeating it
    assert case.ticket_text.endswith("Code base: branch `case/c1-base` of github.com/owner/proj.\n")


def test_validation_errors_name_the_file(tmp_path):
    bad = tmp_path / "c2.toml"
    bad.write_text('id = "c2"\nrepo = "o/p"\n', encoding="utf-8")
    with pytest.raises(CaseError, match="c2.toml: missing required key"):
        load_case(bad)
    with pytest.raises(CaseError, match="must match the file name"):
        load_case(_write(tmp_path, id="c3").rename(tmp_path / "other.toml"))


def test_withheld_union_over_cases(tmp_path):
    _write(tmp_path, id="a", issue="1", mr="10", fix="f1")
    _write(tmp_path, id="b", issue="2", mr="10", fix="f2")
    union = withheld_union(load_cases(tmp_path))
    assert union.issues == ("1", "900", "2") and union.mrs == ("10",) and union.commits == ("f1", "f2")
    assert get_case("b", tmp_path).fix_commits == ("f2",)


def test_repo_case_reproduces_the_old_tc1_exclusions():
    from anvil.kb.config import WITHHELD_COMMIT_PREFIXES, WITHHELD_ISSUE_IDS, WITHHELD_MR_IDS

    assert sorted(WITHHELD_ISSUE_IDS) == sorted(["5776", "5806", "5723", "5750", "5749", "5760"])
    assert sorted(WITHHELD_MR_IDS) == sorted(["1493", "1526", "1448", "1468", "1484", "1479"])
    assert "a62e60b5" in WITHHELD_COMMIT_PREFIXES and len(WITHHELD_COMMIT_PREFIXES) == 6
    case = get_case("rtems-5776")
    assert case.ticket_key == "RTEMS-1" and "Code base: branch `tc1/5776-base`" in case.ticket_text


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """A repo with history: base commit, the fix, and an infra branch with CI files."""
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-q", "-b", "main", cwd=work)
    git("config", "user.email", "t@t", cwd=work)
    git("config", "user.name", "t", cwd=work)
    (work / "src").mkdir()
    (work / "src/a.c").write_text("buggy\n")
    git("add", ".", cwd=work)
    git("commit", "-qm", "before", cwd=work)
    (work / "src/a.c").write_text("fixed\n")
    git("commit", "-qam", "the fix", cwd=work)
    fix = git("rev-parse", "HEAD", cwd=work)
    git("checkout", "-qb", "infra", "HEAD~1", cwd=work)
    (work / ".github").mkdir()
    (work / ".github/ci.yml").write_text("ci\n")
    git("add", ".", cwd=work)
    git("commit", "-qm", "infra", cwd=work)
    remote_root = tmp_path / "remote"
    (remote_root / "owner").mkdir(parents=True)
    git("clone", "-q", "--bare", str(work), str(remote_root / "owner/proj.git"), cwd=tmp_path)
    return tmp_path, remote_root, fix


def test_build_base_is_parent_of_fix_plus_overlay_and_never_moves(upstream):
    tmp, remote_root, fix = upstream
    extra = '\n[base.overlay]\nref = "infra"\npaths = [".github"]\n'
    case = load_case(_write(tmp, id="c9", fix=fix, extra=extra))
    s = PushSettings(github_remote_base=remote_root.as_uri(), anvil_repos_dir=str(tmp / "repos"),
                     github_token="test-token")  # never the real gh token
    clone = tmp / "clone.git"
    git("clone", "-q", "--bare", str(remote_root / "owner/proj.git"), str(clone), cwd=tmp)

    first = build_base(case, push=True, clone=clone, s=s)
    assert first["pushed"] and first["overlaid_files"] == [".github/ci.yml"]
    remote = remote_root / "owner/proj.git"
    assert git("show", "case/c9-base:src/a.c", cwd=remote) == "buggy"  # pre-fix code
    assert git("show", "case/c9-base:.github/ci.yml", cwd=remote) == "ci"  # infra overlaid
    assert git("rev-parse", "case/c9-base^", cwd=remote) == git("rev-parse", f"{fix}^", cwd=remote)

    again = build_base(case, push=True, clone=clone, s=s)
    assert again["same_tree"] is True and again["pushed"] is None  # existing base untouched
