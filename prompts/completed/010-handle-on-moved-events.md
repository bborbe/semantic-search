---
status: completed
summary: Implemented on_moved in _VaultEventHandler with _is_path_indexable helper so atomic-replace writes (Obsidian, obsidian-git) correctly keep files in the index via the debounced pending queues.
container: semantic-search-010-handle-on-moved-events
dark-factory-version: v0.132.0
created: "2026-04-24T11:06:46Z"
queued: "2026-04-24T11:12:04Z"
started: "2026-04-24T11:14:03Z"
completed: "2026-04-24T11:17:23Z"
---

<summary>
- The file watcher correctly handles file renames (move events), not only create / modify / delete
- Editing a file in Obsidian (which writes via temp-file + atomic-replace) keeps the file in the search index instead of silently dropping it
- The Personal-vault decay bug — every Obsidian save removes a file from the index without re-adding it — stops happening
- Behavior is symmetric: a rename from an indexable path to a non-indexable path removes the file; a rename to an indexable path adds it; a rename between two indexable paths does both
- Existing watcher tests (debounce, dotfile filtering, non-md filtering) are unaffected
- Restart-then-search recovers the missing entries on first launchd reload after the fix
</summary>

<objective>
Implement `on_moved` in `_VaultEventHandler` so that file renames (`FileSystemMovedEvent`) are routed through the same `_pending` add queue and `_pending_deletes` queue as create / modify / delete events. After this prompt, an Obsidian save (which on macOS arrives as `delete original + rename temp_to_real`) leaves the renamed file present in the index, restoring incremental indexing for vaults written by tools that use atomic-replace.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.14+, `uv`, strict mypy, src/ layout, dark-factory workflow). Note the project mandate: no direct coding — changes go through dark-factory.

Read these files in full before making any changes:

- `src/semantic_search/indexer.py` — the only production file changed. Specifically:
  - `_VaultEventHandler` class (line 469) — implements `on_modified`, `on_created`, `on_deleted`, but NOT `on_moved`. The base `FileSystemEventHandler.on_moved()` is a no-op, so move events are dropped today.
  - `_is_indexable_event` static helper (line 488) — applies the `.md` + dotfile-segment filter to `event.src_path` only.
  - `on_modified` / `on_created` / `on_deleted` (lines 528-547) — pattern to mirror.
  - `_flush` (line 503) — already processes `_pending` adds and `_pending_deletes` correctly; no change needed there.
- `tests/test_watcher.py` — existing class `TestVaultEventHandlerFiltering` for filter-coverage tests, and `TestVaultEventHandlerDebounce` for flush-routing tests. New tests go in these classes.
- `tests/conftest.py` — `temp_vault` fixture pattern (single vault with one `test-note.md`).
- Imports in `indexer.py` already include `from watchdog.events import FileSystemEvent, FileSystemEventHandler` (line 19). Watchdog's `FileMovedEvent` (the concrete event class delivered to `on_moved`) is a subclass of `FileSystemEvent` with an additional `dest_path` attribute. We will type the handler parameter as `FileSystemEvent` to match the existing handler signatures and access `dest_path` defensively.

**Background — why this bug matters (verbatim from the debug task):**

`_VaultEventHandler` only handles `on_modified`, `on_created`, `on_deleted` — NOT `on_moved`. Obsidian (and obsidian-git autocommit) writes files using an atomic-replace pattern:

1. Write content to `.tempfile.md` → `on_created` is rejected by the dotfile filter (`.tempfile` starts with `.`) ✅
2. Unlink original `file.md` → `on_deleted` removes the entry from the index ✅
3. Rename `.tempfile.md` → `file.md` → `on_moved` is silently dropped ❌

Net result: every Obsidian save removes the file from the index without re-adding it. Over time the index decays. Vaults written by simple tools (which use plain MODIFY events) work correctly; vaults written via atomic-replace (Obsidian, obsidian-git) decay silently.

**Watchdog event-shape facts:**

- `FileMovedEvent.src_path` — original path (the `.tempfile.md`)
- `FileMovedEvent.dest_path` — final path (the real `.md`)
- `event.is_directory` — True for directory renames (skip)
- The base class `FileSystemEvent` does not declare `dest_path`. Use `getattr(event, "dest_path", None)` for type-safe access from the existing `FileSystemEvent` parameter type. Strict mypy will accept this without an `isinstance(event, FileMovedEvent)` import.

**Symmetry rules for `on_moved`:**

| src indexable? | dest indexable? | Action |
|----------------|-----------------|--------|
| no | no | nothing |
| no | yes | add `dest_path` to `_pending` (treat as create) |
| yes | no | add `src_path` to `_pending_deletes` (treat as delete — the indexed file moved out of the watched scope) |
| yes | yes | add `src_path` to deletes AND `dest_path` to pending — `_flush` processes deletes first, then adds |

The atomic-replace case (`.tempfile.md` → `file.md`) is row 2: src is non-indexable (dotfile), dest is indexable, → add dest. The on-disk index already lost the file via the prior `on_deleted`; the new add re-establishes it.
</context>

<requirements>

## 1. Add `on_moved` handler in `_VaultEventHandler`

In `src/semantic_search/indexer.py`, inside the `_VaultEventHandler` class (line 469), add a new method directly after the existing `on_deleted` method (line 542):

```python
def on_moved(self, event: FileSystemEvent) -> None:
    """Handle file rename / move.

    Watchdog delivers ``FileMovedEvent`` to this method. The base class
    ``FileSystemEventHandler.on_moved`` is a no-op, so without this override
    atomic-replace writes (Obsidian, obsidian-git) silently drop their
    indexable destinations and the index decays.

    Routes the event through the same _pending / _pending_deletes queues
    as create / delete so the existing _flush logic handles the work.
    """
    if event.is_directory:
        return

    src_path = str(event.src_path)
    dest_path_attr = getattr(event, "dest_path", None)
    dest_path = str(dest_path_attr) if dest_path_attr is not None else None

    src_indexable = self._is_path_indexable(src_path)
    dest_indexable = dest_path is not None and self._is_path_indexable(dest_path)

    if not src_indexable and not dest_indexable:
        return

    with self._lock:
        if src_indexable:
            self._pending_deletes.add(src_path)
        if dest_indexable and dest_path is not None:
            self._pending[dest_path] = time.time()
        self._schedule_flush()
```

## 2. Extract path-only indexability check

The existing `_is_indexable_event` helper (line 488) only inspects `event.src_path`. The new `on_moved` handler needs the same filter applied to `dest_path` too. Refactor to share the path predicate without breaking the existing event-based call sites.

Inside `_VaultEventHandler`, directly above `_is_indexable_event` (line 488), add:

```python
@staticmethod
def _is_path_indexable(path: str) -> bool:
    """Return True iff this path should trigger an incremental update.

    Rejects:
    - paths that do not end with .md
    - paths containing any dotfile segment (.git, .obsidian, .semantic-search,
      .DS_Store, .tempfile, etc.)
    """
    p = Path(path)
    if p.suffix != ".md":
        return False
    return not any(part.startswith(".") for part in p.parts)
```

Then rewrite `_is_indexable_event` to delegate to the new helper:

```python
@staticmethod
def _is_indexable_event(event: FileSystemEvent) -> bool:
    """Return True iff this event should trigger an incremental update.

    Rejects:
    - directory events
    - paths that do not end with .md
    - paths containing any dotfile segment (.git, .obsidian, .semantic-search,
      .DS_Store, etc.)
    """
    if event.is_directory:
        return False
    return _VaultEventHandler._is_path_indexable(str(event.src_path))
```

The behavior of `_is_indexable_event` is unchanged. Its three existing callers (`on_modified`, `on_created`, `on_deleted`) continue to work without modification.

## 3. Add `TestVaultEventHandlerMoves` class to `tests/test_watcher.py`

Place this new class alongside `TestVaultEventHandlerFiltering` and `TestVaultEventHandlerDebounce` in `tests/test_watcher.py`. Use the existing `temp_vault` fixture and the existing patching style (`patch("semantic_search.indexer.SentenceTransformer")`, `patch("semantic_search.indexer.threading.Timer")`). The `Mock` import already exists in this file from the other test classes.

```python
class TestVaultEventHandlerMoves:
    """Tests for on_moved (rename / atomic-replace) handling.

    Obsidian and obsidian-git write files via temp-file + rename. Without
    on_moved support the index silently decays — the destination .md file
    never gets re-indexed.
    """

    def _make_move_event(
        self, src_path: str, dest_path: str, is_directory: bool = False
    ) -> Mock:
        event = Mock()
        event.src_path = src_path
        event.dest_path = dest_path
        event.is_directory = is_directory
        return event

    def test_atomic_replace_dotfile_to_real_indexes_dest(
        self, temp_vault: Path
    ) -> None:
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

    def test_rename_real_to_real_deletes_src_and_indexes_dest(
        self, temp_vault: Path
    ) -> None:
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

    def test_rename_real_to_dotfile_only_deletes_src(
        self, temp_vault: Path
    ) -> None:
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

    def test_rename_dotfile_to_dotfile_is_ignored(
        self, temp_vault: Path
    ) -> None:
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

    def test_rename_non_md_files_is_ignored(
        self, temp_vault: Path
    ) -> None:
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

    def test_move_event_without_dest_path_treated_as_delete(
        self, temp_vault: Path
    ) -> None:
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
```

## 4. Add a sanity test that `_flush` correctly processes a move's combined queues

Append this test to the existing `TestVaultEventHandlerDebounce` class in `tests/test_watcher.py` (alongside `test_flush_calls_incremental_methods_and_clears_pending`):

```python
def test_flush_after_move_event_calls_remove_then_add(
    self, temp_vault: Path
) -> None:
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
```

## 5. Update `CHANGELOG.md` — add `## Unreleased` section

The current `CHANGELOG.md` jumps directly from the top header to `## v0.8.3`. There is no `## Unreleased` section. Create one above `## v0.8.3` (after the introductory `All notable changes…` line) containing:

```markdown
## Unreleased

- fix: Implement on_moved in _VaultEventHandler so atomic-replace writes (Obsidian, obsidian-git) keep files in the index. Without this, every Obsidian save silently dropped the file from the index because the rename phase was unhandled.
- fix: Extract _is_path_indexable helper so the .md + dotfile-segment filter applies symmetrically to both source and destination paths in move events.
```

Preserve the existing `## v0.8.3` block and everything below it unchanged.

## 6. Strict mypy compliance

- The new `on_moved` method has the same `(self, event: FileSystemEvent) -> None` signature as the sibling handlers — strict mypy is satisfied without importing `FileMovedEvent`.
- The new `_is_path_indexable` static helper has full annotations (`(path: str) -> bool`).
- The `getattr(event, "dest_path", None)` access pattern avoids the typing problem of needing `isinstance(event, FileMovedEvent)` discrimination inside a `FileSystemEvent`-typed parameter.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass. Do not modify any existing test body, only add new ones (Sections 3 and 4).
- Do NOT change the public signatures of `search`, `find_duplicates`, `add_file_to_index`, `remove_file_from_index`, or the CLI / REST / MCP surface.
- Do NOT change `src/semantic_search/factory.py`.
- Do NOT introduce new top-level dependencies. `watchdog.events.FileMovedEvent` is intentionally NOT imported — `getattr` on the existing `FileSystemEvent` parameter type is sufficient.
- Do NOT alter the debounce delay (`_VaultEventHandler.DEBOUNCE_DELAY = 2.0`).
- Do NOT change `_flush` — it already processes deletes before adds, which is exactly what move events need.
- Do NOT change the JSON format of the on-disk index, the meta_file, or any REST response shapes.
- Follow strict mypy typing. All new functions have full annotations.
- Repo-relative paths only in code and tests — no absolute paths, no home-relative paths.
- The new `on_moved` MUST go through `_pending` / `_pending_deletes` + `_schedule_flush` — never call `add_file_to_index` / `remove_file_from_index` directly. The whole point of the debounced queue is to avoid embedding work on the watcher thread.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Specifically confirm:
- All seven new tests in `TestVaultEventHandlerMoves` pass.
- The new `test_flush_after_move_event_calls_remove_then_add` in `TestVaultEventHandlerDebounce` passes.
- All existing tests in `TestVaultEventHandlerFiltering`, `TestVaultEventHandlerDebounce`, `TestVaultIndexerIncremental`, `TestVaultIndexerInit`, `TestVaultIndexerRebuild`, `TestVaultIndexerFindDuplicates`, `TestVaultIndexerInlineTags`, and `TestVaultWatcher` still pass.
- `make test` (full suite) passes.
- `mypy` reports no new errors.

Also confirm by `grep -n "on_moved" src/semantic_search/indexer.py` that the new handler exists. Must show one line for the method definition.

Confirm by `grep -n "_is_path_indexable" src/semantic_search/indexer.py` that both the new helper and the refactored caller exist. Must show two lines.
</verification>
