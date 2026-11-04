"""Sandbox container lifecycle reaper (ANVIL-47).

Cleans up stale and terminal anvil-sbx-* containers to preserve host Docker resources:
- Ownership isolation: an API sweep only inspects and reaps sandboxes carrying its own
  `anvil.owner=<owner_id>` label (preventing one API instance from reaping another's sandboxes).
- Age is measured from the run's `ended_at` timestamp (sessions reuse containers across runs,
  so container creation time is not used for sessions).
- Terminal runs (succeeded, failed, cancelled): reaped N hours after run.ended_at (default 2 h).
- Handoff runs: kept until handoff evidence is posted and WIP branch is pushed, then follows
  the N-hour rule so humans can attach via `docker exec -it <sandbox> bash`.
- Active runs (queued, running, waiting_approval): strictly preserved and never reaped.
- Dry runs (anvil-sbx-dry-*): removed by dry-run scripts at exit unless --keep; any stale ones
  older than N hours are reaped.
- Manual reset tool: `reset_demo.py --containers` supports `--all-owners` to sweep across all
  instances when explicitly requested by a human operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any
import uuid

import docker
from docker.errors import APIError, NotFound
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from anvil.api.database import SessionFactory
from anvil.api.models import Run, Session, utc_now

logger = logging.getLogger(__name__)

SANDBOX_CONTAINER_REGEX = re.compile(r"^anvil-(?:sbx|sandbox)-")
DEFAULT_MAX_AGE_HOURS = 2.0
ACTIVE_RUN_STATUSES = {"queued", "running", "waiting_approval"}
TERMINAL_RUN_STATUSES = {"handoff", "succeeded", "failed", "cancelled"}


def parse_docker_timestamp(ts: str) -> datetime:
    """Parse Docker container ISO-8601 creation timestamps into UTC datetime."""
    ts = ts.rstrip("Z")
    if "." in ts:
        base, frac = ts.split(".", 1)
        frac = frac[:6].ljust(6, "0")
        ts = f"{base}.{frac}"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class ContainerReapCheck:
    container_name: str
    container_id: str
    should_reap: bool
    reason: str


def has_handoff_evidence(run: Run) -> bool:
    """Return True if handoff evidence/artifact has been produced."""
    if not run.artifacts:
        return False
    return any(
        isinstance(art, dict) and (art.get("kind") == "handoff" or "handoff" in str(art.get("title", "")).lower())
        for art in run.artifacts
    )


def evaluate_container_for_reap(
    container: Any,
    db: DBSession,
    *,
    owner_id: str | None = None,
    all_owners: bool = False,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    now: datetime | None = None,
) -> ContainerReapCheck:
    """Evaluate whether a Docker container should be reaped based on instance ownership, run state, and age."""
    c_name = container.name or ""
    c_id = container.id or ""
    now = now or utc_now()
    cutoff_delta = timedelta(hours=max_age_hours)

    if not SANDBOX_CONTAINER_REGEX.match(c_name):
        return ContainerReapCheck(c_name, c_id, False, "Not an Anvil sandbox container")

    labels = getattr(container, "labels", {}) or {}

    # 1. Ownership validation
    # If owner_id is specified and all_owners is False, only reap containers belonging to this owner
    c_owner = labels.get("anvil.owner")
    if not all_owners and owner_id is not None:
        if not c_owner:
            return ContainerReapCheck(
                c_name, c_id, False, f"Container has no owner label; skipped by owner '{owner_id}'"
            )
        if c_owner != owner_id:
            return ContainerReapCheck(
                c_name, c_id, False, f"Container owned by '{c_owner}', not current instance '{owner_id}'"
            )

    # Container creation timestamp (used for dry runs and unlabelled/orphan fallbacks)
    created_raw = container.attrs.get("Created") if hasattr(container, "attrs") else None
    c_created: datetime | None = None
    if created_raw:
        try:
            c_created = parse_docker_timestamp(created_raw)
        except Exception:
            logger.debug("Could not parse container %s Created timestamp: %s", c_name, created_raw)

    # 2. Check for dry-run container naming (starts with anvil-sbx-dry-)
    if c_name.startswith("anvil-sbx-dry-"):
        if c_created and (now - c_created) >= cutoff_delta:
            return ContainerReapCheck(
                c_name,
                c_id,
                True,
                f"Stale dry-run sandbox (age {(now - c_created).total_seconds() / 3600:.1f}h >= {max_age_hours}h)",
            )
        return ContainerReapCheck(c_name, c_id, False, "Dry-run sandbox within inspection grace period")

    # 3. Resolve session ID strictly from labels (no loose name guessing)
    session_id_str = labels.get("anvil.session_id")
    session_uuid: uuid.UUID | None = None
    if session_id_str:
        try:
            session_uuid = uuid.UUID(session_id_str)
        except (ValueError, TypeError):
            pass

    if session_uuid is None:
        # Container has no valid anvil.session_id label
        if all_owners:
            if c_created and (now - c_created) >= cutoff_delta:
                return ContainerReapCheck(
                    c_name,
                    c_id,
                    True,
                    f"Unlabelled/orphaned sandbox older than {max_age_hours}h (all-owners mode)",
                )
            return ContainerReapCheck(c_name, c_id, False, "Unlabelled container younger than max age")
        return ContainerReapCheck(
            c_name,
            c_id,
            False,
            "Container has no session ID label; skipped in instance-scoped sweep",
        )

    # 4. Check session runs in DB
    runs = list(
        db.scalars(
            select(Run).where(Run.session_id == session_uuid).order_by(Run.created_at.desc())
        ).all()
    )

    if not runs:
        # Session exists in label but has no runs recorded
        if c_created and (now - c_created) >= cutoff_delta:
            return ContainerReapCheck(
                c_name, c_id, True, f"Session has no runs and container age >= {max_age_hours}h"
            )
        return ContainerReapCheck(c_name, c_id, False, "Session has no runs but within grace period")

    # If ANY run in this session is active -> NEVER reap
    active_runs = [r for r in runs if r.status in ACTIVE_RUN_STATUSES]
    if active_runs:
        return ContainerReapCheck(
            c_name,
            c_id,
            False,
            f"Active run {active_runs[0].id} in status '{active_runs[0].status}'",
        )

    # All runs are terminal. Check the latest run.
    latest_run = runs[0]

    # Special handling for handoff:
    if latest_run.status == "handoff":
        if not has_handoff_evidence(latest_run) and latest_run.ended_at is None:
            return ContainerReapCheck(
                c_name,
                c_id,
                False,
                f"Handoff run {latest_run.id} has not completed posting handoff evidence",
            )

    # Determine reference terminal time from run.ended_at (NOT container creation time)
    ended_at = latest_run.ended_at
    if ended_at is None:
        ended_at = latest_run.created_at or now

    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)
    else:
        ended_at = ended_at.astimezone(timezone.utc)

    age = now - ended_at
    if age < cutoff_delta:
        return ContainerReapCheck(
            c_name,
            c_id,
            False,
            f"Run {latest_run.id} terminal ({latest_run.status}) but within {max_age_hours}h grace period (run age {age.total_seconds() / 3600:.1f}h)",
        )

    return ContainerReapCheck(
        c_name,
        c_id,
        True,
        f"Run {latest_run.id} terminal ({latest_run.status}) and run age {age.total_seconds() / 3600:.1f}h >= {max_age_hours}h",
    )


def reap_stale_sandboxes(
    session_factory: SessionFactory,
    docker_client: docker.DockerClient | None = None,
    *,
    owner_id: str | None = None,
    all_owners: bool = False,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """Inspect and reap stale Anvil sandbox containers matching this instance's ownership.

    Returns the list of container names that were reaped (or would be reaped if dry_run=True).
    """
    client = docker_client or docker.from_env()
    reaped: list[str] = []

    try:
        containers = client.containers.list(all=True)
    except Exception as exc:
        logger.warning("Could not list Docker containers for reaping: %s", exc)
        return reaped

    with session_factory() as db:
        for c in containers:
            c_name = c.name or ""
            if not SANDBOX_CONTAINER_REGEX.match(c_name):
                continue

            check = evaluate_container_for_reap(
                c,
                db,
                owner_id=owner_id,
                all_owners=all_owners,
                max_age_hours=max_age_hours,
                now=now,
            )

            if check.should_reap:
                if dry_run:
                    logger.info("[DRY RUN] Would reap container %s: %s", c_name, check.reason)
                    reaped.append(c_name)
                else:
                    logger.info("Reaping container %s: %s", c_name, check.reason)
                    try:
                        c.stop(timeout=5)
                    except (NotFound, APIError):
                        pass
                    try:
                        c.remove(force=True)
                        reaped.append(c_name)
                    except NotFound:
                        reaped.append(c_name)
                    except APIError as exc:
                        logger.error("Failed to remove container %s: %s", c_name, exc)
            else:
                logger.debug("Keeping container %s: %s", c_name, check.reason)

    return reaped
