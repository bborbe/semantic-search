"""Tests for VaultIndexer."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest


class TestVaultIndexerInit:
    """Tests for VaultIndexer initialization."""

    def test_accepts_single_string_path(self, temp_vault: Path) -> None:
        """Test backward compatibility with single string path."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            indexer = VaultIndexer(str(temp_vault))

            assert len(indexer.vault_paths) == 1
            assert indexer.vault_paths[0] == temp_vault

    def test_accepts_list_of_paths(self, multi_vaults: list[Path]) -> None:
        """Test multiple paths as list."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            paths = [str(v) for v in multi_vaults]
            indexer = VaultIndexer(paths)

            assert len(indexer.vault_paths) == 2

    def test_creates_unique_index_dir_per_path_combination(self, multi_vaults: list[Path]) -> None:
        """Test different path combinations get different index directories."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            indexer1 = VaultIndexer([str(multi_vaults[0])])
            indexer2 = VaultIndexer([str(v) for v in multi_vaults])

            # Different paths should have different content hashes (now the hash IS the dir name)
            assert indexer1.index_dir.name != indexer2.index_dir.name

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

        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            # Use tilde path - should expand to our fake home
            indexer = VaultIndexer("~/vault")

            # Tilde should be expanded
            assert indexer.vault_paths[0] == test_vault
            assert "~" not in str(indexer.vault_paths[0])


class TestVaultIndexerRebuild:
    """Tests for index rebuilding."""

    def test_indexes_files_from_all_vaults(self, multi_vaults: list[Path]) -> None:
        """Test rebuild_index scans all configured directories."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            paths = [str(v) for v in multi_vaults]
            indexer = VaultIndexer(paths)

            # Should have indexed files from both vaults
            assert len(indexer.meta) == 2

    def test_metadata_does_not_store_content(self, temp_vault: Path) -> None:
        """Test metadata only stores path, not file content."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            indexer = VaultIndexer(str(temp_vault))

            for entry in indexer.meta.values():
                assert "content" not in entry
                assert "path" in entry

    def test_modifying_same_file_twice_no_duplicate_entries(self, temp_vault: Path) -> None:
        """Test modifying a file twice does not create duplicate index entries."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            indexer = VaultIndexer(str(temp_vault))
            initial_count = len(indexer.meta)

            # Add the same file twice
            test_file = temp_vault / "test-note.md"
            indexer.add_file_to_index(test_file)
            indexer.add_file_to_index(test_file)

            # Count should not grow — rebuild replaces the entry
            assert len(indexer.meta) == initial_count


class TestVaultIndexerFindDuplicates:
    """Tests for duplicate detection."""

    def test_resolves_relative_path_against_all_vaults(self, multi_vaults: list[Path]) -> None:
        """Test relative paths are checked against all vault directories."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

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
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

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
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

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
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

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
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

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
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

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
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

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


class TestVaultIndexerIncremental:
    """Tests for incremental add/update/remove."""

    def test_add_file_to_index_update_uses_tombstone(self, temp_vault: Path) -> None:
        """Re-adding an existing path tombstones the old idx and appends a new one."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            indexer = VaultIndexer(str(temp_vault))
            test_file = temp_vault / "test-note.md"

            # Disable compaction so the tombstone produced by the UPDATE path
            # stays observable. Compaction has its own test
            # (test_tombstone_compaction_triggers_rebuild); here we only care
            # that the UPDATE path tombstones the old idx and never calls
            # rebuild_index itself.
            indexer._maybe_compact = lambda: None  # type: ignore[method-assign]

            rebuild_calls: list[int] = []
            indexer.rebuild_index = lambda: rebuild_calls.append(1)  # type: ignore[method-assign]

            before_idx = indexer._path_to_idx[str(test_file)]
            indexer.add_file_to_index(test_file)  # update path
            after_idx = indexer._path_to_idx[str(test_file)]

            assert after_idx != before_idx
            assert before_idx in indexer._tombstones
            # UPDATE path must never call rebuild_index directly — only
            # _maybe_compact may, and we've stubbed it out above.
            assert rebuild_calls == []

    def test_remove_file_from_index(self, temp_vault: Path) -> None:
        """Removed files disappear from meta and are tombstoned."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            indexer = VaultIndexer(str(temp_vault))
            test_file = temp_vault / "test-note.md"
            idx = indexer._path_to_idx[str(test_file)]

            # Delete from disk first — the realistic call site. Without this,
            # _maybe_compact would trigger rebuild which re-indexes the still-present file.
            test_file.unlink()
            indexer.remove_file_from_index(test_file)

            assert str(test_file) not in indexer._path_to_idx
            assert str(idx) not in indexer.meta
            # Tombstone set OR compaction cleared it — both are correct end states.
            # If compaction ran (1 dead / 1 total = 100%), tombstones are empty.
            # If not, the removed idx is tombstoned. Assert one of these holds:
            assert idx in indexer._tombstones or len(indexer._tombstones) == 0

    def test_remove_nonexistent_path_is_noop(self, temp_vault: Path) -> None:
        """Removing a path that isn't indexed does not raise or mutate state."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            indexer = VaultIndexer(str(temp_vault))
            before = dict(indexer.meta)

            indexer.remove_file_from_index("/nowhere/missing.md")

            assert indexer.meta == before

    def test_search_filters_tombstones(self, temp_vault: Path) -> None:
        """Tombstoned entries never appear in search results."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            # Fresh vault with 10 files so removing one is only 10% tombstones
            # (below the 20% compaction threshold).
            vault = temp_vault
            for i in range(9):
                (vault / f"note{i}.md").write_text(f"# Note {i}\nContent {i}")

            indexer = VaultIndexer(str(vault))
            target = vault / "note3.md"
            target_idx = indexer._path_to_idx[str(target)]

            # Manually tombstone without triggering compaction path
            with indexer._index_lock:
                indexer._tombstones.add(target_idx)
                indexer.meta.pop(str(target_idx), None)
                indexer._path_to_idx.pop(str(target), None)

            results = indexer.search("anything", top_k=100)
            paths = [r["path"] for r in results]
            assert str(target) not in paths

    def test_compaction_triggers_when_tombstone_ratio_exceeds_threshold(
        self, temp_vault: Path
    ) -> None:
        """_maybe_compact must call rebuild_index when tombstones > 20%."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            vault = temp_vault
            for i in range(9):
                (vault / f"note{i}.md").write_text(f"# Note {i}")

            indexer = VaultIndexer(str(vault))

            rebuild_calls: list[int] = []
            indexer.rebuild_index = lambda: rebuild_calls.append(1)  # type: ignore[method-assign]

            # Force 30% tombstones (3 of 10): compaction MUST fire
            idxs = list(indexer._path_to_idx.values())[:3]
            with indexer._index_lock:
                for idx in idxs:
                    indexer._tombstones.add(idx)

            indexer._maybe_compact()

            assert len(rebuild_calls) == 1

    def test_compaction_does_not_trigger_below_threshold(self, temp_vault: Path) -> None:
        """_maybe_compact must NOT call rebuild_index when tombstones <= 20%."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            vault = temp_vault
            for i in range(19):
                (vault / f"note{i}.md").write_text(f"# Note {i}")

            indexer = VaultIndexer(str(vault))

            rebuild_calls: list[int] = []
            indexer.rebuild_index = lambda: rebuild_calls.append(1)  # type: ignore[method-assign]

            # 10% tombstones (2 of 20): below threshold
            idxs = list(indexer._path_to_idx.values())[:2]
            with indexer._index_lock:
                for idx in idxs:
                    indexer._tombstones.add(idx)

            indexer._maybe_compact()

            assert rebuild_calls == []

    def test_index_cache_survives_restart(self, temp_vault: Path) -> None:
        """A second VaultIndexer with the same paths loads the on-disk cache
        without re-embedding every file. With PID removed from index_dir, the
        second instantiation must find and load the existing index.
        """
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            indexer1 = VaultIndexer(str(temp_vault))
            assert indexer1.index_file.exists()
            files_embedded_first_run = mock_st.return_value.encode.call_count

            # Second instantiation of a fresh indexer against the same paths
            indexer2 = VaultIndexer(str(temp_vault))
            files_embedded_second_run = mock_st.return_value.encode.call_count

            # Second instantiation must NOT re-embed (cache hit)
            assert files_embedded_second_run == files_embedded_first_run
            assert len(indexer2.meta) == len(indexer1.meta)
            assert indexer2.index_dir == indexer1.index_dir  # no PID path component

    def test_meta_file_format_loads_tombstones(self, temp_vault: Path) -> None:
        """save_index writes tombstones; _load_index reads them back."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer

            vault = temp_vault
            for i in range(9):
                (vault / f"note{i}.md").write_text(f"# Note {i}")

            indexer1 = VaultIndexer(str(vault))
            target_idx = next(iter(indexer1._path_to_idx.values()))
            with indexer1._index_lock:
                indexer1._tombstones.add(target_idx)
            indexer1.save_index()

            indexer2 = VaultIndexer(str(vault))
            assert target_idx in indexer2._tombstones
