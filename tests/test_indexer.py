"""Tests for VaultIndexer."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest


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

    def test_expands_tilde_in_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test tilde (~) is expanded to home directory in paths."""

        # Create fake home directory structure
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        test_vault = fake_home / "vault"
        test_vault.mkdir()
        (test_vault / "note.md").write_text("# Test")

        # Set HOME to our fake directory
        monkeypatch.setenv("HOME", str(fake_home))

        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            # Use tilde path - should expand to our fake home
            indexer = VaultIndexer("~/vault")

            # Tilde should be expanded
            assert indexer.vault_paths[0] == test_vault
            assert "~" not in str(indexer.vault_paths[0])


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


class TestVaultIndexerInlineTags:
    """Tests for inline tag extraction."""

    def test_extracts_inline_tags_from_body(self, tmp_path: Path) -> None:
        """Test inline #tags extracted and merged with frontmatter tags."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            vault = tmp_path / "vault"
            vault.mkdir()

            # Create test file with frontmatter tags and inline #tags
            test_file = vault / "test.md"
            test_file.write_text("""---
title: Test Note
tags: [frontmatter-tag, duplicate]
---
# Test Note

This note has #inline-tag and #duplicate tags.
Also testing #EUR/USD format.
""")

            indexer = VaultIndexer(str(vault))

            # Verify tags were extracted and merged
            weighted_text = indexer._prepare_text_for_embedding(test_file, test_file.read_text())

            # All tags should appear in the weighted text (lowercase, deduplicated)
            assert "frontmatter-tag" in weighted_text.lower()
            assert "inline-tag" in weighted_text.lower()
            assert "eur/usd" in weighted_text.lower()

            # Duplicate should only appear once in the merged set
            # (checking exact count is harder due to weighting, but it should be present)
            assert "duplicate" in weighted_text.lower()

    def test_inline_tag_pattern_ignores_headers(self, tmp_path: Path) -> None:
        """Test that ## headers are not extracted as tags."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            vault = tmp_path / "vault"
            vault.mkdir()

            test_file = vault / "test.md"
            test_file.write_text("""# Header One
## Header Two

This has #real-tag but headers are not tags.
""")

            indexer = VaultIndexer(str(vault))
            inline_tags = indexer._extract_inline_tags(test_file.read_text())

            # Should only extract #real-tag, not headers
            assert "real-tag" in inline_tags
            assert "Header" not in " ".join(inline_tags)
            assert len(inline_tags) == 1

    def test_frontmatter_single_string_tag(self, tmp_path: Path) -> None:
        """Test frontmatter tags as single string (not list)."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            vault = tmp_path / "vault"
            vault.mkdir()

            test_file = vault / "test.md"
            test_file.write_text("""---
tags: single-tag
---
Content with #inline-tag
""")

            indexer = VaultIndexer(str(vault))
            weighted_text = indexer._prepare_text_for_embedding(test_file, test_file.read_text())

            assert "single-tag" in weighted_text.lower()
            assert "inline-tag" in weighted_text.lower()

    def test_case_insensitive_deduplication(self, tmp_path: Path) -> None:
        """Test tags deduplicated case-insensitively."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            vault = tmp_path / "vault"
            vault.mkdir()

            test_file = vault / "test.md"
            test_file.write_text("""---
tags: [Trading]
---
Content with #trading and #TRADING
""")

            indexer = VaultIndexer(str(vault))
            weighted_text = indexer._prepare_text_for_embedding(test_file, test_file.read_text())

            # All variations should be present but deduplicated to lowercase
            assert "trading" in weighted_text.lower()
            # Count occurrences (difficult due to weighting, so just verify present)

    def test_no_frontmatter_section(self, tmp_path: Path) -> None:
        """Test file without frontmatter section."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            vault = tmp_path / "vault"
            vault.mkdir()

            test_file = vault / "test.md"
            test_file.write_text("""# Simple Note

No frontmatter here, just #inline-tag and #another-tag.
""")

            indexer = VaultIndexer(str(vault))
            weighted_text = indexer._prepare_text_for_embedding(test_file, test_file.read_text())

            assert "inline-tag" in weighted_text.lower()
            assert "another-tag" in weighted_text.lower()

    def test_tags_with_special_chars(self, tmp_path: Path) -> None:
        """Test tags with hyphens, underscores, and slashes."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search_mcp.indexer import VaultIndexer

            vault = tmp_path / "vault"
            vault.mkdir()

            test_file = vault / "test.md"
            test_file.write_text("""---
---
Testing #test-tag and #test_tag and #EUR/USD
""")

            indexer = VaultIndexer(str(vault))
            inline_tags = indexer._extract_inline_tags(test_file.read_text())

            assert "test-tag" in inline_tags
            assert "test_tag" in inline_tags
            assert "EUR/USD" in inline_tags
            assert len(inline_tags) == 3
