"""Bug-replay cases (ANVIL-38).

    uv run python -m anvil.cases list
    uv run python -m anvil.cases show rtems-5776
    uv run python -m anvil.cases withheld                 # union the KB guard uses
    uv run python -m anvil.cases build-base rtems-5776    # dry run; --push creates the branch
    uv run python -m anvil.cases ticket rtems-5776        # print; --create files it in Jira
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .spec import get_case, load_cases, withheld_union


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anvil.cases", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("withheld")
    for name in ("show", "build-base", "ticket"):
        p = sub.add_parser(name)
        p.add_argument("case_id")
        if name == "build-base":
            p.add_argument("--push", action="store_true", help="create the branch on the mirror (never moves one)")
        if name == "ticket":
            p.add_argument("--create", action="store_true", help="file it in Jira when the case has no key yet")
    args = parser.parse_args(argv)

    if args.command == "list":
        for case in load_cases():
            print(f"{case.id}\t{case.kind}\t{case.repo}\t{case.base_branch}\t{case.ticket_key or '-'}")
        return 0
    if args.command == "withheld":
        print(json.dumps(asdict(withheld_union(load_cases())), indent=2))
        return 0

    case = get_case(args.case_id)
    if args.command == "show":
        data = asdict(case)
        data["path"] = str(case.path)
        data["ticket_body"] = f"<{len(case.ticket_body)} chars>"
        print(json.dumps(data, indent=2, default=str))
        return 0
    if args.command == "build-base":
        from .base import build_base

        print(json.dumps(build_base(case, push=args.push), indent=2))
        return 0
    if args.command == "ticket":
        if not args.create:
            print(f"# {case.ticket_summary}\n\n{case.ticket_text}")
            return 0
        if case.ticket_key:
            print(json.dumps({"ok": False, "error": f"case already has ticket {case.ticket_key}"}))
            return 1
        from anvil.tools.jira import jira_create

        # Without the 'anvil' label: labelling is what starts a real run, and that stays a human act.
        labels = [label for label in case.ticket_labels if label != "anvil"]
        created = jira_create(case.ticket_project, "Bug", case.ticket_summary, case.ticket_text, labels)
        print(json.dumps({"ok": True, **created, "note": "add the key to the case file as ticket.key"}))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
