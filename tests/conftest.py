"""Pytest fixtures for testing."""

from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def temp_vault(tmp_path: Path) -> Path:
    """Create a temporary vault directory with test markdown files."""
    vault = tmp_path / "vault"
    vault.mkdir()

    # Create test markdown file
    (vault / "test-note.md").write_text("""---
title: Test Note
tags: [testing, example]
---
# Test Note

This is a test note for semantic search.
""")
    return vault


@pytest.fixture
def multi_vaults(tmp_path: Path) -> list[Path]:
    """Create multiple temporary vault directories."""
    vaults = []
    for i in range(2):
        vault = tmp_path / f"vault{i}"
        vault.mkdir()
        (vault / f"note{i}.md").write_text(f"""---
title: Note {i}
---
# Note {i}

Content for vault {i}.
""")
        vaults.append(vault)
    return vaults


@pytest.fixture
def mock_sentence_transformer() -> Mock:
    """Create a mock SentenceTransformer."""
    mock = Mock()
    mock.get_sentence_embedding_dimension.return_value = 384
    mock.encode.return_value = [[0.1] * 384]
    return mock
