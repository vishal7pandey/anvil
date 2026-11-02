class ToolError(RuntimeError):
    """An outward action failed or was refused; the message is safe to show the model (no secrets)."""
