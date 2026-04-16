"""Tests for VaultWatcher."""

from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np


class TestVaultWatcher:
    """Tests for VaultWatcher."""

    def test_watches_all_vault_paths(self, multi_vaults: list[Path]) -> None:
        """Test watcher schedules observer for each vault path."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.Observer") as mock_observer_cls:
                mock_observer = Mock()
                mock_observer_cls.return_value = mock_observer

                from semantic_search.indexer import VaultIndexer, VaultWatcher

                paths = [str(v) for v in multi_vaults]
                indexer = VaultIndexer(paths)
                watcher = VaultWatcher(indexer)

                watcher.start(background=True)

                # Should schedule observer for each vault
                assert mock_observer.schedule.call_count == 2

                watcher.stop()


class TestVaultEventHandlerDebounce:
    """Tests for _VaultEventHandler debouncing."""

    def _make_event(self, path: str, is_directory: bool = False) -> Mock:
        event = Mock()
        event.src_path = path
        event.is_directory = is_directory
        return event

    def test_modified_event_schedules_flush(self, temp_vault: Path) -> None:
        """Test that on_modified records pending path and schedules a timer."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer = Mock()
                mock_timer_cls.return_value = mock_timer

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = self._make_event("/vault/note.md")
                handler.on_modified(event)

                assert "/vault/note.md" in handler._pending
                mock_timer_cls.assert_called_once()
                mock_timer.start.assert_called_once()

    def test_created_event_schedules_flush(self, temp_vault: Path) -> None:
        """Test that on_created records pending path and schedules a timer."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer = Mock()
                mock_timer_cls.return_value = mock_timer

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = self._make_event("/vault/new-note.md")
                handler.on_created(event)

                assert "/vault/new-note.md" in handler._pending
                mock_timer_cls.assert_called_once()
                mock_timer.start.assert_called_once()

    def test_deleted_event_schedules_flush(self, temp_vault: Path) -> None:
        """Test that on_deleted records pending delete and schedules a timer."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer = Mock()
                mock_timer_cls.return_value = mock_timer

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = self._make_event("/vault/gone.md")
                handler.on_deleted(event)

                assert "/vault/gone.md" in handler._pending_deletes
                mock_timer_cls.assert_called_once()
                mock_timer.start.assert_called_once()

    def test_multiple_events_cancel_previous_timer(self, temp_vault: Path) -> None:
        """Test that rapid events cancel and reschedule the timer."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer = Mock()
                mock_timer_cls.return_value = mock_timer

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                # Fire three events rapidly
                for i in range(3):
                    event = self._make_event(f"/vault/note{i}.md")
                    handler.on_modified(event)

                # Timer should have been created 3 times and cancelled 2 times
                assert mock_timer_cls.call_count == 3
                assert mock_timer.cancel.call_count == 2

    def test_directory_events_ignored(self, temp_vault: Path) -> None:
        """Test that directory events do not schedule a flush."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                dir_event = self._make_event("/vault/subdir", is_directory=True)
                handler.on_modified(dir_event)
                handler.on_created(dir_event)
                handler.on_deleted(dir_event)

                mock_timer_cls.assert_not_called()

    def test_flush_calls_incremental_methods_and_clears_pending(self, temp_vault: Path) -> None:
        """Flush must route pending adds to add_file_to_index and pending deletes
        to remove_file_from_index — NEVER to rebuild_index (that would cause the
        runaway rebuild loop this fix targets).
        """
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer, _VaultEventHandler

            indexer = VaultIndexer(str(temp_vault))
            handler = _VaultEventHandler(indexer)

            handler._pending["/vault/a.md"] = 1.0
            handler._pending_deletes.add("/vault/b.md")

            add_calls: list[str] = []
            remove_calls: list[str] = []
            rebuild_calls: list[int] = []

            indexer.add_file_to_index = lambda p: add_calls.append(str(p))  # type: ignore[assignment,method-assign]
            indexer.remove_file_from_index = lambda p: remove_calls.append(str(p))  # type: ignore[assignment,method-assign]
            indexer.rebuild_index = lambda: rebuild_calls.append(1)  # type: ignore[method-assign]

            handler._flush()

            assert add_calls == ["/vault/a.md"]
            assert remove_calls == ["/vault/b.md"]
            assert rebuild_calls == []  # flush must NEVER rebuild
            assert len(handler._pending) == 0
            assert len(handler._pending_deletes) == 0

    def test_flush_delete_calls_remove_not_add(self, temp_vault: Path) -> None:
        """on_deleted → _flush must call remove_file_from_index, not add_file_to_index."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer, _VaultEventHandler

            indexer = VaultIndexer(str(temp_vault))
            handler = _VaultEventHandler(indexer)

            add_calls: list[str] = []
            remove_calls: list[str] = []
            indexer.add_file_to_index = lambda p: add_calls.append(str(p))  # type: ignore[assignment,method-assign]
            indexer.remove_file_from_index = lambda p: remove_calls.append(str(p))  # type: ignore[assignment,method-assign]

            handler._pending_deletes.add("/vault/gone.md")
            handler._flush()

            assert remove_calls == ["/vault/gone.md"]
            assert add_calls == []


class TestVaultEventHandlerFiltering:
    """Tests for _is_indexable_event filtering."""

    def _make_event(self, path: str, is_directory: bool = False) -> Mock:
        event = Mock()
        event.src_path = path
        event.is_directory = is_directory
        return event

    def test_non_md_file_is_ignored(self, temp_vault: Path) -> None:
        """Events for .txt / .log / no-extension files do not schedule a flush."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                for path in ["/vault/a.txt", "/vault/b.log", "/vault/c"]:
                    handler.on_modified(self._make_event(path))
                    handler.on_created(self._make_event(path))
                    handler.on_deleted(self._make_event(path))

                mock_timer_cls.assert_not_called()
                assert len(handler._pending) == 0
                assert len(handler._pending_deletes) == 0

    def test_dotfile_segment_is_ignored(self, temp_vault: Path) -> None:
        """Paths with any segment starting with '.' are skipped.

        Covers .git/, .obsidian/, .semantic-search/, .DS_Store, and nested cases.
        """
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                ignored_paths = [
                    "/vault/.git/index",
                    "/vault/.git/objects/abc.md",
                    "/vault/.obsidian/workspace.json",
                    "/vault/.obsidian/plugins/foo/main.md",
                    "/vault/.semantic-search/vector_index.faiss",
                    "/vault/.DS_Store",
                    "/vault/sub/.hidden/note.md",
                ]
                for path in ignored_paths:
                    handler.on_modified(self._make_event(path))

                mock_timer_cls.assert_not_called()

    def test_plain_md_file_is_indexed(self, temp_vault: Path) -> None:
        """A plain .md path under a non-dot directory does trigger a flush."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer = Mock()
                mock_timer_cls.return_value = mock_timer

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                handler.on_modified(self._make_event("/vault/sub/note.md"))

                assert "/vault/sub/note.md" in handler._pending
                mock_timer_cls.assert_called_once()
