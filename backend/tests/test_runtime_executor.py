import asyncio
import uuid

import pytest
from langchain_core.messages import AIMessage

from anvil.runtime.executor import DeepAgentExecutor, sandbox_name, work_branch

RUN = {"run_id": "r-123456789", "session_id": "s-1", "repo": "vishal7pandey/rtems-examples",
       "base_ref": "main", "mode": "autonomous", "merge_policy": "human", "jira_key": "RTEMS-1"}


class FakeAgent:
    def __init__(self, items, on_handoff=None, fail=False, delay=0.0):
        self.items, self.on_handoff, self.fail, self.delay = items, on_handoff, fail, delay

    async def astream(self, *_args, **_kwargs):
        for item in self.items:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield item
        if self.on_handoff:
            self.on_handoff({"goal": "g", "tried": "t", "evidence": "e", "hypothesis": "h", "next_step": "n"})
        if self.fail:
            raise ValueError("boom")


class OfflineExecutor(DeepAgentExecutor):
    """No Docker, no Postgres: exercises the event flow only."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.received = None

    def sandbox_for(self, run):
        return object()

    def checkout(self, sandbox, run):
        return {"repo": run["repo"], "path": "/workspace/x", "base_ref": "main", "branch": work_branch(run),
                "base_sha": "abc", "head_sha": "abc"}

    async def _persistence(self):
        return None, None

    def run_record(self, run, checkout):
        return checkout


def factory_for(items, **kw):
    def factory(config, sandbox, *, tools, on_handoff, checkpointer, store):
        return FakeAgent(items, on_handoff=on_handoff if kw.get("handoff") else None,
                         fail=kw.get("fail", False), delay=kw.get("delay", 0.0))
    return factory


async def collect(gen):
    return [ev async for ev in gen]


def test_names():
    assert sandbox_name("s-1/../x") == "anvil-sbx-s-1..x"
    assert work_branch(RUN) == "anvil/rtems-1"
    assert work_branch({"run_id": "abcdef123"}) == "anvil/run-abcdef12"


async def test_start_success_flow():
    msg = AIMessage(content="done")
    ex = OfflineExecutor(agent_factory=factory_for([((), "updates", {"model": {"messages": [msg]}})]))
    events = await collect(ex.start(RUN, "fix it"))
    types = [e["type"] for e in events]
    assert types == ["run.status", "run.record", "message", "run.status"]
    assert events[0]["data"]["status"] == "running" and events[-1]["data"]["status"] == "succeeded"
    assert [e["seq"] for e in events] == [1, 2, 3, 4]


async def test_handoff_and_failure():
    ex = OfflineExecutor(agent_factory=factory_for([], handoff=True))
    events = await collect(ex.start(RUN, "x"))
    assert events[-1]["data"]["status"] == "handoff" and events[-2]["data"]["kind"] == "handoff"

    ex = OfflineExecutor(agent_factory=factory_for([], fail=True))
    events = await collect(ex.start(RUN, "x"))
    assert events[-1]["data"]["status"] == "failed" and "boom" in events[-2]["data"]["text"]


async def test_cancel_stops_stream():
    items = [((), "updates", {"model": {"messages": [AIMessage(content=f"step {i}")]}}) for i in range(50)]
    ex = OfflineExecutor(agent_factory=factory_for(items, delay=0.01))
    seen = []

    async def consume():
        async for ev in ex.start(RUN, "x"):
            seen.append(ev)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    await ex.cancel(RUN)
    await task
    assert seen[-1]["data"]["status"] == "cancelled"
    assert len(seen) < 50


# -- integration: real container, real clone --------------------------------------------------

docker = pytest.importorskip("docker")


def _docker_ok():
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _docker_ok(), reason="Docker not reachable")
def test_checkout_is_single_branch_and_creates_work_branch():
    # buildpack-deps:*-scm has bash + git; the sandbox drops all capabilities, so nothing can be
    # apt-installed inside it (which is the point).
    image = "buildpack-deps:bookworm-scm"
    ex = DeepAgentExecutor(image=image)
    run = {**RUN, "session_id": f"test-{uuid.uuid4().hex[:8]}"}
    from anvil.runtime.sandbox import DockerSandbox, SandboxSpec
    sb = DockerSandbox.start(SandboxSpec(image=image, name=f"claude-{run['session_id']}", user="root", uid=0))
    try:
        info = ex.checkout(sb, run)
        assert info["branch"] == "anvil/rtems-1" and len(info["base_sha"]) == 40
        refs = sb.execute(f"cd {info['path']} && git branch -a --format='%(refname)'").output.split()
        # Only the base branch is known: nothing else (e.g. branches holding withheld fixes) is reachable.
        remote_branches = {r for r in refs if r.startswith("refs/remotes/") and not r.endswith("/HEAD")}
        assert remote_branches == {"refs/remotes/origin/main"}
        assert {r for r in refs if r.startswith("refs/heads/")} == {"refs/heads/main", "refs/heads/anvil/rtems-1"}
        fetch = sb.execute(f"git -C {info['path']} config --get-all remote.origin.fetch").output.split()
        assert fetch == ["+refs/heads/main:refs/remotes/origin/main"]  # a later `git fetch` stays single-branch
        # Offline by default: even an explicit fetch of another branch must fail.
        leak = sb.execute(f"git -C {info['path']} fetch origin refs/heads/main:refs/remotes/origin/leak", timeout=60)
        assert leak.exit_code != 0
        assert info["network"] == "offline"
        again = ex.checkout(sb, run)  # idempotent on a second message in the same session
        assert again["branch"] == "anvil/rtems-1"
    finally:
        sb.stop()
