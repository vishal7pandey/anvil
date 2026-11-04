import asyncio
from datetime import datetime, timedelta
from threading import Event
from decimal import Decimal
from time import sleep
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from anvil.api.app import create_app
from anvil.api.config import Settings
from anvil.api.metrics import SpendTotals
from anvil.api.models import Approval, Base, Run, RunEvent, Session, User, utc_now


AUTH = {"Authorization": "Bearer test-token"}


def executor_event(run, offset, event_type, data, agent="main"):
    return {
        "run_id": str(run["run_id"]),
        "seq": 1,
        "type": event_type,
        "agent": agent,
        "data": data,
    }


class SuccessfulExecutor:
    async def start(self, run, message):
        assert isinstance(run["run_id"], str)
        assert isinstance(run["session_id"], str)
        assert run["last_event_seq"] == 2
        assert run["last_seq"] == 2
        assert run["network"] in {"offline", "online"}
        yield executor_event(run, 1, "run.status", {"status": "running"})
        yield executor_event(
            run, 2, "message.delta", {"role": "assistant", "text": f"received: {message}"}
        )
        yield executor_event(
            run,
            3,
            "run.record",
            {
                "base_sha": "a" * 40,
                "branch": "anvil/test",
                "sandbox_image_digest": "sha256:test",
                "prompt_hash": "prompt-hash",
                "model_aliases": {"main": "test-model"},
                "skills_hash": "skills-hash",
                "budgets": {"usd": 1},
            },
        )
        yield executor_event(
            run, 4, "usage", {"input_tokens": 10, "output_tokens": 4, "usd": 0.012345}
        )
        yield executor_event(run, 5, "run.status", {"status": "succeeded"})

    async def resume(self, run, decisions):
        yield executor_event(run, 1, "run.status", {"status": "succeeded"})

    async def cancel(self, run):
        return None


class WaitingExecutor:
    def __init__(self):
        self.cancelled = asyncio.Event()
        self.started = Event()
        self.cancel_run = None

    async def start(self, run, message):
        self.started.set()
        await self.cancelled.wait()
        yield executor_event(
            self.cancel_run,
            1,
            "run.status",
            {"status": "cancelled"},
        )

    async def resume(self, run, decisions):
        yield executor_event(run, 1, "run.status", {"status": "succeeded"})

    async def cancel(self, run):
        self.cancel_run = run
        assert run["last_seq"] == run["last_event_seq"]
        self.cancelled.set()


class UnresponsiveCancelExecutor(WaitingExecutor):
    async def cancel(self, run):
        self.cancel_run = run
        await asyncio.Event().wait()


class ApprovalExecutor:
    def __init__(self, approval_ids):
        self.approval_ids = approval_ids
        self.decisions = None

    async def start(self, run, message):
        for offset, approval_id in enumerate(self.approval_ids, start=1):
            yield executor_event(
                run,
                offset,
                "approval.request",
                {
                    "approval_id": str(approval_id),
                    "action": "git_push",
                    "args": {"branch": f"anvil/example-{offset}"},
                    "reason": "Push the reviewed branch",
                },
            )
        yield executor_event(
            run,
            len(self.approval_ids) + 1,
            "run.status",
            {"status": "waiting_approval"},
        )

    async def resume(self, run, decisions):
        self.decisions = decisions
        assert run["last_seq"] == run["last_event_seq"]
        yield executor_event(run, 1, "run.status", {"status": "running"})
        yield executor_event(run, 2, "run.status", {"status": "succeeded"})

    async def cancel(self, run):
        return None


@pytest.fixture
def api_factory(tmp_path):
    apps = []

    def create(*, executor=None, **kwargs):
        database_url = f"sqlite:///{(tmp_path / f'api-{len(apps)}.db').as_posix()}"
        app = create_app(
            settings=Settings(
                demo_token=SecretStr("test-token"),
                demo_user_id="test-user",
                demo_user_email="test@example.invalid",
            ),
            database_url=database_url,
            executor=executor,
            spend_log_provider=kwargs.pop(
                "spend_log_provider",
                lambda run_ids: {
                    str(run_id): SpendTotals(0, Decimal("0")) for run_id in run_ids
                },
            ),
            start_intake_loop=kwargs.pop("start_intake_loop", False),
            **kwargs,
        )
        Base.metadata.create_all(app.state.engine)
        apps.append(app)
        return app

    yield create
    for app in apps:
        for task in app.state.run_tasks.values():
            if not task.done():
                task.cancel()
        app.state.engine.dispose()
        if app.state.litellm_engine is not None:
            app.state.litellm_engine.dispose()


@pytest.fixture
def client_factory(api_factory):
    clients = []

    def create(app):
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.__exit__(None, None, None)


def create_session(client: TestClient) -> str:
    response = client.post(
        "/api/sessions",
        headers=AUTH,
        json={
            "repo": "https://github.com/example/repo.git",
            "environment": "local",
            "mode": "interactive",
            "merge_policy": "human",
            "model_profile": "anvil-main",
            "title": "test run",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_authentication_and_session_crud(api_factory, client_factory):
    app = api_factory()
    client = client_factory(app)

    assert client.get("/api/sessions").status_code == 401
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    preflight = client.options(
        "/api/sessions",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"

    session_id = create_session(client)
    listed = client.get("/api/sessions", headers=AUTH)
    assert listed.status_code == 200
    assert [session["id"] for session in listed.json()] == [session_id]
    assert listed.json()[0]["repo"] == "example/repo"


def test_list_runs_for_session_newest_first(api_factory, client_factory):
    app = api_factory(executor=SuccessfulExecutor())
    client = client_factory(app)

    session_id = create_session(client)

    # Create three runs in the session
    run_ids = []
    for i in range(3):
        start = client.post(
            f"/api/sessions/{session_id}/messages",
            headers=AUTH,
            json={"text": f"message {i}"},
        )
        assert start.status_code == 202
        run_ids.append(start.json()["id"])

    # List runs for the session: newest first
    runs = client.get(f"/api/sessions/{session_id}/runs", headers=AUTH)
    assert runs.status_code == 200
    body = runs.json()
    assert len(body) == 3
    assert [r["id"] for r in body] == [run_ids[2], run_ids[1], run_ids[0]]
    assert all(r["session_id"] == session_id for r in body)


def test_list_runs_for_session_404_for_unknown_session(api_factory, client_factory):
    app = api_factory()
    client = client_factory(app)

    response = client.get(
        "/api/sessions/11111111-1111-1111-1111-111111111111/runs", headers=AUTH
    )
    assert response.status_code == 404


def test_list_runs_for_session_is_user_scoped(api_factory, client_factory):
    app = api_factory(executor=SuccessfulExecutor())
    client = client_factory(app)

    # Session owned by the demo user
    session_id = create_session(client)
    client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "owned run"},
    )

    # A second client without the demo token cannot see the session's runs
    other = {"Authorization": "Bearer wrong-token"}
    response = client.get(f"/api/sessions/{session_id}/runs", headers=other)
    assert response.status_code == 401


def test_authentication_requires_configured_demo_token(client_factory):
    app = create_app(
        settings=Settings(
            demo_token=SecretStr(""),
            demo_user_id="test-user",
            demo_user_email="test@example.invalid",
        ),
        database_url="sqlite://",
        start_intake_loop=False,
    )
    Base.metadata.create_all(app.state.engine)
    client = client_factory(app)

    response = client.get("/api/sessions", headers=AUTH)

    assert response.status_code == 503
    assert response.json()["detail"] == "Demo authentication is not configured"


def test_message_run_and_sse_resume(api_factory, client_factory):
    def spend_log_provider(run_ids):
        return {
            str(run_id): SpendTotals(1, Decimal("0.023456"))
            for run_id in run_ids
        }

    app = api_factory(
        executor=SuccessfulExecutor(),
        spend_log_provider=spend_log_provider,
    )
    client = client_factory(app)
    session_id = create_session(client)

    response = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "hello", "scenario": "TC1"},
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    assert response.json()["trigger"] == "ui"
    assert response.json()["run_record"]["scenario"] == "TC1"

    full_stream = client.get(f"/api/runs/{run_id}/events", headers=AUTH)
    assert full_stream.status_code == 200
    assert "id: 1" in full_stream.text
    assert "id: 7" in full_stream.text
    assert "event: run.record" in full_stream.text
    assert "event: message.delta" in full_stream.text
    assert '"status":"succeeded"' in full_stream.text

    resumed = client.get(
        f"/api/runs/{run_id}/events",
        headers={**AUTH, "Last-Event-ID": "2"},
    )
    assert resumed.status_code == 200
    assert "id: 1" not in resumed.text
    assert "id: 2" not in resumed.text
    assert "id: 3" in resumed.text
    assert "id: 4" in resumed.text
    assert "id: 5" in resumed.text
    assert "id: 6" in resumed.text
    assert "id: 7" in resumed.text
    ids = [
        line.removeprefix("id: ")
        for line in full_stream.text.splitlines()
        if line.startswith("id: ")
    ]
    assert ids == [str(sequence) for sequence in range(1, 8)]

    with app.state.session_factory() as db:
        run = db.get(Run, UUID(run_id))
        run.last_event_seq += 1
        db.add(
            RunEvent(
                run_id=run.id,
                seq=run.last_event_seq,
                type="tool.result",
                agent="main",
                data={"name": "github_checks", "output": "build-test: failure"},
            )
        )
        db.commit()

    run = client.get(f"/api/runs/{run_id}", headers=AUTH)
    assert run.status_code == 200
    assert run.json()["status"] == "succeeded"
    assert run.json()["repo"] == "example/repo"
    assert run.json()["input_tokens"] == 10
    assert run.json()["output_tokens"] == 4
    assert run.json()["usd"] == pytest.approx(0.023456)
    assert run.json()["cost_usd"] == pytest.approx(0.023456)
    assert run.json()["cost_status"] == "available"
    assert run.json()["wall_time_seconds"] is not None
    assert run.json()["model_calls"] == 1
    assert run.json()["human_touches"] == 1
    assert run.json()["ci_rounds"] == 1
    assert run.json()["outcome"] == "succeeded"
    assert run.json()["network"] == "offline"
    assert run.json()["run_record"]["branch"] == "anvil/test"
    assert run.json()["run_record"]["skills_hash"] == "skills-hash"
    assert run.json()["skill_versions"] == {"hash": "skills-hash"}
    assert run.json()["branch"] == "anvil/test"

    metrics = client.get(
        "/api/metrics",
        headers=AUTH,
        params={"scenario": "TC1"},
    )
    assert metrics.status_code == 200
    assert metrics.json()["run_count"] == 1
    assert metrics.json()["model_calls"] == 1
    assert metrics.json()["input_tokens"] == 10
    assert metrics.json()["output_tokens"] == 4
    assert metrics.json()["usd"] == pytest.approx(0.023456)
    assert metrics.json()["cost_usd"] == pytest.approx(0.023456)
    assert metrics.json()["cost_status"] == "available"
    assert metrics.json()["human_touches"] == 1
    assert metrics.json()["ci_rounds"] == 1
    assert metrics.json()["outcomes"] == {"succeeded": 1}

    no_matching_scenario = client.get(
        "/api/metrics",
        headers=AUTH,
        params={"scenario": "TC2"},
    )
    assert no_matching_scenario.status_code == 200
    assert no_matching_scenario.json()["run_count"] == 0


def test_run_metrics_fail_explicitly_when_spend_logs_are_missing(
    api_factory, client_factory
):
    app = api_factory(
        executor=SuccessfulExecutor(),
        spend_log_provider=lambda _run_ids: {},
    )
    client = client_factory(app)
    session_id = create_session(client)

    start = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "hello"},
    )
    run_id = start.json()["id"]
    client.get(f"/api/runs/{run_id}/events", headers=AUTH)

    response = client.get(f"/api/runs/{run_id}", headers=AUTH)

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "LiteLLM spend logs are not yet available for runs with token usage"
    )
    assert response.headers["retry-after"] == "1"


def test_stale_missing_spend_logs_return_unknown_cost(api_factory, client_factory):
    app = api_factory(
        executor=SuccessfulExecutor(),
        spend_log_provider=lambda _run_ids: {},
    )
    client = client_factory(app)
    session_id = create_session(client)
    start = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "hello"},
    )
    run_id = UUID(start.json()["id"])
    client.get(f"/api/runs/{run_id}/events", headers=AUTH)

    with app.state.session_factory() as db:
        usage_event = db.scalar(
            select(RunEvent).where(
                RunEvent.run_id == run_id,
                RunEvent.type == "usage",
            )
        )
        assert usage_event is not None
        usage_event.ts = utc_now() - timedelta(minutes=6)
        db.commit()

    run = client.get(f"/api/runs/{run_id}", headers=AUTH)
    assert run.status_code == 200
    assert run.json()["model_calls"] is None
    assert run.json()["usd"] is None
    assert run.json()["cost_usd"] is None
    assert run.json()["cost_status"] == "missing"

    metrics = client.get("/api/metrics", headers=AUTH)
    assert metrics.status_code == 200
    assert metrics.json()["model_calls"] is None
    assert metrics.json()["usd"] is None
    assert metrics.json()["cost_usd"] is None
    assert metrics.json()["cost_status"] == "missing"


def test_aggregate_cost_sums_priced_runs_and_counts_the_rest(api_factory, client_factory):
    priced: set[str] = set()
    app = api_factory(
        executor=SuccessfulExecutor(),
        spend_log_provider=lambda run_ids: {
            str(run_id): SpendTotals(1, Decimal("0.5"))
            for run_id in run_ids
            if str(run_id) in priced
        },
    )
    client = client_factory(app)
    session_id = create_session(client)
    run_ids = []
    for _ in range(2):
        start = client.post(
            f"/api/sessions/{session_id}/messages", headers=AUTH, json={"text": "hello"}
        )
        run_ids.append(start.json()["id"])
        client.get(f"/api/runs/{run_ids[-1]}/events", headers=AUTH)
    priced.add(run_ids[0])

    with app.state.session_factory() as db:
        for event in db.scalars(
            select(RunEvent).where(RunEvent.run_id == UUID(run_ids[1]), RunEvent.type == "usage")
        ):
            event.ts = utc_now() - timedelta(minutes=6)
        db.commit()

    metrics = client.get("/api/metrics", headers=AUTH)
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["run_count"] == 2
    assert body["cost_status"] == "partial"
    assert body["runs_missing_cost"] == 1
    assert body["usd"] == pytest.approx(0.5)
    assert body["model_calls"] == 1


def test_message_rejected_without_executor(api_factory, client_factory):
    app = api_factory()
    client = client_factory(app)
    session_id = create_session(client)

    response = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "do real work"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "RunExecutor is not configured"


def test_approval_decision_resumes_run(api_factory, client_factory):
    approval_ids = [uuid4(), uuid4()]
    executor = ApprovalExecutor(approval_ids)
    app = api_factory(executor=executor)
    client = client_factory(app)
    session_id = create_session(client)
    start = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "approve this"},
    )
    run_id = start.json()["id"]
    for _ in range(50):
        with app.state.session_factory() as db:
            if all(db.get(Approval, approval_id) is not None for approval_id in approval_ids):
                break
        sleep(0.02)
    else:
        pytest.fail("executor did not persist all approval requests")

    first_response = client.post(
        f"/api/approvals/{approval_ids[0]}",
        headers=AUTH,
        json={"decision": "approve", "comment": "Reviewed"},
    )
    assert first_response.status_code == 200
    assert executor.decisions is None

    response = client.post(
        f"/api/approvals/{approval_ids[1]}",
        headers=AUTH,
        json={"decision": "reject", "comment": "Not ready"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    events = client.get(f"/api/runs/{run_id}/events", headers=AUTH)
    assert "event: approval.response" in events.text
    assert "event: approval.resume_requested" in events.text
    assert '"status":"succeeded"' in events.text
    assert executor.decisions == [
        {"type": "approve"},
        {"type": "reject", "message": "Not ready"},
    ]
    with app.state.session_factory() as db:
        assert all(
            db.get(Approval, approval_id).resumed_at is not None
            for approval_id in approval_ids
        )


def test_approval_decision_persists_without_resuming_terminal_run(api_factory, client_factory):
    app = api_factory()
    client = client_factory(app)
    create_session(client)
    approval_id = uuid4()

    with app.state.session_factory() as db:
        user = db.get(User, "test-user")
        session = db.scalar(select(Session).where(Session.user_id == user.id))
        run = Run(
            session_id=session.id,
            trigger="ui",
            repo=session.repo,
            status="succeeded",
            mode=session.mode,
            input_text="approve this",
        )
        db.add(run)
        db.flush()
        db.add(Approval(id=approval_id, run_id=run.id, action="git_push"))
        db.commit()
        run_id = run.id

    response = client.post(
        f"/api/approvals/{approval_id}",
        headers=AUTH,
        json={"decision": "approve"},
    )

    assert response.status_code == 409
    events = client.get(f"/api/runs/{run_id}/events", headers=AUTH)
    assert "event: approval.response" not in events.text


def test_approval_inbox_and_run_timeline_are_owner_scoped(api_factory, client_factory):
    app = api_factory()
    client = client_factory(app)
    session_id = UUID(create_session(client))
    approval_id = uuid4()
    other_user_run_id = uuid4()
    run_id = uuid4()

    with app.state.session_factory() as db:
        user = db.get(User, "test-user")
        session = db.get(Session, session_id)
        run = Run(
            id=run_id,
            session_id=session.id,
            trigger="ui",
            repo=session.repo,
            status="waiting_approval",
            mode=session.mode,
            input_text="approve this",
        )
        approval = Approval(
            id=approval_id,
            run_id=run.id,
            action="git_push",
            args={"branch": "anvil/example"},
            reason="Push the reviewed branch",
        )
        db.add_all(
            [
                run,
                approval,
                RunEvent(
                    run_id=run.id,
                    seq=1,
                    type="approval.request",
                    agent="main",
                    data={
                        "approval_id": str(approval_id),
                        "action": "git_push",
                        "args": {"branch": "anvil/example"},
                        "reason": "Push the reviewed branch",
                    },
                ),
                RunEvent(
                    run_id=run.id,
                    seq=2,
                    type="run.status",
                    agent="api",
                    data={"status": "waiting_approval"},
                ),
            ]
        )

        foreign_user = User(id="other-user", email="other@example.invalid")
        foreign_session = Session(
            user_id=foreign_user.id,
            repo="other/repo",
            environment="local",
            mode="interactive",
            model_profile="anvil-main",
        )
        db.add(foreign_user)
        db.add(foreign_session)
        db.flush()
        db.add(
            Run(
                id=other_user_run_id,
                session_id=foreign_session.id,
                trigger="ui",
                repo=foreign_session.repo,
                status="waiting_approval",
                mode=foreign_session.mode,
                input_text="private",
            )
        )
        db.flush()
        db.add(
            Approval(
                id=uuid4(),
                run_id=other_user_run_id,
                action="git_push",
                args={},
            )
        )
        db.commit()

    inbox = client.get("/api/approvals", headers=AUTH)
    assert inbox.status_code == 200
    assert len(inbox.json()) == 1
    approval_item = inbox.json()[0]
    assert datetime.fromisoformat(approval_item["requested_at"])
    assert {key: value for key, value in approval_item.items() if key != "requested_at"} == {
        "approval_id": str(approval_id),
        "run_id": str(run_id),
        "session_id": str(session_id),
        "session_title": "test run",
        "repo": "example/repo",
        "run_status": "waiting_approval",
        "action": "git_push",
        "args": {"branch": "anvil/example"},
        "reason": "Push the reviewed branch",
        "agent": "main",
    }

    runs = client.get("/api/runs", headers=AUTH)
    assert runs.status_code == 200
    assert [item["id"] for item in runs.json()] == [str(run_id)]

    timeline = client.get(f"/api/runs/{run_id}/timeline", headers=AUTH)
    assert timeline.status_code == 200
    assert [event["seq"] for event in timeline.json()] == [1, 2]
    assert timeline.json()[0]["data"]["approval_id"] == str(approval_id)

    assert client.get(
        f"/api/runs/{other_user_run_id}/timeline",
        headers=AUTH,
    ).status_code == 404


def test_manual_jira_intake_creates_and_starts_run(api_factory, client_factory):
    transitions = []

    def candidates(*, max_results):
        assert max_results == 20
        return [
            {
                "key": "RTEMS-1",
                "summary": "Investigate the example issue",
                "description": "Code base: release-branch\nAdditional details.",
                "labels": ["anvil", "anvil-online"],
            }
        ]

    def transition(key, target):
        transitions.append((key, target))

    app = api_factory(
        executor=SuccessfulExecutor(),
        jira_candidates=candidates,
        jira_transition=transition,
        start_intake_loop=False,
    )
    client = client_factory(app)

    response = client.post("/api/intake/jira/poll", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    assert transitions == [("RTEMS-1", "In Progress")]
    run_id = response.json()["run_ids"][0]
    stream = client.get(f"/api/runs/{run_id}/events", headers=AUTH)
    assert "event: message.delta" in stream.text
    run = client.get(f"/api/runs/{run_id}", headers=AUTH).json()
    assert run["trigger"] == "jira:RTEMS-1"
    assert run["mode"] == "autonomous"
    assert run["base_ref"] == "release-branch"
    assert run["jira_key"] == "RTEMS-1"
    assert run["network"] == "online"
    assert run["repo"] == "vishal7pandey/rtems"


def test_relabelled_ticket_gets_a_new_run_after_the_old_one_finished(api_factory, client_factory):
    # RTEMS-4, 2026-11-03: the earlier run had failed, the ticket was reset and relabelled, and
    # intake moved it to In Progress but reused the failed run, so nothing ran.
    def candidates(*, max_results):
        return [{"key": "RTEMS-4", "summary": "Retry me", "description": "Code base: main", "labels": ["anvil"]}]

    app = api_factory(
        executor=SuccessfulExecutor(),
        jira_candidates=candidates,
        jira_transition=lambda key, target: None,
        start_intake_loop=False,
    )
    client = client_factory(app)

    first = client.post("/api/intake/jira/poll", headers=AUTH).json()["run_ids"][0]
    client.get(f"/api/runs/{first}/events", headers=AUTH)  # drain: the run reaches a terminal status
    assert client.get(f"/api/runs/{first}", headers=AUTH).json()["status"] == "succeeded"

    second = client.post("/api/intake/jira/poll", headers=AUTH).json()
    assert second["accepted"] == 1 and second["run_ids"][0] != first


@pytest.mark.parametrize(
    ("description", "repo", "ref"),
    [
        # RTEMS-1 as written: mid-sentence, backticks, trailing full stop.
        (
            "Uniprocessor, RTEMS_DEBUG off. Reproduce on `sparc/erc32` (SIS). "
            "Code base: branch `tc1/5776-base` of github.com/vishal7pandey/rtems.",
            "vishal7pandey/rtems",
            "tc1/5776-base",
        ),
        # RTEMS-2 as written: own line, parenthetical after the repo.
        (
            "Code base: branch `tc2/upgrade-base-leon3` of github.com/vishal7pandey/rtems-lwip "
            "(lwIP 2.1.3, with the LEON3 BSP definition and GRETH driver).",
            "vishal7pandey/rtems-lwip",
            "tc2/upgrade-base-leon3",
        ),
        ("Code base: release-branch\nAdditional details.", None, "release-branch"),
        ("code base: branch main of https://github.com/acme/widget.git", "acme/widget", "main"),
    ],
)
def test_code_base_line_is_parsed_wherever_it_appears(description, repo, ref):
    from anvil.api.app import _code_base

    assert _code_base(description) == (repo, ref)


def test_intake_never_guesses_a_base_branch(api_factory, client_factory):
    """No "Code base:" line means no run: defaulting to main would check out withheld fixes."""
    transitions = []

    def candidates(*, max_results):
        return [
            {"key": "RTEMS-9", "summary": "No code base", "description": "Just text.", "labels": ["anvil"]},
            {
                "key": "RTEMS-2",
                "summary": "Upgrade lwIP",
                "description": "Code base: branch `tc2/upgrade-base-leon3` of github.com/vishal7pandey/rtems-lwip.",
                "labels": ["anvil"],
            },
        ]

    app = api_factory(
        executor=SuccessfulExecutor(),
        jira_candidates=candidates,
        jira_transition=lambda key, target: transitions.append((key, target)),
        start_intake_loop=False,
    )
    client = client_factory(app)

    response = client.post("/api/intake/jira/poll", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    assert transitions == [("RTEMS-2", "In Progress")]
    run_id = response.json()["run_ids"][0]
    client.get(f"/api/runs/{run_id}/events", headers=AUTH)  # let the run finish first
    run = client.get(f"/api/runs/{run_id}", headers=AUTH).json()
    assert run["repo"] == "vishal7pandey/rtems-lwip"
    assert run["base_ref"] == "tc2/upgrade-base-leon3"


def test_cancel_requests_stop_and_records_terminal_cancelled_status(api_factory, client_factory):
    executor = WaitingExecutor()
    app = api_factory(executor=executor)
    client = client_factory(app)
    session_id = create_session(client)
    start = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "wait until cancelled"},
    )
    run_id = start.json()["id"]
    assert executor.started.wait(timeout=5)

    response = client.post(f"/api/runs/{run_id}/cancel", headers=AUTH)

    assert response.status_code == 202
    assert response.json()["cancel_requested_at"] is not None
    events = client.get(f"/api/runs/{run_id}/events", headers=AUTH)
    assert "event: run.cancel_requested" in events.text
    assert '"status":"cancelled"' in events.text
    ids = [
        line.removeprefix("id: ")
        for line in events.text.splitlines()
        if line.startswith("id: ")
    ]
    assert ids == [str(sequence) for sequence in range(1, 5)]
    run = client.get(f"/api/runs/{run_id}", headers=AUTH).json()
    assert run["status"] == "cancelled"


def test_cancel_falls_back_to_task_cancellation_after_timeout(
    api_factory,
    client_factory,
    monkeypatch,
):
    monkeypatch.setattr("anvil.api.app.CANCEL_TIMEOUT_SECONDS", 0.01)
    app = api_factory(executor=UnresponsiveCancelExecutor())
    client = client_factory(app)
    session_id = create_session(client)
    start = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "wait until fallback cancellation"},
    )
    run_id = start.json()["id"]

    response = client.post(f"/api/runs/{run_id}/cancel", headers=AUTH)

    assert response.status_code == 202
    events = client.get(f"/api/runs/{run_id}/events", headers=AUTH)
    assert '"status":"cancelled"' in events.text
    run = client.get(f"/api/runs/{run_id}", headers=AUTH).json()
    assert run["status"] == "cancelled"


def test_get_running_run_computes_wall_time_with_sqlite_naive_timestamps(
    api_factory, client_factory
):
    executor = WaitingExecutor()
    app = api_factory(executor=executor)
    client = client_factory(app)
    session_id = create_session(client)
    start = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "inspect while still running"},
    )
    run_id = start.json()["id"]
    assert executor.started.wait(timeout=5)

    running = client.get(f"/api/runs/{run_id}", headers=AUTH)
    assert running.status_code == 200
    assert running.json()["status"] == "running"
    assert running.json()["ended_at"] is None
    assert running.json()["wall_time_seconds"] is not None
    assert running.json()["wall_time_seconds"] >= 0.0

    client.post(f"/api/runs/{run_id}/cancel", headers=AUTH)
    client.get(f"/api/runs/{run_id}/events", headers=AUTH)



def test_events_drop_nul_bytes_postgres_jsonb_rejects():
    from anvil.api.app import _strip_nul

    data = {"name": "grep", "output": "match\x00in binary", "list": ["a\x00b", 1, None], "n\x00k": True}
    assert _strip_nul(data) == {"name": "grep", "output": "matchin binary", "list": ["ab", 1, None], "nk": True}


def test_anvil_interactive_label_starts_an_interactive_run(api_factory, client_factory):
    # TC3 (ANVIL-50): a review's per-finding accept/reject gate is the interactive approval gate.
    def candidates(*, max_results):
        return [{"key": "RTEMS-3", "summary": "CRA review", "description": "Code base: main",
                 "labels": ["anvil", "anvil-interactive"]}]

    app = api_factory(executor=SuccessfulExecutor(), jira_candidates=candidates,
                      jira_transition=lambda key, target: None, start_intake_loop=False)
    client = client_factory(app)
    run_id = client.post("/api/intake/jira/poll", headers=AUTH).json()["run_ids"][0]
    run = client.get(f"/api/runs/{run_id}", headers=AUTH).json()
    assert run["mode"] == "interactive" and run["network"] == "offline"


def test_concurrent_first_requests_on_a_clean_db_do_not_race(api_factory, client_factory):
    # ANVIL-78: _authenticated_user used to get-then-insert the demo user per request; on a clean
    # database two first requests raced and one got users_pkey -> 500. The lifespan now seeds it
    # once (INSERT ... ON CONFLICT DO NOTHING) and the dependency only reads.
    import concurrent.futures

    app = api_factory()
    client = client_factory(app)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(
            lambda _: client.get("/api/sessions", headers=AUTH).status_code, range(20)))
    assert statuses == [200] * 20


