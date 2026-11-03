"""Configuration for the Anvil knowledge base.

Everything is read from the environment so the same code runs on the laptop, in
CI, and inside the test fixtures. Nothing here holds a secret: tokens are read
from the environment by the caller and never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_DATABASE_URL = "postgresql+psycopg://anvil:anvil@localhost:5433/anvil"
DEFAULT_GATEWAY_URL = "http://localhost:4000"

# Upstream RTEMS issues used by the TC1 demo. Their records (and the fix
# discussion / merge requests / fix commit prefixes) must never enter the
# knowledge base: the eval would leak the answer it is supposed to withhold.
# Keep this list in sync with docs/03-demo-scenarios.md and the ANVIL-14 ticket.
TC1_EXCLUDED_ISSUE_IDS: tuple[str, ...] = (
    "5776",
    "5806",
    "5723",
    "5750",
    "5749",
    "5760",
)

TC1_EXCLUDED_MR_IDS: tuple[str, ...] = (
    "1493",
    "1526",
    "1448",
    "1468",
    "1484",
    "1479",
)

TC1_EXCLUDED_COMMIT_PREFIXES: tuple[str, ...] = (
    "a62e60b5",
    "97cf8529",
    "f2ad71ee",
    "af256031",
    "338bab40",
    "80b1ba90",
)


@dataclass(frozen=True)
class KBSettings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "ANVIL_DATABASE_URL", DEFAULT_DATABASE_URL
        )
    )
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
        default_factory=lambda: os.environ.get("RTEMS_GITLAB_PROJECT", "rtems/rtems")
    )
    gitlab_token: str | None = field(
        default_factory=lambda: os.environ.get("GITLAB_TOKEN") or None
    )
    excluded_issue_ids: tuple[str, ...] = TC1_EXCLUDED_ISSUE_IDS
    excluded_mr_ids: tuple[str, ...] = TC1_EXCLUDED_MR_IDS
    excluded_commit_prefixes: tuple[str, ...] = TC1_EXCLUDED_COMMIT_PREFIXES
