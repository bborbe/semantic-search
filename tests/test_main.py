"""Tests for __main__ entry points."""

import re
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


def test_main_cli_version_long_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`semantic-search --version` prints the version and exits 0."""
    monkeypatch.setattr(sys, "argv", ["semantic-search", "--version"])
    from semantic_search.__main__ import main_cli

    with pytest.raises(SystemExit) as exc_info:
        main_cli()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert re.match(r"^semantic-search v[0-9]+\.[0-9]+", captured.out), (
        f"expected version on stdout, got: {captured.out!r}"
    )


def test_main_cli_version_short_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`semantic-search -V` prints the version and exits 0."""
    monkeypatch.setattr(sys, "argv", ["semantic-search", "-V"])
    from semantic_search.__main__ import main_cli

    with pytest.raises(SystemExit) as exc_info:
        main_cli()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert re.match(r"^semantic-search v[0-9]+\.[0-9]+", captured.out), (
        f"expected version on stdout, got: {captured.out!r}"
    )


def test_main_version_long_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`semantic-search-mcp --version` prints the version and exits 0."""
    monkeypatch.setattr(sys, "argv", ["semantic-search-mcp", "--version"])
    from semantic_search.__main__ import main

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert re.match(r"^semantic-search-mcp v[0-9]+\.[0-9]+", captured.out), (
        f"expected version on stdout, got: {captured.out!r}"
    )


def test_main_version_short_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`semantic-search-mcp -V` prints the version and exits 0."""
    monkeypatch.setattr(sys, "argv", ["semantic-search-mcp", "-V"])
    from semantic_search.__main__ import main

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert re.match(r"^semantic-search-mcp v[0-9]+\.[0-9]+", captured.out), (
        f"expected version on stdout, got: {captured.out!r}"
    )
