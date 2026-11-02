"""Shared Atlassian Cloud HTTP client (Jira + Confluence). Host process only, never the sandbox (ADR-004)."""

from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ENV = Path(__file__).resolve().parents[3] / ".env"


class ToolError(RuntimeError):
    """An outward action failed; the message is safe to show the model (no secrets)."""


class AtlassianSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ENV, extra="ignore")
    atlassian_site: str = "https://vishal7pandey.atlassian.net"
    atlassian_email: str = ""
    atlassian_api_token: str = ""


def site() -> str:
    return AtlassianSettings().atlassian_site.rstrip("/")


def client() -> httpx.Client:
    s = AtlassianSettings()
    if not (s.atlassian_email and s.atlassian_api_token):
        raise ToolError("ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN are not set in the API process environment")
    return httpx.Client(
        base_url=s.atlassian_site.rstrip("/"),
        auth=(s.atlassian_email, s.atlassian_api_token),
        headers={"Accept": "application/json"},
        timeout=30,
    )


def request(method: str, path: str, **kwargs: Any) -> Any:
    """Call the Atlassian REST API; raise ToolError with the server's message on failure."""
    with client() as c:
        r = c.request(method, path, **kwargs)
    if r.is_error:
        try:
            body = r.json()
            detail = "; ".join(body.get("errorMessages") or []) or str(body.get("errors") or body.get("message") or body)
        except ValueError:
            detail = r.text[:500]
        raise ToolError(f"{method} {path} -> HTTP {r.status_code}: {detail}")
    return r.json() if r.content else None
