from collections.abc import AsyncIterator
from typing import Any, Protocol


class RunExecutor(Protocol):
    def start(
        self, run: dict[str, Any], message: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Start a run and yield contract-shaped event payloads."""

    def resume(
        self, run: dict[str, Any], decisions: list[dict[str, Any]]
    ) -> AsyncIterator[dict[str, Any]]:
        """Resume a run with decisions for its pending approval requests."""

    async def cancel(self, run: dict[str, Any]) -> None:
        """Request cancellation; an open event stream reports the terminal status."""
