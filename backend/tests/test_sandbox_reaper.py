from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DBSession, sessionmaker

from anvil.api.config import Settings
from anvil.api.models import Base, Run, Session, User
from anvil.runtime.reaper import (
    DEFAULT_MAX_AGE_HOURS,
    ContainerReapCheck,
    evaluate_container_for_reap,
    has_handoff_evidence,
    parse_docker_timestamp,
    reap_stale_sandboxes,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    user = User(id="demo", email="demo@anvil.local")
    session.add(user)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def test_parse_docker_timestamp():
    # Nanoseconds (9 digits)
    dt1 = parse_docker_timestamp("2026-11-04T12:00:00.123456789Z")
    assert dt1 == datetime(2026, 11, 4, 12, 0, 0, 123456, tzinfo=timezone.utc)

    # Microseconds (6 digits)
    dt2 = parse_docker_timestamp("2026-11-04T12:00:00.654321Z")
    assert dt2 == datetime(2026, 11, 4, 12, 0, 0, 654321, tzinfo=timezone.utc)

    # Seconds only
    dt3 = parse_docker_timestamp("2026-11-04T12:00:00Z")
    assert dt3 == datetime(2026, 11, 4, 12, 0, 0, 0, tzinfo=timezone.utc)


def test_ignore_non_sandbox_containers(db_session: DBSession):
    c = MagicMock()
    c.name = "postgres"
    c.id = "c-postgres-1"
    res = evaluate_container_for_reap(c, db_session)
    assert not res.should_reap
    assert "Not an Anvil sandbox container" in res.reason


def test_active_run_never_reaped(db_session: DBSession):
    sess_id = uuid.uuid4()
    session_obj = Session(
        id=sess_id,
        user_id="demo",
        repo="vishal7pandey/rtems",
        environment="test",
        mode="autonomous",
        merge_policy="human",
        model_profile="default",
    )
    db_session.add(session_obj)

    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
    old_time = now - timedelta(hours=5)

    run = Run(
        id=uuid.uuid4(),
        session_id=sess_id,
        trigger="test",
        repo="vishal7pandey/rtems",
        status="running",
        mode="autonomous",
        input_text="Fix issue",
        started_at=old_time,
        ended_at=None,
    )
    db_session.add(run)
    db_session.commit()

    c = MagicMock()
    c.name = f"anvil-sbx-{str(sess_id)[:24]}"
    c.id = "c-active-1"
    c.labels = {"anvil.session_id": str(sess_id)}
    c.attrs = {"Created": old_time.isoformat() + "Z"}

    res = evaluate_container_for_reap(c, db_session, max_age_hours=2.0, now=now)
    assert not res.should_reap
    assert "Active run" in res.reason


def test_succeeded_run_grace_period(db_session: DBSession):
    sess_id = uuid.uuid4()
    session_obj = Session(
        id=sess_id,
        user_id="demo",
        repo="vishal7pandey/rtems",
        environment="test",
        mode="autonomous",
        merge_policy="human",
        model_profile="default",
    )
    db_session.add(session_obj)

    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
    # Ended 1 hour ago (< 2.0h max age)
    ended_at = now - timedelta(hours=1)

    run = Run(
        id=uuid.uuid4(),
        session_id=sess_id,
        trigger="test",
        repo="vishal7pandey/rtems",
        status="succeeded",
        mode="autonomous",
        input_text="Fix issue",
        started_at=ended_at - timedelta(minutes=20),
        ended_at=ended_at,
    )
    db_session.add(run)
    db_session.commit()

    c = MagicMock()
    c.name = f"anvil-sbx-{str(sess_id)[:24]}"
    c.id = "c-succ-1"
    c.labels = {"anvil.session_id": str(sess_id)}
    c.attrs = {"Created": (ended_at - timedelta(minutes=20)).isoformat() + "Z"}

    # 1h age -> keep
    res = evaluate_container_for_reap(c, db_session, max_age_hours=2.0, now=now)
    assert not res.should_reap
    assert "within 2.0h grace period" in res.reason

    # 3h age -> reap
    now_later = now + timedelta(hours=2)
    res_later = evaluate_container_for_reap(c, db_session, max_age_hours=2.0, now=now_later)
    assert res_later.should_reap
    assert "terminal (succeeded)" in res_later.reason


def test_failed_and_cancelled_runs(db_session: DBSession):
    sess_id = uuid.uuid4()
    session_obj = Session(
        id=sess_id,
        user_id="demo",
        repo="vishal7pandey/rtems",
        environment="test",
        mode="autonomous",
        merge_policy="human",
        model_profile="default",
    )
    db_session.add(session_obj)

    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
    # Ended 2.5 hours ago
    ended_at = now - timedelta(hours=2.5)

    run = Run(
        id=uuid.uuid4(),
        session_id=sess_id,
        trigger="test",
        repo="vishal7pandey/rtems",
        status="failed",
        mode="autonomous",
        input_text="Fix issue",
        ended_at=ended_at,
    )
    db_session.add(run)
    db_session.commit()

    c = MagicMock()
    c.name = f"anvil-sbx-{str(sess_id)[:24]}"
    c.id = "c-fail-1"
    c.labels = {"anvil.session_id": str(sess_id)}

    res = evaluate_container_for_reap(c, db_session, max_age_hours=2.0, now=now)
    assert res.should_reap
    assert "terminal (failed)" in res.reason


def test_handoff_run_survives_until_note_posted(db_session: DBSession):
    sess_id = uuid.uuid4()
    session_obj = Session(
        id=sess_id,
        user_id="demo",
        repo="vishal7pandey/rtems",
        environment="test",
        mode="autonomous",
        merge_policy="human",
        model_profile="default",
    )
    db_session.add(session_obj)

    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)

    # 1. Handoff in progress: status handoff but no artifacts and ended_at is None
    run = Run(
        id=uuid.uuid4(),
        session_id=sess_id,
        trigger="test",
        repo="vishal7pandey/rtems",
        status="handoff",
        mode="autonomous",
        input_text="Fix issue",
        artifacts=[],
        ended_at=None,
    )
    db_session.add(run)
    db_session.commit()

    c = MagicMock()
    c.name = f"anvil-sbx-{str(sess_id)[:24]}"
    c.id = "c-handoff-1"
    c.labels = {"anvil.session_id": str(sess_id)}

    res1 = evaluate_container_for_reap(c, db_session, max_age_hours=2.0, now=now)
    assert not res1.should_reap
    assert "has not completed posting handoff evidence" in res1.reason

    # 2. Handoff completed: artifact posted and ended_at set, 30m ago
    run.artifacts = [{"kind": "handoff", "title": "Handoff note (anvil/rtems-1-wip)"}]
    run.ended_at = now - timedelta(minutes=30)
    db_session.commit()

    res2 = evaluate_container_for_reap(c, db_session, max_age_hours=2.0, now=now)
    assert not res2.should_reap
    assert "within 2.0h grace period" in res2.reason

    # 3. Handoff completed, 2.5 hours ago
    run.ended_at = now - timedelta(hours=2.5)
    db_session.commit()

    res3 = evaluate_container_for_reap(c, db_session, max_age_hours=2.0, now=now)
    assert res3.should_reap
    assert "terminal (handoff)" in res3.reason


def test_dry_run_sandbox_reaping(db_session: DBSession):
    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)

    # Recent dry run sandbox (30 min old)
    c_recent = MagicMock()
    c_recent.name = "anvil-sbx-dry-a1b2c3d4"
    c_recent.id = "c-dry-1"
    c_recent.labels = {}
    c_recent.attrs = {"Created": (now - timedelta(minutes=30)).isoformat() + "Z"}

    res_recent = evaluate_container_for_reap(c_recent, db_session, max_age_hours=2.0, now=now)
    assert not res_recent.should_reap

    # Stale dry run sandbox (3 hours old)
    c_stale = MagicMock()
    c_stale.name = "anvil-sbx-dry-e5f6g7h8"
    c_stale.id = "c-dry-2"
    c_stale.labels = {}
    c_stale.attrs = {"Created": (now - timedelta(hours=3)).isoformat() + "Z"}

    res_stale = evaluate_container_for_reap(c_stale, db_session, max_age_hours=2.0, now=now)
    assert res_stale.should_reap
    assert "Stale dry-run sandbox" in res_stale.reason


def test_orphaned_container_reaping(db_session: DBSession):
    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)

    # 1. Container owned by this instance but session ID is not in DB (or has no runs)
    dummy_session_id = str(uuid.uuid4())
    c_orphan_owned = MagicMock()
    c_orphan_owned.name = f"anvil-sbx-{dummy_session_id.replace('-', '')[:12]}"
    c_orphan_owned.id = "c-orphan-1"
    c_orphan_owned.labels = {"anvil.owner": "test_owner", "anvil.session_id": dummy_session_id}
    c_orphan_owned.attrs = {"Created": (now - timedelta(hours=3)).isoformat() + "Z"}

    res = evaluate_container_for_reap(
        c_orphan_owned, db_session, max_age_hours=2.0, owner_id="test_owner", now=now
    )
    assert res.should_reap
    assert "Session has no runs and container age >= 2.0h" in res.reason

    # 2. Unlabelled container in all-owners mode (e.g. manual reset_demo.py --all-owners)
    c_unlabelled = MagicMock()
    c_unlabelled.name = "anvil-sbx-99999999"
    c_unlabelled.id = "c-orphan-2"
    c_unlabelled.labels = {}
    c_unlabelled.attrs = {"Created": (now - timedelta(hours=3)).isoformat() + "Z"}

    res_all = evaluate_container_for_reap(
        c_unlabelled, db_session, max_age_hours=2.0, all_owners=True, now=now
    )
    assert res_all.should_reap
    assert "all-owners mode" in res_all.reason


def test_reap_stale_sandboxes_execution():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    db = session_factory()
    user = User(id="demo", email="demo@anvil.local")
    db.add(user)

    sess_id = uuid.uuid4()
    s = Session(
        id=sess_id,
        user_id="demo",
        repo="vishal7pandey/rtems",
        environment="test",
        mode="autonomous",
        merge_policy="human",
        model_profile="default",
    )
    db.add(s)

    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
    run = Run(
        id=uuid.uuid4(),
        session_id=sess_id,
        trigger="test",
        repo="vishal7pandey/rtems",
        status="succeeded",
        mode="autonomous",
        input_text="Fix issue",
        ended_at=now - timedelta(hours=3),
    )
    db.add(run)
    db.commit()
    db.close()

    c_to_reap = MagicMock()
    c_to_reap.name = f"anvil-sbx-{str(sess_id)[:24]}"
    c_to_reap.id = "c-reap-1"
    c_to_reap.labels = {"anvil.session_id": str(sess_id)}

    docker_client = MagicMock()
    docker_client.containers.list.return_value = [c_to_reap]

    # Dry run mode: does not call stop/remove
    reaped_dry = reap_stale_sandboxes(
        session_factory, docker_client, max_age_hours=2.0, dry_run=True, now=now
    )
    assert reaped_dry == [c_to_reap.name]
    c_to_reap.stop.assert_not_called()
    c_to_reap.remove.assert_not_called()

    # Real reap: calls stop & remove
    reaped_real = reap_stale_sandboxes(
        session_factory, docker_client, max_age_hours=2.0, dry_run=False, now=now
    )
    assert reaped_real == [c_to_reap.name]
    c_to_reap.stop.assert_called_once_with(timeout=5)
    c_to_reap.remove.assert_called_once_with(force=True)


def test_settings_reaper_defaults():
    s = Settings()
    assert s.sandbox_reaper_enabled is True
    assert s.sandbox_reaper_interval_seconds == 300
    assert s.sandbox_max_age_hours == 2.0


def test_reset_containers_stale_only(monkeypatch):
    from scripts.reset_demo import reset_containers

    mock_reap = MagicMock(return_value=["anvil-sbx-12345"])
    monkeypatch.setattr("anvil.runtime.reaper.reap_stale_sandboxes", mock_reap)

    ok = reset_containers(dry_run=True, stale_only=True, max_age_hours=1.5)
    assert ok is True
    assert mock_reap.called
    assert mock_reap.call_args.kwargs["max_age_hours"] == 1.5
    assert mock_reap.call_args.kwargs["dry_run"] is True


@pytest.mark.asyncio
async def test_app_lifespan_reaper_task(monkeypatch):
    from anvil.api.app import create_app
    from anvil.api.models import Base

    monkeypatch.setattr("anvil.runtime.reaper.reap_stale_sandboxes", MagicMock(return_value=[]))
    app = create_app(
        database_url="sqlite:///:memory:",
        start_intake_loop=False,
        start_reaper_loop=True,
    )
    Base.metadata.create_all(app.state.engine)
    async with app.router.lifespan_context(app):
        assert app.state.reaper_task is not None
        assert not app.state.reaper_task.cancelled()
    assert app.state.reaper_task.cancelled()


def test_dry_run_scripts_keep_argument():
    from pathlib import Path

    dry_run_py = Path(__file__).resolve().parents[1] / "scripts" / "dry_run.py"
    tc1_dry_run_py = Path(__file__).resolve().parents[1] / "scripts" / "tc1_dry_run.py"

    assert "--keep" in dry_run_py.read_text(encoding="utf-8")
    assert "sandbox.stop(remove=True)" in dry_run_py.read_text(encoding="utf-8")

    assert "--keep" in tc1_dry_run_py.read_text(encoding="utf-8")
    assert "sandbox.stop(remove=True)" in tc1_dry_run_py.read_text(encoding="utf-8")


def test_foreign_owner_sandbox_never_reaped(db_session: DBSession):
    sess_id = uuid.uuid4()
    s = Session(
        id=sess_id,
        user_id="demo",
        repo="vishal7pandey/rtems",
        environment="test",
        mode="autonomous",
        merge_policy="human",
        model_profile="default",
    )
    db_session.add(s)

    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
    run = Run(
        id=uuid.uuid4(),
        session_id=sess_id,
        trigger="test",
        repo="vishal7pandey/rtems",
        status="succeeded",
        mode="autonomous",
        input_text="Fix issue",
        ended_at=now - timedelta(hours=4),
    )
    db_session.add(run)
    db_session.commit()

    c = MagicMock()
    c.name = f"anvil-sbx-{str(sess_id)[:24]}"
    c.id = "c-foreign-1"
    c.labels = {"anvil.session_id": str(sess_id), "anvil.owner": "anvil_devin"}

    # Evaluated by claude's API -> MUST NOT REAP
    res = evaluate_container_for_reap(
        c, db_session, owner_id="anvil_claude", max_age_hours=2.0, now=now
    )
    assert not res.should_reap
    assert "not current instance 'anvil_claude'" in res.reason

    # Evaluated by devin's API -> REAPED (ended 4h ago >= 2h)
    res_devin = evaluate_container_for_reap(
        c, db_session, owner_id="anvil_devin", max_age_hours=2.0, now=now
    )
    assert res_devin.should_reap

    # Evaluated in all_owners mode -> REAPED
    res_all = evaluate_container_for_reap(
        c, db_session, all_owners=True, max_age_hours=2.0, now=now
    )
    assert res_all.should_reap


def test_unlabelled_sandbox_skipped_by_instance_sweep(db_session: DBSession):
    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
    c = MagicMock()
    c.name = "anvil-sbx-unknown-container"
    c.id = "c-unlabelled-1"
    c.labels = {}
    c.attrs = {"Created": (now - timedelta(hours=5)).isoformat() + "Z"}

    res = evaluate_container_for_reap(
        c, db_session, owner_id="anvil_claude", max_age_hours=2.0, now=now
    )
    assert not res.should_reap
    assert "has no owner label; skipped" in res.reason


def test_container_created_long_ago_run_ended_just_now_kept(db_session: DBSession):
    sess_id = uuid.uuid4()
    s = Session(
        id=sess_id,
        user_id="demo",
        repo="vishal7pandey/rtems",
        environment="test",
        mode="autonomous",
        merge_policy="human",
        model_profile="default",
    )
    db_session.add(s)

    now = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
    # Container created 5 hours ago
    c_created = now - timedelta(hours=5)
    # But run only ended 10 minutes ago
    ended_at = now - timedelta(minutes=10)

    run = Run(
        id=uuid.uuid4(),
        session_id=sess_id,
        trigger="test",
        repo="vishal7pandey/rtems",
        status="succeeded",
        mode="autonomous",
        input_text="Fix issue",
        started_at=ended_at - timedelta(minutes=25),
        ended_at=ended_at,
    )
    db_session.add(run)
    db_session.commit()

    c = MagicMock()
    c.name = f"anvil-sbx-{str(sess_id)[:24]}"
    c.id = "c-reused-1"
    c.labels = {"anvil.session_id": str(sess_id), "anvil.owner": "anvil_claude"}
    c.attrs = {"Created": c_created.isoformat() + "Z"}

    res = evaluate_container_for_reap(
        c, db_session, owner_id="anvil_claude", max_age_hours=2.0, now=now
    )
    assert not res.should_reap
    assert "within 2.0h grace period" in res.reason


def test_executor_labels_sandbox_with_owner(monkeypatch):
    from anvil.runtime.executor import DeepAgentExecutor
    from anvil.runtime.sandbox import DockerSandbox
    from docker.errors import NotFound

    ex = DeepAgentExecutor(db_url="postgresql://anvil:anvil@localhost:5433/anvil_devin")
    assert ex.owner_id == "anvil_devin"

    mock_docker = MagicMock()
    mock_docker.containers.get.side_effect = NotFound("no container")
    mock_start = MagicMock()
    monkeypatch.setattr(DockerSandbox, "start", mock_start)

    ex._docker = mock_docker
    run = {"session_id": "sess-xyz-987", "repo": "vishal7pandey/rtems"}
    ex.sandbox_for(run)

    assert mock_start.called
    spec = mock_start.call_args[0][0]
    assert spec.labels["anvil.owner"] == "anvil_devin"
    assert spec.labels["anvil.session_id"] == "sess-xyz-987"
    assert spec.labels["anvil.repo"] == "vishal7pandey/rtems"


