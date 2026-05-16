"""Tests for semantic_search.logging_setup."""

import logging

import pytest

from semantic_search.logging_setup import configure_logging


class TestConfigureLoggingStream:
    """configure_logging must route log records to stderr, not stdout.

    The stdio MCP transport (`semantic-search-mcp serve`) uses stdout for
    JSON-RPC framing. Any log line on stdout corrupts the protocol channel
    and breaks the client connection. This test pins the stream choice.
    """

    def test_logger_writes_to_stderr_not_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        """logger.error must appear on stderr and must NOT appear on stdout."""
        # basicConfig is idempotent: clear any handlers a prior test installed,
        # otherwise our configure_logging call is a no-op and the assertion
        # below would pass for the wrong reason.
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)

        configure_logging("INFO")

        logger = logging.getLogger("semantic_search.test_logging_setup")
        logger.error("test-msg-12345")

        # Flush all handlers so capsys sees the bytes.
        for handler in logging.getLogger().handlers:
            handler.flush()

        captured = capsys.readouterr()
        assert "test-msg-12345" in captured.err, (
            "logger output must be on stderr to avoid corrupting the "
            "stdio MCP JSON-RPC channel on stdout"
        )
        assert "test-msg-12345" not in captured.out, (
            "logger output leaked to stdout — would corrupt the stdio MCP "
            "protocol channel during `semantic-search-mcp serve`"
        )
