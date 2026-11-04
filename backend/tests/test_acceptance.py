"""ANVIL-44: case acceptance checks, run host-side after the agent finishes."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil.cases import CaseError, load_case
from anvil.cases.acceptance import SANDBOX_CHECKS, run_acceptance
from anvil.cases.spec import CHECKS_DIR

sys.path.insert(0, str(CHECKS_DIR))
from vendored_matches_upstream import normalize, strip_marker  # noqa: E402

VMU = CHECKS_DIR / "vendored_matches_upstream.py"
CFM = CHECKS_DIR / "changed_files_match.py"


def git(*args, cwd):
    return subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())


# -- reading a file "without the marker" -----------------------------------------------------------

def test_strip_marker_keeps_the_upstream_reading():
    patched = ("a\n#ifdef __rtems__\nrtems only\n#else\nupstream\n#endif /* __rtems__ */\n"
               "#ifndef __rtems__\nkept\n#else\ndropped\n#endif\n"
               "#ifdef __rtems__\n#if X\nnested rtems\n#endif\n#endif\n#if Y\nplain\n#endif\nz\n")
    assert strip_marker(patched, "__rtems__") == "a\nupstream\nkept\n#if Y\nplain\n#endif\nz\n"


def test_normalize_ignores_trailing_space_blank_lines_and_directive_labels():
    assert normalize("x  \n\n#endif /* A */\n") == normalize("x\n#endif\n")
    assert normalize("x\n") != normalize("y\n")


# -- vendored_matches_upstream.py on a small real history ------------------------------------------

@pytest.fixture
def project(tmp_path):
    up = tmp_path / "proj" / "upstream"
    up.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=up)
    git("config", "user.email", "t@t", cwd=up)
    git("config", "user.name", "t", cwd=up)
    write(up / "src/a.c", "int a = 1;\n")
    write(up / "src/b.h", "#define B 1\n")
    git("add", ".", cwd=up)
    git("commit", "-qm", "v1", cwd=up)
    git("tag", "V1", cwd=up)
    write(up / "src/a.c", "int a = 2;\n")
    git("commit", "-qam", "v2", cwd=up)
    git("tag", "V2", cwd=up)

    repo = tmp_path / "proj"
    git("init", "-q", "-b", "base", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    write(repo / "file-import.json", json.dumps({"files-to-import": ["src/a.c", "src/b.h"]}))
    write(repo / "vend/src/a.c", "int a = 1;\n")  # v1 + a local patch in b.h
    write(repo / "vend/src/b.h", "#define B 1\n#ifdef __rtems__\n#define RTEMS 1\n#endif\n")
    (repo / ".gitignore").write_text("upstream/\n")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "base", cwd=repo)
    git("checkout", "-q", "-b", "work", cwd=repo)
    git("checkout", "-q", "V2", cwd=up)
    return repo


def vmu(repo, *extra):
    out = subprocess.run([sys.executable, str(VMU), "--manifest", "file-import.json", "--vendored", "vend",
                          "--upstream", "upstream", "--tag", "V2", "--marker", "__rtems__",
                          "--base-ref", "base", *extra], cwd=repo, capture_output=True, text=True)
    return out.returncode, json.loads(out.stdout)


def test_stale_vendored_tree_fails(project):  # TC2 run 7: pointer moved, sources not re-imported
    code, result = vmu(project)
    assert code == 1 and "vend/src/a.c: differs from V2 outside __rtems__ blocks" in result["problems"]


def test_honest_upgrade_with_patches_kept_passes(project):
    write(project / "vend/src/a.c", "int a = 2;\n")
    code, result = vmu(project)
    assert code == 0 and result["ok"] and result["checked"] == 2


def test_reimport_that_drops_local_patches_fails_unless_declared(project):  # TC2 runs 3-5
    write(project / "vend/src/a.c", "int a = 2;\n")
    write(project / "vend/src/b.h", "#define B 1\n")
    code, result = vmu(project)
    assert code == 1 and any("local patches lost" in p for p in result["problems"])
    assert vmu(project, "--allow-dropped", "src/b.h")[0] == 0  # retired on purpose, like upstream did


def test_shrinking_the_manifest_fails(project):
    write(project / "vend/src/a.c", "int a = 2;\n")
    write(project / "file-import.json", json.dumps({"files-to-import": ["src/b.h"]}))
    code, result = vmu(project)
    assert code == 1 and "manifest dropped src/a.c, which V2 still has" in result["problems"]


def test_submodule_not_at_tag_fails(project):
    write(project / "vend/src/a.c", "int a = 2;\n")
    git("checkout", "-q", "V1", cwd=project / "upstream")
    code, result = vmu(project)
    assert code == 1 and "upstream is not checked out at V2" in result["problems"]


# -- changed_files_match.py ------------------------------------------------------------------------

def test_change_must_include_a_test(project):
    def cfm():
        out = subprocess.run([sys.executable, str(CFM), "--base-ref", "base", "--pattern", "testsuites/*"],
                             cwd=project, capture_output=True, text=True)
        return out.returncode, json.loads(out.stdout)

    write(project / "vend/src/a.c", "int a = 2;\n")
    git("commit", "-qam", "fix only", cwd=project)
    code, result = cfm()
    assert code == 1 and result["problems"][0].startswith("the committed change has no matching file")
    write(project / "testsuites/t/init.c", "test\n")
    git("add", ".", cwd=project)
    git("commit", "-qm", "test", cwd=project)
    assert cfm() == (0, {"ok": True, "matched": ["testsuites/t/init.c"], "problems": []})


# -- the host-side runner --------------------------------------------------------------------------

CASE = """
id = "c1"
kind = "component-upgrade"
repo = "o/p"
as_of = "2026-01-01"
[upstream]
fix_commits = ["abc"]
[base]
branch = "case/base"
[ticket]
project = "P"
summary = "s"
body = "b"
[[acceptance.checks]]
name = "upgrade is real"
script = "vendored_matches_upstream.py"
args = ["--base-ref", "{base_ref}"]
"""


class Sandbox:
    def __init__(self, output, exit_code):
        self.output, self.exit_code, self.uploaded, self.commands = output, exit_code, [], []

    def upload_files(self, files):
        self.uploaded += [p for p, _ in files]
        return [SimpleNamespace(path=p, error=None) for p, _ in files]

    def execute(self, cmd, timeout=None):
        self.commands.append(cmd)
        return SimpleNamespace(output=self.output, exit_code=self.exit_code)


def test_runner_uploads_fresh_scripts_and_reports_failures(tmp_path):
    (tmp_path / "c1.toml").write_text(CASE, encoding="utf-8")
    case = load_case(tmp_path / "c1.toml")
    bad = Sandbox(json.dumps({"ok": False, "problems": ["lwip/a.c differs"], "more_problems": 128}), 1)
    problems = run_acceptance(case, bad, "/workspace/p")
    assert bad.uploaded == [SANDBOX_CHECKS + "vendored_matches_upstream.py"]
    assert "origin/case/base" in bad.commands[0] and bad.commands[0].startswith("cd /workspace/p && python3 ")
    assert problems == ["acceptance check 'upgrade is real' failed: lwip/a.c differs (+128 more)"]
    assert run_acceptance(case, Sandbox(json.dumps({"ok": True, "problems": []}), 0), "/workspace/p") == []


def test_unknown_check_script_is_a_case_error(tmp_path):
    (tmp_path / "c1.toml").write_text(CASE.replace("vendored_matches_upstream.py", "nope.py"), encoding="utf-8")
    with pytest.raises(CaseError, match="nope.py not found"):
        load_case(tmp_path / "c1.toml")


def test_verify_outcome_includes_case_acceptance(monkeypatch):
    from anvil.tools import github, run_scope

    monkeypatch.setattr(github, "_gh", lambda *a, **k: [{"number": 3, "base": {"ref": "b"}}])
    monkeypatch.setattr(github, "github_checks", lambda *a, **k: {"state": "success", "failing": []})
    monkeypatch.setattr(run_scope, "case_problems", lambda run, sb: ["acceptance check 'x' failed: stale"])
    sandbox = SimpleNamespace(execute=lambda cmd, timeout=None: SimpleNamespace(
        output="anvil/r-2\n" if "rev-parse" in cmd else "lwip-upstream\n", exit_code=0))
    run = {"run_id": "r", "repo": "o/p", "base_ref": "b", "jira_key": "RTEMS-2"}
    # PR into base + green CI, yet not verified: the case check failed (TC2 run 7)
    assert run_scope.verify_outcome(run, sandbox) == ["acceptance check 'x' failed: stale"]
