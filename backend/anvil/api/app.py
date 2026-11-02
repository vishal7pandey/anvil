import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from anvil.api.config import Settings, get_settings
from anvil.api.database import SessionFactory, create_session_factory, get_db
from anvil.api.executor import RunExecutor
from anvil.api.models import Approval, Run, RunEvent, Session, User, utc_now
from anvil.api.schemas import (
    ApprovalDecision,
    ApprovalResponse,
    IntakeResponse,
    MessageCreate,
    RunResponse,
    SessionCreate,
    SessionResponse,
)

logger = logging.getLogger(__name__)
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

JiraCandidates = Callable[..., list[dict[str, Any]]]
JiraTransition = Callable[[str, str], Any]


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
            user = User(id=settings.demo_user_id, email=settings.demo_user_email)
            db.add(user)
            db.commit()
            db.refresh(user)
        return user


def _append_event(
    db: DBSession,
    run: Run,
    event_type: str,
    data: dict[str, Any],
    agent: str | None = None,
) -> RunEvent:
    locked_run = db.scalar(select(Run).where(Run.id == run.id).with_for_update())
    if locked_run is None:
        raise ValueError("Cannot append an event to a missing run")
    locked_run.last_event_seq += 1
    event = RunEvent(
        run_id=locked_run.id,
        seq=locked_run.last_event_seq,
        type=event_type,
        agent=agent,
        data=data,
    )
    db.add(event)
    return event


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

    with session_factory() as db:
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None:
            raise ValueError("Executor event references a missing run")
        if run.status in TERMINAL_RUN_STATUSES:
            raise ValueError("Executor emitted an event after the run finished")
        if sequence != run.last_event_seq + 1:
            raise ValueError(
                f"Executor event seq {sequence} is not the next sequence "
                f"after {run.last_event_seq}"
            )

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
                if field in data:
                    setattr(run, field, data[field])
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
        run.last_event_seq = sequence
        db.add(
            RunEvent(
                run_id=run.id,
                seq=sequence,
                type=event_type,
                agent=agent,
                data=data,
            )
        )
        db.commit()


def _mark_run_failed(session_factory: SessionFactory, run_id: UUID, error: str) -> None:
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
    with session_factory() as db:
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        run.status = "cancelled"
        run.ended_at = utc_now()
        _append_event(db, run, "run.status", {"status": "cancelled"}, "api")
        db.commit()


def _mark_run_interrupted(session_factory: SessionFactory, run_id: UUID) -> None:
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
        async for event in executor.start(run_payload, message):
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
    approval_responses: list[dict[str, Any]],
    previous_task: asyncio.Task[Any] | None,
) -> None:
    current_task = asyncio.current_task()
    if previous_task is not None and previous_task is not current_task and not previous_task.done():
        await previous_task
    run_status = await run_in_threadpool(
        _read_run_status,
        app.state.session_factory,
        run_id,
    )
    if run_status not in TERMINAL_RUN_STATUSES:
        await run_in_threadpool(
            _record_approval_responses,
            app.state.session_factory,
            run_id,
            approval_responses,
        )
        await _execute_resume(app, run_id, decisions)
    elif app.state.run_tasks.get(run_id) is current_task:
        app.state.run_tasks.pop(run_id, None)


def _record_approval_responses(
    session_factory: SessionFactory,
    run_id: UUID,
    responses: list[dict[str, Any]],
) -> None:
    with session_factory() as db:
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return
        for response in responses:
            _append_event(
                db,
                run,
                "approval.response",
                response,
                "user",
            )
        db.commit()


def _schedule_resume(
    app: FastAPI,
    run_id: UUID,
    decisions: list[dict[str, Any]],
    approval_responses: list[dict[str, Any]],
) -> None:
    previous_task = app.state.run_tasks.get(run_id)
    task = asyncio.create_task(
        _resume_after_active_task(
            app,
            run_id,
            decisions,
            approval_responses,
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
    base_ref = _code_base_ref(description)

    with session_factory() as db:
        user = db.get(User, user_id)
        if user is None:
            user = User(id=user_id, email=user_email)
            db.add(user)
            db.flush()
        existing = db.scalar(
            select(Run).where(Run.trigger == f"jira:{key}").order_by(Run.created_at.desc())
        )
        if existing is not None:
            return existing.id, message, existing.status == "queued"

        session = Session(
            user_id=user_id,
            repo=issue.get("repo") or "github.com/vishal7pandey/rtems",
            environment="local",
            mode="autonomous",
            merge_policy="human",
            model_profile="anvil-main",
            title=f"{key}: {summary}",
        )
        db.add(session)
        db.flush()
        run = Run(
            session_id=session.id,
            trigger=f"jira:{key}",
            repo=session.repo,
            status="queued",
            mode="autonomous",
            input_text=message,
            network=_jira_network(issue),
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


def _code_base_ref(description: str) -> str:
    for line in description.splitlines():
        label, separator, value = line.partition(":")
        if separator and label.strip().casefold() == "code base" and value.strip():
            return value.strip()
    return "main"


def _jira_network(issue: dict[str, Any]) -> str:
    labels = issue.get("labels", [])
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ValueError("Jira intake candidate labels must be a list of strings")
    return "online" if "anvil-online" in labels else "offline"


async def poll_jira_intake(app: FastAPI) -> list[UUID]:
    if app.state.executor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RunExecutor is not configured",
        )
    candidates_fn, transition_fn = _load_jira_helpers(app)
    candidates = await run_in_threadpool(candidates_fn, max_results=20)
    started: list[UUID] = []
    user_id = app.state.settings.demo_user_id
    for issue in candidates:
        run_id, message, should_start = await run_in_threadpool(
            _get_or_create_intake_run,
            app.state.session_factory,
            issue,
            user_id,
            app.state.settings.demo_user_email,
        )
        await run_in_threadpool(transition_fn, issue["key"], "In Progress")
        if should_start:
            _schedule_run(app, run_id, message)
            started.append(run_id)
    return started


async def _jira_intake_loop(app: FastAPI) -> None:
    while True:
        try:
            await poll_jira_intake(app)
        except _jira_expected_errors() as exc:
            logger.error("Jira intake poll failed (%s)", type(exc).__name__)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


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
    start_intake_loop: bool = True,
) -> FastAPI:
    configured_settings = settings or get_settings()
    engine, session_factory = create_session_factory(
        database_url or configured_settings.database_dsn
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.executor is not None and app.state.start_intake_loop:
            app.state.intake_task = asyncio.create_task(
                _jira_intake_loop(app),
                name="anvil-jira-intake",
            )
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
    app.state.session_factory = session_factory
    app.state.executor = executor
    app.state.jira_candidates = jira_candidates
    app.state.jira_transition = jira_transition
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
    ) -> Run:
        if request.app.state.executor is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="RunExecutor is not configured",
            )
        session = db.scalar(
            select(Session).where(Session.id == session_id, Session.user_id == user.id)
        )
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        run = Run(
            session_id=session.id,
            trigger="ui",
            repo=session.repo,
            status="queued",
            mode=session.mode,
            input_text=payload.text,
            network="offline",
            base_ref="main",
        )
        db.add(run)
        db.flush()
        _append_event(db, run, "message", {"role": "user", "text": payload.text}, "user")
        session.updated_at = utc_now()
        db.commit()
        db.refresh(run)
        _schedule_run(request.app, run.id, payload.text)
        return run

    @app.get("/api/runs/{run_id}", response_model=RunResponse)
    def get_run(
        run_id: UUID,
        db: DBSession = Depends(get_db),
        user: User = Depends(_authenticated_user),
    ) -> Run:
        return _load_run(db, run_id, user.id)

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
        approval_responses: list[dict[str, Any]] = []
        if resume_group:
            if request.app.state.executor is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="RunExecutor is not configured",
                )
            for item in pending_group:
                item.resumed_at = decision_time
                approval_responses.append(
                    {
                        "approval_id": str(item.id),
                        "decision": "approve" if item.status == "approved" else "reject",
                        "comment": item.comment,
                    }
                )
                if item.status == "approved":
                    decisions.append({"type": "approve"})
                else:
                    decisions.append(
                        {
                            "type": "reject",
                            "message": item.comment or "Rejected by user",
                        }
                    )
        db.commit()
        db.refresh(approval)
        if resume_group:
            _schedule_resume(request.app, run.id, decisions, approval_responses)
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
        run.cancel_requested_at = utc_now()
        db.commit()
        db.refresh(run)
        run_payload = await run_in_threadpool(
            _load_run_payload,
            request.app.state.session_factory,
            run.id,
        )
        try:
            await executor.cancel(run_payload)
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


app = create_app()
