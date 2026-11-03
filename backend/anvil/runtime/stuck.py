"""Runtime-side stuck detection (docs/02-architecture.md "Stuck detection and handoff").

The runtime, not the model, decides that a run is stuck. The detector watches the contract events
the executor emits and returns a reason when a rule trips. The executor then stops the agent and
runs the handoff procedure. The model can also hand off on its own via the `handoff` tool.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass
class Budgets:
    max_tool_calls: int = field(default_factory=lambda: _env_int("ANVIL_MAX_TOOL_CALLS", 150))
    # Cumulative tokens grow with context (every call resends it): the TC2 dry run hit 3M at call 67
    # while progressing, for ~$1 thanks to prompt caching. Wall time and tool calls bound real cost.
    max_tokens: int = field(default_factory=lambda: _env_int("ANVIL_MAX_TOKENS", 12_000_000))
    max_seconds: int = field(default_factory=lambda: _env_int("ANVIL_MAX_SECONDS", 45 * 60))
    same_failure_limit: int = 3
    # Reading a large C code base legitimately takes many calls before the first edit (TC1 dry run:
    # 30 tripped mid-investigation), so this is generous.
    no_progress_tool_calls: int = field(default_factory=lambda: _env_int("ANVIL_NO_PROGRESS_CALLS", 80))
    ci_red_rounds: int = 3


_NOISE = re.compile(r"0x[0-9a-f]+|\b\d+\b|/tmp/\S+")


def failure_signature(cmd: str, output_tail: str) -> str:
    """Same command + same last error line = same failure, ignoring numbers, addresses, temp paths."""
    lines = [ln for ln in output_tail.strip().splitlines() if ln.strip()]
    last = next((ln for ln in reversed(lines) if re.search(r"error|fail|fatal|undefined|abort", ln, re.I)),
                lines[-1] if lines else "")
    norm = _NOISE.sub("#", f"{' '.join(cmd.split())}|{last.strip()}".lower())
    return hashlib.sha1(norm.encode()).hexdigest()[:12]


class StuckDetector:
    """Feed every contract event to `observe`; it returns a reason string once a rule trips."""

    def __init__(self, budgets: Budgets | None = None, clock=time.monotonic) -> None:
        self.b = budgets or Budgets()
        self._clock = clock
        self._started = clock()
        self.tool_calls = 0
        self.tokens = 0
        self.failures: Counter[str] = Counter()
        self.ci_red = 0
        self._diff_state: str | None = None
        self._calls_since_progress = 0
        self.reason: str | None = None

    @property
    def deadline(self) -> float:
        """The clock value the time budget ends at (same clock as the detector)."""
        return self._started + self.b.max_seconds

    def note_diff_state(self, state: str) -> None:
        """Executor reports a hash of `git diff` + untracked files after editing/executing tools."""
        if state != self._diff_state:
            self._diff_state = state
            self._calls_since_progress = 0

    def observe(self, ev: dict[str, Any]) -> str | None:
        if self.reason:
            return self.reason
        t, d = ev.get("type"), ev.get("data", {})
        if t == "tool.call":
            self.tool_calls += 1
            self._calls_since_progress += 1
        elif t == "usage":
            self.tokens += int(d.get("input_tokens", 0)) + int(d.get("output_tokens", 0))
        elif t == "sandbox.exec" and d.get("exit_code") not in (0, None):
            sig = failure_signature(d.get("cmd", ""), d.get("output_tail", ""))
            self.failures[sig] += 1
            if self.failures[sig] >= self.b.same_failure_limit:
                return self._trip(f"the same command failed {self.failures[sig]} times with the same error "
                                  f"(`{d.get('cmd', '')[:120]}`)")
        elif t == "tool.result" and d.get("name") == "github_checks" and "failure" in str(d.get("output", "")).lower():
            self.ci_red += 1
            if self.ci_red >= self.b.ci_red_rounds:
                return self._trip(f"CI still red after {self.ci_red} rounds")

        if self.tool_calls >= self.b.max_tool_calls:
            return self._trip(f"step budget reached ({self.tool_calls} tool calls)")
        if self.tokens >= self.b.max_tokens:
            return self._trip(f"token budget reached ({self.tokens} tokens)")
        if self._clock() - self._started >= self.b.max_seconds:
            return self._trip(f"time budget reached ({self.b.max_seconds // 60} min)")
        if self._diff_state is not None and self._calls_since_progress >= self.b.no_progress_tool_calls:
            return self._trip(f"no change to the code in the last {self._calls_since_progress} tool calls")
        return None

    def _trip(self, reason: str) -> str:
        self.reason = reason
        return reason
