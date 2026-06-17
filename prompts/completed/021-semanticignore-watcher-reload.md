---
status: completed
spec: [003-semanticignore-support]
summary: 'Wired .semanticignore ignore gate into _VaultEventHandler: ignored paths are dropped in on_created/on_modified/on_moved, and .semanticignore changes trigger atomic per-vault reload via _maybe_reload_ignore'
container: semantic-search-semanticignore-exec-021-semanticignore-watcher-reload
dark-factory-version: v0.181.0
created: "2026-06-17T00:00:00Z"
queued: "2026-06-17T10:26:28Z"
started: "2026-06-17T10:40:40Z"
completed: "2026-06-17T10:46:23Z"
branch: dark-factory/semanticignore-support
---

## Summary

- The file-watcher now respects `.semanticignore`: a create/modify/move event for an ignored path no longer adds that path to the index
- Editing `.semanticignore` while the watcher is running reloads that vault's rules, so subsequent events use the new patterns
- Reload is per-vault and thread-safe, so a file event arriving mid-reload sees a consistent ruleset
- No behavior change for vaults without a `.semanticignore` file

## Objective

Wire the ignore filter into the watchdog event handler so ignored paths are not indexed on file events, and add runtime reload: when `.semanticignore` changes, the owning vault's `VaultIgnore` reloads atomically and later events honor the new rules.

## Context

Read these files before making changes:

- `/workspace/CLAUDE.md` — project conventions
- `/workspace/docs/dod.md` — Definition of Done
- `/workspace/src/semantic_search/ignore.py` — `VaultIgnore` with `is_ignored(path)` and `reload()` (prompt 1). The `reload()` method already rebuilds the matcher atomically. If this file does NOT exist, STOP and report `Status: failed` with `".semanticignore ignore module not yet deployed (prompt 1)"`.
- `/workspace/src/semantic_search/indexer.py`:
  - `VaultIndexer` now has `self._ignores: dict[Path, VaultIgnore]` and `self.vault_paths: list[Path]` (added in prompt 2). If `self._ignores` does NOT exist on `VaultIndexer`, STOP and report `Status: failed` with `"indexer ignore wiring not yet deployed (prompt 2)"`.
  - `VaultWatcher.__init__(self, indexer: VaultIndexer)` (line 519); `start` creates one `_VaultEventHandler(self.indexer)` shared across all scheduled vault roots (lines 526-529).
  - `_VaultEventHandler.__init__(self, indexer: VaultIndexer)` (line 555) — holds `self.indexer`, `self._pending: dict[str, float]`, `self._pending_deletes: set[str]`, `self._lock`.
  - `_VaultEventHandler._is_path_indexable(path: str) -> bool` (lines 570-582) — static; rejects non-`.md` and any dotfile path segment. Because `.semanticignore` starts with `.`, this method ALREADY rejects it for add/update routing.
  - `_VaultEventHandler._is_indexable_event(event) -> bool` (lines 584-596).
  - `on_modified`/`on_created` (lines 623-635) add `str(event.src_path)` to `self._pending` then `_schedule_flush()`.
  - `on_moved` (lines 644-673) routes dest path into `self._pending` via `_is_path_indexable`.
  - `_flush` (lines 598-621) calls `self.indexer.remove_file_from_index` then `self.indexer.add_file_to_index`.
- `/workspace/tests/test_watcher.py` — handler test patterns: `_make_event` builds a `Mock` with `.src_path` and `.is_directory`; tests patch `semantic_search.indexer.SentenceTransformer` and `semantic_search.indexer.threading.Timer`.
- `/workspace/tests/conftest.py` — `temp_vault`, `multi_vaults`.

## Requirements

1. **Add an ignore gate to the event handler.** Add a private method to `_VaultEventHandler`:

   ```python
   def _is_ignored_path(self, path: str) -> bool:
       """Return True iff the path is excluded by the owning vault's .semanticignore.

       Finds the vault root that owns the path (via VaultIndexer.vault_paths) and
       delegates to the indexer's ignore check. Paths outside all vault roots are
       treated as NOT ignored.
       """
   ```

   Implementation: iterate `self.indexer.vault_paths`; find the owning root where `Path(path).resolve().is_relative_to(vp.resolve())`; if found, return `self.indexer._is_ignored(owning_root, Path(path))` (the helper added in prompt 2). If no owning root, return `False`.

2. **Gate the add/update routes.** In `on_modified` and `on_created`, after the existing `if not self._is_indexable_event(event): return` guard, add: `if self._is_ignored_path(str(event.src_path)): return`. The path must NOT be added to `self._pending` when ignored (AC5). Do NOT change the delete routing in `on_deleted` — removing an ignored path from the index is harmless (it would not be present) and we never want a tombstone to be skipped.

3. **Gate `on_moved` destination.** In `on_moved`, when computing `dest_indexable`, also exclude ignored destinations: a destination should only be queued into `self._pending` if it is indexable AND `not self._is_ignored_path(dest_path)`. Leave the source-delete routing as-is.

4. **Runtime reload of `.semanticignore`.** The handler must detect changes to a `.semanticignore` file and reload the owning vault's rules. Because `_is_path_indexable` and `_is_indexable_event` reject dotfiles (so `.semanticignore` events never reach the add/update path), add an explicit early check at the TOP of `on_modified`, `on_created`, and `on_deleted` (before the `_is_indexable_event` guard):

   ```python
   if self._maybe_reload_ignore(str(event.src_path)):
       return
   ```

   And add the method:

   ```python
   def _maybe_reload_ignore(self, path: str) -> bool:
       """If path is a vault's .semanticignore file, reload that vault's rules and return True.

       Returns False if the path is not a .semanticignore file, so the caller
       continues normal event handling.
       """
   ```

   Implementation:
   - If `Path(path).name != ".semanticignore"`, return `False`.
   - Determine the owning vault root from `self.indexer.vault_paths` (the root the file lives directly under; use `is_relative_to` on resolved paths, then pick the root for which the file is `vault_root / ".semanticignore"` or simply the matching root). Match the convention used in requirement 1.
   - If an owning root is found and present in `self.indexer._ignores`, call `self.indexer._ignores[owning_root].reload()`. `VaultIgnore.reload()` already swaps its matcher atomically, satisfying the per-vault atomic-swap requirement.
   - Log at INFO: `logger.info(f"reloaded .semanticignore for vault {owning_root}")` (uses the module logger in `indexer.py`).
   - Return `True` (the event was a `.semanticignore` change and is fully handled — no indexing follows).
   - On `on_deleted` of `.semanticignore`: still call `reload()`; with the file gone, `VaultIgnore` falls back to ignore-nothing, which is the correct post-deletion behavior. Return `True`.

5. **Thread safety.** `_maybe_reload_ignore` mutates only via `VaultIgnore.reload()`, which builds-then-assigns atomically, and `is_ignored` reads a single attribute. No new lock is required around the reload call itself for correctness of the swap. Do NOT introduce a global lock that would serialize unrelated vault events. (If `VaultIgnore.reload()` from prompt 1 does NOT build-then-assign atomically, treat that as a prompt-1 defect and report it in `## Improvements` — do not work around it with a coarse lock here.)

6. **Follow DoD:** docstrings/type hints on both new methods, no `print`, no bare `except Exception`.

7. **Integration tests in `/workspace/tests/test_watcher.py`** (mocked `SentenceTransformer` per the existing idiom; build a real vault on `tmp_path`). Cover:
   - **AC5** — Vault with `.semanticignore` containing `archive/` and a real file `archive/old.md`. Construct `VaultIndexer(str(vault))`, then `_VaultEventHandler(indexer)`. Fire a synthetic `on_created` (and separately `on_modified`) event whose `src_path` is `str(vault / "archive" / "old.md")`. Assert the path is NOT added to `handler._pending` (the ignore gate dropped it). Optionally also assert that after a `_flush` the path is absent from `indexer.meta`.
   - **Non-ignored still indexed** — Fire an event for `vault / "kept.md"` (not matched by patterns) and assert it IS added to `handler._pending`.
   - **AC6 (runtime reload)** — Start with `.semanticignore` empty (or containing an unrelated pattern). Construct indexer + handler. Confirm `handler._is_ignored_path(str(vault / "new-secret.md"))` is False. Then write `new-secret.md` into `.semanticignore`, fire an `on_modified` event whose `src_path` is `str(vault / ".semanticignore")` (this triggers `_maybe_reload_ignore`). Assert `_maybe_reload_ignore` returned True / the reload ran (e.g. assert via `handler._is_ignored_path(str(vault / "new-secret.md"))` now returning True). Then fire an `on_created` event for `vault / "new-secret.md"` and assert it is NOT added to `handler._pending`.
   - **on_moved ignored destination** — Fire an `on_moved` event whose `dest_path` matches an ignore pattern; assert the dest path is not queued into `handler._pending`.
   - **on_deleted of `.semanticignore` reloads to accept-all** — Vault with `.semanticignore` containing `secret.md`. Construct indexer + handler. Confirm `handler._is_ignored_path(str(vault / "secret.md"))` is True. Delete the `.semanticignore` file on disk, then fire an `on_deleted` event whose `src_path` is `str(vault / ".semanticignore")`. Assert `_maybe_reload_ignore` returned True and that `handler._is_ignored_path(str(vault / "secret.md"))` is now False (the post-deletion accept-all fallback).
   - **outside-vault path is never ignored** — Construct indexer with a single vault on `tmp_path`. Call `handler._is_ignored_path("/tmp/somewhere/else/x.md")` (or any path outside `tmp_path`) and assert it returns False. Pins the "no owning root → not ignored" branch of requirement 1.
   - To avoid the debounce timer firing real embedding work in tests, follow the existing pattern of patching `semantic_search.indexer.threading.Timer` (see `test_watcher.py` `TestVaultEventHandlerDebounce`), OR call `handler.on_modified(...)` and inspect `handler._pending` directly without flushing.

8. **Coverage:** the new gate branches, both new methods, and the reload path must be exercised; target ≥ 80% on changed code.

## Constraints

[Copied from spec — the executing agent has no memory of the spec.]

- Exclusion applies to file-watcher events (create/modify/move) — an event for an ignored path must NOT add it to the index.
- Changes to `.semanticignore` at runtime cause patterns for THAT vault to reload; subsequent events use the new rules.
- Reload must be atomic per vault (atomic swap of the compiled matcher); a file event arriving mid-reload must see a consistent ruleset.
- `.semanticignore` created/modified/deleted at runtime → reload that vault's filter; INFO log.
- Must preserve the existing hardcoded skip for paths containing `.semantic-search` and the existing dotfile rejection.
- Multiple vault roots each load their own `.semanticignore`; a pattern in vault A does not affect vault B.
- Must satisfy project DoD: docstrings, full type hints, no `print`, `make precommit` clean.
- Do NOT change `VaultIndexer` public API or the on-disk format in this prompt.
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass.

## Verification

```bash
cd /workspace && uv run pytest tests/test_watcher.py tests/test_indexer.py tests/test_ignore.py -v   # all pass, exit 0
cd /workspace && uv run pytest -q                                                                    # full suite green
cd /workspace && make precommit                                                                      # ruff + mypy strict + pytest, exit 0
```
