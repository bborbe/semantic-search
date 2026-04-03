"""Tests for __main__ entry points."""

import sys

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
