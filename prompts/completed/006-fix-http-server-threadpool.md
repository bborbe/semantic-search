---
status: completed
summary: Wrapped VaultIndexer.search and VaultIndexer.find_duplicates in starlette.concurrency.run_in_threadpool in http_server.py, added two threadpool-verification tests, and updated CHANGELOG.md.
container: semantic-search-006-fix-http-server-threadpool
dark-factory-version: v0.110.2
created: "2026-04-16T10:00:00Z"
queued: "2026-04-16T10:07:38Z"
started: "2026-04-16T10:42:10Z"
completed: "2026-04-16T10:47:45Z"
---

<summary>
- HTTP endpoints that call the synchronous indexer now run those calls in a thread pool instead of blocking the asyncio event loop
- `/health` stays responsive even while the indexer is embedding a file in the background
- `/search` and `/duplicates` no longer starve one another or the MCP handler when the indexer is busy
- `/reindex` keeps its current behavior (explicit user action, blocking is acceptable)
- Added test verifies a sync indexer call really runs off the event loop thread
</summary>

<objective>
Make the unified HTTP server responsive under concurrent load by wrapping the blocking `VaultIndexer.search` and `VaultIndexer.find_duplicates` calls in `starlette.concurrency.run_in_threadpool`. Today both handlers are declared `async def` but call the synchronous indexer directly, which blocks the single asyncio event loop — causing `/health` and `/mcp` to hang while a search query is in flight. After this prompt, a long-running indexer call on one request no longer blocks unrelated endpoints.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.14+, `uv`, strict mypy, src/ layout, dark-factory workflow).

Read these files before making changes:

- `src/semantic_search/http_server.py` — defines the Starlette handlers. `search`, `duplicates`, `reindex`, `health` are all declared `async def` but `search` and `duplicates` currently call `indexer.search(...)` / `indexer.find_duplicates(...)` synchronously inside the coroutine body. That blocks the event loop thread while the (sync, CPU- and IO-bound) indexer work runs.
- `src/semantic_search/indexer.py` — `VaultIndexer.search` and `VaultIndexer.find_duplicates` are synchronous; they acquire `self._index_lock` (a `threading.Lock`) and call `self.model.encode(...)` (sentence-transformers, blocking). They are safe to call from a worker thread — FAISS operations are released under `_index_lock`, and sentence-transformers is thread-safe.
- `tests/test_http_server.py` — existing `TestClient(build_app())` + `patch("semantic_search.http_server.get_indexer")` pattern. Reuse it.

**Why `run_in_threadpool` is the right primitive here:**

Starlette provides `starlette.concurrency.run_in_threadpool` for exactly this case: a sync callable you need to `await` from inside an async handler. It dispatches to anyio's default worker thread pool, returning control to the event loop until the worker finishes. This is the same mechanism Starlette uses under the hood when you register a regular `def` view.

Do NOT use `asyncio.to_thread` — Starlette's threadpool is preconfigured (size, ContextVar propagation). Do NOT use `loop.run_in_executor` — verbose and loses request-scoped context.

`/reindex` intentionally remains synchronous from the caller's perspective: it's an explicit user action that implies "please block me until done." `/health` does not call any blocking indexer method; it only reads `len(indexer.meta)` (O(1) dict length), so it stays as-is.
</context>

<requirements>

1. **Import `run_in_threadpool`** at the top of `src/semantic_search/http_server.py`, alongside the existing starlette imports:

   ```python
   from starlette.concurrency import run_in_threadpool
   ```

   Place it in the starlette import block sorted per ruff/isort — alphabetical by submodule, so between `from starlette.applications import Starlette` and `from starlette.requests import Request`. Run `ruff check --fix` if unsure; a misplaced import fails `make precommit`.

2. **Wrap `indexer.search` in the `search` handler.**

   In `async def search(request: Request) -> JSONResponse`, replace:

   ```python
   results: list[Any] = indexer.search(q, top_k)
   ```

   with:

   ```python
   results: list[Any] = await run_in_threadpool(indexer.search, q, top_k)
   ```

   Do not change anything else in this handler. The `try/except` wrapper, JSON response shape, 400 on missing `q`, and logging stay exactly as they are.

3. **Wrap `indexer.find_duplicates` in the `duplicates` handler.**

   In `async def duplicates(request: Request) -> JSONResponse`, replace:

   ```python
   results = indexer.find_duplicates(file_path)
   ```

   with:

   ```python
   results = await run_in_threadpool(indexer.find_duplicates, file_path)
   ```

   Do not change the `indexer.duplicate_threshold = threshold` line — it's a simple attribute assignment and does not block. Do not change anything else in this handler.

4. **Leave `health` and `reindex` unchanged.**

   - `health` only reads `len(indexer.meta)` (O(1) dict length, non-blocking) — no wrap needed.
   - `reindex` is an explicit blocking user action ("rebuild now, tell me when done") — leave `get_indexer()` sync as before. Wrapping it would only mask slow reindexes, not help.

5. **Add a test `test_search_runs_in_threadpool` in `tests/test_http_server.py`.**

   Add it as a new method on the existing `TestSearchEndpoint` class:

   ```python
   def test_search_runs_in_threadpool(self) -> None:
       """Sync indexer.search must be awaited via run_in_threadpool so a slow
       query does not block the asyncio event loop.

       We prove this by checking the thread on which indexer.search executes is
       NOT the main thread (which hosts the event loop under TestClient).
       """
       import threading

       main_thread_id = threading.get_ident()
       observed_thread_ids: list[int] = []

       def fake_search(q: str, top_k: int) -> list[dict[str, object]]:
           observed_thread_ids.append(threading.get_ident())
           return [{"path": "a.md", "score": 0.9}]

       with patch("semantic_search.http_server.get_indexer") as mock_get:
           mock_indexer = MagicMock()
           mock_indexer.search.side_effect = fake_search
           mock_get.return_value = mock_indexer
           with TestClient(build_app()) as client:
               resp = client.get("/search?q=hello&top_k=5")

       assert resp.status_code == 200
       assert len(observed_thread_ids) == 1
       assert observed_thread_ids[0] != main_thread_id, (
           "indexer.search ran on the event loop thread — it must be dispatched "
           "to a worker thread via run_in_threadpool"
       )
   ```

6. **Add a test `test_duplicates_runs_in_threadpool`** as a new method on the existing `TestDuplicatesEndpoint` class in `tests/test_http_server.py`:

   ```python
   def test_duplicates_runs_in_threadpool(self) -> None:
       """Sync indexer.find_duplicates must be awaited via run_in_threadpool."""
       import threading

       main_thread_id = threading.get_ident()
       observed_thread_ids: list[int] = []

       def fake_find(file_path: str) -> list[dict[str, object]]:
           observed_thread_ids.append(threading.get_ident())
           return [{"path": "similar.md", "score": 0.9}]

       with patch("semantic_search.http_server.get_indexer") as mock_get:
           mock_indexer = MagicMock()
           mock_indexer.find_duplicates.side_effect = fake_find
           mock_get.return_value = mock_indexer
           with TestClient(build_app()) as client:
               resp = client.get("/duplicates?file=note.md")

       assert resp.status_code == 200
       assert len(observed_thread_ids) == 1
       assert observed_thread_ids[0] != main_thread_id, (
           "indexer.find_duplicates ran on the event loop thread — it must be "
           "dispatched via run_in_threadpool"
       )
   ```

7. **Update `CHANGELOG.md`** under `## Unreleased`:
   - `fix: Run blocking VaultIndexer.search and VaultIndexer.find_duplicates calls via starlette.concurrency.run_in_threadpool so HTTP endpoints no longer block the asyncio event loop. /health and /mcp now stay responsive while a search or duplicate query is in flight.`

8. **Strict mypy compliance.** `run_in_threadpool` is generic; the existing `results: list[Any] = ...` annotation in `search` remains valid since `indexer.search` returns `list[dict[str, Any]]`. For `duplicates`, `results` is untyped locally today (the later `isinstance(results, dict)` check handles both return shapes); keep it that way. Do not add narrowing annotations — they conflict with the union return type of `find_duplicates`.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass.
- Do NOT change the JSON response shapes of any endpoint.
- Do NOT change `VaultIndexer.search` or `VaultIndexer.find_duplicates` — they stay synchronous. This prompt only changes how the HTTP layer calls them.
- Do NOT wrap `reindex` — it's intentionally a blocking explicit user action.
- Do NOT wrap `health` — it does no blocking work.
- Do NOT use `asyncio.to_thread` or `loop.run_in_executor` — use `starlette.concurrency.run_in_threadpool`.
- Do NOT make `VaultIndexer.search` / `find_duplicates` `async` — callers outside HTTP (CLI, MCP tool functions) rely on them being sync.
- Do not introduce new top-level dependencies (`starlette` is already a direct dep; `starlette.concurrency` ships with it).
- Repo-relative paths only.
- Follow strict mypy typing.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Specifically confirm:
- `tests/test_http_server.py::TestSearchEndpoint::test_search_runs_in_threadpool` passes.
- `tests/test_http_server.py::TestDuplicatesEndpoint::test_duplicates_runs_in_threadpool` passes.
- All existing HTTP server tests still pass.
- `make test` (full suite) passes.
</verification>
