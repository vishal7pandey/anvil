import functools
from typing import Any, Callable


class ToolError(RuntimeError):
    """An outward action failed or was refused; the message is safe to show the model (no secrets)."""


def returning_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a model-facing tool so a ToolError goes back to the model as the tool result.

    Without this a refused or failed outward call (a bad repo name, a 404, a protected path) ends the
    whole run, after the agent has done the work. The model can usually correct the call or hand off.
    Name, docstring and annotations are kept, so the tool schema and the approval gate (by name) are
    unchanged. Other exceptions still propagate: those are our bugs, not the model's.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError as exc:
            return {"ok": False, "error": str(exc)}

    return wrapper
