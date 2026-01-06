"""Tests for VaultWatcher."""

from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest


class TestVaultWatcher:
    """Tests for VaultWatcher."""

    def test_watches_all_vault_paths(self, multi_vaults: list[Path]) -> None:
        """Test watcher schedules observer for each vault path."""
        with patch("semantic_search_mcp.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search_mcp.indexer.Observer") as mock_observer_cls:
                mock_observer = Mock()
                mock_observer_cls.return_value = mock_observer

                from semantic_search_mcp.indexer import VaultIndexer, VaultWatcher

                paths = [str(v) for v in multi_vaults]
                indexer = VaultIndexer(paths)
                watcher = VaultWatcher(indexer)

                watcher.start(background=True)

                # Should schedule observer for each vault
                assert mock_observer.schedule.call_count == 2

                watcher.stop()
