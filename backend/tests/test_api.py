import asyncio
from time import sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from anvil.api.app import create_app
from anvil.api.config import Settings
from anvil.api.models import Approval, Base, Run, Session, User


AUTH = {"Authorization": "Bearer test-token"}


def executor_event(run, offset, event_type, data, agent="main"):
    return {
        "run_id": str(run["run_id"]),
        "seq": run["last_event_seq"] + offset,
        "type": event_type,
        "agent": agent,
        "data": data,
    }


class SuccessfulExecutor:
    async def start(self, run, message):
        assert isinstance(run["run_id"], str)
        assert isinstance(run["session_id"], str)
        assert run["last_event_seq"] == 1
        assert run["last_seq"] == 1
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
        self.cancel_run = None

    async def start(self, run, message):
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
    app = api_factory(executor=SuccessfulExecutor())
    client = client_factory(app)
    session_id = create_session(client)

    response = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "hello"},
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    assert response.json()["trigger"] == "ui"

    full_stream = client.get(f"/api/runs/{run_id}/events", headers=AUTH)
    assert full_stream.status_code == 200
    assert "id: 1" in full_stream.text
    assert "id: 6" in full_stream.text
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

    run = client.get(f"/api/runs/{run_id}", headers=AUTH)
    assert run.status_code == 200
    assert run.json()["status"] == "succeeded"
    assert run.json()["repo"] == "example/repo"
    assert run.json()["input_tokens"] == 10
    assert run.json()["output_tokens"] == 4
    assert run.json()["usd"] == pytest.approx(0.012345)
    assert run.json()["network"] == "offline"
    assert run.json()["run_record"]["branch"] == "anvil/test"
    assert run.json()["run_record"]["skills_hash"] == "skills-hash"
    assert run.json()["skill_versions"] == {"hash": "skills-hash"}
    assert run.json()["branch"] == "anvil/test"


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

    response = client.post(f"/api/runs/{run_id}/cancel", headers=AUTH)

    assert response.status_code == 202
    assert response.json()["cancel_requested_at"] is not None
    events = client.get(f"/api/runs/{run_id}/events", headers=AUTH)
    assert '"status":"cancelled"' in events.text
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
