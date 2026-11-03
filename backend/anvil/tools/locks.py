"""Shared-laptop resource locks (C:\\Dev\\comms\\locks\\<name>.lock), same protocol the agents use by hand."""

import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from anvil.tools.errors import ToolError

LOCKS_DIR = Path(os.environ.get("ANVIL_LOCKS_DIR", r"C:\Dev\comms\locks"))


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
            if time.monotonic() >= deadline:
                held = path.read_text(errors="replace").strip() if path.exists() else "?"
                raise ToolError(f"lock {path.name} still held after {timeout:.0f} s by: {held}") from None
            time.sleep(poll)
    with os.fdopen(fd, "w") as f:
        f.write(f"{owner} {datetime.now():%Y-%m-%d %H:%M} {what}\n")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
