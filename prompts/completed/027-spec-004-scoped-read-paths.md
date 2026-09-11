---
status: completed
spec: [004-per-vault-scoping]
summary: 'Scoped the search, duplicate, and content read paths: added _path_within_roots, roots params with geometric window widening on search, and per-request scope pass-through in the HTTP handlers, with deterministic-encoder scoping probes (including the fail-open filter probe) and updated call-shape assertions'
execution_id: semantic-search-exec-027-spec-004-scoped-read-paths
dark-factory-version: dev
created: "2026-09-11T11:27:52Z"
queued: "2026-09-11T12:32:41Z"
started: "2026-09-11T12:58:41Z"
completed: "2026-09-11T13:08:50Z"
---

# Scope the search, duplicate, and content read paths

<summary>
- A scoped search returns exactly the results a server indexing only that scope's directories would return — same paths, same order
- A scoped search is never short-changed: if the union index's nearest matches all live outside the requested scope, the search still returns the requested number of in-scope results
- Duplicate detection compares a file only against documents inside the requested scope, so a near-identical file in another scope is never returned
- Content fetch serves a file only when it resolves inside the requested scope, and reuses the route's existing rejection for an out-of-scope path
- The scope reaches every read as a per-request value passed in — it is never stored on the shared indexer, so one request's scope can never be seen by another
- The response envelopes of all three routes are unchanged; scoping narrows the result set, it does not reshape the payload
- The scoping tests drive a real app over real temporary content roots, with embeddings that are deterministic rather than constant, so ordering differences are observable
- Every scoping test builds its own temporary content roots, because the container has no vault content mounted
</summary>

<objective>
Make the three read paths honour the scope that prompt 1 only validated: a scoped search, a scoped duplicate check, and a scoped content fetch each return exactly what a single-purpose daemon returns today. This is the prompt that carries the leak risk — the scope must travel as a per-request argument through the thread pool into the indexer, never as state on the shared indexer object.
</objective>

<context>
Read `CLAUDE.md` for project conventions and `docs/dod.md` for the Definition of Done.

Prompt 1 has already landed. Its contract, which this prompt builds on:

- `scopes.yaml` at the repo root declares five scopes; `src/semantic_search/scopes.py` exposes `ScopeMap` (with `.union_roots`), `load_scope_map(path)`, `validate_scope_map(scope_map)`, `resolve_scope(scope_map, raw)` (raising `MissingScopeError` / `UnknownScopeError`), and `SCOPE_MAP_ENV`.
- `src/semantic_search/http_server.py` has `build_app(scope_map: ScopeMap) -> Starlette`, a scope gate on `/search`, `/duplicates`, and `/content` that returns HTTP 400 with `MISSING_SCOPE` / `UNKNOWN_SCOPE` in the handler order *parameter check → scope gate → readiness gate → work*, and `/health` reporting `scope_map.union_roots`.
- `src/semantic_search/factory.py` has `declare_index_roots(roots)` and `reset()`. The index root set is declared once in `main()` — **not** in `build_app` — so whichever caller creates the indexer first still builds over the declared set; a test that declares nothing gets an index over the roots it passes. `factory.reset()` clears `_indexer`, `_watcher`, and the declared root set, and is the only supported way to reset the process-wide singleton.

Read these files before making changes:

- `src/semantic_search/indexer.py`:
  - `search(query, top_k=5)` — oversamples with `oversample = min(top_k * 4, self.index.ntotal)`, snapshots `self.meta` and `self._tombstones` under `self._index_lock`, skips tombstoned and missing positions, and stops at `top_k`. This is the ordering the fixture replay depends on.
  - `find_duplicates(file_path)` — resolves a relative path against `self.vault_paths`, reads and embeds the file, searches the **whole** index (`self.index.ntotal`), and keeps entries above `self.duplicate_threshold` that are not the file itself. Returns an `{"error": ...}` dict when the file is missing or unreadable.
  - `get_content(path, snippet=False, query=None, context_lines=20)` — resolves the path, then checks `any(resolved_path.is_relative_to(vp.resolve()) for vp in self.vault_paths)` and raises `ValueError("path not in indexed roots")` when outside, then `FileNotFoundError`, then reads. The HTTP handler maps those to `PATH_OUTSIDE_ROOTS` (400) and `FILE_NOT_FOUND` (404).
  - `_embed_text(text)` calls `self.model.encode([text], normalize_embeddings=True, show_progress_bar=False)` and then `.astype("float32")` — the exact call shape any fake encoder must satisfy.
- `src/semantic_search/http_server.py` — the handlers, and the `run_in_threadpool(indexer.search, q, top_k)` / `run_in_threadpool(indexer.find_duplicates, file_path)` / `run_in_threadpool(indexer.get_content, path, snippet, query, context_lines)` call sites. Pass the roots positionally to `run_in_threadpool`, matching the tools in prompt 3.
- `tests/test_http_server.py` — the call-shape assertions this prompt must update: `mock_indexer.search.assert_called_once_with("test query", 3)` (one site) and four `mock_indexer.get_content` assertions (three `assert_called_once_with`, one `assert_called_with`; three end in `..., None, 20` — two with `True`, one with `False` — and one ends in `True, "UNIQUE_TOKEN_XYZ", 5`). Two `side_effect` callables are also fixed-arity and raise `TypeError` once the handlers pass the trailing roots argument: `fake_search(q: str, top_k: int)` and `fake_find(file_path: str)`. The two zero-argument `_build_indexer_in_background` side effects are prompt 1's update, not this prompt's.
- `tests/test_indexer.py` — the mocking convention used throughout: `patch("semantic_search.indexer.SentenceTransformer")` with `get_sentence_embedding_dimension.return_value = 384` and `encode.return_value = np.array([[0.1] * 384])`; `TestVaultIndexerIncremental` shows the real-thread concurrency style.
- `tests/conftest.py` — the autouse `_isolated_indexer_cache` fixture and the `temp_vault` / `multi_vaults` fixtures; the existing `mock_sentence_transformer` returns a constant vector and is **not** usable for the ordering probes here. This prompt adds the deterministic fake encoder fixture to this file (requirement 6), because prompts 3 and 4 consume it too.

Pattern guides (in-container paths):

- `/home/node/.claude/plugins/marketplaces/coding/docs/python-architecture-patterns.md` — constructor injection, and the distinction between dependencies and runtime parameters: the scope is runtime data, so it is a method argument, not constructor state.
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-ioc-guide.md` — "Avoid Global Singleton Dependencies" and "Avoid Setter Injection".
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-factory-pattern.md` — the process-wide singleton this prompt's comparison probe has to reset before a second index can be built, and the shape `factory.reset()` follows.
</context>

<requirements>

1. **Add one path-membership helper to `src/semantic_search/indexer.py`** and use it for every scope comparison:

   ```python
   def _path_within_roots(path: Path, roots: Sequence[Path]) -> bool:
       """Return True iff `path`, after resolving symlinks, lies inside one of `roots`."""
   ```

   Both sides are resolved before comparing (`is_relative_to`), because a string-prefix comparison on an unresolved path is bypassable with `..` or a symlink — this is the same resolve-then-compare discipline `get_content` already uses for its vault-root check. Factor `get_content`'s existing inline check onto this helper; within `get_content`, do not leave two implementations of the same comparison. The other inline `is_relative_to` comparisons in `indexer.py` — `add_file_to_index` (around `indexer.py:319`) and `_VaultEventHandler._is_ignored_path` / `_maybe_reload_ignore` (around `:815` / `:830`) — are ownership lookups, not scope checks, and are explicitly out of scope: they need the owning root rather than a boolean, so they cannot use `_path_within_roots` as-is. Leave them untouched. Add `Sequence` to the module's imports (`from collections.abc import Sequence`, matching the style in `http_server.py`).

2. **`VaultIndexer.search(self, query: str, top_k: int = 5, roots: Sequence[Path] | None = None) -> list[dict[str, Any]]`**

   - `roots is None` means "every indexed root" and must behave exactly as today — the stdio transport and the CLI depend on it.
   - `roots` given means: only paths inside those roots may appear in the result, and the ordering is the index's own nearest-neighbour ordering restricted to in-scope documents. A scoped result must equal what an index built over only those roots would return, path for path and position for position.
   - **Widen rather than truncate.** Keep the existing starting window (`min(top_k * 4, ntotal)`) and, when fewer than `top_k` in-scope results were collected and the window is still smaller than `ntotal`, widen the window geometrically and search again until `top_k` in-scope results are found or the window reaches `ntotal`. The window is always derived from `top_k` and bounded by the index size — a request must never be able to force an unbounded scan, and a scope must never return fewer than `top_k` results merely because out-of-scope documents crowded the window.
   - Filter on the path stored in `meta`, **after resolution** (`_path_within_roots`), so a symlink inside a scope pointing into another scope cannot smuggle an out-of-scope document into a result set.
   - Keep the tombstone skipping, the `self.meta` snapshot under `self._index_lock`, and the `top_k` cap unchanged.

3. **`VaultIndexer.find_duplicates(self, file_path: str | Path, roots: Sequence[Path] | None = None) -> list[dict[str, Any]] | dict[str, str]`**

   - A candidate is returned only when it is inside `roots` (again via `_path_within_roots`, on the resolved path). A near-identical file in another scope is never returned.
   - `roots is None` keeps today's behaviour.
   - Keep the existing relative-path resolution against `self.vault_paths`, the missing-file / unreadable-file error dicts, the `duplicate_threshold` comparison, and the self-exclusion.
   - The `file` argument itself is deliberately **not** scope-checked — only the candidate set is filtered. That is visibility-equivalent to today (a caller who can name a path can already read it through `/content`), it is not a widening, and it adds no refusal: `/duplicates` has no error-code field in its envelope (its error path returns `{"error": <string>}`), so inventing one would be a contract change no acceptance criterion covers.
   - Do **not** add a threshold parameter and do **not** extend the handler's `indexer.duplicate_threshold = threshold` assignment — that per-request mutation of the shared indexer is exactly the pattern this prompt must not repeat for the scope.

4. **`VaultIndexer.get_content(self, path: str, snippet: bool = False, query: str | None = None, context_lines: int = 20, roots: Sequence[Path] | None = None) -> dict[str, str]`**

   - Check the scope **before** the existence check: resolve the path, then require `_path_within_roots(resolved_path, roots)`, then `exists()`, then read. An out-of-scope path must raise `ValueError("path not in indexed roots")` — the same exception and message the route already maps to `PATH_OUTSIDE_ROOTS` — even when the file does not exist, so the route never reveals whether an out-of-scope file exists.
   - `roots is None` keeps the existing check against `self.vault_paths`.
   - The returned dict (`path`, `content`, `mode`) is unchanged.

5. **Wire the resolved scope through the HTTP handlers** in `src/semantic_search/http_server.py`:

   - Replace prompt 1's validate-and-discard gate with resolve-and-pass: each of `/search`, `/duplicates`, and `/content` resolves the request's scope once, refuses with the existing 400 `MISSING_SCOPE` / `UNKNOWN_SCOPE` response when resolution fails, and otherwise passes the resolved roots into the indexer call.
   - Pass the roots as the trailing positional argument to `run_in_threadpool(...)`, e.g. `run_in_threadpool(indexer.search, q, top_k, roots)`.
   - **The scope is a per-request value.** It must not be assigned to the indexer, to a module global, or to any object that outlives the request — the interleaved-request acceptance probe in prompt 3 exists specifically to catch that. No `indexer.scope = ...`, no `indexer.roots = ...`, no post-construction mutation of the app.
   - Keep the handler order from prompt 1 (parameter check → scope gate → readiness gate → work) and keep every response envelope exactly as it is: `/search` still returns `{"query", "results", "count"}`, `/duplicates` still returns `{"file", "threshold", "duplicates", "count"}`, `/content` still returns the indexer's dict with its existing error codes.

6. **Add the deterministic fake encoder to `tests/conftest.py` as a fixture.** Prompts 3 and 4 need the same fake, so it lives in the shared conftest and is never defined in, or cross-imported from, a test module.

   - Fixture name: `deterministic_sentence_transformer`. It returns a **class** (not an instance), so a test can pass it straight to `patch("semantic_search.indexer.SentenceTransformer", deterministic_sentence_transformer)`.
   - It must satisfy the indexer's real usage: constructed with the model name (`SentenceTransformer("all-MiniLM-L6-v2")`), `get_sentence_embedding_dimension()` returning the vector width, and `encode(texts, normalize_embeddings=True, show_progress_bar=False)` returning a 2-D array of shape `(len(texts), dim)` that the caller can `.astype("float32")`.
   - Vectors are derived deterministically from the text (for example a fixed vocabulary of probe tokens counted into a small vector, L2-normalised), so "these documents are closer to this query than those" is expressible. A constant vector — the convention in the existing `mock_sentence_transformer` fixture — cannot express that and must not be used for these probes.
   - Identical text maps to an identical vector across app instances and index builds, so two indexes built under the same fixture are directly comparable.

7. **Create `tests/test_scoping.py`** — the scoping probes, over a real Starlette app and real temp content roots. Build one temp parent directory with two sibling content roots, `A` and `B`, and drive the app with `starlette.testclient.TestClient` over a scope map whose roots are those temp dirs (`ScopeMap(scopes={"scope_a": (A,), "scope_b": (B,)})` constructed directly is enough; the scope names in tests are local to the test map). The index is built by a background task, so wait for `/health` to report `ready: true` (bounded poll) before issuing a probe. Reset the process-wide state before and after each probe — `factory.reset()`, `http_server._indexer = None`, `http_server._indexer_ready = asyncio.Event()`, `http_server._indexer_error = None` — otherwise a stale indexer from an earlier test decides what is indexed.

   Probes to cover:

   - **Scoped search leaks nothing and orders identically.** A term present in both roots, with the geometry that makes the leak assertion load-bearing asserted rather than assumed: an unscoped search for that same term on the app's own indexer (`http_server.get_indexer()`) must return at least one B path, so that deleting the scope filter changes the result instead of returning the same A-only list. Then `GET /search?q=<term>&scope=scope_a&top_k=20` returns at least one result and every `results[].path` is under A. Then the ordering oracle: a second `TestClient(build_app(...))` does **not** give you a sub-index for free — `factory.create_indexer` returns the process-wide singleton, and `_build_indexer_in_background` early-returns while `_indexer_ready` is already set, so a second app would serve the first app's union index filtered with the same roots and the two path lists would be equal even with the scope filter deleted. Build the second index over **A's roots alone**: exit the first app's context, reset the process state (`factory.reset()`, `http_server._indexer = None`, `http_server._indexer_error = None`, and `http_server._indexer_ready = asyncio.Event()` as a fresh unset event), then enter a second `TestClient(build_app(...))` whose scope map's union is A alone, so its own background build indexes A only; wait for its `/health` to report ready and issue the same query against it. (Acceptable alternative: compare the scoped HTTP result against a directly constructed `VaultIndexer([str(A)])` searched with the same deterministic fake encoder and `roots` omitted.) Assert the two `[r["path"] for r in results]` lists are equal and non-empty; if both sides read one index, the assertion proves nothing.
   - **A scoped search is not truncated by out-of-scope neighbours.** The corpus must make the widening loop the only way to pass: at least `top_k` documents in A, and **more than `top_k * 4`** documents in B that are all nearer the probe query than every A document — so the first retrieval window (`min(top_k * 4, ntotal)`) is entirely out of scope and a filter without widening returns fewer than `top_k`. Assert that geometry rather than assuming it: the A count is `>= top_k`, the B count is `> top_k * 4`, and an unscoped `search(q, top_k=len(B documents))` on the app's own indexer (`http_server.get_indexer()`) returns only B paths. Then `scope=scope_a` still returns exactly `top_k` results, every one of them under A.
   - **Duplicate detection returns no out-of-scope path.** Two near-identical files in A and a near-identical file in B: `GET /duplicates?file=<one of the A files>&scope=scope_a` returns at least one entry in `duplicates[]` and every returned `path` is under A.
   - **Content fetch refuses an out-of-scope path with the route's existing code.** `GET /content?path=<path under B>&scope=scope_a` returns HTTP 400 with `error.code == "PATH_OUTSIDE_ROOTS"`, while the same path with `scope=scope_b` returns 200.
   - **Unit-level coverage in the same file** for the `roots=None` path: `VaultIndexer.search` / `find_duplicates` / `get_content` called without `roots` behave exactly as before (this is the stdio and CLI contract).

8. **Update the affected assertions in `tests/test_http_server.py`** — the mocked call-shape assertions now include the resolved roots. Update all five (`mock_indexer.search.assert_called_once_with("test query", 3)` and the four `get_content` assertions), and widen the two fixed-arity `side_effect` callables (`fake_search(q: str, top_k: int)` and `fake_find(file_path: str)`) to accept the trailing roots argument — without that they raise `TypeError` the moment the handler passes it. Do not weaken the assertions to `assert_called_once()`; keep asserting the exact argument tuple. The two zero-argument `_build_indexer_in_background` side effects belong to prompt 1 — do not change them here.

9. **Extend the existing `## Unreleased` section in `CHANGELOG.md`** (prompt 1 created it). If the narrowed result set needs a user-facing sentence — a scoped request now answers only from that scope's roots, with the response envelopes unchanged — add it as a bullet under that existing heading. Do not add a second `## Unreleased` section, and do not touch a released section.

10. **Strict typing and style.** Full annotations, docstrings on every new function, no broad `except Exception` in new code, no `print()` in `src/`, no new dependency. `make precommit` must be clean.

**Self-check before finishing:** re-run `<verification>` and confirm every command behaves as stated; then walk each requirement against the change and confirm that the four behavioural probes (scoped search equality, no truncation, scoped duplicates, scoped content) actually fail if you remove the scope filter — a test that passes without the filter proves nothing.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Do NOT store the scope anywhere that outlives a request: not on `VaultIndexer`, not on the app, not in a module global, not as a post-construction attribute. It is a per-request argument.
- Do NOT change the frozen contract: the parameter name is `scope`, the five scope names are `personal`, `brogrammers`, `boss`, `openbrain`, `starcitzen`, the refusal is HTTP 400 carrying `MISSING_SCOPE` / `UNKNOWN_SCOPE`, and an out-of-scope `/content` path reuses `PATH_OUTSIDE_ROOTS` — no new error code for that case.
- Do NOT reshape the response envelopes of `/search`, `/duplicates`, or `/content`. Scoping narrows the result set; it does not change field names or types.
- Do NOT add a per-scope result-count or memory limit, and do NOT change the embedding model, chunking, or quantization.
- Do NOT add a threshold parameter to `find_duplicates`, and do NOT extend the existing per-request `duplicate_threshold` assignment to the scope.
- Do NOT touch `/health` (stays scopeless, reports the union root set) or `/reindex` (stays scopeless, rebuilds the whole union index).
- Do NOT touch the `/mcp` mount, the FastMCP tools, or `src/semantic_search/server.py` — prompt 3 owns the MCP surface.
- Do NOT edit `src/semantic_search/factory.py` or add new process-wide state: `declare_index_roots` and `reset()` are prompt 1's surface, and this prompt only calls them. In particular, do NOT call `declare_index_roots` from `build_app` — the declaration happens in `main()` only.
- Do NOT define the deterministic fake encoder in `tests/test_scoping.py`, and do NOT import it from another test module — it is the shared fixture in `tests/conftest.py` that prompts 3 and 4 consume.
- Do NOT change what the stdio MCP transport or the `semantic-search` CLI read: both keep `CONTENT_PATH` as their root source and gain no scope handling.
- Do NOT create or edit `docs/design/per-vault-scoping.md`, any other file under `docs/`, the `commands/*.md` files, or anything under `scenarios/` — those are direct edits handled outside the prompt pipeline.
- Tests must build their own temp content roots; the container has no vault content mounted, and the committed `scopes.yaml` names host directories that do not exist inside it. Never point a test at the committed map.
- Keep `make precommit` green: format + test + lint + typecheck, mypy strict, ruff clean. Existing tests not explicitly updated here must keep passing.
- Repo-relative paths everywhere except the roots inside `scopes.yaml`.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Then confirm each of these:

```bash
# The read paths really take the scope as an argument, and the comparison exists once
grep -c '_path_within_roots' src/semantic_search/indexer.py            # must print >= 4 (definition + 3 uses)
grep -n 'roots: Sequence\[Path\] | None = None' src/semantic_search/indexer.py   # must match search, find_duplicates, get_content

# The scope is never parked on the shared indexer or the app
! grep -nE '(indexer|app|self)\.(scope|roots|scope_roots)\s*=' src/semantic_search/http_server.py src/semantic_search/indexer.py

# No scope state in a module global. `_build_indexer_in_background` assigns through
# `global _indexer, _indexer_error`; the `global _indexer` in `reindex` is a read-only
# declaration. None of them is scope state — leave them all in place.
# This check is about scope state only, and it is anchored to statement position so a
# comment line that merely mentions "global scope" cannot trip it.
! grep -rnE '^[[:space:]]*global[[:space:]]+.*(scope|roots)' src/semantic_search/http_server.py src/semantic_search/scopes.py

# The scoped probes pass
uv run pytest tests/test_scoping.py -q
uv run pytest tests/test_http_server.py tests/test_indexer.py tests/test_server.py -q
uv run pytest -q
```

Then prove the filter is load-bearing rather than incidental: temporarily make `_path_within_roots` return `True` unconditionally, re-run `uv run pytest tests/test_scoping.py -q`, and confirm the scoped-search, duplicates, and content probes **fail**. Then restore the helper to its original body and re-run `make precommit` — a botched restore would ship a fail-open filter, which the spec calls the highest-consequence bug this change can introduce, so the restore is only complete when the full gate is green again. Report that observation in the prompt's completion summary.
</verification>
