from collections.abc import AsyncIterator
from typing import Any, Protocol


class RunExecutor(Protocol):
    def start(
        self, run: dict[str, Any], message: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Start a run and yield contract-shaped event payloads."""
