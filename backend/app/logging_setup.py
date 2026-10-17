"""Logging configuration shared by the CLI and (later) the API."""

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Third-party libraries are chatty at INFO/DEBUG.
    for noisy in ("pdfminer", "httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
