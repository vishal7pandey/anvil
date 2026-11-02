"""Live GitHub + SonarCloud smoke test, run by the owner: `uv run python scripts/smoke_github.py [repo]` from backend/.

Reads GITHUB_TOKEN from the repo .env. On the mirror (default `rtems`): creates branch
`anvil/smoke-<time>` with one file, opens a draft PR, reads checks / code scanning / Sonar, checks
that github_merge refuses (not green yet), then closes the PR and deletes the branch. Never merges.
git_push itself needs a live sandbox: it is exercised in the M1 walking skeleton.
"""

import base64
import sys
import time

from anvil.tools import github, sonar
from anvil.tools.errors import ToolError

repo = sys.argv[1] if len(sys.argv) > 1 else "rtems"
name, branch = github.full_name(repo), f"anvil/smoke-{int(time.time())}"
main_sha = github._gh("GET", f"/repos/{name}/git/ref/heads/main")["object"]["sha"]
github._gh("POST", f"/repos/{name}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": main_sha})
pr = None
try:
    content = base64.b64encode(b"anvil smoke test, safe to delete\n").decode()
    github._gh("PUT", f"/repos/{name}/contents/ANVIL_SMOKE.md", json={"message": "ANVIL-13: smoke", "content": content, "branch": branch})
    pr = github.github_open_pr(repo, branch, "ANVIL-13: smoke test (will be closed)", "Agent: devin\n\nSmoke test, closed automatically.", draft=True)
    print("open_pr ok:", pr["url"])
    print("checks ok:", github.github_checks(repo, pr["number"], wait_seconds=60)["state"])
    try:
        github.github_merge(repo, pr["number"])
        print("merge: UNEXPECTED, merged")
    except ToolError as e:
        print("merge refused ok:", e)
    # Before CodeQL / SonarCloud have analysed the PR these return 404 "not found": reported, not fatal.
    for label, call in [("code_scanning", lambda: f"{len(github.github_code_scanning_alerts(repo, pr['number']))} alerts"), ("sonar", lambda: sonar.sonar_issues(pr["number"])["quality_gate"])]:
        try:
            print(f"{label} ok:", call())
        except ToolError as e:
            print(f"{label} (no analysis yet?):", e)
finally:
    if pr:
        github._gh("PATCH", f"/repos/{name}/pulls/{pr['number']}", json={"state": "closed"})
    github._gh("DELETE", f"/repos/{name}/git/refs/heads/{branch}")
    print("cleanup ok:", branch, "deleted")
