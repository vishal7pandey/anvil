"""Configuration for the Anvil knowledge base.

Everything is read from the environment so the same code runs on the laptop, in
CI, and inside the test fixtures. Nothing here holds a secret: tokens are read
from the environment by the caller and never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_DATABASE_URL = "postgresql+psycopg://anvil:anvil@127.0.0.1:5433/anvil"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:4000"


def kb_database_url() -> str:
    """ANVIL_KB_DATABASE_URL, else the run database (ANVIL_DATABASE_URL), else the local default.

    A `localhost` host is rewritten to 127.0.0.1 on Windows (ANVIL-101): the KB store never went through the
    API's normalisation, so a `.env` saying `localhost` made the snapshot import and every `kb_search`
    connection wait about 30 s on IPv6 first (a 6-minute import)."""
    from anvil.api.config import normalize_database_url

    return normalize_database_url(os.environ.get("ANVIL_KB_DATABASE_URL")
                                  or os.environ.get("ANVIL_DATABASE_URL") or DEFAULT_DATABASE_URL)

# Everything an eval case withholds (its upstream issues, merge requests and fix commits) must
# never enter the knowledge base: the eval would leak the answer it is supposed to withhold.
# The list is the union over all case files (cases/*.toml, ANVIL-38), not a constant here.
def _withheld() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    from anvil.cases.spec import cases_dir, load_cases, withheld_union

    cases = load_cases()
    if not cases:
        # Fail loudly: an empty leak guard would let withheld answers into the KB silently.
        raise RuntimeError(
            f"no eval cases found in {cases_dir()}; the KB leak guard would be empty. "
            "Set ANVIL_CASES_DIR to the repo's cases/ directory."
        )
    union = withheld_union(cases)
    return union.issues, union.mrs, union.commits


WITHHELD_ISSUE_IDS, WITHHELD_MR_IDS, WITHHELD_COMMIT_PREFIXES = _withheld()
# Old names, kept for importers.
TC1_EXCLUDED_ISSUE_IDS = WITHHELD_ISSUE_IDS
TC1_EXCLUDED_MR_IDS = WITHHELD_MR_IDS
TC1_EXCLUDED_COMMIT_PREFIXES = WITHHELD_COMMIT_PREFIXES


@dataclass(frozen=True)
class KBSettings:
    # The KB is one shared, read-only corpus; run state may live in a per-agent database
    # (start-api.ps1 -DatabaseUrl). ANVIL_KB_DATABASE_URL keeps the two apart (ANVIL-51).
    database_url: str = field(default_factory=lambda: kb_database_url())
    gateway_url: str = field(
        default_factory=lambda: os.environ.get("ANVIL_GATEWAY_URL", DEFAULT_GATEWAY_URL)
    )
    gateway_key: str = field(
        default_factory=lambda: os.environ.get(
            "LITELLM_MASTER_KEY", "sk-anvil-local"
        )
    )
    embed_model: str = field(
        default_factory=lambda: os.environ.get("ANVIL_EMBED_MODEL", "anvil-embed")
    )
    embed_dim: int = field(
        default_factory=lambda: int(os.environ.get("ANVIL_EMBED_DIM", "1536"))
    )
    embed_batch_size: int = field(
        default_factory=lambda: int(os.environ.get("ANVIL_EMBED_BATCH_SIZE", "64"))
    )
    chunk_chars: int = field(
        default_factory=lambda: int(os.environ.get("ANVIL_KB_CHUNK_CHARS", "2000"))
    )
    chunk_overlap: int = field(
        default_factory=lambda: int(os.environ.get("ANVIL_KB_CHUNK_OVERLAP", "200"))
    )
    gitlab_url: str = field(
        default_factory=lambda: os.environ.get(
            "RTEMS_GITLAB_URL", "https://gitlab.rtems.org"
        )
    )
    gitlab_project: str = field(
        default_factory=lambda: os.environ.get("RTEMS_GITLAB_PROJECT", "rtems/rtos/rtems")
    )
    # Projects crawled for issues + MRs (ANVIL-39). Comma-separated. Add a project only when every
    # eval case on it declares its withheld set: rtems/pkg/rtems-lwip MRs contain the TC2 upgrade.
    gitlab_projects: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            p.strip()
            for p in os.environ.get("KB_GITLAB_PROJECTS", "rtems/rtos/rtems").split(",")
            if p.strip()
        )
    )
    # Resume cursors of the GitLab crawl (newest updated_at per project and kind).
    crawl_state_path: str = field(
        default_factory=lambda: os.environ.get("KB_CRAWL_STATE", ".cache/kb-crawl-state.json")
    )
    gitlab_token: str | None = field(
        default_factory=lambda: os.environ.get("GITLAB_TOKEN") or None
    )
    excluded_issue_ids: tuple[str, ...] = TC1_EXCLUDED_ISSUE_IDS
    excluded_mr_ids: tuple[str, ...] = TC1_EXCLUDED_MR_IDS
    excluded_commit_prefixes: tuple[str, ...] = TC1_EXCLUDED_COMMIT_PREFIXES
