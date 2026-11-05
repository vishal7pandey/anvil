"""vendor_reimport: the mechanical part of a vendored-component upgrade (ANVIL-82).

A component-upgrade case used to have the agent drive diff -u / patch -p1 and .rej handling across
~150 files; that failed TC2 runs 2-6. This tool does a git three-way merge per file instead:

    base   = the old upstream tag inside <component>-upstream (a git submodule/clone that already
             has both tags — submodule init fetched them while the sandbox had network)
    ours   = the current vendored tree (old import + local patches)
    theirs = the new upstream tag

`git merge-file` writes conflict markers only where a local patch truly collides with an upstream
change; files the agent touched but upstream didn't stay patched, files the agent didn't touch take
the upstream version, new upstream files land, removed-upstream files go away unless locally patched
(reported as kept-with-local-changes). The tool then points the upstream dir at the new tag so the
submodule check is satisfied too.

The agent's remaining job is resolving the listed conflicts, rebuilding and testing — the same job
the case asks for, minus the fragile patch arithmetic.
"""

from __future__ import annotations

import shlex
from typing import Any, Callable

MAX_OUTPUT = 6000

START = "__ANVIL_VRI__"
END = "__ANVIL_VRI_END__"


def vendor_command(path: str, component: str, from_tag: str, to_tag: str) -> str:
    """Shell for the sandbox: merge the vendored tree with the new upstream tag, per file."""
    comp = shlex.quote(component)
    upstream = f"{component}-upstream"
    up = shlex.quote(upstream)
    p = shlex.quote(path)
    return (
        f"cd {p} || exit 2; "
        f"[ -d {comp} ] || {{ echo 'no component dir {component}'; exit 2; }}; "
        f"[ -d {up}/.git ] || [ -f {up}/.git ] || {{ echo 'no upstream git dir {upstream}'; exit 2; }}; "
        f"B=/tmp/vri-base-$$; T=/tmp/vri-theirs-$$; "
        f"rm -rf $B $T; mkdir -p $B $T; "
        f"git -C {up} archive {shlex.quote(from_tag)} | tar -x -C $B || {{ echo 'base tag unreadable'; exit 2; }}; "
        f"git -C {up} archive {shlex.quote(to_tag)} | tar -x -C $T || {{ echo 'target tag unreadable'; exit 2; }}; "
        # union of the three file lists drives every decision
        f"(cd $B && find . -type f | sort; cd {p}/{component} && find . -type f | sort; "
        f" cd $T && find . -type f | sort) | sort -u > /tmp/vri-files-$$; "
        f"echo {START}; "
        # per file: b/o/t presence + cmp decide: copy-theirs, keep-ours, merge-file, or conflict
        f"while IFS= read -r f; do "
        f"  b=$B/$f; o={component}/$f; t=$T/$f; "
        f"  if [ ! -f $t ]; then "
        f"    if [ ! -f $o ]; then :; "
        f"    elif [ ! -f $b ] || ! cmp -s $o $b; then echo \"kept $f  # removed upstream, local changes\"; "
        f"    else rm $o && echo \"removed $f\"; fi; "
        f"  elif [ ! -f $b ]; then "
        f"    if [ ! -f $o ]; then mkdir -p $(dirname $o) && cp $t $o && echo \"added $f\"; "
        f"    elif cmp -s $o $t; then echo \"same $f\"; "
        f"    else echo \"conflict $f  # added both sides differently\"; fi; "
        f"  elif [ ! -f $o ]; then cp $t $o && echo \"added $f\"; "
        f"  elif cmp -s $o $t; then echo \"same $f\"; "
        f"  elif cmp -s $o $b; then cp $t $o && echo \"merged $f\"; "
        f"  elif cmp -s $t $b; then echo \"kept $f\"; "
        f"  else "
        f"    if git merge-file -p $o $b $t > /tmp/vri-m-$$ 2>/dev/null; then mv /tmp/vri-m-$$ $o; echo \"merged $f\"; "
        f"    else mv /tmp/vri-m-$$ $o; echo \"conflict $f\"; fi; "
        f"  fi; "
        f"done < /tmp/vri-files-$$; "
        # point the upstream dir at the new tag — the case wants the submodule at it
        f"git -C {up} checkout -q {shlex.quote(to_tag)} 2>/dev/null && echo \"tag {to_tag} checked out\" || "
        f"echo \"warn: could not checkout {to_tag} in {upstream}\"; "
        f"echo {END}"
    )


def parse(output: str, exit_code: int) -> dict[str, Any]:
    """The report the agent reads: which files merged, which genuinely conflict."""
    if START not in output or END not in output:
        return {"ok": False, "exit_code": exit_code, "output": output.strip()[-MAX_OUTPUT:]}
    block = output.split(START, 1)[1].split(END, 1)[0]
    buckets: dict[str, list[str]] = {"merged": [], "conflict": [], "added": [], "removed": [],
                                   "kept": [], "same": []}
    tail = []
    for line in block.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] in buckets:
            buckets[parts[0]].append(parts[1])
        elif line.strip():
            tail.append(line.strip())
    return {
        "ok": not buckets["conflict"],
        "merged": buckets["merged"],
        "conflicts": buckets["conflict"],
        "added": buckets["added"],
        "removed": buckets["removed"],
        "kept_local": buckets["kept"],
        "unchanged": len(buckets["same"]),
        "output": "\n".join(tail)[-MAX_OUTPUT:],
        "note": ("Resolve the conflict files: they carry <<<<<<< markers, each is a local patch that "
                 "truly collides with the upstream change. Rebuild and test after." if buckets["conflict"]
                 else "No true conflicts — vendored tree matches the new tag plus your local patches."),
    }


def make_vendor_reimport(run: dict[str, Any], sandbox: Any) -> Callable[..., dict[str, Any]]:
    def vendor_reimport(component: str, from_tag: str, to_tag: str) -> dict[str, Any]:
        """Upgrade a vendored component to a new upstream tag with a three-way merge.

        Merges each file of `<component>/` (ours: old import + your local patches) from `from_tag`
        (base) to `to_tag` (theirs) of `<component>-upstream`. Writes conflict markers only where a
        local patch truly collides; lists the conflict files for you to resolve. Also checks the
        upstream dir out at the new tag. Then rebuild, test, and commit.
        """
        from anvil.runtime.executor import repo_path  # at call time, like lint_changed

        res = sandbox.execute(vendor_command(repo_path(run["repo"]), component, from_tag, to_tag),
                              timeout=1800)
        return parse(res.output or "", res.exit_code if res.exit_code is not None else 1)

    return vendor_reimport
