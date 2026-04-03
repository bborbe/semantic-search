---
status: draft
---

<summary>
- File modifications no longer append duplicate entries to the FAISS index
- Modified files replace their existing index entry instead of accumulating
- File content is no longer stored in index metadata, only file paths
- File watcher events are debounced to prevent rapid-fire re-indexing
- Deleted file handling no longer triggers a full index rebuild
</summary>

<objective>
Fix unbounded memory growth in the indexer. Currently every file modification appends a new FAISS vector and stores full file content in metadata without removing the old entry. Combined with 5+ watched directories and frequent file saves, this causes the process to consume multiple GB of RAM. After this fix, memory usage should stabilize regardless of how many file modifications occur.
</objective>

<context>
Read CLAUDE.md for project conventions.
Read `src/semantic_search_mcp/indexer.py` — find `VaultIndexer`, `VaultWatcher`, and `_VaultEventHandler` classes.

The indexer has four problems causing memory growth:

1. `add_file_to_index` (line ~192) appends a new vector + metadata entry on every file modify. The old entry for the same path is never removed. The FAISS index and `self.meta` dict grow without bound.

2. `self.meta` stores full file content (`{"path": ..., "content": content}`) for every entry. With duplicates from problem 1, this accumulates fast.

3. `on_deleted` triggers `rebuild_index()` for every single file deletion — expensive and creates temporary memory spike where old + new index coexist.

4. `_VaultEventHandler` has no debouncing. Obsidian autosave and git operations fire dozens of events per second, each triggering `model.encode()` and `save_index()`.
</context>

<requirements>
1. In `VaultIndexer`, add a reverse lookup dict `self._path_to_idx: dict[str, int]` mapping file path to its current index position. Initialize it in `__init__` and populate it in `_load_index` and `rebuild_index`.

2. Change `add_file_to_index` to check `self._path_to_idx` for an existing entry. If the file already exists in the index, do a full `rebuild_index()` instead of appending a duplicate. This is simpler than trying to remove a single vector from FAISS (which doesn't support efficient single-vector removal for `IndexFlatIP`).

3. Remove `"content": content` from metadata storage. Change `self.meta[str(idx)]` to only store `{"path": str(file_path)}`. Update `rebuild_index` and `add_file_to_index` accordingly. The `find_duplicates` method reads content directly from the file path, so it does not need stored content.

4. In `_VaultEventHandler`, add debouncing:
   - Add a `self._pending: dict[str, float]` dict mapping file paths to their last event timestamp
   - Add a `self._pending_deletes: set[str]` for deleted file paths
   - Add a `self._debounce_timer: threading.Timer | None` for scheduling the flush
   - On `on_modified` and `on_created`: record the path + current time in `_pending`, then schedule a flush after 2 seconds (cancel any existing timer first)
   - On `on_deleted`: add path to `self._pending_deletes` and schedule the same 2-second flush
   - The flush method calls `self.indexer.rebuild_index()` once for ALL pending changes (modifications + deletes combined). This avoids calling `add_file_to_index` per file which would trigger N separate rebuilds. Clear both `_pending` and `_pending_deletes` after the single rebuild.

5. Update existing tests in `tests/test_indexer.py` and `tests/test_watcher.py` to reflect:
   - Metadata no longer contains `"content"` key
   - Watcher events are debounced (may need to adjust timing or mock the timer)

6. Add a test that verifies modifying the same file twice does not create duplicate index entries.
</requirements>

<constraints>
- Do NOT commit — dark-factory handles git
- Existing tests must still pass
- Do not change the search or find_duplicates public API signatures
- Do not change the embedding model or weighting strategy
- Keep the FAISS IndexFlatIP index type (do not switch to a different index)
- The debounce delay should be a class constant, not hardcoded inline
</constraints>

<verification>
Run `make precommit` -- must pass.
</verification>
