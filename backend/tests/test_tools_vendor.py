"""ANVIL-82: vendor_reimport does the mechanical three-way merge; the agent resolves the rest.

Runs the generated sandbox script through real bash+git on a fake vendored tree: old tag,
patched vendored dir, new tag.
"""
import shutil
import subprocess
from pathlib import Path

import pytest
from anvil.tools.vendor import END, START, parse, vendor_command

bash = shutil.which("bash")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _fake_tree(tmp_path: Path) -> Path:
    """lwip-upstream with tags old/new; lwip vendored = old + local patches."""
    up = tmp_path / "lwip-upstream"
    up.mkdir()
    _git(up, "init", "-q")
    _git(up, "config", "user.email", "t@t")
    _git(up, "config", "user.name", "t")
    # OLD tag: three files. a.c is long so the local patch (bottom) and the upstream change
    # (top) live in clearly separate diff3 regions — a tiny file would overlap and conflict.
    (up / "a.c").write_text("".join(f"line{i}\n" for i in range(1, 30)))
    (up / "b.c").write_text("b1\nshared\nb3\n")
    (up / "gone.c").write_text("old only\n")
    _git(up, "add", ".")
    _git(up, "commit", "-qm", "v1")
    _git(up, "tag", "old")
    # NEW tag: a.c changed at the top, b.c changes the line the local patch also changes
    # (true collision), gone.c deleted, new.c added
    (up / "a.c").write_text("a-v2\n" + "".join(f"line{i}\n" for i in range(1, 30)))
    (up / "b.c").write_text("b1\nupstream shared\nb3\n")
    (up / "gone.c").unlink()
    (up / "new.c").write_text("new in v2\n")
    _git(up, "add", "-A")
    _git(up, "commit", "-qm", "v2")
    _git(up, "tag", "new")

    # vendored = OLD tag's contents (not the working dir, which sits at the new tag) + patches
    vendor = tmp_path / "lwip"
    vendor.mkdir()
    tar = subprocess.run(["git", "-C", str(up), "archive", "old"],
                         check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(vendor)], input=tar, check=True)
    (vendor / "a.c").write_text("".join(f"line{i}\n" for i in range(1, 30)) + "rtems patch\n")
    (vendor / "b.c").write_text("b1\nlocal shared\nb3\n")      # collides with upstream's change
    return tmp_path


@pytest.mark.skipif(bash is None, reason="needs bash")
def test_reimport_merges_clean_flags_true_conflicts_and_moves_the_tag(tmp_path):
    tree = _fake_tree(tmp_path)
    cmd = vendor_command(tree.as_posix(), "lwip", "old", "new")
    res = subprocess.run([bash, "-c", cmd], capture_output=True, text=True)
    out = parse(res.stdout, res.returncode)
    assert out["conflicts"] == ["./b.c"]            # the real collision only
    assert "./a.c" in out["merged"]                 # local patch + upstream change merged
    assert "./new.c" in out["added"]                # new upstream file landed
    assert "./gone.c" in out["removed"]             # removed upstream
    vend = tree / "lwip"
    assert "<<<<<<<" in (vend / "b.c").read_text()  # conflict markers written for the agent
    assert "a-v2" in (vend / "a.c").read_text() and "rtems patch" in (vend / "a.c").read_text()
    head = subprocess.run(["git", "-C", str(tree / "lwip-upstream"), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    tag = subprocess.run(["git", "-C", str(tree / "lwip-upstream"), "rev-parse", "new"],
                         capture_output=True, text=True).stdout.strip()
    assert head == tag                                 # upstream dir sits at the new tag


def test_parse_turns_the_marker_block_into_a_report():
    out = parse(f"noise\n{START}\nmerged ./a.c\nconflict ./b.c\nkept ./c.c  # removed upstream, local changes\n{END}\n", 0)
    assert out["merged"] == ["./a.c"] and out["conflicts"] == ["./b.c"]
    assert out["ok"] is False                        # conflicts -> not ok until resolved

