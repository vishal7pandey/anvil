"""SonarCloud host tool. The rtems mirror is public, so reads are anonymous unless SONAR_TOKEN is set."""

from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from anvil.tools._atlassian import REPO_ENV
from anvil.tools.errors import ToolError


class SonarSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ENV, extra="ignore")
    sonar_host_url: str = "https://sonarcloud.io"
    sonar_token: str = ""
    sonar_project: str = "vishal7pandey_rtems"


def _get(path: str, params: dict) -> dict:
    s = SonarSettings()
    headers = {"Authorization": f"Bearer {s.sonar_token}"} if s.sonar_token else {}
    with httpx.Client(base_url=s.sonar_host_url or "https://sonarcloud.io", headers=headers, timeout=30) as c:
        r = c.get(path, params=params)
    if r.is_error:
        raise ToolError(f"GET {path} -> HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def sonar_issues(pr: int, project: str = "") -> dict[str, Any]:
    """SonarCloud quality gate and open issues for a PR: rule, severity, type, message, file and line."""
    project = project or SonarSettings().sonar_project
    gate = _get("/api/qualitygates/project_status", {"projectKey": project, "pullRequest": pr})["projectStatus"]
    data = _get("/api/issues/search", {"componentKeys": project, "pullRequest": pr, "resolved": "false", "ps": 100})
    issues = [
        {
            "key": i["key"],
            "rule": i["rule"],
            "severity": i.get("severity"),
            "type": i.get("type"),
            "message": i.get("message"),
            "path": i.get("component", "").split(":", 1)[-1],
            "line": i.get("line"),
        }
        for i in data.get("issues", [])
    ]
    failed = [c["metricKey"] for c in gate.get("conditions", []) if c.get("status") == "ERROR"]
    return {"pr": pr, "quality_gate": gate.get("status"), "failed_conditions": failed, "total": data.get("total", len(issues)), "issues": issues}
