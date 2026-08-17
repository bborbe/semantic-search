---
status: completed
summary: Routed the /reindex HTTP endpoint through a shared compaction guard (force_rebuild), so a manual reindex can no longer run concurrently with an automatic compaction and file changes made during a manual reindex are re-applied; busy rebuilds now return HTTP 409 REINDEX_IN_PROGRESS.
execution_id: semantic-search-exec-024-route-reindex-endpoint-through-compaction-guard
dark-factory-version: dev
created: "2026-08-17T08:35:00Z"
queued: "2026-08-17T07:25:05Z"
started: "2026-08-17T07:25:26Z"
completed: "2026-08-17T07:39:47Z"
---

# Route the /reindex endpoint through the compaction guard

<summary>
- A manually triggered reindex can no longer run at the same time as an automatic one
- Asking for a reindex while one is already running gets a clear "already in progress" answer instead of starting a second one
- File changes that land while a manual reindex is running are re-applied afterwards instead of being silently dropped
- The guarantee that only one index rebuild runs at a time now holds for every way a rebuild can be started, not just the automatic path
- Existing reindex behaviour is unchanged when nothing else is running
- No change to the success response of the reindex endpoint
</summary>

<objective>
Close the last unguarded entry point into a full index rebuild. `v0.18.1` made automatic compaction non-reentrant, but the manual `/reindex` HTTP endpoint still calls `rebuild_index` directly, so it can run concurrently with a compaction and silently discards any file change that lands while it runs. After this prompt, every rebuild trigger goes through one guard, and the objective claimed in prompt `023` — at most one `rebuild_index` at a time — is actually true.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.13+, `uv`, strict mypy, src/ layout, dark-factory workflow — no direct coding).

Read these files before making changes:

- `src/semantic_search/indexer.py` — `VaultIndexer`.
  - `_maybe_compact` — owns the guard. Checks-and-sets `self._compacting` under `self._compact_lock`, runs `rebuild_index()` outside that lock, calls `_replay_dirty_after_compact()`, clears the flag in a `finally`. This is the pattern to reuse; do not duplicate its body.
  - `_replay_dirty_after_compact` — drains `_dirty_during_compact` / `_deleted_during_compact`, re-applies deletes then adds, then drains again. Its docstring explains why it must run with `_compacting` still `True`.
  - `add_file_to_index` / `remove_file_from_index` — record into the dirty sets only while `_compacting` is `True`.
  - `rebuild_index` — the expensive full re-embed. Currently public and called from two places.
- `src/semantic_search/http_server.py` — `reindex()` handler for `GET /reindex` (~line 218). It currently calls `await run_in_threadpool(_indexer.rebuild_index)` directly. This is the bug. The handler already returns `_not_ready_response()` when the initial build has not finished; follow that existing early-return style.
- `tests/test_indexer.py` — class `TestVaultIndexerIncremental` holds the concurrency tests (`test_compaction_is_not_reentrant` uses real threads plus a `threading.Barrier` to force genuine overlap — follow that shape, not mocked call counts). Mocking convention throughout: `patch("semantic_search.indexer.SentenceTransformer")` with `np.array([[0.1] * 384])`.
- `tests/conftest.py` — `temp_vault` fixture.
- `scenarios/005-http-content-fetch-error-responses.md` — the existing nested error-response contract for this server: HTTP status plus a body shaped `{"error": {"code": "...", ...}}` with codes like `PATH_OUTSIDE_ROOTS`, `FILE_NOT_FOUND`. A new error code must match that shape.

**Why this matters (verified 2026-08-17):**

`grep -n 'rebuild_index' src/semantic_search/*.py` returns exactly one caller outside `indexer.py`: `http_server.py:230`. Because that call bypasses `_maybe_compact`, it never sets `_compacting`. Two concrete consequences:

1. A manual `/reindex` and an automatic compaction can execute simultaneously — two full re-embeds of the same corpus, which is the resource-contention failure mode `023` was written to eliminate, reached through a different trigger.
2. `_compacting` stays `False` for the whole manual rebuild, so `add_file_to_index` / `remove_file_from_index` record nothing. The post-rebuild swap then discards every file change that landed during the rebuild — the lost-update bug that `_replay_dirty_after_compact` exists to prevent, reintroduced on this path.

**Design decision — do not block the HTTP request.** A rebuild takes minutes on a large vault. Making `/reindex` wait for an in-flight compaction would hold an HTTP connection open for that long. Return a busy response instead, so the client learns the truth immediately and can retry.
</context>

<requirements>

1. **Add a public `force_rebuild()` method to `VaultIndexer`**, placed directly after `_maybe_compact`.

   Contract:
   - Signature returns `bool` — `True` if this call performed the rebuild, `False` if it was skipped because a rebuild was already in flight.
   - Acquires the guard exactly as `_maybe_compact` does: check-and-set `self._compacting` under `self._compact_lock`; return `False` immediately if it was already set.
   - Runs `self.rebuild_index()` **outside** `_compact_lock`, then `self._replay_dirty_after_compact()`, then clears `_compacting` in a `finally` — same ordering and same reasoning as `_maybe_compact`.
   - Unlike `_maybe_compact`, it does **not** consult the tombstone ratio: a manual reindex is unconditional.
   - Logs at INFO when it starts and when it is skipped, distinguishing the manual path from compaction in the message.

   Factor the shared guard so the flag/replay/`finally` sequence exists in ONE place rather than being copy-pasted between `_maybe_compact` and `force_rebuild` — e.g. a private `_run_guarded_rebuild() -> bool` that both call. Duplicating it is how the two paths drift apart again.

2. **Route the endpoint through it.** In `src/semantic_search/http_server.py`, `reindex()` must call `force_rebuild` instead of `rebuild_index`:

   - Keep the existing not-ready early return unchanged.
   - Update the handler's docstring. It currently opens `"""Handle /reindex endpoint. Blocks until reindex completes.` — after this change it blocks only on the branch that actually performs the rebuild; the busy branch returns 409 immediately. Say so.
   - `await run_in_threadpool(_indexer.force_rebuild)` and branch on the returned bool.
   - `True` → return the **existing** success body unchanged (`status`, `message`, `indexed_files`). Do not alter that shape.
   - `False` → return HTTP **409** with body `{"error": {"code": "REINDEX_IN_PROGRESS", "message": "<explain another rebuild is running; retry later>"}}`.

   **This server has two different error shapes — use the nested one.** The `/content` endpoint uses nested `{"error": {"code": ..., "message": ...}}` (`http_server.py` ~lines 164–212: `MISSING_PATH`, `PATH_OUTSIDE_ROOTS`, `FILE_NOT_FOUND`, `UNREADABLE_FILE`). Older handlers — including `reindex()`'s own `except` block and `/search` — use flat `{"error": "<string>"}`. The nested form is the newer convention and the only one with machine-readable codes, so the new response follows it. Do not copy the flat form from the adjacent lines. Leave every existing error response exactly as it is; this prompt adds one, it does not migrate the others.

3. **Keep `rebuild_index` callable but make the guarded path the obvious one.** Do not rename or remove `rebuild_index` — `_load_index` calls it for the initial build, where no guard is wanted or possible. Add a short docstring note on `rebuild_index` stating that external callers should use `force_rebuild()` and that calling it directly bypasses the concurrency guard and dirty-path replay.

4. **Tests in `tests/test_indexer.py`**, in `TestVaultIndexerIncremental`:

   - `force_rebuild` returns `True` and actually rebuilds when nothing is in flight.
   - With `_compacting` forced `True`, `force_rebuild` returns `False` and does **not** call `rebuild_index`.
   - Two threads calling `force_rebuild` concurrently result in exactly one rebuild; use the real-thread + `threading.Barrier` approach from `test_compaction_is_not_reentrant` so the overlap is genuine.
   - `_compacting` is cleared when `rebuild_index` raises inside `force_rebuild` (stub it to raise; assert a later `force_rebuild` still attempts).
   - A file added while `force_rebuild` is running is replayed afterwards — i.e. the manual path records into the dirty set and drains it, proving requirement 1's replay wiring.

5. **Test the endpoint's busy branch in `tests/test_http_server.py`** (it exists — add to it, do not create a parallel module). Assert the 409 status and that the parsed body carries `error.code == "REINDEX_IN_PROGRESS"`. This is the boundary the new value crosses: the code string is only meaningful if it survives serialization to the wire, so assert on the deserialized response, not on a constant.

6. **Add `scenarios/006-http-reindex-concurrency.md`** following the structure of `scenarios/005-http-content-fetch-error-responses.md` (frontmatter `status: active`, then `## Setup` / `## Action` / `## Expected` / `## Cleanup` checkbox sections). It must cover: a normal `/reindex` returning 200, and a second `/reindex` issued while the first is still running returning 409 with `error.code == "REINDEX_IN_PROGRESS"`. Use a free port not already used by scenarios 002/004/005 (they use 18321/18322/18323). Every assertion must be a runnable shell command whose exit code is the verdict — and verify each command's own logic, not just its intent; scenario 004 shipped an assertion that could never pass.

7. **Update `CHANGELOG.md`** under `## Unreleased` (create the section if absent — it is currently absent because `v0.18.1` consumed it):
   - `fix: route the /reindex endpoint through the compaction guard — a manual reindex can no longer run concurrently with an automatic compaction, and file changes made during a manual reindex are re-applied instead of being discarded by the post-rebuild swap.`
   - `feat: /reindex returns HTTP 409 with error code REINDEX_IN_PROGRESS when a rebuild is already in flight, instead of starting a second one.`

8. **Strict mypy compliance.** Full annotations on `force_rebuild` and any extracted helper. No new `type: ignore` in `src/` without a justification comment.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- All existing tests must pass unchanged. This prompt adds tests; it does not rename or modify existing ones.
- Do NOT change the **success** response body of `/reindex`, nor any other endpoint's response shape.
- Do NOT change the public signatures of `search`, `find_duplicates`, `add_file_to_index`, `remove_file_from_index`, `save_index`, or `_load_index`.
- Do NOT remove or rename `rebuild_index`; `_load_index` depends on it for the initial build.
- Do NOT hold `_compact_lock` while calling `rebuild_index`, `add_file_to_index`, or `remove_file_from_index` — holding it across those calls reintroduces the multi-minute stall that `023` removed. The lock guards the flag and the dirty sets only.
- Do NOT make `/reindex` wait for an in-flight rebuild. Returning busy is the chosen behaviour; blocking holds an HTTP connection for minutes.
- Compaction threshold stays a hardcoded `0.2`; `search` oversample stays `4`; `DEBOUNCE_DELAY` stays `2.0`. No new config knobs.
- Do not change `factory.py` or `ignore.py`.
- No new top-level dependencies.
- No broad `except Exception` in new code — enumerate exception types (`docs/dod.md`).
- Repo-relative paths only in code, tests, and the scenario file.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Specifically confirm:
- `grep -rn 'rebuild_index' src/semantic_search/http_server.py` returns no matches — the endpoint no longer calls it directly.
- `grep -rn '_indexer.force_rebuild' src/semantic_search/http_server.py` returns exactly one match.
- The guard sequence (set `_compacting` → rebuild → replay → clear in `finally`) appears in exactly ONE place in `src/semantic_search/indexer.py`, with both `_maybe_compact` and `force_rebuild` delegating to it.
- The new concurrency test proves exactly one rebuild across two genuinely concurrent `force_rebuild` calls.
- The endpoint test asserts HTTP 409 and `error.code == "REINDEX_IN_PROGRESS"`.
- `scenarios/006-http-reindex-concurrency.md` exists with `status: active` and Setup/Action/Expected/Cleanup sections.
- `make test` (full suite) passes.
</verification>
