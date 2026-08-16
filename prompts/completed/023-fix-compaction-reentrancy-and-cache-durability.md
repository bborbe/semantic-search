---
status: completed
summary: Made index compaction non-reentrant (atomic _compact_lock guard + dirty-path replay after rebuild) and made the index cache durable (atomic temp-file+rename save, corrupt-cache recovery that rebuilds instead of raising) in VaultIndexer, with 7 new tests and CHANGELOG entries.
execution_id: semantic-search-exec-023-fix-compaction-reentrancy-and-cache-durability
dark-factory-version: dev
created: "2026-08-17T00:20:00Z"
queued: "2026-08-16T22:18:30Z"
started: "2026-08-16T22:19:23Z"
completed: "2026-08-16T22:26:25Z"
---

# Fix compaction re-entrancy and make index cache durable

<summary>
- Only one index compaction can run at a time; further compaction requests while one is in flight are skipped instead of starting their own
- A busy vault no longer drives the service into unbounded parallel rebuilds that never finish
- Files changed while a compaction is running are re-applied afterwards, so edits made during a rebuild are not silently lost
- The on-disk index cache is written atomically, so a crash, kill, or power loss can no longer leave a half-written cache behind
- A damaged or unreadable cache no longer bricks the service on startup; it is discarded and rebuilt automatically
- Startup reports a clear log line when it recovers from a damaged cache
- Existing search, duplicate-detection, and ignore behavior is unchanged
</summary>

<objective>
Make index compaction non-reentrant and index persistence crash-safe, so that a high-churn vault can no longer drive the indexer into unbounded parallel rebuilds, and so that an interrupted write leaves a recoverable cache instead of a permanently unhealthy service. After this prompt, at most one `rebuild_index` runs at a time, an interrupted `save_index` never leaves a partial file, and a corrupt cache triggers a rebuild instead of an unhandled exception.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.13+, `uv`, strict mypy, src/ layout, dark-factory workflow — no direct coding).

Read these files before making changes:

- `src/semantic_search/indexer.py` — contains `VaultIndexer` (embedding + FAISS + persistence) and `_VaultEventHandler` (debounced watchdog callbacks). This prompt changes `VaultIndexer` only.
  - `_maybe_compact` — decides whether to compact; currently calls `rebuild_index()` unconditionally when the tombstone ratio exceeds 20%.
  - `rebuild_index` — full re-embed of every file across all vault paths; swaps `index` / `meta` / `_path_to_idx` / `_tombstones` under `_index_lock`, then calls `save_index()`.
  - `save_index` — writes `vector_index.faiss` and `index_meta.json` in place under `_index_lock`.
  - `_load_index` — loads both files when present, otherwise calls `rebuild_index()`.
  - `add_file_to_index` / `remove_file_from_index` — incremental hot path; both end with `save_index()` then `_maybe_compact()`.
- `tests/test_indexer.py` — existing conventions: `patch("semantic_search.indexer.SentenceTransformer")`, 384-dim vectors via `np.array([[0.1] * 384])`, `temp_vault` fixture from `tests/conftest.py`. Class `TestVaultIndexerIncremental` already holds the compaction-threshold tests (`test_compaction_triggers_when_tombstone_ratio_exceeds_threshold`, `test_compaction_does_not_trigger_below_threshold`) — extend that class, follow its style.
- `tests/conftest.py` — `temp_vault` (single vault, one `test-note.md`) and `multi_vaults` fixtures. Reuse them.

**Observed production failure being fixed (all three are one chain):**

1. `_VaultEventHandler._schedule_flush` creates a new `threading.Timer` thread per debounced batch. `_flush` runs on that thread and calls `add_file_to_index` / `remove_file_from_index`, each of which ends in `_maybe_compact()`.
2. `rebuild_index` on a ~14,000-file vault takes minutes and deliberately runs outside `_index_lock` ("embedding is slow"). `_tombstones` is only cleared at the very end. So every flush arriving during a rebuild still observes a >20% tombstone ratio and starts *another* full rebuild.
3. With enough concurrent rebuild threads, GIL contention means none of them finish, so the tombstone ratio can never reset and every subsequent file event adds another rebuild. Observed: 1,234 threads, 877 blocked in `take_gil`, ~1,500% CPU, 1,188 compactions started with zero completed over 3.5 days.
4. Killing the wedged process mid-`save_index` truncated `index_meta.json` (the file is opened with `"w"`, which truncates, then streamed). On restart `_load_index` called `json.load` with no error handling, raised `JSONDecodeError`, and the service reported `{"status": "error", "ready": false}` permanently — it never fell back to rebuilding.

**Why a guard and not a lock around the whole rebuild:** holding `_index_lock` across `rebuild_index` would block every search and every incremental update for the duration of a multi-minute re-embed. The correct shape is a separate non-blocking guard that makes redundant compaction requests a no-op, leaving `_index_lock` for its existing short critical sections only.

**Why dirty-path replay is needed:** `rebuild_index` builds `new_meta` / `new_path_to_idx` from a filesystem walk started before the swap, then overwrites the live dicts wholesale. Any file indexed or removed while the rebuild was in flight is discarded by that swap. Today this is masked because rebuilds never complete; once compaction is serialized and rebuilds start finishing, the lost-update becomes reachable. Fix it in the same change.
</context>

<requirements>

1. **Add compaction guard state** to `VaultIndexer.__init__`, next to the existing `self._index_lock` declaration:

   - `self._compact_lock: threading.Lock` — guards the compaction-in-progress flag and the dirty set. Distinct from `_index_lock`.
   - `self._compacting: bool` — `True` while a `rebuild_index` triggered by compaction is in flight.
   - `self._dirty_during_compact: set[str]` — paths added/updated during an in-flight compaction.
   - `self._deleted_during_compact: set[str]` — paths removed during an in-flight compaction.

   All four need explicit type annotations (strict mypy).

2. **Make `_maybe_compact` non-reentrant.** Keep the existing 20% threshold check and its INFO log line unchanged. Change the behavior so that:

   - Before starting, it checks and sets `_compacting` under `_compact_lock` in one atomic step. If `_compacting` was already `True`, log at DEBUG (`"[Indexer] Compaction already in progress, skipping"`) and return without rebuilding.
   - The rebuild runs outside `_compact_lock` (it must not serialize the incremental path behind a multi-minute operation).
   - `_compacting` is reset to `False` in a `finally` block so an exception during rebuild cannot wedge compaction off permanently.
   - After a successful rebuild, replay the recorded dirty paths (requirement 4) before clearing the flag.

   Keep the existing docstring contract note that `_maybe_compact` must be called without holding `_index_lock`.

3. **Record mutations that happen during compaction.** In `add_file_to_index` and `remove_file_from_index`, after the existing `_index_lock` critical section and before the existing `save_index()` call, record the path under `_compact_lock` when `_compacting` is `True`:

   - `add_file_to_index` → add the path to `_dirty_during_compact`, discard it from `_deleted_during_compact`.
   - `remove_file_from_index` → add the path to `_deleted_during_compact`, discard it from `_dirty_during_compact`.

   Do not record anything when `_compacting` is `False` — the sets must stay empty on the normal path.

4. **Replay dirty paths after a compaction rebuild.** Add a private helper (suggested name `_replay_dirty_after_compact`) that:

   - Drains and clears both sets under `_compact_lock`.
   - Calls `remove_file_from_index` for drained deletes, then `add_file_to_index` for drained adds (deletes first, matching the ordering rationale already documented in `_VaultEventHandler._flush`).
   - Is called from `_maybe_compact` after `rebuild_index()` returns and before `_compacting` is cleared, so replayed operations are not themselves recorded as dirty.
   - Logs at INFO when it replays anything, including both counts.

   Guard against unbounded recursion: the replayed `add_file_to_index` / `remove_file_from_index` calls will each call `_maybe_compact` again, which must short-circuit on the still-set `_compacting` flag. Confirm this ordering is correct — the flag stays `True` throughout replay.

5. **Make `save_index` atomic.** Replace the in-place writes with write-to-temp-then-rename, so a crash mid-write leaves the previous good cache intact:

   - Write both artifacts to sibling temp paths in the same directory as their targets (same filesystem is required for an atomic rename — do NOT use the system temp dir).
   - `os.replace(tmp, final)` for each, which is atomic on POSIX and Windows.
   - Write the FAISS index first, then the meta JSON, then rename in the same order, so the meta never references vectors that were not persisted.
   - Remove temp files on failure so repeated failures cannot accumulate garbage.
   - Keep the existing `_index_lock` scope and the existing `"[Indexer] Index saved"` INFO log.

   `os` is NOT currently imported in this module (only `hashlib`, `json`, `logging`, `re`, `tempfile`, `threading`, `time`). Add `import os` in the correct alphabetical position within the existing stdlib import block.

6. **Recover from a corrupt or unreadable cache in `_load_index`.** Wrap the existing load block (the `faiss.read_index` + `json.load` + `_path_to_idx` reconstruction inside `with self._index_lock:`) so that a failure to read either artifact does not propagate:

   - Catch the failure modes that a truncated or damaged cache actually produces — at minimum `json.JSONDecodeError`, `OSError`, `ValueError`, and `RuntimeError` (FAISS raises `RuntimeError` on a damaged index file). Per `docs/dod.md` no broad `except Exception` — enumerate the types.
   - On failure: log at WARNING with the offending path and the error, reset `index` / `meta` / `_path_to_idx` / `_tombstones` to the same empty state the no-cache branch uses, and fall through to `rebuild_index()`.
   - The successful path, its existing INFO log line, and the old/new meta-format compatibility handling must be unchanged.

7. **Add tests to `tests/test_indexer.py`** in the existing `TestVaultIndexerIncremental` class (compaction) and a new class `TestVaultIndexerPersistence` (atomicity + recovery). Cover:

   - **Compaction is not reentrant:** with the tombstone ratio forced above threshold, invoking `_maybe_compact` from several threads concurrently results in exactly one `rebuild_index` call. Stub `rebuild_index` with a callable that records its invocation and blocks briefly (e.g. on a `threading.Event` the test sets) so the overlap window is real rather than incidental.
   - **Flag is cleared on exception:** stub `rebuild_index` to raise; assert the exception propagates and that a subsequent `_maybe_compact` with the ratio still above threshold does attempt a rebuild (proving `_compacting` was reset).
   - **Dirty replay:** with `_compacting` forced `True`, call `add_file_to_index` and `remove_file_from_index`, assert the paths land in the correct sets; then call the replay helper and assert the corresponding indexer methods were invoked and both sets are empty.
   - **No recording on the normal path:** with `_compacting` `False`, `add_file_to_index` leaves both sets empty.
   - **Atomic save leaves no temp files:** after `save_index()`, the index dir contains exactly the two expected filenames — assert no leftover temp artifacts.
   - **Corrupt meta recovers:** build an indexer, truncate `index_meta.json` to a prefix of its content (mirroring the real truncation), construct a second `VaultIndexer` over the same vault, and assert it rebuilds successfully — `meta` is non-empty and no exception escapes.
   - **Corrupt FAISS index recovers:** same shape, but corrupt `vector_index.faiss` with junk bytes.

   Follow the existing `patch("semantic_search.indexer.SentenceTransformer")` mocking style used throughout the file. Where a test needs the tombstone ratio above threshold, follow the existing pattern in `test_compaction_triggers_when_tombstone_ratio_exceeds_threshold`.

8. **Update `CHANGELOG.md`** under the existing `## Unreleased` section, appending to the current bullet list:
   - `fix: serialize index compaction — only one rebuild runs at a time, and requests arriving during an in-flight rebuild are skipped instead of starting their own. A high-churn vault previously drove the indexer into unbounded parallel rebuilds that saturated the GIL and never completed.`
   - `fix: re-apply files added or removed during a compaction rebuild, so edits made while the index rebuilds are no longer discarded by the post-rebuild swap.`
   - `fix: write the index cache atomically (temp file + rename) so an interrupted write can no longer leave a truncated index_meta.json behind.`
   - `fix: rebuild the index instead of failing permanently when the on-disk cache is corrupt or unreadable — a damaged cache previously left the service stuck reporting ready=false.`

9. **Strict mypy compliance.** All new methods and attributes fully annotated, matching the module's existing style. No new `type: ignore` in `src/` without a justification comment.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Existing tests must all still pass, unchanged. This prompt adds tests; it does not modify or rename existing ones.
- Do NOT change the public signatures of `search`, `find_duplicates`, `add_file_to_index`, `remove_file_from_index`, `rebuild_index`, or `save_index`, and do not change the CLI / REST / MCP surface or any JSON response shape.
- Keep the FAISS `IndexFlatIP` index type — no switch to `IndexIDMap` or any other index kind.
- Compaction threshold stays a hardcoded `0.2`; the `search` oversample factor stays a hardcoded `4`; `_VaultEventHandler.DEBOUNCE_DELAY` stays `2.0`. Do not add config knobs for any of them.
- Do NOT hold `_index_lock` for the duration of `rebuild_index` — that would block search and incremental updates for minutes. Use the separate `_compact_lock` for the guard only.
- `_compact_lock` must never be held while calling `rebuild_index`, `add_file_to_index`, or `remove_file_from_index` — holding it across those calls reintroduces the stall this prompt removes.
- Do not change `src/semantic_search/factory.py`, `src/semantic_search/ignore.py`, or `_VaultEventHandler`.
- Do not introduce new top-level dependencies.
- The atomic rename must target the same directory as the final file — a cross-filesystem rename is not atomic and `os.replace` may fail across devices.
- The meta-file format stays `{"meta": ..., "tombstones": ...}` with the existing backwards-compatible bare-dict load path preserved.
- No broad `except Exception` — enumerate exception types (see `docs/dod.md`).
- Repo-relative paths only in code and tests — no absolute or home-relative paths.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Specifically confirm:
- The new non-reentrancy test asserts exactly one `rebuild_index` call across concurrent `_maybe_compact` invocations.
- The exception test proves `_compacting` is reset via `finally`.
- The dirty-replay tests prove paths are recorded only while compacting, and that replay drains both sets.
- The persistence tests prove `save_index` leaves no temp files, and that both a truncated `index_meta.json` and a corrupted `vector_index.faiss` recover by rebuilding rather than raising.
- Every pre-existing test class in `tests/test_indexer.py` (`TestVaultIndexerInit`, `TestVaultIndexerRebuild`, `TestVaultIndexerFindDuplicates`, `TestVaultIndexerInlineTags`, `TestVaultIndexerIncremental`, `TestCacheMigration`, `TestVaultIndexerGetContent`, `TestEmbedNoProgressBar`, `TestVaultIgnoreIntegration`) and `tests/test_watcher.py` (`TestVaultWatcher`, `TestVaultEventHandlerDebounce`, `TestVaultEventHandlerFiltering`, `TestVaultEventHandlerMoves`, `TestVaultIgnoreGate`) still passes unmodified — in particular `TestCacheMigration`, which exercises `_migrate_from_tempdir` alongside the `_load_index` path this prompt changes.
- `make test` (full suite) passes.
</verification>
