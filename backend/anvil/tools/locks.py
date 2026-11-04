"""Shared-laptop resource locks (C:\\Dev\\comms\\locks\\<name>.lock), same protocol the agents use by hand."""

import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from anvil.tools.errors import ToolError

LOCKS_DIR = Path(os.environ.get("ANVIL_LOCKS_DIR", r"C:\Dev\comms\locks"))


def is_free_placeholder(text: str) -> bool:
    """A hand-written lock file saying nobody holds it ("agent: none"), as the agents' manual protocol
    leaves behind. The file exists, but the resource is free: waiting on it would stall a run for the whole
    timeout (a 30-minute test wait on 11-05)."""
    return any(line.strip().lower().replace(" ", "") == "agent:none" for line in text.splitlines())


@contextmanager
def acquire_lock(name: str, what: str = "", owner: str = "anvil-runtime", timeout: float = 3600, poll: float = 30) -> Iterator[Path]:
    """Hold `<name>.lock` (e.g. 'network-heavy', 'rtems-build') while the block runs; wait up to `timeout` s."""
    path = Path(os.environ.get("ANVIL_LOCKS_DIR", LOCKS_DIR)) / f"{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            held = path.read_text(errors="replace").strip() if path.exists() else "?"
            if is_free_placeholder(held):
                path.unlink(missing_ok=True)   # take it over; a narrow race with another waiter is acceptable here
                continue
            if time.monotonic() >= deadline:
                raise ToolError(f"lock {path.name} still held after {timeout:.0f} s by: {held}") from None
            time.sleep(poll)
    with os.fdopen(fd, "w") as f:
        f.write(f"{owner} {datetime.now():%Y-%m-%d %H:%M} {what}\n")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
