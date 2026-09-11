"""Pytest fixtures for testing."""

from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _isolated_indexer_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the indexer's user_cache_dir into a per-test tmp dir.

    Without this, VaultIndexer writes its FAISS index and meta to the host's
    persistent user cache (~/Library/Caches/semantic-search/<hash>). Stale
    entries from earlier runs can collide with a fresh test's content_hash
    and be loaded as "0 entries", causing _path_to_idx lookups to KeyError.
    """
    cache_root = tmp_path / "indexer-cache"
    cache_root.mkdir()
    monkeypatch.setattr(
        "semantic_search.indexer.user_cache_dir",
        lambda *args, **kwargs: str(cache_root),
    )


@pytest.fixture(autouse=True)
def _reset_http_transport() -> None:
    """Clear the process-wide HTTP-transport marker before each test.

    pytest collects every module into a single process: the modules that
    construct an app (test_http_server, test_mcp_scoping) set the marker via
    build_app, and both sort before test_server, whose direct tool calls must
    keep seeing the stdio transport. This seam exists for no other reason.
    """
    from semantic_search.scopes import reset_http_transport

    reset_http_transport()


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


# Vocabulary the deterministic encoder counts. Tests pick document/query text
# from these tokens so relative similarity is expressible ("these documents are
# closer to this query than those").
_PROBE_TOKENS = (
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
)


class _DeterministicSentenceTransformer:
    """Deterministic stand-in for SentenceTransformer.

    Encodes each text as an L2-normalised count vector over the fixed
    _PROBE_TOKENS vocabulary, so identical text maps to an identical vector
    across app instances and index builds, and relative similarity between
    documents and a query is expressible. The constant-vector convention of
    mock_sentence_transformer cannot express ordering differences, so the
    scoping probes (and prompts 3 and 4) use this fixture instead.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def get_sentence_embedding_dimension(self) -> int:
        return len(_PROBE_TOKENS)

    def encode(
        self,
        texts: list[str],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        vectors = np.zeros((len(texts), len(_PROBE_TOKENS)), dtype="float64")
        for row, text in enumerate(texts):
            lowered = text.lower()
            for column, token in enumerate(_PROBE_TOKENS):
                vectors[row, column] = lowered.count(token)
        if normalize_embeddings:
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            vectors = vectors / norms
        return vectors


@pytest.fixture
def deterministic_sentence_transformer() -> type:
    """Return the deterministic SentenceTransformer stand-in class.

    Pass it straight to `patch("semantic_search.indexer.SentenceTransformer",
    deterministic_sentence_transformer)` so the indexer constructs its model
    from it and embeds probe text into deterministic, L2-normalised count
    vectors. The vector for a given text is identical across app instances
    and index builds.
    """
    return _DeterministicSentenceTransformer
