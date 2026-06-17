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

    def test_flush_after_move_event_calls_remove_then_add(self, temp_vault: Path) -> None:
        """End-to-end: an on_moved event between two indexable paths must result in
        _flush calling remove_file_from_index for src then add_file_to_index for dest.
        """
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            from semantic_search.indexer import VaultIndexer, _VaultEventHandler

            indexer = VaultIndexer(str(temp_vault))
            handler = _VaultEventHandler(indexer)

            # Simulate the queues populated by a successful on_moved
            handler._pending_deletes.add("/vault/old.md")
            handler._pending["/vault/new.md"] = 1.0

            call_order: list[str] = []
            indexer.add_file_to_index = lambda p: call_order.append(  # type: ignore[assignment,method-assign]
                f"add:{p}"
            )
            indexer.remove_file_from_index = lambda p: call_order.append(  # type: ignore[assignment,method-assign]
                f"remove:{p}"
            )

            handler._flush()

            assert call_order == ["remove:/vault/old.md", "add:/vault/new.md"]
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


class TestVaultEventHandlerMoves:
    """Tests for on_moved (rename / atomic-replace) handling.

    Obsidian and obsidian-git write files via temp-file + rename. Without
    on_moved support the index silently decays — the destination .md file
    never gets re-indexed.
    """

    def _make_move_event(self, src_path: str, dest_path: str, is_directory: bool = False) -> Mock:
        event = Mock()
        event.src_path = src_path
        event.dest_path = dest_path
        event.is_directory = is_directory
        return event

    def test_atomic_replace_dotfile_to_real_indexes_dest(self, temp_vault: Path) -> None:
        """The Obsidian / obsidian-git case: rename `.tempfile.md` → `file.md`
        must add `file.md` to _pending.
        """
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = self._make_move_event(
                    src_path="/vault/.note.md.tmp",
                    dest_path="/vault/note.md",
                )
                handler.on_moved(event)

                assert "/vault/note.md" in handler._pending
                assert "/vault/.note.md.tmp" not in handler._pending_deletes
                mock_timer_cls.assert_called_once()

    def test_rename_real_to_real_deletes_src_and_indexes_dest(self, temp_vault: Path) -> None:
        """Renaming `a.md` → `b.md` must delete `a.md` and add `b.md`.

        _flush already processes deletes before adds.
        """
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = self._make_move_event(
                    src_path="/vault/a.md",
                    dest_path="/vault/b.md",
                )
                handler.on_moved(event)

                assert "/vault/a.md" in handler._pending_deletes
                assert "/vault/b.md" in handler._pending
                mock_timer_cls.assert_called_once()

    def test_rename_real_to_dotfile_only_deletes_src(self, temp_vault: Path) -> None:
        """Renaming `note.md` → `.note.md.tmp` (rare backup pattern) must delete
        `note.md` and NOT add the dotfile destination.
        """
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = self._make_move_event(
                    src_path="/vault/note.md",
                    dest_path="/vault/.note.md.tmp",
                )
                handler.on_moved(event)

                assert "/vault/note.md" in handler._pending_deletes
                assert "/vault/.note.md.tmp" not in handler._pending
                mock_timer_cls.assert_called_once()

    def test_rename_dotfile_to_dotfile_is_ignored(self, temp_vault: Path) -> None:
        """Both endpoints non-indexable (e.g. `.git/index.lock` → `.git/index`)
        must not schedule a flush.
        """
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = self._make_move_event(
                    src_path="/vault/.git/index.lock",
                    dest_path="/vault/.git/index",
                )
                handler.on_moved(event)

                assert len(handler._pending) == 0
                assert len(handler._pending_deletes) == 0
                mock_timer_cls.assert_not_called()

    def test_rename_non_md_files_is_ignored(self, temp_vault: Path) -> None:
        """Both endpoints non-md (e.g. `a.txt` → `b.txt`) must not schedule a flush."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = self._make_move_event(
                    src_path="/vault/a.txt",
                    dest_path="/vault/b.txt",
                )
                handler.on_moved(event)

                assert len(handler._pending) == 0
                assert len(handler._pending_deletes) == 0
                mock_timer_cls.assert_not_called()

    def test_directory_move_is_ignored(self, temp_vault: Path) -> None:
        """Directory rename events (is_directory=True) must short-circuit."""
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = self._make_move_event(
                    src_path="/vault/notes",
                    dest_path="/vault/notes-renamed",
                    is_directory=True,
                )
                handler.on_moved(event)

                assert len(handler._pending) == 0
                assert len(handler._pending_deletes) == 0
                mock_timer_cls.assert_not_called()

    def test_move_event_without_dest_path_treated_as_delete(self, temp_vault: Path) -> None:
        """If dest_path is missing (defensive — should never happen with watchdog),
        treat the event as a delete of the indexable src_path.
        """
        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(temp_vault))
                handler = _VaultEventHandler(indexer)

                event = Mock()
                event.src_path = "/vault/note.md"
                event.is_directory = False
                # Explicitly remove dest_path so getattr returns None
                del event.dest_path

                handler.on_moved(event)

                assert "/vault/note.md" in handler._pending_deletes
                assert len(handler._pending) == 0
                mock_timer_cls.assert_called_once()


class TestVaultIgnoreGate:
    """Tests for .semanticignore integration in _VaultEventHandler."""

    def _make_event(self, path: str, is_directory: bool = False) -> Mock:
        event = Mock()
        event.src_path = path
        event.is_directory = is_directory
        return event

    def _make_move_event(self, src_path: str, dest_path: str, is_directory: bool = False) -> Mock:
        event = Mock()
        event.src_path = src_path
        event.dest_path = dest_path
        event.is_directory = is_directory
        return event

    def test_ignored_path_not_queued_on_created(self, tmp_path: Path) -> None:
        """AC5: on_created for an ignored path must NOT add to _pending."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("archive/\n")
        archive = vault / "archive"
        archive.mkdir()
        (archive / "old.md").write_text("# Old note\n")

        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(vault))
                handler = _VaultEventHandler(indexer)

                handler.on_created(self._make_event(str(archive / "old.md")))

                assert str(archive / "old.md") not in handler._pending

    def test_ignored_path_not_queued_on_modified(self, tmp_path: Path) -> None:
        """AC5: on_modified for an ignored path must NOT add to _pending."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("archive/\n")
        archive = vault / "archive"
        archive.mkdir()
        (archive / "old.md").write_text("# Old note\n")

        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(vault))
                handler = _VaultEventHandler(indexer)

                handler.on_modified(self._make_event(str(archive / "old.md")))

                assert str(archive / "old.md") not in handler._pending

    def test_non_ignored_path_queued(self, tmp_path: Path) -> None:
        """Non-ignored path IS added to _pending on created/modified events."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("archive/\n")
        (vault / "kept.md").write_text("# Kept note\n")

        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(vault))
                handler = _VaultEventHandler(indexer)

                handler.on_created(self._make_event(str(vault / "kept.md")))

                assert str(vault / "kept.md") in handler._pending

    def test_runtime_reload_on_semanticignore_modified(self, tmp_path: Path) -> None:
        """AC6: modifying .semanticignore reloads rules; subsequent events honor new patterns."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("")
        (vault / "new-secret.md").write_text("# Secret\n")

        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(vault))
                handler = _VaultEventHandler(indexer)

                # Initially new-secret.md is NOT ignored
                assert handler._is_ignored_path(str(vault / "new-secret.md")) is False

                # Update .semanticignore on disk and trigger a reload
                (vault / ".semanticignore").write_text("new-secret.md\n")
                result = handler._maybe_reload_ignore(str(vault / ".semanticignore"))

                assert result is True
                # After reload the pattern is active
                assert handler._is_ignored_path(str(vault / "new-secret.md")) is True

                # A created event for the now-ignored file must not reach _pending
                handler.on_created(self._make_event(str(vault / "new-secret.md")))
                assert str(vault / "new-secret.md") not in handler._pending

    def test_on_moved_ignored_destination_not_queued(self, tmp_path: Path) -> None:
        """on_moved to an ignored destination must not add dest to _pending."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("archive/\n")
        (vault / "archive").mkdir()
        (vault / "kept.md").write_text("# Kept\n")

        dest = str(vault / "archive" / "moved.md")

        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(vault))
                handler = _VaultEventHandler(indexer)

                handler.on_moved(
                    self._make_move_event(src_path=str(vault / "kept.md"), dest_path=dest)
                )

                assert dest not in handler._pending
                # Source must still be queued for deletion
                assert str(vault / "kept.md") in handler._pending_deletes

    def test_on_deleted_semanticignore_reloads_to_accept_all(self, tmp_path: Path) -> None:
        """Deleting .semanticignore reloads to accept-all; ignored paths become indexable."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("secret.md\n")
        (vault / "secret.md").write_text("# Secret\n")

        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(vault))
                handler = _VaultEventHandler(indexer)

                assert handler._is_ignored_path(str(vault / "secret.md")) is True

                # Delete the file on disk, then trigger a reload
                (vault / ".semanticignore").unlink()
                result = handler._maybe_reload_ignore(str(vault / ".semanticignore"))

                assert result is True
                # With no .semanticignore the filter falls back to accept-all
                assert handler._is_ignored_path(str(vault / "secret.md")) is False

    def test_outside_vault_path_never_ignored(self, tmp_path: Path) -> None:
        """Paths outside all vault roots are never reported as ignored."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("secret.md\n")

        with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
            mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

            with patch("semantic_search.indexer.threading.Timer") as mock_timer_cls:
                mock_timer_cls.return_value = Mock()

                from semantic_search.indexer import VaultIndexer, _VaultEventHandler

                indexer = VaultIndexer(str(vault))
                handler = _VaultEventHandler(indexer)

                assert handler._is_ignored_path("/tmp/somewhere/else/x.md") is False
