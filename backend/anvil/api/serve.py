import asyncio
import sys

import uvicorn


def api_loop_factory(use_subprocess: bool = False) -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def main() -> None:
    uvicorn.run(
        "anvil.api.app:app",
        loop="anvil.api.serve:api_loop_factory",
    )


if __name__ == "__main__":
    main()
