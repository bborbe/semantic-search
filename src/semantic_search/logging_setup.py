"""Logging configuration."""

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s",
        level=log_level,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
