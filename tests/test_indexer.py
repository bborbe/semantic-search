"""Tests for VaultIndexer."""

from pathlib import Path
from unittest.mock import patch

import numpy as np


class TestVaultIndexerInit:
    """Tests for VaultIndexer initialization."""

    def test_accepts_single_string_path(self, temp_vault: Path) -> None:
        """Test backward compatibility with single string path."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            indexer = VaultIndexer(str(temp_vault))

            assert len(indexer.vault_paths) == 1
            assert indexer.vault_paths[0] == temp_vault

    def test_accepts_list_of_paths(self, multi_vaults: list[Path]) -> None:
        """Test multiple paths as list."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            paths = [str(v) for v in multi_vaults]
            indexer = VaultIndexer(paths)

            assert len(indexer.vault_paths) == 2

    def test_creates_unique_index_dir_per_path_combination(self, multi_vaults: list[Path]) -> None:
        """Test different path combinations get different index directories."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            indexer1 = VaultIndexer([str(multi_vaults[0])])
            indexer2 = VaultIndexer([str(v) for v in multi_vaults])

            # Different paths should have different content hashes
            assert indexer1.index_dir.parent.name != indexer2.index_dir.parent.name


class TestVaultIndexerRebuild:
    """Tests for index rebuilding."""

    def test_indexes_files_from_all_vaults(self, multi_vaults: list[Path]) -> None:
        """Test rebuild_index scans all configured directories."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            paths = [str(v) for v in multi_vaults]
            indexer = VaultIndexer(paths)

            # Should have indexed files from both vaults
            assert len(indexer.meta) == 2


class TestVaultIndexerFindDuplicates:
    """Tests for duplicate detection."""

    def test_resolves_relative_path_against_all_vaults(self, multi_vaults: list[Path]) -> None:
        """Test relative paths are checked against all vault directories."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            paths = [str(v) for v in multi_vaults]
            indexer = VaultIndexer(paths)

            # Relative path should be found in second vault
            result = indexer.find_duplicates("note1.md")

            # Should not return error
            assert not isinstance(result, dict) or "error" not in result
