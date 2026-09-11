"""Tests for the factory's declared-root precedence and reset support."""

from pathlib import Path
from unittest.mock import patch

from semantic_search import factory


def test_declared_roots_win_over_caller_list(tmp_path: Path) -> None:
    """After declare_index_roots([A]), create_indexer([B]) builds over A —
    the declared set wins over the caller's list."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    factory.reset()
    try:
        factory.declare_index_roots([str(root_a)])
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = [[0.1] * 384]
            indexer = factory.create_indexer([str(root_b)])
        assert indexer.vault_paths == [root_a]
    finally:
        factory.reset()


def test_no_declaration_uses_caller_list(tmp_path: Path) -> None:
    """With no declaration, create_indexer([B]) builds over B as before.

    reset() between the cases ensures the first case's declaration and
    singleton cannot decide what the second case indexes.
    """
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    factory.reset()
    try:
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = [[0.1] * 384]
            indexer = factory.create_indexer([str(root_b)])
        assert indexer.vault_paths == [root_b]
    finally:
        factory.reset()


def test_declare_index_roots_overwrites_previous_declaration(tmp_path: Path) -> None:
    """A second declare_index_roots replaces the first, and the singleton is
    untouched (the index already exists, so the new declaration does not
    rebuild it)."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    factory.reset()
    try:
        factory.declare_index_roots([str(root_a)])
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = [[0.1] * 384]
            indexer = factory.create_indexer([str(root_b)])
        assert indexer.vault_paths == [root_a]

        # Re-declaring while the singleton exists must not rebuild the index
        factory.declare_index_roots([str(root_b)])
        assert factory.create_indexer([]) is indexer
    finally:
        factory.reset()
