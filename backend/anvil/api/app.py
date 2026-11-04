import asyncio
import hmac
import json
import logging
import os
import re
from pathlib import Path
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Lock
from typing import Any
from uuid import UUID

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite as sqlite_dialect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from anvil.api.config import Settings, get_settings
from anvil.logs import set_log_context
from anvil.api.database import SessionFactory, create_session_factory, get_db
from anvil.api.executor import RunExecutor
from anvil.api.metrics import (
    SpendTotals,
    create_litellm_engine,
    load_litellm_spend_totals,
)
from anvil.api.runtime_factory import create_executor_from_environment
from anvil.api.models import Approval, Run, RunEvent, Session, User, utc_now
from anvil.api.schemas import (
    ApprovalDecision,
    ApprovalInboxResponse,
    ApprovalResponse,
    EventResponse,
    IntakeResponse,
    MetricsResponse,
    MessageCreate,
    RunResponse,
    SessionCreate,
    SessionResponse,
    normalize_repository,
)

logger = logging.getLogger(__name__)
# One lock per run serialises every write that allocates an event seq (executor events, status
# changes, cancel requests): two writers in the same process must not read the same last_seq.
_RUN_WRITE_LOCKS: dict[UUID, Lock] = {}


def _run_write_lock(run_id: UUID) -> Lock:
    # dict.setdefault is atomic under the GIL; defaultdict's __missing__ is not, and two threads
    # creating the first lock for a run at once would each get their own.
    return _RUN_WRITE_LOCKS.setdefault(run_id, Lock())
bearer_scheme = HTTPBearer(auto_error=False)
RUN_STATUSES = {
    "queued",
    "running",
    "waiting_approval",
    "handoff",
    "succeeded",
    "failed",
    "cancelled",
}
TERMINAL_RUN_STATUSES = {"handoff", "succeeded", "failed", "cancelled"}
POLL_INTERVAL_SECONDS = 60
CANCEL_TIMEOUT_SECONDS = 5
SPEND_LOG_GRACE_PERIOD = timedelta(minutes=5)

JiraCandidates = Callable[..., list[dict[str, Any]]]
JiraTransition = Callable[[str, str], Any]
SpendLogProvider = Callable[[list[UUID]], dict[str, SpendTotals]]


def _spend_totals_for_runs(
    request: Request,
    run_ids: list[UUID],
) -> dict[str, SpendTotals]:
    if not run_ids:
        return {}
    provider: SpendLogProvider | None = request.app.state.spend_log_provider
    if provider is not None:
        return {str(run_id): total for run_id, total in provider(run_ids).items()}

    engine = request.app.state.litellm_engine
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiteLLM spend log database is not configured",
        )
    try:
        return load_litellm_spend_totals(engine, run_ids)
    except SQLAlchemyError as exc:
        logger.exception("Unable to read LiteLLM spend logs")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiteLLM spend logs are unavailable",
        ) from exc


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run_metrics(
    db: DBSession,
    request: Request,
    runs: list[Run],
) -> dict[UUID, dict[str, Any]]:
    if not runs:
        return {}

    run_ids = [run.id for run in runs]
    events_by_run: dict[UUID, list[RunEvent]] = defaultdict(list)
    events = db.scalars(select(RunEvent).where(RunEvent.run_id.in_(run_ids)))
    for event in events:
        events_by_run[event.run_id].append(event)

    spend_totals = _spend_totals_for_runs(request, run_ids)
    missing_spend_logs = [
        run
        for run in runs
        if (run.input_tokens or run.output_tokens)
        and str(run.id) not in spend_totals
    ]
    now = utc_now()
    waiting_for_spend_logs: list[Run] = []
    for run in missing_spend_logs:
        usage_timestamps = [
            _as_utc(event.ts)
            for event in events_by_run[run.id]
            if event.type == "usage"
        ]
        last_usage_at = max(
            usage_timestamps,
            default=_as_utc(run.ended_at or run.started_at or run.created_at),
        )
        if now - last_usage_at <= SPEND_LOG_GRACE_PERIOD:
            waiting_for_spend_logs.append(run)

    if waiting_for_spend_logs:
        logger.error(
            "LiteLLM spend logs are missing for %d runs with token usage",
            len(waiting_for_spend_logs),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiteLLM spend logs are not yet available for runs with token usage",
            headers={"Retry-After": "1"},
        )

    missing_spend_run_ids = {run.id for run in missing_spend_logs}
    metrics: dict[UUID, dict[str, Any]] = {}
    for run in runs:
        run_events = events_by_run[run.id]
        spend_is_missing = run.id in missing_spend_run_ids
        wall_time_seconds = None
        if run.started_at is not None:
            ended_at = _as_utc(run.ended_at) if run.ended_at is not None else now
            wall_time_seconds = max(
                0.0,
                (ended_at - _as_utc(run.started_at)).total_seconds(),
            )
        human_messages = sum(
            event.type == "message"
            and (event.agent == "user" or event.data.get("role") == "user")
            for event in run_events
        )
        ci_rounds = sum(
            event.type == "tool.result"
            and event.data.get("name") == "github_checks"
            and "failure" in str(event.data.get("output", "")).casefold()
            for event in run_events
        )
        spend = spend_totals.get(str(run.id), SpendTotals(0, Decimal("0")))
        model_calls = None if spend_is_missing else spend.model_calls
        usd = None if spend_is_missing else float(spend.usd)
        metrics[run.id] = {
            "wall_time_seconds": wall_time_seconds,
            "model_calls": model_calls,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "usd": usd,
            "cost_usd": usd,
            "cost_status": "missing" if spend_is_missing else "available",
            "human_touches": run.human_touches + human_messages,
            "ci_rounds": ci_rounds,
            "outcome": run.status,
        }
    return metrics


def _seed_demo_user(engine: Any, settings: Settings) -> None:
    """Insert the demo user once at app start, or do nothing when it exists.

    Seeding here keeps the per-request dependency a pure read: two first requests on a clean
    database used to race db.get -> db.add and one got users_pkey (ANVIL-78).
    """
    insert = sqlite_dialect.insert if engine.dialect.name == "sqlite" else postgresql.insert
    stmt = (
        insert(User)
        .values(id=settings.demo_user_id, email=settings.demo_user_email)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def _authenticated_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    settings: Settings = request.app.state.settings
    expected_token = settings.demo_token.get_secret_value()
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo authentication is not configured",
        )
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials, expected_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    with request.app.state.session_factory() as db:
        user = db.get(User, settings.demo_user_id)
        if user is None:
            # The lifespan seeds it; unreachable unless the database was reset under a running app.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Demo user is not seeded",
            )
        return user


def _append_event(
    db: DBSession,
    run: Run,
    event_type: str,
    data: dict[str, Any],
    agent: str | None = None,
) -> RunEvent:
    # populate_existing: re-read the row even though `run` is already in this session's identity
    # map. Without it a request that loaded the run earlier (cancel_run) allocated seq from a stale
    # last_event_seq after another thread had committed events (ANVIL-36). Autoflush writes this
    # session's pending changes first, so they are not lost by the refresh.
    locked_run = db.scalar(
        select(Run)
        .where(Run.id == run.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_run is None:
        raise ValueError("Cannot append an event to a missing run")
    locked_run.last_event_seq += 1
    event = RunEvent(
        run_id=locked_run.id,
        seq=locked_run.last_event_seq,
        type=event_type,
        agent=agent,
        data=_strip_nul(data),
    )
    db.add(event)
    return event


def _strip_nul(value: Any) -> Any:
    """Postgres JSONB rejects U+0000. A grep over binary files put one in a tool result and the insert
    killed rehearsal eed58f6c (ANVIL-32), so drop it from every string before storing an event."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_strip_nul(v) for v in value]
    return value


def _load_run(db: DBSession, run_id: UUID, user_id: str) -> Run:
    run = db.scalar(
        select(Run)
        .join(Session, Run.session_id == Session.id)
        .where(Run.id == run_id, Session.user_id == user_id)
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


def _run_is_owned(session_factory: SessionFactory, run_id: UUID, user_id: str) -> bool:
    with session_factory() as db:
        return (
            db.scalar(
                select(Run.id)
                .join(Session, Run.session_id == Session.id)
                .where(Run.id == run_id, Session.user_id == user_id)
            )
            is not None
        )


def _run_payload(run: Run) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "id": run.id,
        "session_id": str(run.session_id),
        "trigger": run.trigger,
        "repo": run.repo,
        "status": run.status,
        "mode": run.mode,
        "network": run.network,
        "base_ref": run.base_ref,
        "branch": run.branch,
        "merge_policy": run.session.merge_policy,
        "jira_key": run.jira_key,
        "model_profile": run.session.model_profile,
        "last_event_seq": run.last_event_seq,
        "last_seq": run.last_event_seq,
        "base_sha": run.base_sha,
        "sandbox_image_digest": run.sandbox_image_digest,
        "model_aliases": run.model_aliases,
        "skill_versions": run.skill_versions,
        "prompt_hash": run.prompt_hash,
        "budgets": run.budgets,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "usd": float(run.usd),
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "human_touches": run.human_touches,
        "artifacts": run.artifacts,
        "run_record": run.run_record,
    }


def _persist_executor_event(
    session_factory: SessionFactory,
    run_id: UUID,
    payload: dict[str, Any],
) -> None:
    try:
        event_run_id = UUID(str(payload["run_id"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("Executor events must include the matching run_id") from exc
    sequence = payload.get("seq")
    event_type = payload.get("type")
    data = payload.get("data", {})
    agent = payload.get("agent")
    if event_run_id != run_id:
        raise ValueError("Executor event run_id does not match the active run")
    if type(sequence) is not int or sequence < 1:
        raise ValueError("Executor events must include a positive integer seq")
    if not isinstance(event_type, str) or not event_type or "data" not in payload:
        raise ValueError("Executor events must contain seq, type, and object data")
    if not isinstance(data, dict):
        raise ValueError("Executor events must contain seq, type, and object data")
    if agent is not None and not isinstance(agent, str):
        raise ValueError("Executor event agent must be a string or null")

    with _run_write_lock(run_id):
        with session_factory() as db:
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if run is None:
                raise ValueError("Executor event references a missing run")
            if run.status in TERMINAL_RUN_STATUSES:
                raise ValueError("Executor emitted an event after the run finished")
            if event_type == "run.status":
                new_status = data.get("status")
                if new_status not in RUN_STATUSES:
                    raise ValueError(f"Executor reported an unsupported run status: {new_status}")
                run.status = new_status
                if new_status == "running" and run.started_at is None:
                    run.started_at = utc_now()
                if new_status in TERMINAL_RUN_STATUSES:
                    run.ended_at = utc_now()
                if new_status == "failed":
                    run.error = "Run failed"
            elif event_type == "usage":
                input_tokens = data.get("input_tokens", 0)
                output_tokens = data.get("output_tokens", 0)
                usd = Decimal(str(data.get("usd", 0)))
                if (
                    type(input_tokens) is not int
                    or type(output_tokens) is not int
                    or input_tokens < 0
                    or output_tokens < 0
                    or not usd.is_finite()
                    or usd < 0
                ):
                    raise ValueError("Usage values must be non-negative")
                run.input_tokens += input_tokens
                run.output_tokens += output_tokens
                run.usd += usd
            elif event_type == "artifact":
                run.artifacts = [*run.artifacts, data]
            elif event_type == "run.record":
                string_fields = {
                    "base_sha",
                    "branch",
                    "sandbox_image_digest",
                    "prompt_hash",
                    "skills_hash",
                }
                object_fields = {"model_aliases", "skill_versions", "budgets"}
                for field in string_fields:
                    if field in data and data[field] is not None and not isinstance(data[field], str):
                        raise ValueError(f"Run record field {field} must be a string or null")
                for field in object_fields:
                    if field in data and not isinstance(data[field], dict):
                        raise ValueError(f"Run record field {field} must be an object")
                run.run_record = {**run.run_record, **data}
                for field in string_fields | object_fields:
                    if field in data and field != "skills_hash":
                        setattr(run, field, data[field])
                if "skills_hash" in data and "skill_versions" not in data:
                    run.skill_versions = {"hash": data["skills_hash"]}
            elif event_type == "approval.request":
                try:
                    approval_id = UUID(str(data["approval_id"]))
                except (KeyError, ValueError) as exc:
                    raise ValueError("Approval events must include a UUID approval_id") from exc
                action = data.get("action")
                args = data.get("args", {})
                reason = data.get("reason")
                if not isinstance(action, str) or not isinstance(args, dict):
                    raise ValueError("Approval events must include an action and object args")
                if reason is not None and not isinstance(reason, str):
                    raise ValueError("Approval event reason must be a string or null")
                approval = db.get(Approval, approval_id)
                if approval is None:
                    db.add(
                        Approval(
                            id=approval_id,
                            run_id=run_id,
                            action=action,
                            args=args,
                            reason=reason,
                        )
                    )
                elif approval.run_id != run_id:
                    raise ValueError("Approval event UUID is already in use by another run")
            _append_event(db, run, event_type, data, agent)
            db.commit()


def _mark_run_failed(session_factory: SessionFactory, run_id: UUID, error: str) -> None:
    with _run_write_lock(run_id):
        with session_factory() as db:
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            run.status = "failed"
            run.error = error
            run.ended_at = utc_now()
            _append_event(
                db,
                run,
                "run.status",
                {"status": "failed", "error": error},
                "api",
            )
            db.commit()


def _mark_run_started(session_factory: SessionFactory, run_id: UUID) -> None:
    with _run_write_lock(run_id):
        with session_factory() as db:
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            run.status = "running"
            if run.started_at is None:
                run.started_at = utc_now()
            _append_event(db, run, "run.status", {"status": "running"}, "api")
            db.commit()


def _mark_run_cancelled(session_factory: SessionFactory, run_id: UUID) -> None:
    with _run_write_lock(run_id):
        with session_factory() as db:
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            run.status = "cancelled"
            run.ended_at = utc_now()
            _append_event(db, run, "run.status", {"status": "cancelled"}, "api")
            db.commit()


def _mark_run_interrupted(session_factory: SessionFactory, run_id: UUID) -> None:
    with _run_write_lock(run_id):
        with session_factory() as db:
            run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return
            cancelled = run.cancel_requested_at is not None
            new_status = "cancelled" if cancelled else "failed"
            error = None if cancelled else "Interrupted by API shutdown"
            run.status = new_status
            run.error = error
            run.ended_at = utc_now()
            _append_event(
                db,
                run,
                "run.status",
                {"status": new_status, **({"error": error} if error else {})},
                "api",
            )
            db.commit()


async def _execute_run(app: FastAPI, run_id: UUID, message: str) -> None:
    current_task = asyncio.current_task()
    executor: RunExecutor = app.state.executor
    session_factory = app.state.session_factory
    try:
        await run_in_threadpool(_mark_run_started, session_factory, run_id)
        run_payload = await run_in_threadpool(_load_run_payload, session_factory, run_id)
        set_log_context(run_id=str(run_id))
        async for event in executor.start(run_payload, message):
            await run_in_threadpool(_persist_executor_event, session_factory, run_id, event)
            if event.get("type") == "run.status":
                logger.info("run %s status=%s", run_id, event.get("data", {}).get("status"))
            if (
                event.get("type") == "run.status"
                and event.get("data", {}).get("status") == "waiting_approval"
            ):
                break

        run_status = await run_in_threadpool(_read_run_status, session_factory, run_id)
        if run_status not in TERMINAL_RUN_STATUSES | {"waiting_approval"}:
            await run_in_threadpool(
                _mark_run_failed,
                session_factory,
                run_id,
                "RunExecutor ended without a terminal run.status event",
            )
    except asyncio.CancelledError:
        await asyncio.shield(
            run_in_threadpool(_mark_run_interrupted, session_factory, run_id)
        )
        raise
    except (ValueError, TypeError):
        logger.error("Executor returned an invalid event for run %s", run_id)
        await run_in_threadpool(
            _mark_run_failed,
            session_factory,
            run_id,
            "RunExecutor returned an invalid event",
        )
    except Exception as exc:
        logger.error(
            "RunExecutor failed for run %s (%s)",
            run_id,
            type(exc).__name__,
        )
        await run_in_threadpool(
            _mark_run_failed,
            session_factory,
            run_id,
            "RunExecutor failed; see API logs",
        )
    finally:
        if app.state.run_tasks.get(run_id) is current_task:
            app.state.run_tasks.pop(run_id, None)


async def _execute_resume(
    app: FastAPI,
    run_id: UUID,
    decisions: list[dict[str, Any]],
) -> None:
    current_task = asyncio.current_task()
    executor: RunExecutor = app.state.executor
    session_factory = app.state.session_factory
    try:
        run_payload = await run_in_threadpool(_load_run_payload, session_factory, run_id)
        async for event in executor.resume(run_payload, decisions):
            await run_in_threadpool(_persist_executor_event, session_factory, run_id, event)
            if (
                event.get("type") == "run.status"
                and event.get("data", {}).get("status") == "waiting_approval"
            ):
                break

        run_status = await run_in_threadpool(_read_run_status, session_factory, run_id)
        if run_status not in TERMINAL_RUN_STATUSES | {"waiting_approval"}:
            await run_in_threadpool(
                _mark_run_failed,
                session_factory,
                run_id,
                "RunExecutor resume ended without a terminal or approval status event",
            )
    except asyncio.CancelledError:
        await asyncio.shield(
            run_in_threadpool(_mark_run_interrupted, session_factory, run_id)
        )
        raise
    except (ValueError, TypeError):
        logger.error("Executor returned an invalid resume event for run %s", run_id)
        await run_in_threadpool(
            _mark_run_failed,
            session_factory,
            run_id,
            "RunExecutor returned an invalid resume event",
        )
    except Exception as exc:
        logger.error(
            "RunExecutor resume failed for run %s (%s)",
            run_id,
            type(exc).__name__,
        )
        await run_in_threadpool(
            _mark_run_failed,
            session_factory,
            run_id,
            "RunExecutor resume failed; see API logs",
        )
    finally:
        if app.state.run_tasks.get(run_id) is current_task:
            app.state.run_tasks.pop(run_id, None)


async def _resume_after_active_task(
    app: FastAPI,
    run_id: UUID,
    decisions: list[dict[str, Any]],
    previous_task: asyncio.Task[Any] | None,
) -> None:
    current_task = asyncio.current_task()
    try:
        if (
            previous_task is not None
            and previous_task is not current_task
            and not previous_task.done()
        ):
            await previous_task
        run_status = await run_in_threadpool(
            _read_run_status,
            app.state.session_factory,
            run_id,
        )
        if run_status not in TERMINAL_RUN_STATUSES:
            await _execute_resume(app, run_id, decisions)
    except asyncio.CancelledError:
        await asyncio.shield(
            run_in_threadpool(_mark_run_interrupted, app.state.session_factory, run_id)
        )
        raise
    finally:
        if app.state.run_tasks.get(run_id) is current_task:
            app.state.run_tasks.pop(run_id, None)


def _schedule_resume(
    app: FastAPI,
    run_id: UUID,
    decisions: list[dict[str, Any]],
) -> None:
    previous_task = app.state.run_tasks.get(run_id)
    task = asyncio.create_task(
        _resume_after_active_task(
            app,
            run_id,
            decisions,
            previous_task,
        ),
        name=f"anvil-run-resume-{run_id}",
    )
    app.state.run_tasks[run_id] = task





def _read_run_status(session_factory: SessionFactory, run_id: UUID) -> str | None:
    with session_factory() as db:
        run = db.get(Run, run_id)
        return run.status if run else None


def _schedule_run(app: FastAPI, run_id: UUID, message: str) -> None:
    existing = app.state.run_tasks.get(run_id)
    if existing is not None and not existing.done():
        return
    app.state.run_tasks[run_id] = asyncio.create_task(
        _execute_run(app, run_id, message),
        name=f"anvil-run-{run_id}",
    )


def _load_jira_helpers(app: FastAPI) -> tuple[JiraCandidates, JiraTransition]:
    candidates = app.state.jira_candidates
    transition = app.state.jira_transition
    if candidates is not None and transition is not None:
        return candidates, transition

    try:
        from anvil.tools.jira import jira_intake_candidates, jira_transition
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Jira intake tools are not available",
        ) from exc
    return jira_intake_candidates, jira_transition


def _jira_expected_errors() -> tuple[type[BaseException], ...]:
    try:
        from anvil.tools._atlassian import ToolError
    except ImportError:
        return (HTTPException, SQLAlchemyError, ValueError)
    return (HTTPException, SQLAlchemyError, ToolError, ValueError)


def _get_or_create_intake_run(
    session_factory: SessionFactory,
    issue: dict[str, Any],
    user_id: str,
    user_email: str,
    source: str = "jira",
) -> tuple[UUID, str, bool]:
    key = issue.get("key")
    summary = issue.get("summary")
    if not isinstance(key, str) or not isinstance(summary, str):
        raise ValueError("Jira intake candidate requires key and summary")
    description = issue.get("description") or ""
    if not isinstance(description, str):
        description = json.dumps(description, ensure_ascii=False)
    message = f"Work on Jira issue {key}: {summary}"
    if description:
        message = f"{message}\n\n{description}"
    code_base_repo, base_ref = _code_base(description)

    with session_factory() as db:
        user = db.get(User, user_id)
        if user is None:
            user = User(id=user_id, email=user_email)
            db.add(user)
            db.flush()
        # Reuse only a run that is still going (a poll racing its own To Do -> In Progress transition).
        # A finished run (failed, handoff, cancelled, succeeded) must not block a new one: a human
        # putting the ticket back to To Do with the `anvil` label is asking for a retry.
        existing = db.scalar(
            select(Run)
            .where(Run.trigger == f"{source}:{key}", Run.status.not_in(TERMINAL_RUN_STATUSES))
            .order_by(Run.created_at.desc())
        )
        if existing is not None:
            return existing.id, message, existing.status == "queued"

        repo_value = issue.get("repo") or code_base_repo or "vishal7pandey/rtems"
        session = Session(
            user_id=user_id,
            # normalize_repository validates both shapes since ANVIL-55 (paths must exist as git repos).
            repo=normalize_repository(repo_value),
            environment="local",
            mode=_jira_mode(issue),
            merge_policy="human",
            model_profile="anvil-main",
            title=f"{key}: {summary}",
        )
        db.add(session)
        db.flush()
        run = Run(
            session_id=session.id,
            trigger=f"{source}:{key}",
            repo=session.repo,
            status="queued",
            mode=session.mode,
            input_text=message,
            network="offline" if source == "local" else _jira_network(issue),
            base_ref=base_ref,
            jira_key=key,
        )
        db.add(run)
        db.flush()
        _append_event(
            db,
            run,
            "message",
            {"role": "user", "text": message},
            "user",
        )
        db.commit()
        return run.id, message, True


# "Code base: branch `tc1/5776-base` of github.com/vishal7pandey/rtems." anywhere in the ticket
# (backticks and the "of github.com/..." part optional). Answer-leak control: a TC ticket's base
# branch is what keeps the withheld fix out of the sandbox, so never guess it.
# "of" accepts github.com/<owner>/<repo> or an absolute local path (ANVIL-56 offline path); a path
# with spaces is not supported, say so in the ticket docs.
_CODE_BASE = re.compile(
    r"code base:\s*(?:branch\s+)?`?(?P<ref>[A-Za-z0-9._/-]+?)`?"
    r"(?:\s+of\s+(?:(?:https?://)?github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"|(?P<path>(?:[A-Za-z]:[\\/]|/)\S+)))?"
    r"(?=[\s.,;)]|$)",
    re.IGNORECASE,
)

_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/)")


def _is_local_path(value: str | None) -> bool:
    return bool(value) and bool(_LOCAL_PATH.match(value))


def _code_base(description: str) -> tuple[str | None, str]:
    """(repo or None, base ref) from the ticket's "Code base:" line; ValueError if there is none."""
    match = _CODE_BASE.search(description)
    if match is None:
        raise ValueError(
            "Jira intake candidate has no 'Code base: branch <ref> of github.com/<owner>/<repo>' line"
        )
    return (match.group("repo") or (match.group("path") or "").rstrip(".,;")) or None, match.group("ref")


def _jira_labels(issue: dict[str, Any]) -> list[str]:
    labels = issue.get("labels", [])
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ValueError("Jira intake candidate labels must be a list of strings")
    return labels


def _jira_network(issue: dict[str, Any]) -> str:
    return "online" if "anvil-online" in _jira_labels(issue) else "offline"


def _jira_mode(issue: dict[str, Any]) -> str:
    """Run mode from the ticket: `anvil-interactive` makes every outward tool wait for a human, which
    is how a review's per-finding accept/reject gate works (TC3, ANVIL-50). Default: autonomous."""
    return "interactive" if "anvil-interactive" in _jira_labels(issue) else "autonomous"


def _atlassian_configured() -> bool:
    import os

    return all(os.environ.get(v) for v in ("ATLASSIAN_SITE", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"))


async def poll_jira_intake(app: FastAPI) -> list[UUID]:
    if app.state.executor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RunExecutor is not configured",
        )
    # Sources: the local ticket store whenever a tickets dir is set, and Jira only when credentials
    # exist (an offline laptop must never reach Atlassian, ANVIL-56).
    sources: list[tuple[JiraCandidates, JiraTransition, str]] = []
    tickets_dir = getattr(app.state, "tickets_dir", None)
    if tickets_dir:
        from anvil.tickets import LocalTracker

        tracker = LocalTracker(tickets_dir)
        sources.append((tracker.candidates, tracker.transition, "local"))
    if app.state.jira_candidates is not None or _atlassian_configured():
        candidates_fn, transition_fn = _load_jira_helpers(app)
        sources.append((candidates_fn, transition_fn, "jira"))

    started: list[UUID] = []
    user_id = app.state.settings.demo_user_id
    first_exc: Exception | None = None
    for candidates_fn, transition_fn, source in sources:
        try:
            candidates = await run_in_threadpool(candidates_fn, max_results=20)
        except _jira_expected_errors() as exc:
            logger.error("%s intake poll failed (%s)", source, type(exc).__name__)
            first_exc = first_exc or exc
            continue
        for issue in candidates:
            try:
                run_id, message, should_start = await run_in_threadpool(
                    _get_or_create_intake_run,
                    app.state.session_factory,
                    issue,
                    user_id,
                    app.state.settings.demo_user_email,
                    source,
                )
            except ValueError as exc:
                # One malformed ticket must not block the others, and must not start on a guessed base.
                logger.warning("Skipping %s intake candidate %s: %s", source, issue.get("key"), exc)
                continue
            try:
                await run_in_threadpool(transition_fn, issue["key"], "In Progress")
            except _jira_expected_errors() as exc:
                logger.warning("%s transition for %s failed: %s", source, issue.get("key"), exc)
            if should_start:
                _schedule_run(app, run_id, message)
                started.append(run_id)
    # A failed source still lets the others run, but the loop must see the failure so its backoff
    # fires (ANVIL-80) — swallowing it here meant the backoff never engaged.
    if first_exc is not None:
        raise first_exc
    return started


async def _jira_intake_loop(app: FastAPI) -> None:
    """Poll forever; back off exponentially (to 15 min) while failing, log only state changes
    (ANVIL-80: a misconfigured Jira must not fill the log with 60 s retries forever)."""
    delay = POLL_INTERVAL_SECONDS
    failures = 0
    while True:
        try:
            await poll_jira_intake(app)
        except _jira_expected_errors() as exc:
            failures += 1
            if failures == 1:
                logger.error("Intake poll failing (%s); backing off up to 15 min", type(exc).__name__)
            delay = min(POLL_INTERVAL_SECONDS * (2 ** failures), 900)
        else:
            if failures:
                logger.info("Intake poll recovered after %s consecutive failures", failures)
            failures = 0
            delay = POLL_INTERVAL_SECONDS
        await asyncio.sleep(delay)


def _events_after(
    session_factory: SessionFactory,
    run_id: UUID,
    sequence: int,
) -> tuple[list[RunEvent], str | None]:
    with session_factory() as db:
        events = list(
            db.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.seq > sequence)
                .order_by(RunEvent.seq)
            )
        )
        status_value = db.scalar(select(Run.status).where(Run.id == run_id))
        return events, status_value


def create_app(
    *,
    settings: Settings | None = None,
    database_url: str | None = None,
    executor: RunExecutor | None = None,
    jira_candidates: JiraCandidates | None = None,
    jira_transition: JiraTransition | None = None,
    tickets_dir: str | None = None,
    spend_log_provider: SpendLogProvider | None = None,
    start_intake_loop: bool = True,
) -> FastAPI:
    configured_settings = settings or get_settings()
    from anvil.logs import configure_logging

    configure_logging(Path(os.environ.get("ANVIL_LOGS_DIR") or Path(__file__).resolve().parents[3] / "logs"))
    configured_database_url = database_url or configured_settings.database_dsn
    engine, session_factory = create_session_factory(
        configured_database_url
    )
    litellm_engine = create_litellm_engine(configured_database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _seed_demo_user(app.state.engine, app.state.settings)
        if app.state.executor is not None and app.state.start_intake_loop:
            # No ticket source configured -> no loop at all, and say so once (ANVIL-80).
            if app.state.jira_candidates or _atlassian_configured() or getattr(app.state, "tickets_dir", None):
                app.state.intake_task = asyncio.create_task(
                    _jira_intake_loop(app),
                    name="anvil-jira-intake",
                )
            else:
                logger.info("intake disabled: no tickets dir and no Jira credentials")
        try:
            yield
        finally:
            tasks = list(app.state.run_tasks.values())
            if app.state.intake_task is not None:
                app.state.intake_task.cancel()
                tasks.append(app.state.intake_task)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            app.state.engine.dispose()
            if app.state.litellm_engine is not None:
                app.state.litellm_engine.dispose()

    app = FastAPI(
        title="Anvil Control API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        allow_credentials=False,
    )
    app.state.settings = configured_settings
    app.state.engine = engine
    app.state.litellm_engine = litellm_engine
    app.state.session_factory = session_factory
    app.state.executor = executor
    app.state.spend_log_provider = spend_log_provider
    app.state.jira_candidates = jira_candidates
    app.state.jira_transition = jira_transition
    import os as _os

    app.state.tickets_dir = tickets_dir or _os.environ.get("ANVIL_TICKETS_DIR")
    app.state.start_intake_loop = start_intake_loop
    app.state.run_tasks = {}
    app.state.intake_task = None

    @app.post(
        "/api/sessions",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_session(
        payload: SessionCreate,
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> Session:
        session = Session(user_id=user.id, **payload.model_dump())
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @app.get("/api/sessions", response_model=list[SessionResponse])
    def list_sessions(
        limit: int = Query(default=50, ge=1, le=100),
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> list[Session]:
        return list(
            db.scalars(
                select(Session)
                .where(Session.user_id == user.id)
                .order_by(Session.updated_at.desc())
                .limit(limit)
            )
        )

    @app.post(
        "/api/sessions/{session_id}/messages",
        response_model=RunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_run(
        session_id: UUID,
        payload: MessageCreate,
        request: Request,
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> dict[str, Any]:
        if request.app.state.executor is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="RunExecutor is not configured",
            )
        # The session row is locked while we check for an active run, so two messages sent at once
        # cannot both start one (ANVIL-71: runs in a session share its sandbox and checkout).
        session = db.scalar(
            select(Session)
            .where(Session.id == session_id, Session.user_id == user.id)
            .with_for_update()
        )
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        active = db.scalar(
            select(Run)
            .where(Run.session_id == session.id, Run.status.not_in(TERMINAL_RUN_STATUSES))
            .order_by(Run.created_at.desc())
        )
        if active is not None:
            # A run waiting for approval is answered in the Approvals panel, not with a new message.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run {active.id} is {active.status} in this session: wait for it to finish, "
                       f"decide its approval, or cancel it before sending another message.",
            )

        run = Run(
            session_id=session.id,
            trigger="ui",
            repo=session.repo,
            status="queued",
            mode=session.mode,
            input_text=payload.text,
            network="offline",
            base_ref="main",
            run_record={"scenario": payload.scenario} if payload.scenario else {},
        )
        db.add(run)
        db.flush()
        _append_event(db, run, "message", {"role": "user", "text": payload.text}, "user")
        session.updated_at = utc_now()
        db.commit()
        db.refresh(run)
        _schedule_run(request.app, run.id, payload.text)
        response = RunResponse.model_validate(run).model_dump()
        response.update(
            {
                "human_touches": 1,
                "outcome": run.status,
            }
        )
        return response

    @app.get("/api/metrics", response_model=MetricsResponse)
    def get_metrics(
        request: Request,
        from_: datetime | None = Query(default=None, alias="from"),
        to: datetime | None = None,
        scenario: str | None = Query(default=None, min_length=1, max_length=100),
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> dict[str, Any]:
        for bound_name, bound in (("from", from_), ("to", to)):
            if bound is not None and (bound.tzinfo is None or bound.utcoffset() is None):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{bound_name} must include a timezone",
                )
        if from_ is not None and to is not None and from_ > to:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="'from' must be before or equal to 'to'",
            )
        if scenario is not None:
            scenario = scenario.strip()
            if not scenario:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="scenario must not be blank",
                )

        statement = (
            select(Run)
            .join(Session, Run.session_id == Session.id)
            .where(Session.user_id == user.id)
        )
        if from_ is not None:
            statement = statement.where(Run.created_at >= from_)
        if to is not None:
            statement = statement.where(Run.created_at <= to)
        if scenario is not None:
            statement = statement.where(Run.run_record["scenario"].as_string() == scenario)
        runs = list(db.scalars(statement.order_by(Run.created_at)).all())
        metrics_by_run = _run_metrics(db, request, runs)
        run_metrics = [metrics_by_run[run.id] for run in runs]
        wall_times = [
            item["wall_time_seconds"]
            for item in run_metrics
            if item["wall_time_seconds"] is not None
        ]
        # Sum what is known and say how much is not: one run without spend rows (e.g. from before
        # spend-log tagging) must not turn every total into "n/a".
        priced = [item for item in run_metrics if item["cost_status"] != "missing"]
        runs_missing_cost = len(run_metrics) - len(priced)
        outcomes = Counter(run.status for run in runs)
        total_wall_time = sum(wall_times)
        if run_metrics and not priced:
            cost_status, model_calls, usd = "missing", None, None
        else:
            cost_status = "partial" if runs_missing_cost else "available"
            model_calls = sum(item["model_calls"] for item in priced)
            usd = sum(item["usd"] for item in priced)
        return {
            "from": from_,
            "to": to,
            "scenario": scenario,
            "run_count": len(runs),
            "total_wall_time_seconds": total_wall_time,
            "average_wall_time_seconds": (
                total_wall_time / len(wall_times) if wall_times else None
            ),
            "model_calls": model_calls,
            "input_tokens": sum(item["input_tokens"] for item in run_metrics),
            "output_tokens": sum(item["output_tokens"] for item in run_metrics),
            "usd": usd,
            "cost_usd": usd,
            "cost_status": cost_status,
            "runs_missing_cost": runs_missing_cost,
            "human_touches": sum(item["human_touches"] for item in run_metrics),
            "ci_rounds": sum(item["ci_rounds"] for item in run_metrics),
            "outcomes": dict(outcomes),
        }

    @app.get("/api/runs", response_model=list[RunResponse])
    def list_runs(
        limit: int = Query(default=50, ge=1, le=100),
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> list[Run]:
        return list(
            db.scalars(
                select(Run)
                .join(Session, Run.session_id == Session.id)
                .where(Session.user_id == user.id)
                .order_by(Run.created_at.desc())
                .limit(limit)
            )
        )

    @app.get("/api/approvals", response_model=list[ApprovalInboxResponse])
    def list_pending_approvals(
        limit: int = Query(default=50, ge=1, le=100),
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> list[dict[str, Any]]:
        rows = db.execute(
            select(Approval, Run, Session)
            .join(Run, Approval.run_id == Run.id)
            .join(Session, Run.session_id == Session.id)
            .where(
                Session.user_id == user.id,
                Approval.status == "pending",
                Run.status.not_in(TERMINAL_RUN_STATUSES),
            )
            .order_by(Approval.requested_at.desc(), Approval.id)
            .limit(limit)
        ).all()
        if not rows:
            return []

        run_ids = {run.id for _, run, _ in rows}
        approval_agents: dict[str, str | None] = {}
        for event in db.scalars(
            select(RunEvent).where(
                RunEvent.run_id.in_(run_ids),
                RunEvent.type == "approval.request",
            )
        ):
            approval_id = event.data.get("approval_id")
            if approval_id is not None:
                approval_agents[str(approval_id)] = event.agent

        return [
            {
                "approval_id": approval.id,
                "run_id": run.id,
                "session_id": session.id,
                "session_title": session.title,
                "repo": run.repo,
                "run_status": run.status,
                "action": approval.action,
                "args": approval.args,
                "reason": approval.reason,
                "agent": approval_agents.get(str(approval.id)),
                "requested_at": approval.requested_at,
            }
            for approval, run, session in rows
        ]

    @app.get("/api/runs/{run_id}/timeline", response_model=list[EventResponse])
    def get_run_timeline(
        run_id: UUID,
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> list[RunEvent]:
        _load_run(db, run_id, user.id)
        return list(
            db.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.seq)
            )
        )

    @app.get("/api/runs/{run_id}", response_model=RunResponse)
    def get_run(
        run_id: UUID,
        request: Request,
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> dict[str, Any]:
        run = _load_run(db, run_id, user.id)
        response = RunResponse.model_validate(run).model_dump()
        response.update(_run_metrics(db, request, [run])[run.id])
        return response

    @app.get("/api/runs/{run_id}/events")
    async def stream_run_events(
        run_id: UUID,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        user: User = Depends(_authenticated_user),
    ) -> EventSourceResponse:
        owned = await run_in_threadpool(
            _run_is_owned,
            request.app.state.session_factory,
            run_id,
            user.id,
        )
        if not owned:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        try:
            sequence = int(last_event_id) if last_event_id else 0
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Last-Event-ID must be an integer sequence",
            ) from exc
        if sequence < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Last-Event-ID must be non-negative",
            )

        async def event_stream() -> AsyncIterator[ServerSentEvent]:
            cursor = sequence
            while True:
                if await request.is_disconnected():
                    return
                events, run_status = await run_in_threadpool(
                    _events_after,
                    request.app.state.session_factory,
                    run_id,
                    cursor,
                )
                for event in events:
                    yield ServerSentEvent(
                        id=str(event.seq),
                        event=event.type,
                        data=json.dumps(
                            {
                                "run_id": str(event.run_id),
                                "seq": event.seq,
                                "ts": event.ts.isoformat(),
                                "type": event.type,
                                "agent": event.agent,
                                "data": event.data,
                            },
                            separators=(",", ":"),
                        ),
                    )
                    cursor = event.seq
                if run_status in TERMINAL_RUN_STATUSES and not events:
                    return
                await asyncio.sleep(0.2)

        return EventSourceResponse(
            event_stream(),
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/approvals/{approval_id}",
        response_model=ApprovalResponse,
    )
    async def decide_approval(
        approval_id: UUID,
        payload: ApprovalDecision,
        request: Request,
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> Approval:
        approval = db.scalar(
            select(Approval)
            .join(Run, Approval.run_id == Run.id)
            .join(Session, Run.session_id == Session.id)
            .where(Approval.id == approval_id, Session.user_id == user.id)
            .with_for_update()
        )
        if approval is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
        if approval.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approval has already been decided",
            )
        run = approval.run
        if run.status in TERMINAL_RUN_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Run has already finished",
            )
        approval.status = "approved" if payload.decision == "approve" else "rejected"
        approval.comment = payload.comment
        decision_time = utc_now()
        approval.decided_at = decision_time
        run.human_touches += 1
        _append_event(
            db,
            run,
            "approval.response",
            {
                "approval_id": str(approval.id),
                "decision": payload.decision,
                "comment": payload.comment,
            },
            "user",
        )

        pending_group = list(
            db.scalars(
                select(Approval)
                .where(Approval.run_id == run.id, Approval.resumed_at.is_(None))
                .order_by(Approval.requested_at, Approval.id)
                .with_for_update()
            )
        )
        resume_group = bool(pending_group) and all(
            item.status != "pending" for item in pending_group
        )
        decisions: list[dict[str, Any]] = []
        if resume_group:
            if request.app.state.executor is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="RunExecutor is not configured",
                )
            for item in pending_group:
                item.resumed_at = decision_time
                if item.status == "approved":
                    decisions.append({"type": "approve"})
                else:
                    decisions.append(
                        {
                            "type": "reject",
                            "message": item.comment or "Rejected by user",
                        }
                    )
            _append_event(
                db,
                run,
                "approval.resume_requested",
                {"approval_ids": [str(item.id) for item in pending_group]},
                "api",
            )
        db.commit()
        db.refresh(approval)
        if resume_group:
            _schedule_resume(request.app, run.id, decisions)
        return approval

    @app.post(
        "/api/runs/{run_id}/cancel",
        response_model=RunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def cancel_run(
        run_id: UUID,
        request: Request,
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> Run:
        run = db.scalar(
            select(Run)
            .join(Session, Run.session_id == Session.id)
            .where(Run.id == run_id, Session.user_id == user.id)
            .with_for_update()
        )
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        if run.status in TERMINAL_RUN_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Run has already finished",
            )
        executor: RunExecutor | None = request.app.state.executor
        if executor is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="RunExecutor is not configured",
            )
        task = request.app.state.run_tasks.get(run.id)
        # The executor thread may be appending this run's events right now: same lock, or two
        # writers can allocate the same seq (ANVIL-36).
        with _run_write_lock(run.id):
            run.cancel_requested_at = utc_now()
            _append_event(
                db,
                run,
                "run.cancel_requested",
                {"run_id": str(run.id)},
                "user",
            )
            db.commit()
        db.refresh(run)
        run_payload = await run_in_threadpool(
            _load_run_payload,
            request.app.state.session_factory,
            run.id,
        )
        try:
            await asyncio.wait_for(
                executor.cancel(run_payload),
                timeout=CANCEL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("RunExecutor cancellation timed out for run %s", run.id)
            if task is not None and not task.done():
                task.cancel()
        except Exception as exc:
            logger.error(
                "RunExecutor cancellation failed for run %s (%s)",
                run.id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="RunExecutor cancellation failed",
            ) from exc
        if task is None or task.done():
            await run_in_threadpool(
                _mark_run_cancelled,
                request.app.state.session_factory,
                run.id,
            )
            db.refresh(run)
        return run

    @app.post("/api/intake/jira/poll", response_model=IntakeResponse)
    async def manual_jira_poll(
        request: Request,
        user: User = Depends(_authenticated_user),
    ) -> IntakeResponse:
        try:
            run_ids = await poll_jira_intake(request.app)
        except HTTPException:
            raise
        except _jira_expected_errors() as exc:
            logger.warning("Manual Jira intake poll failed (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Jira intake poll failed",
            ) from exc
        return IntakeResponse(accepted=len(run_ids), run_ids=run_ids)

    return app


def _load_run_payload(session_factory: SessionFactory, run_id: UUID) -> dict[str, Any]:
    with session_factory() as db:
        run = db.get(Run, run_id)
        if run is None:
            raise ValueError("Run does not exist")
        payload = _run_payload(run)
        payload["input_text"] = run.input_text
        return payload


app = create_app(executor=create_executor_from_environment())
