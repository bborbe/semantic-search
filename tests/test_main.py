"""Tests for __main__ entry points."""

import sys
from unittest.mock import patch

import pytest


def test_main_cli_rejects_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    """main_cli() must exit with code 1 when given 'serve' command."""
    monkeypatch.setattr(sys, "argv", ["semantic-search", "serve"])
    from semantic_search.__main__ import main_cli

    with pytest.raises(SystemExit) as exc_info:
        main_cli()
    assert exc_info.value.code == 1


def test_main_cli_rejects_unknown_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """main_cli() must exit with code 1 for unknown commands."""
    monkeypatch.setattr(sys, "argv", ["semantic-search", "unknown"])
    from semantic_search.__main__ import main_cli

    with pytest.raises(SystemExit) as exc_info:
        main_cli()
    assert exc_info.value.code == 1


def test_main_cli_rejects_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """main_cli() must exit with code 1 when no subcommand is given."""
    monkeypatch.setattr(sys, "argv", ["semantic-search"])
    from semantic_search.__main__ import main_cli

    with pytest.raises(SystemExit) as exc_info:
        main_cli()
    assert exc_info.value.code == 1


def test_serve_invokes_stdio_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """`serve` subcommand must call the stdio MCP run function with no args.

    `_serve()` does `from .server import run as run_mcp` at call time, so we
    patch `semantic_search.server.run` (the SOURCE attribute) rather than any
    name in `__main__` — patching in `__main__` would be clobbered by the
    local re-import inside `_serve()`.
    """
    import semantic_search.__main__ as main_module

    monkeypatch.setattr(sys, "argv", ["semantic-search-mcp", "serve"])

    with patch("semantic_search.server.run") as mock_run:
        main_module.main()

    mock_run.assert_called_once_with()
