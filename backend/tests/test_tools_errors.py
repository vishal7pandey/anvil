import pytest
from langchain_core.tools import tool as as_langchain_tool

from anvil.tools.errors import ToolError, returning_errors


def git_push(repo: str, branch: str) -> dict:
    """Push your branch."""
    if repo == "bad":
        raise ToolError("host git clone failed (128): Repository not found")
    if repo == "bug":
        raise KeyError("ours")
    return {"repo": repo, "branch": branch}


def test_tool_error_becomes_a_result_the_model_sees():
    wrapped = returning_errors(git_push)
    assert wrapped("rtems", "anvil/rtems-1") == {"repo": "rtems", "branch": "anvil/rtems-1"}
    assert wrapped(repo="bad", branch="anvil/rtems-1") == {
        "ok": False, "error": "host git clone failed (128): Repository not found"}


def test_other_exceptions_still_propagate():
    with pytest.raises(KeyError):
        returning_errors(git_push)("bug", "anvil/rtems-1")


def test_wrapper_keeps_the_model_facing_schema_and_name():
    # interrupt_on (the approval gate) matches tools by name; the schema must not change either.
    wrapped = returning_errors(git_push)
    assert wrapped.__name__ == "git_push" and wrapped.__doc__ == git_push.__doc__
    plain, gated = as_langchain_tool(git_push), as_langchain_tool(wrapped)
    assert gated.name == plain.name == "git_push"
    assert gated.args == plain.args
    assert gated.invoke({"repo": "bad", "branch": "anvil/x"})["ok"] is False
