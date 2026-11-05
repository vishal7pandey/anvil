"""Shared Atlassian Cloud HTTP client (Jira + Confluence). Host process only, never the sandbox (ADR-004)."""

from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from anvil.tools.errors import ToolError

REPO_ENV = Path(__file__).resolve().parents[3] / ".env"


class AtlassianSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ENV, extra="ignore")
    atlassian_site: str = "https://vishal7pandey.atlassian.net"
    atlassian_email: str = ""
    atlassian_api_token: str = ""
    anvil_confluence_write_spaces: str = "RTKB"


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
        try:
            r = c.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            # ANVIL-100 #38: a network/DNS/timeout failure is a tool error, not a traceback.
            raise ToolError(f"{method} {path} failed to reach Atlassian: {exc}") from exc
    if r.is_error:
        try:
            body = r.json()
            detail = "; ".join(body.get("errorMessages") or []) or str(body.get("errors") or body.get("message") or body)
        except ValueError:
            detail = r.text[:500]
        raise ToolError(f"{method} {path} -> HTTP {r.status_code}: {detail}")
    return r.json() if r.content else None
