"""ANVIL-84: JSON-lines logs with run context, daily rotation, full command output per run."""

import json
import logging
from types import SimpleNamespace

from anvil.logs import JSONLFormatter, configure_logging, record_commands, set_log_context


def test_formatter_carries_run_context():
    record = logging.LogRecord("anvil.test", logging.INFO, __file__, 1, "hello", (), None)
    set_log_context(run_id="r-1", session_id="s-1", agent="local")
    try:
        line = JSONLFormatter().format(record)
    finally:
        set_log_context(run_id=None)  # reset
    entry = json.loads(line)
    assert entry["run_id"] == "r-1" and entry["session_id"] == "s-1" and entry["agent"] == "local"
    assert entry["msg"] == "hello" and entry["level"] == "INFO" and entry["ts"]


def test_configured_file_is_jsonl_and_survives_reconfig(tmp_path):
    path = configure_logging(tmp_path / "logs")
    assert configure_logging(tmp_path / "logs") == path  # once, not per call
    logging.getLogger("anvil.test").info("persisted %s", 1)
    for h in logging.getLogger().handlers:
        h.flush()
    line = next(l for l in path.read_text().splitlines() if "persisted" in l)
    assert json.loads(line)["msg"] == "persisted 1"


def test_record_commands_writes_full_output(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_RUNS_DIR", str(tmp_path / "runs"))
    calls = []

    class Sb:
        def execute(self, command, *, timeout=None):
            calls.append(command)
            return SimpleNamespace(output=f"out:{command} " + "x" * 9000, exit_code=0)

    sb = Sb()
    record_commands(sb, "r-77")
    sb.execute("first")
    sb.execute("second")
    one = (tmp_path / "runs" / "r-77" / "commands" / "0001.log").read_text()
    assert one.startswith("$ first") and "out:first" in one and len(one) > 9000
    assert (tmp_path / "runs" / "r-77" / "commands" / "0002.log").exists()
    assert calls == ["first", "second"]  # output returned unchanged


def test_record_commands_resumes_numbering_after_restart(tmp_path, monkeypatch):
    """An approval-resumed run re-wraps the sandbox; it must not overwrite part one's logs."""
    monkeypatch.setenv("ANVIL_RUNS_DIR", str(tmp_path / "runs"))
    d = tmp_path / "runs" / "r-9" / "commands"
    d.mkdir(parents=True)
    (d / "0001.log").write_text("first")
    (d / "0002.log").write_text("second")

    class Sb:
        on_exec = None

        def execute(self, command, *, timeout=None):
            if self.on_exec:
                self.on_exec(command, "full-" + command, 0)
            return SimpleNamespace(output="tail", exit_code=0)

    sb = Sb()
    record_commands(sb, "r-9")
    sb.execute("third")
    assert (d / "0001.log").read_text() == "first"
    assert "third" in (d / "0003.log").read_text()
