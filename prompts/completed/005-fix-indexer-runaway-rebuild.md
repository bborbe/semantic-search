---
status: completed
summary: Converted VaultIndexer from full-rebuild-per-event to true incremental add/update/remove with tombstone-based logical deletes, PID-free index cache, dotfile event filtering, and self-compaction at 20% tombstone ratio.
container: semantic-search-005-fix-indexer-runaway-rebuild
dark-factory-version: v0.110.2
created: "2026-04-16T10:00:00Z"
queued: "2026-04-16T10:07:38Z"
started: "2026-04-16T10:16:53Z"
completed: "2026-04-16T10:42:07Z"
---

<summary>
- Rebuilding the full index on every file change is replaced by true incremental updates (add, update, remove single files)
- File cache survives process restart; startup loads existing index from disk and only rebuilds when no cache is present
- File watcher ignores events that should never trigger indexing (non-markdown files, dotfile directories like `.git`, `.obsidian`, `.semantic-search`)
- Watcher event handler routes modifications and deletions to per-file incremental methods, never to full rebuild
- Updates to an already-indexed file tombstone the stale entry and append a fresh one, so search results always reflect the latest content
- Deleted files can be explicitly removed from the index
- Search and duplicate-detection filter out tombstoned entries so removed or replaced files never appear in results
- Index self-compacts by rebuilding when tombstone ratio grows past a threshold
- New tests cover incremental add/update, remove, tombstone-aware search, compaction trigger, cache-survives-restart, and event-handler filtering
</summary>

<objective>
Eliminate the runaway rebuild loop in the file watcher by converting the indexer to true incremental add/update/remove operations, persisting the FAISS index across process restarts, and filtering watcher events to ignore non-markdown and dotfile paths. After this prompt, a file modification produces one embedding + one index append (not a full ~4000-file rebuild), and `.semantic-search/` writes inside watched paths no longer cascade into more rebuilds.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.14+, `uv`, strict mypy, src/ layout, dark-factory workflow). Note the project mandate: no direct coding — changes go through dark-factory.

Read these files before making changes:

- `src/semantic_search/indexer.py` — contains `VaultIndexer` (embedding + FAISS), `VaultWatcher` (watchdog wiring), `_VaultEventHandler` (debounced event callbacks). This prompt modifies all three classes.
- `src/semantic_search/factory.py` — thread-safe singleton constructor for `VaultIndexer` + `VaultWatcher`. Do not change; this prompt only changes indexer internals.
- `tests/conftest.py` — provides `temp_vault` (single vault with one `test-note.md`) and `multi_vaults` (two vaults, one file each) fixtures. Reuse these.
- `tests/test_indexer.py` — existing `unittest.mock.patch` conventions: `patch("semantic_search.indexer.SentenceTransformer")`, return 384-dim vectors via `np.array([[0.1] * 384])`. Follow this pattern for new tests.
- `tests/test_watcher.py` — existing pattern for patching `threading.Timer` via `patch("semantic_search.indexer.threading.Timer")`. The existing test `test_flush_calls_rebuild_and_clears_pending` MUST be updated in this prompt because flush no longer calls `rebuild_index` (see step 9 below).

**Root cause of the runaway loop being fixed here:**

1. `_VaultEventHandler._flush` calls `self.indexer.rebuild_index()` on every flush, re-embedding all ~4000 vault files.
2. `on_modified`/`on_created`/`on_deleted` have no event filtering, so events for `.git/`, `.obsidian/`, `.semantic-search/`, `.DS_Store`, and non-`.md` files all trigger full rebuilds.
3. `save_index` writes into `.semantic-search/` (or `/tmp/semantic-search/{hash}/{pid}/`); when the index dir lives inside a watched path, this creates its own events and loops.
4. `add_file_to_index` has an UPDATE branch that calls `rebuild_index()` — not truly incremental.
5. No `remove_file_from_index` method exists.
6. `index_dir` includes `os.getpid()`, so the on-disk cache never survives restart — every boot rebuilds from scratch even when the vault hasn't changed.

**User's architectural directive (verbatim):** "we should rebuild index on startup ... and than only add/update/remove pages." Full-vault rebuild is the ONE-TIME startup path (when no cache exists) and the compaction path (when tombstones exceed threshold). It must NOT be on the per-event path.

**Why tombstoning (not true FAISS deletion):** `faiss.IndexFlatIP` does not support efficient single-vector removal. The idiomatic pattern is "logical delete" (tombstone) + periodic compaction via full rebuild. See the similar approach already used in the codebase: `rebuild_index` constructs a new `IndexFlatIP` and swaps it under `_index_lock`.
</context>

<requirements>

1. **Remove PID from `index_dir`** so the index cache survives process restart.

   In `VaultIndexer.__init__`, change:

   ```python
   self.index_dir = (
       Path(tempfile.gettempdir()) / "semantic-search" / content_hash / str(os.getpid())
   )
   ```

   to:

   ```python
   self.index_dir = Path(tempfile.gettempdir()) / "semantic-search" / content_hash
   ```

   Remove the now-unused `import os` line IF `os` is not used elsewhere in the module. Check all usages first — `os` may be used by other code. If in doubt, leave the import. Also remove `import os` only if you are certain no other reference exists.

   Do NOT change `_load_index`'s existing load-if-present / rebuild-if-missing logic. That is the correct startup behavior (the user's directive).

2. **Add a tombstone set** on `VaultIndexer`.

   In `VaultIndexer.__init__`, after the existing `self._path_to_idx: dict[str, int] = {}` line, add:

   ```python
   self._tombstones: set[int] = set()  # logically-deleted idx positions
   ```

   Type annotation required (strict mypy). `int` (not `str`) because `meta` keys are stored as str but we compare against the FAISS row index which is an int in search results.

3. **Persist tombstones alongside `meta`** in `meta_file`.

   Change `save_index` so the JSON written to `self.meta_file` contains both `meta` and `tombstones` under named keys. Old format was a bare dict; new format is:

   ```python
   {
       "meta": self.meta,
       "tombstones": sorted(self._tombstones),  # list for JSON
   }
   ```

   Change `_load_index` accordingly. After reading the JSON, handle BOTH layouts for forwards/backwards compatibility:

   - If the top-level value is a dict containing the key `"meta"`, treat it as the new format: `self.meta = data["meta"]`, `self._tombstones = set(data.get("tombstones", []))`.
   - Otherwise (old format — a bare `{idx_str: {"path": ...}}` dict), treat the whole value as `self.meta` and initialize `self._tombstones = set()`.

   Log at INFO level the loaded counts: `f"[Indexer] Loaded index with {len(self.meta)} entries, {len(self._tombstones)} tombstones"`.

4. **Add incremental update path in `add_file_to_index`.**

   Locate `add_file_to_index(self, file_path: str | Path) -> None`. Replace the current UPDATE branch:

   ```python
   # If file already exists in index, do a full rebuild to replace the entry
   if str(file_path) in self._path_to_idx:
       self.rebuild_index()
       return
   ```

   with tombstone-and-append semantics. The full method should read:

   ```python
   def add_file_to_index(self, file_path: str | Path) -> None:
       """Add a new file or update an existing one in the index.

       On update: tombstone the old idx, append a new embedding, update lookups.
       Never triggers a full rebuild on the hot path.
       """
       file_path = Path(file_path)
       if not file_path.exists() or file_path.suffix != ".md":
           return

       content = self._read_file(file_path)
       if content is None:
           return

       weighted_text = self._prepare_text_for_embedding(file_path, content)
       vec = self._embed_text(weighted_text)

       path_str = str(file_path)
       with self._index_lock:
           # Tombstone the old entry if this path is already indexed
           old_idx = self._path_to_idx.get(path_str)
           if old_idx is not None:
               self._tombstones.add(old_idx)
               self.meta.pop(str(old_idx), None)

           new_idx = self.index.ntotal  # next row position before add
           self.index.add(vec)
           self.meta[str(new_idx)] = {"path": path_str}
           self._path_to_idx[path_str] = new_idx

       self.save_index()
       self._maybe_compact()
       logger.info(f"[Indexer] Indexed {file_path} (idx={new_idx})")
   ```

   Note: `self.index.ntotal` (exposed by `faiss.IndexFlatIP`) gives the number of vectors currently in the index. Using it BEFORE `self.index.add(vec)` yields the row position of the new vector.

5. **Add `remove_file_from_index` method** on `VaultIndexer`, placed directly after `add_file_to_index`:

   ```python
   def remove_file_from_index(self, file_path: str | Path) -> None:
       """Remove a file from the index by tombstoning its entry.

       No-op if the path is not currently indexed.
       """
       path_str = str(Path(file_path))
       with self._index_lock:
           old_idx = self._path_to_idx.pop(path_str, None)
           if old_idx is None:
               return
           self._tombstones.add(old_idx)
           self.meta.pop(str(old_idx), None)
       self.save_index()
       self._maybe_compact()
       logger.info(f"[Indexer] Removed {file_path} (idx={old_idx})")
   ```

6. **Add `_maybe_compact` helper** on `VaultIndexer`, placed after `remove_file_from_index`:

   ```python
   def _maybe_compact(self) -> None:
       """Rebuild the index if tombstone ratio exceeds 20%.

       Compaction drops tombstoned vectors and reclaims memory / search cost.
       Must be called without holding self._index_lock.
       """
       with self._index_lock:
           live = len(self.meta)
           dead = len(self._tombstones)
       total = live + dead
       if total == 0:
           return
       if dead > 0.2 * total:
           logger.info(
               f"[Indexer] Compacting: {dead} tombstones / {total} total "
               f"(> 20%), rebuilding index"
           )
           self.rebuild_index()
   ```

   Compaction threshold is a hardcoded `0.2`. Do NOT introduce a config knob for this — keep the change surface small.

7. **Make `search` tombstone-aware.**

   Replace the body of `search(self, query: str, top_k: int = 5)` with over-sampling + filtering:

   ```python
   def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
       """Search for related notes, skipping tombstoned entries."""
       if len(self.meta) == 0:
           return []

       vec = self._embed_text(query)
       with self._index_lock:
           # Oversample to account for tombstoned rows we will skip
           oversample = min(top_k * 4, self.index.ntotal)
           if oversample == 0:
               return []
           distances, indices = self.index.search(vec, oversample)
           meta_snapshot = dict(self.meta)
           tombstones_snapshot = set(self._tombstones)

       results: list[dict[str, Any]] = []
       for score, idx in zip(distances[0], indices[0], strict=True):
           if idx < 0:  # FAISS returns -1 for missing slots when k > ntotal
               continue
           if int(idx) in tombstones_snapshot:
               continue
           if str(idx) not in meta_snapshot:
               continue
           results.append({"path": meta_snapshot[str(idx)]["path"], "score": float(score)})
           if len(results) >= top_k:
               break
       return results
   ```

   Oversample factor of 4 is chosen to comfortably absorb up to 75% tombstones before compaction kicks in. Do NOT widen this further — compaction keeps the ratio bounded.

8. **Make `find_duplicates` tombstone-aware.**

   In `find_duplicates`, after the existing `distances, indices = self.index.search(vec, len(self.meta))` and `meta_snapshot = dict(self.meta)` lines, ALSO capture a tombstone snapshot under the same lock:

   ```python
   with self._index_lock:
       distances, indices = self.index.search(vec, self.index.ntotal)
       meta_snapshot = dict(self.meta)
       tombstones_snapshot = set(self._tombstones)
   ```

   Note: change `len(self.meta)` to `self.index.ntotal` here — `len(self.meta)` is now smaller than the FAISS vector count (tombstoned entries are removed from `meta`). We must search the full index and filter.

   In the result-building loop, add the tombstone check as an additional guard:

   ```python
   for score, idx in zip(distances[0], indices[0], strict=True):
       if idx < 0:
           continue
       if int(idx) in tombstones_snapshot:
           continue
       if (
           str(idx) in meta_snapshot
           and score > self.duplicate_threshold
           and Path(meta_snapshot[str(idx)]["path"]).resolve() != file_path.resolve()
       ):
           duplicates.append({"path": meta_snapshot[str(idx)]["path"], "score": float(score)})
   ```

9. **Reset `_tombstones` inside `rebuild_index`.**

   In `rebuild_index`, inside the `with self._index_lock:` block that swaps `self.index` / `self.meta` / `self._path_to_idx`, also clear the tombstones:

   ```python
   with self._index_lock:
       self.index = new_index
       self.meta = new_meta
       self._path_to_idx = new_path_to_idx
       self._tombstones = set()
   ```

10. **Filter events in `_VaultEventHandler`.**

    Add a static helper on the class (above `on_modified`):

    ```python
    @staticmethod
    def _is_indexable_event(event: FileSystemEvent) -> bool:
        """Return True iff this event should trigger an incremental update.

        Rejects:
        - directory events
        - paths that do not end with .md
        - paths containing any dotfile segment (.git, .obsidian, .semantic-search, .DS_Store, etc.)
        """
        if event.is_directory:
            return False
        path = Path(str(event.src_path))
        if path.suffix != ".md":
            return False
        return not any(part.startswith(".") for part in path.parts)
    ```

    Then in `on_modified`, `on_created`, `on_deleted`, replace the `if not event.is_directory:` guard with `if not self._is_indexable_event(event): return`. The bodies still update `_pending`/`_pending_deletes` and call `_schedule_flush`.

    Example for `on_modified`:

    ```python
    def on_modified(self, event: FileSystemEvent) -> None:
        if not self._is_indexable_event(event):
            return
        with self._lock:
            self._pending[str(event.src_path)] = time.time()
            self._schedule_flush()
    ```

    Apply the same pattern to `on_created` and `on_deleted`.

11. **Rewrite `_VaultEventHandler._flush` to use incremental operations.**

    Replace the current body of `_flush`:

    ```python
    def _flush(self) -> None:
        """Process all pending changes with a single rebuild."""
        with self._lock:
            self._pending.clear()
            self._pending_deletes.clear()
            self._debounce_timer = None
        logger.info("[EventHandler] Flushing pending file changes, rebuilding index...")
        self.indexer.rebuild_index()
    ```

    with per-file incremental processing:

    ```python
    def _flush(self) -> None:
        """Process all pending changes incrementally (no full rebuild)."""
        with self._lock:
            adds = list(self._pending.keys())
            deletes = list(self._pending_deletes)
            self._pending.clear()
            self._pending_deletes.clear()
            self._debounce_timer = None

        if not adds and not deletes:
            return

        logger.info(
            f"[EventHandler] Flushing {len(adds)} add/update(s), "
            f"{len(deletes)} delete(s)"
        )
        # Process deletes first so a rename (delete + create of new path) is correct
        for path in deletes:
            try:
                self.indexer.remove_file_from_index(path)
            except Exception:
                logger.exception(f"[EventHandler] Failed to remove {path}")
        for path in adds:
            try:
                self.indexer.add_file_to_index(path)
            except Exception:
                logger.exception(f"[EventHandler] Failed to index {path}")
    ```

    `_flush` MUST NOT call `self.indexer.rebuild_index()`. Compaction-triggered rebuilds happen inside the indexer via `_maybe_compact`, not from the handler.

12. **Update the existing test `test_flush_calls_rebuild_and_clears_pending` in `tests/test_watcher.py`.**

    This test currently asserts `_flush` calls `rebuild_index`. That behavior changed in step 11. Rename and rewrite:

    ```python
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
    ```

13. **Add new tests in `tests/test_indexer.py`** under a new class `TestVaultIndexerIncremental`:

    ```python
    class TestVaultIndexerIncremental:
        """Tests for incremental add/update/remove."""

        def test_add_file_to_index_update_uses_tombstone(self, temp_vault: Path) -> None:
            """Re-adding an existing path tombstones the old idx and appends a new one — no rebuild from UPDATE path."""
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

        def test_compaction_does_not_trigger_below_threshold(
            self, temp_vault: Path
        ) -> None:
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
    ```

14. **Add new tests in `tests/test_watcher.py`** under a new class `TestVaultEventHandlerFiltering`:

    ```python
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
    ```

    Also add one more test, under the existing `TestVaultEventHandlerDebounce` class, alongside `test_flush_calls_incremental_methods_and_clears_pending` from step 12:

    ```python
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
    ```

15. **Update `CHANGELOG.md`** under `## Unreleased`:
    - `fix: Convert file watcher from full index rebuild per event to true incremental add/update/remove, ending the runaway rebuild loop that re-embedded all ~4000 vault files on every save.`
    - `fix: Filter watcher events to ignore non-markdown files and any path with a dotfile segment (.git, .obsidian, .semantic-search, .DS_Store). Eliminates self-triggered rebuilds caused by save_index writes.`
    - `fix: Remove process-PID from FAISS index cache path so the embedded index survives process restart (startup now loads from disk instead of re-embedding).`
    - `feat: Tombstone-based logical delete for indexed entries; search and find_duplicates filter tombstones; index self-compacts when tombstone ratio exceeds 20%.`

16. **Strict mypy compliance.** All new methods (`remove_file_from_index`, `_maybe_compact`, `_is_indexable_event`) must have full type annotations matching the project's existing style. The new tombstone attribute must be typed `set[int]`. JSON serialization uses `sorted(self._tombstones)` to produce a deterministic `list[int]`.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass, EXCEPT `test_flush_calls_rebuild_and_clears_pending` which is replaced by `test_flush_calls_incremental_methods_and_clears_pending` in step 12 (same test class, new name reflecting the new behavior).
- Do NOT change the public signatures of `search`, `find_duplicates`, `add_file_to_index`, or the CLI / REST / MCP surface.
- Do NOT change JSON response shapes of any REST endpoint.
- Keep the FAISS `IndexFlatIP` index type — no switch to `IndexIDMap`, no switch to a different index kind.
- Compaction threshold is hardcoded `0.2`. Do not add a config knob.
- Oversample factor in `search` is hardcoded `4`. Do not add a config knob.
- Debounce delay stays `_VaultEventHandler.DEBOUNCE_DELAY = 2.0` — do not change.
- `_flush` MUST NOT call `self.indexer.rebuild_index()`. Compaction-triggered rebuilds are initiated inside the indexer via `_maybe_compact`, never from the handler path. This is load-bearing — the whole point of this prompt.
- Do not change `src/semantic_search/factory.py`.
- Do not introduce new top-level dependencies.
- Follow strict mypy typing. All new functions have full annotations.
- Repo-relative paths only in code and tests — no absolute paths, no home-relative paths.
- The meta_file JSON format change must be backwards compatible: load both the old bare-dict format AND the new `{"meta": ..., "tombstones": ...}` format. An existing on-disk cache from a prior version must still load without crashing.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Specifically confirm:
- All new tests in `TestVaultIndexerIncremental` pass.
- All new tests in `TestVaultEventHandlerFiltering` pass.
- `test_flush_calls_incremental_methods_and_clears_pending` (renamed) passes and asserts `rebuild_calls == []`.
- `test_index_cache_survives_restart` passes, proving the cache path no longer contains a PID segment.
- `test_meta_file_format_loads_tombstones` passes, proving round-trip persistence of tombstones.
- Existing tests in `TestVaultIndexerInit`, `TestVaultIndexerRebuild`, `TestVaultIndexerFindDuplicates`, `TestVaultIndexerInlineTags`, `TestVaultWatcher`, `TestVaultEventHandlerDebounce` still pass.
- `make test` (full suite) passes.
</verification>
