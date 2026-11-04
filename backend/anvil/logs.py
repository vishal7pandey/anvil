"""Structured, run-correlated logging (ANVIL-84).

What changed for the owner: `run-api.cmd` used to overwrite api-8000.log on every restart, records
carried no run id, and a sandbox command's full output existed only as an event tail.

- `configure_logging()` adds a daily-rotated JSON-lines file under `logs/` (gitignored, 14 days):
  `logs/api.jsonl`, `logs/api.jsonl.YYYY-MM-DD` after midnight. The console stays plain text.
- `set_log_context()` stashes run_id/session_id/agent/event on a contextvar; every record written
  inside the run's task carries them, so `grep <run_id> logs/*.jsonl` shows that run's story.
- `record_commands()` wraps `sandbox.execute`: the Nth command's full output lands in
  `private/runs/<run_id>/commands/NNNN.log` and a log line links seq -> file inside the run context.
"""

from __future__ import annotations

import contextvars
import itertools
import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable

from anvil.runtime.source import runs_dir

log = logging.getLogger(__name__)

_LOG_CTX: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("anvil_log_context", default={})


def set_log_context(**fields: Any) -> None:
    """Attach run/session/agent fields to every log record written in this task context."""
    _LOG_CTX.set({k: str(v) for k, v in fields.items() if v is not None})


class JSONLFormatter(logging.Formatter):
    """One JSON object per record; run context fields ride in when set."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in _LOG_CTX.get({}).items():
            entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(logs_dir: Path | str) -> Path:
    """Install the daily-rotated JSONL file handler once; returns the file it writes to."""
    out = Path(logs_dir)
    out.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_anvil_jsonl", False):
            return Path(handler.baseFilename)
    handler = TimedRotatingFileHandler(out / "api.jsonl", when="midnight", backupCount=14, encoding="utf-8")
    handler.setFormatter(JSONLFormatter())
    handler._anvil_jsonl = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return Path(handler.baseFilename)


def _safe_run_id(run_id: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(run_id)) or "unknown"


def record_commands(sandbox: Any, run_id: Any) -> None:
    """Wrap sandbox.execute so every command's full output lands in commands/NNNN.log on the host.

    The event tail stays exactly as before; a log line (`sandbox command N -> path`, tagged with
    the run) is the link from the record stream to the full output file.
    """
    target = runs_dir(str(run_id)) / "commands"
    target.mkdir(parents=True, exist_ok=True)
    # Resume-safe numbering (review on #83): an approval-resumed run re-wraps the sandbox, so seed
    # the counter past the files the first part already wrote instead of overwriting them.
    existing = [int(p.stem) for p in target.glob("*.log") if p.stem.isdigit()]
    seq = itertools.count(max(existing, default=0) + 1)

    def write(command: str, output: str, exit_code: Any) -> None:
        n = next(seq)
        try:
            path = target / f"{n:04d}.log"
            path.write_text(f"$ {command}\n\n(exit {exit_code})\n\n{output or ''}", encoding="utf-8")
            log.info("sandbox command %s -> %s | %s", n, path, (command or "")[:120])
        except OSError as exc:
            log.warning("could not write command output for run %s: %s", run_id, exc)

    if hasattr(sandbox, "on_exec"):
        # Real DockerSandbox: the hook fires inside execute with the FULL output, before the
        # agent-visible truncation (review on #83).
        sandbox.on_exec = write
        return

    original: Callable[..., Any] = sandbox.execute

    def execute(command: str, *, timeout: int | None = None) -> Any:
        res = original(command, timeout=timeout)
        write(command, res.output or "", res.exit_code)
        return res

    sandbox.execute = execute
