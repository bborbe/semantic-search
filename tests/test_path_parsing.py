"""Tests for CONTENT_PATH parsing."""

import os

import pytest


def test_single_path_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test single path is parsed correctly."""
    monkeypatch.setenv("CONTENT_PATH", "/path/to/vault")

    raw_paths = os.environ.get("CONTENT_PATH", "./content")
    paths = [p.strip() for p in raw_paths.split(",") if p.strip()]

    assert paths == ["/path/to/vault"]


def test_multiple_paths_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test comma-separated paths are parsed correctly."""
    monkeypatch.setenv("CONTENT_PATH", "/vault1,/vault2,/vault3")

    raw_paths = os.environ.get("CONTENT_PATH", "./content")
    paths = [p.strip() for p in raw_paths.split(",") if p.strip()]

    assert paths == ["/vault1", "/vault2", "/vault3"]


def test_paths_with_whitespace_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test whitespace around paths is trimmed."""
    monkeypatch.setenv("CONTENT_PATH", " /vault1 , /vault2 , /vault3 ")

    raw_paths = os.environ.get("CONTENT_PATH", "./content")
    paths = [p.strip() for p in raw_paths.split(",") if p.strip()]

    assert paths == ["/vault1", "/vault2", "/vault3"]


def test_empty_segments_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test empty segments from double commas are ignored."""
    monkeypatch.setenv("CONTENT_PATH", "/vault1,,/vault2,")

    raw_paths = os.environ.get("CONTENT_PATH", "./content")
    paths = [p.strip() for p in raw_paths.split(",") if p.strip()]

    assert paths == ["/vault1", "/vault2"]


def test_default_path_when_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test default path is used when CONTENT_PATH not set."""
    monkeypatch.delenv("CONTENT_PATH", raising=False)

    raw_paths = os.environ.get("CONTENT_PATH", "./content")
    paths = [p.strip() for p in raw_paths.split(",") if p.strip()]

    assert paths == ["./content"]
