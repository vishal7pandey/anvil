"""ANVIL-41: the time budget holds inside a long command, and whole-repo pre-commit is refused."""

import time

import pytest

from anvil.runtime.sandbox import budget_timeout, refusal
from anvil.runtime.stuck import Budgets, StuckDetector
from tests.test_runtime_executor import RUN, FakeAgent, OfflineExecutor, collect


@pytest.mark.parametrize("cmd", [
    "pre-commit run --all-files",
    "cd /workspace/rtems && pre-commit run --all-files || true",
    "bash -lc 'cd /workspace/rtems && pre-commit run --all-files'",
    'bash -lc "cd /workspace/rtems && pre-commit run -a"',
    "pre-commit run clang-format --all-files --show-diff-on-failure",
])
def test_whole_repo_pre_commit_is_refused_and_points_to_lint_changed(cmd):
    reason = refusal(cmd)
    assert reason and "lint_changed" in reason  # the one way to lint (ANVIL-74)


@pytest.mark.parametrize("cmd", [
    "pre-commit run --files cpukit/posix/src/mutexinit.c testsuites/psxtests/psx05/init.c",
    "pre-commit install",
    "git commit -F /tmp/msg && echo --all-files",
    "./waf -j4 --all-files-in-name-only",
])
def test_other_commands_pass(cmd):
    assert refusal(cmd) is None


def test_budget_timeout_clamps_to_what_is_left():
    assert budget_timeout(1800, None, 100.0) == 1800  # no budget: unchanged
    assert budget_timeout(1800, 400.0, 100.0) == 300  # 5 min left
    assert budget_timeout(60, 400.0, 100.0) == 60  # short command: unchanged
    assert budget_timeout(0, 400.0, 100.0) == 300  # "no timeout" still ends with the budget
    assert budget_timeout(1800, 100.0, 100.5) is None  # used up: refuse


def test_detector_deadline_uses_its_clock():
    clock = iter([1000.0]).__next__
    assert StuckDetector(Budgets(max_seconds=2700), clock=clock).deadline == 3700.0


async def test_executor_sets_the_deadline_while_the_agent_runs_and_clears_it_for_handoff():
    seen = {}

    class Recording(OfflineExecutor):
        def sandbox_for(self, run):
            sb = super().sandbox_for(run)
            seen["sandbox"] = sb
            return sb

    def factory(config, sandbox, *, tools, on_handoff, checkpointer, store, skills):
        class Agent(FakeAgent):
            async def astream(self, *a, **k):
                seen["during"] = sandbox.deadline
                async for item in super().astream(*a, **k):
                    yield item
        return Agent([], on_handoff=on_handoff)

    ex = Recording(agent_factory=factory, budgets=Budgets(max_seconds=2700))
    before = time.monotonic()
    events = await collect(ex.start(RUN, "x"))
    assert events[-1]["data"]["status"] == "handoff"
    assert before + 2700 <= seen["during"] <= time.monotonic() + 2700
    assert seen["sandbox"].deadline is None
