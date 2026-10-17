"""Start the API server: `uv run python -m app.api [--host H] [--port P] [--log-level L]`.

psycopg's async mode cannot run on the Windows proactor loop, which uvicorn picks by
default on Windows; the server then starts but fails on its first database call. This
entry point always uses a selector loop. With the uvicorn command line use
`--loop app.api.__main__:selector_loop_factory --factory app.api.app:create_app`.
"""

import argparse
import asyncio
import sys

import uvicorn

from app.config import get_settings
from app.logging_setup import configure_logging


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """uvicorn `loop` setting (a custom value is used as the loop factory itself): a
    selector event loop on every platform."""
    return asyncio.SelectorEventLoop()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="DD Monitor HTTP API")
    parser.add_argument("--host", default=settings.api_host)
    parser.add_argument("--port", type=int, default=settings.api_port)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    uvicorn.run(
        "app.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        loop="app.api.__main__:selector_loop_factory",
        log_config=None,  # keep the app's logging setup
        access_log=False,  # the app logs one line per request itself
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
