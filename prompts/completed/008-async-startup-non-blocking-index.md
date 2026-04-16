---
status: completed
summary: HTTP server now binds its port immediately and builds the VaultIndexer in a background asyncio task; /health reports indexing/ready state and /search, /duplicates, /reindex return HTTP 503 with Retry-After while not ready.
container: semantic-search-008-async-startup-non-blocking-index
dark-factory-version: v0.111.2
created: "2026-04-16T11:30:00Z"
queued: "2026-04-16T11:17:34Z"
started: "2026-04-16T11:17:36Z"
completed: "2026-04-16T11:19:58Z"
---

<summary>
- HTTP server binds its port immediately on startup instead of waiting for the initial index build to finish
- `/health` stays responsive during a cold start and reports whether the indexer is ready yet
- `/search` and `/duplicates` return HTTP 503 with a `Retry-After` header while the index is still building, instead of hanging or returning errors
- MCP mount at `/mcp` accepts connections right away; MCP tools that need the indexer surface the same "not ready" error once wired through the existing indexer accessor
- Adds tests that prove `/health` reports the indexing phase, `/search` returns 503 when the indexer is not ready, and `/health` reports `ready` once indexing is done
- Cold-start UX fixed without changing any response shape on the ready path, any CLI flag, or the MCP tool surface
</summary>

<objective>
Make `src/semantic_search/http_server.py` start the Uvicorn server and bind the port BEFORE the initial `VaultIndexer` build finishes. Today `main()` calls `get_indexer()` synchronously before `uvicorn.run(app)`, so the port is only bound after `_load_index()` (which can call `rebuild_index()` and take minutes on a cold vault) returns. Move the blocking indexer construction into a background task launched from a Starlette lifespan, track readiness via an `asyncio.Event`, and have request handlers check that Event.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.14+, `uv`, strict mypy, src/ layout, dark-factory workflow, tests in `tests/test_*.py`, pytest with pytest-asyncio, `unittest.mock.patch` for mocks).

Read these files before making changes:

- `src/semantic_search/http_server.py` — the entire file will change. Current blocking call path is `main()` → `get_indexer()` → `create_indexer(CONTENT_PATHS)` → `VaultIndexer.__init__` → `_load_index()` → `rebuild_index()`.
- `src/semantic_search/factory.py` — `create_indexer` is a thread-safe singleton guarded by a `threading.Lock`. It is safe to call from a worker thread (`asyncio.to_thread`). It returns a `VaultIndexer` and also starts a `VaultWatcher` in a daemon background thread.
- `src/semantic_search/indexer.py` — `VaultIndexer.__init__` does all the expensive work (`_load_index()` → possibly `rebuild_index()`, which walks every `*.md` file under each vault path and calls `self.model.encode(...)` for each). FAISS + sentence-transformers are both safe to call from a worker thread.
- `tests/test_http_server.py` — existing patterns: `TestClient(build_app())` as a context manager, `patch("semantic_search.http_server.get_indexer")` to stub the indexer, one class per endpoint (`TestHealthEndpoint`, `TestSearchEndpoint`, `TestDuplicatesEndpoint`, `TestReindexEndpoint`, `TestMcpMount`). Reuse these patterns.
- `prompts/completed/006-fix-http-server-threadpool.md` — style reference for this prompt (imports, wrap sync-in-async with `run_in_threadpool`, mypy strict, CHANGELOG update).

**Current code that must change (copy verbatim for the find-and-replace):**

The module state and accessor at the top of `http_server.py`:

```python
_indexer: VaultIndexer | None = None


def get_indexer() -> VaultIndexer:
    """Get or create the indexer instance."""
    global _indexer
    if _indexer is None:
        logger.info(f"Creating indexer for paths: {CONTENT_PATHS}")
        _indexer = create_indexer(CONTENT_PATHS)
    return _indexer
```

The health handler:

```python
async def health(request: Request) -> JSONResponse:
    """Handle /health endpoint."""
    try:
        indexer = get_indexer()
        return JSONResponse(
            {"status": "ok", "paths": CONTENT_PATHS, "indexed_files": len(indexer.meta)}
        )
    except Exception as e:
        logger.exception("Error handling /health request")
        return JSONResponse({"error": str(e)}, status_code=500)
```

The `build_app` function:

```python
def build_app() -> Starlette:
    """Build the unified Starlette app with REST routes and MCP mount."""
    mcp_app = mcp.http_app(path="/mcp")
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/search", search, methods=["GET"]),
        Route("/duplicates", duplicates, methods=["GET"]),
        Route("/reindex", reindex, methods=["GET", "POST"]),
        Mount("/", app=mcp_app),
    ]
    # Pass the MCP app's lifespan so FastMCP's session manager starts.
    return Starlette(routes=routes, lifespan=mcp_app.lifespan)
```

And `main()`:

```python
def main() -> None:
    """Entry point for semantic-search-http binary."""
    ...
    logger.info(f"Building index for: {CONTENT_PATHS}")
    get_indexer()  # pre-warm the singleton before accepting requests

    app = build_app()
    logger.info(f"Serving REST + MCP on http://{args.host}:{args.port}")
    ...
    uvicorn.run(app, host=args.host, port=args.port)
```

**Why an `asyncio.Event` + lifespan + `asyncio.to_thread`:**

- Starlette's `lifespan` context manager runs inside the event loop, so this is where we launch the background indexing task.
- `asyncio.to_thread(create_indexer, CONTENT_PATHS)` dispatches the blocking constructor to anyio's default worker thread pool, leaving the event loop free to accept connections on the bound port.
- `asyncio.Event` is awaitable and thread-safe-for-signalling when set from the loop thread; we set it from the lifespan callback (on the loop), not from the worker thread.
- We combine our new lifespan with the existing `mcp_app.lifespan` using `contextlib.asynccontextmanager` so FastMCP's session manager still starts. FastMCP 3.x requires its lifespan to run; dropping it breaks `/mcp`.

Do NOT call `asyncio.run(...)` or `loop.run_until_complete(...)` from inside the lifespan — you are already on the loop. Do NOT `await create_indexer(...)` directly — it is sync and would block the loop.
</context>

<requirements>

1. **Add the readiness state at module scope** in `src/semantic_search/http_server.py`, replacing the existing `_indexer: VaultIndexer | None = None` line:

   ```python
   _indexer: VaultIndexer | None = None
   _indexer_ready: asyncio.Event = asyncio.Event()
   _indexer_error: str | None = None
   ```

   Add `import asyncio` to the top of the file in the stdlib import block (alphabetical, before `import logging`). Also add `import contextlib` in the same block (needed for requirement 6).

   Rationale: `_indexer_ready` is an `asyncio.Event` (not `threading.Event`) because handlers check it from the loop thread; only the lifespan coroutine (also on the loop) calls `.set()` after the worker thread returns. `_indexer_error` captures the exception message when the background build fails, so `/health` can surface the failure instead of lying about "still indexing forever".

2. **Replace `get_indexer()`** in `src/semantic_search/http_server.py` with a non-creating accessor:

   ```python
   def get_indexer() -> VaultIndexer:
       """Return the ready indexer instance, or raise RuntimeError if not ready.

       The indexer is built in a background task launched from the Starlette
       lifespan (see `_build_indexer_in_background`). Handlers MUST gate on
       `_indexer_ready.is_set()` before calling this.
       """
       if _indexer is None:
           raise RuntimeError("Indexer not initialized yet")
       return _indexer
   ```

   Rationale: the old `get_indexer()` lazily built the indexer on first request — that would just move the blocking work from startup into the first `/health` call. The new contract is: the lifespan owns initialization; handlers only read.

3. **Add a new coroutine `_build_indexer_in_background`** immediately after `get_indexer`:

   ```python
   async def _build_indexer_in_background() -> None:
       """Build the VaultIndexer in a worker thread, then mark ready.

       Called from the Starlette lifespan so the server can bind its port
       immediately while the (slow, blocking) initial embedding pass runs.
       """
       global _indexer, _indexer_error
       logger.info(f"Indexer build starting in background for paths: {CONTENT_PATHS}")
       try:
           _indexer = await asyncio.to_thread(create_indexer, CONTENT_PATHS)
           logger.info(
               f"Indexer build complete: {len(_indexer.meta)} files indexed"
           )
       except Exception as e:
           _indexer_error = str(e)
           logger.exception("Indexer build failed")
       finally:
           _indexer_ready.set()
   ```

   Notes:
   - `_indexer_ready.set()` runs in the `finally` block so even a failed build unblocks `/health` to return an error (otherwise health would hang forever on failure).
   - Use `logger.exception(...)` (not `logger.error(..., exc_info=True)`) to match project style.
   - Do NOT re-raise — the lifespan task must not crash the server; `/health` surfaces the failure instead.

4. **Rewrite the `health` handler** to never call the blocking path, and to report indexing status:

   ```python
   async def health(request: Request) -> JSONResponse:
       """Handle /health endpoint. Never blocks on indexer construction."""
       if _indexer_error is not None:
           return JSONResponse(
               {
                   "status": "error",
                   "ready": False,
                   "error": _indexer_error,
                   "paths": CONTENT_PATHS,
               },
               status_code=500,
           )
       if not _indexer_ready.is_set() or _indexer is None:
           return JSONResponse(
               {
                   "status": "indexing",
                   "ready": False,
                   "paths": CONTENT_PATHS,
               }
           )
       return JSONResponse(
           {
               "status": "ok",
               "ready": True,
               "paths": CONTENT_PATHS,
               "indexed_files": len(_indexer.meta),
           }
       )
   ```

   The ready-path response MUST include every field the previous implementation returned (`status`, `paths`, `indexed_files`) plus the new `ready: true` boolean. Adding a field is backwards-compatible; removing one is not.

5. **Add a helper `_not_ready_response`** and gate the two blocking handlers on it. Insert the helper above `search`:

   ```python
   def _not_ready_response() -> JSONResponse:
       """503 response returned while the initial index build is in flight."""
       return JSONResponse(
           {"error": "indexing in progress", "ready": False},
           status_code=503,
           headers={"Retry-After": "5"},
       )
   ```

   In `async def search(request: Request) -> JSONResponse`, insert the readiness gate **after** the existing `q` parameter check and **before** the `get_indexer()` call:

   ```python
   # (existing check)
   if not q:
       return JSONResponse({"error": "Missing 'q' parameter"}, status_code=400)

   # NEW: gate on readiness before touching the indexer
   if not _indexer_ready.is_set() or _indexer is None:
       return _not_ready_response()
   ```

   Same pattern in `async def duplicates(request: Request) -> JSONResponse`: keep the existing `if not file_path: return ... 400` check first, insert the 503 gate after it and before `get_indexer()`. This preserves the existing 400-on-missing-param contract so `test_search_missing_query_returns_400` and `test_duplicates_missing_file_returns_400` still pass without readiness wiring.

   Do NOT remove the existing `run_in_threadpool(...)` wrappers or the rest of the handler bodies.

6. **Compose the lifespans** in `build_app` so FastMCP's session manager AND our background indexer both run. Replace the existing `build_app` body with:

   ```python
   def build_app() -> Starlette:
       """Build the unified Starlette app with REST routes and MCP mount."""
       mcp_app = mcp.http_app(path="/mcp")

       @contextlib.asynccontextmanager
       async def combined_lifespan(app: Starlette) -> AsyncIterator[None]:
           # Launch the indexer build as a background task — do NOT await it.
           # The server binds its port as soon as this lifespan yields.
           task = asyncio.create_task(_build_indexer_in_background())
           async with mcp_app.lifespan(app):
               try:
                   yield
               finally:
                   if not task.done():
                       task.cancel()
                       with contextlib.suppress(asyncio.CancelledError):
                           await task

       routes = [
           Route("/health", health, methods=["GET"]),
           Route("/search", search, methods=["GET"]),
           Route("/duplicates", duplicates, methods=["GET"]),
           Route("/reindex", reindex, methods=["GET", "POST"]),
           Mount("/", app=mcp_app),
       ]
       return Starlette(routes=routes, lifespan=combined_lifespan)
   ```

   Add `from collections.abc import AsyncIterator` to the typing/stdlib imports. Add `import contextlib` if not already added in requirement 1.

   Notes:
   - `asyncio.create_task(...)` starts the task on the loop immediately; it runs concurrently with whatever else the server does.
   - `async with mcp_app.lifespan(app):` preserves FastMCP's startup/shutdown. Do NOT flatten this — FastMCP registers its session manager here.
   - The `finally` block cancels a still-running indexer build on shutdown so the process exits cleanly. `contextlib.suppress(asyncio.CancelledError)` keeps shutdown quiet when cancellation races completion.
   - Cancellation only interrupts the `asyncio.to_thread` wrapper, not the worker thread itself (Python threads cannot be force-killed). That is acceptable for our scope: the daemon worker exits when the process does.

7. **Update `main()`** — remove the pre-warm `get_indexer()` call so startup no longer blocks on it. Replace:

   ```python
   logger.info(f"Building index for: {CONTENT_PATHS}")
   get_indexer()  # pre-warm the singleton before accepting requests

   app = build_app()
   ```

   with:

   ```python
   logger.info(f"Indexer will build in background for: {CONTENT_PATHS}")
   app = build_app()
   ```

   Leave the other log lines (`Serving REST + MCP on ...`, endpoint list) unchanged.

8. **Update `reindex` handler** to gate on readiness the same way. Currently `reindex` reassigns `_indexer = None` and calls `get_indexer()`, which with the new accessor would raise `RuntimeError`. Replace the `reindex` body with:

   ```python
   async def reindex(request: Request) -> JSONResponse:
       """Handle /reindex endpoint. Blocks until reindex completes.

       Returns 503 if the initial index build is still running — the client
       cannot meaningfully reindex something that has not finished indexing
       the first time.
       """
       global _indexer
       if not _indexer_ready.is_set() or _indexer is None:
           return _not_ready_response()
       try:
           logger.info("Forcing reindex...")
           await run_in_threadpool(_indexer.rebuild_index)
           return JSONResponse(
               {
                   "status": "ok",
                   "message": "Reindex complete",
                   "indexed_files": len(_indexer.meta),
               }
           )
       except Exception as e:
           logger.exception("Error handling /reindex request")
           return JSONResponse({"error": str(e)}, status_code=500)
   ```

   Rationale: the old body called `_indexer = None; get_indexer()` to force a full re-init. That dropped the `VaultWatcher` on the floor (leaked thread) AND required the lazy `get_indexer()` behavior we just removed. Calling `_indexer.rebuild_index()` directly preserves the watcher and the indexer singleton, and goes through `run_in_threadpool` to stay off the event loop.

9. **Add three tests** in `tests/test_http_server.py`. Each new test MUST reset module state in a `finally` so it does not leak into other tests.

   a. On the existing `TestHealthEndpoint` class, add:

   ```python
   def test_health_returns_indexing_status_when_not_ready(self) -> None:
       """Before the background build finishes, /health must report
       status=indexing without blocking on the indexer."""
       import semantic_search.http_server as http_server

       original_event = http_server._indexer_ready
       original_indexer = http_server._indexer
       original_error = http_server._indexer_error
       try:
           http_server._indexer_ready = asyncio.Event()  # unset
           http_server._indexer = None
           http_server._indexer_error = None
           with TestClient(build_app()) as client:
               resp = client.get("/health")
           assert resp.status_code == 200
           data = resp.json()
           assert data["status"] == "indexing"
           assert data["ready"] is False
           assert "paths" in data
       finally:
           http_server._indexer_ready = original_event
           http_server._indexer = original_indexer
           http_server._indexer_error = original_error

   def test_health_returns_ok_when_ready(self) -> None:
       """Once the Event is set and _indexer is populated, /health returns
       the full ready response with indexed_files count."""
       import semantic_search.http_server as http_server

       original_event = http_server._indexer_ready
       original_indexer = http_server._indexer
       original_error = http_server._indexer_error
       try:
           ready_event = asyncio.Event()
           ready_event.set()
           http_server._indexer_ready = ready_event
           mock_indexer = MagicMock()
           mock_indexer.meta = {"0": {}, "1": {}, "2": {}}
           http_server._indexer = mock_indexer
           http_server._indexer_error = None
           with TestClient(build_app()) as client:
               resp = client.get("/health")
           assert resp.status_code == 200
           data = resp.json()
           assert data["status"] == "ok"
           assert data["ready"] is True
           assert data["indexed_files"] == 3
           assert "paths" in data
       finally:
           http_server._indexer_ready = original_event
           http_server._indexer = original_indexer
           http_server._indexer_error = original_error
   ```

   b. On the existing `TestSearchEndpoint` class, add:

   ```python
   def test_search_returns_503_when_not_ready(self) -> None:
       """While the index is still building, /search returns 503 with a
       Retry-After header — not a 500 and not a hang."""
       import semantic_search.http_server as http_server

       original_event = http_server._indexer_ready
       original_indexer = http_server._indexer
       try:
           http_server._indexer_ready = asyncio.Event()  # unset
           http_server._indexer = None
           with TestClient(build_app()) as client:
               resp = client.get("/search?q=hello")
           assert resp.status_code == 503
           assert resp.headers.get("retry-after") == "5"
           data = resp.json()
           assert data["error"] == "indexing in progress"
           assert data["ready"] is False
       finally:
           http_server._indexer_ready = original_event
           http_server._indexer = original_indexer
   ```

   Add `import asyncio` at the top of `tests/test_http_server.py` if not present.

   **Important about existing tests:** the handlers now check `_indexer_ready` before calling `get_indexer()`, so existing "happy path" tests must pre-set the ready state. Update each test below by wrapping the `TestClient` block with save/restore of `http_server._indexer_ready` and `http_server._indexer`:

   ```python
   import semantic_search.http_server as http_server

   original_event = http_server._indexer_ready
   original_indexer = http_server._indexer
   try:
       ready_event = asyncio.Event()
       ready_event.set()
       http_server._indexer_ready = ready_event
       http_server._indexer = mock_indexer
       with TestClient(build_app()) as client:
           ...
   finally:
       http_server._indexer_ready = original_event
       http_server._indexer = original_indexer
   ```

   Apply to: `test_search_with_query`, `test_search_runs_in_threadpool`, `test_duplicates_with_file`, `test_duplicates_runs_in_threadpool`, `test_duplicates_indexer_returns_error_dict_returns_400`, `test_reindex_post`.

   For the existing `test_health_returns_ok`: the new `health` handler does NOT call `get_indexer()` at all — it reads module state directly. **Remove** the `patch("semantic_search.http_server.get_indexer")` context manager from that test and use the save/restore pattern above instead (same as the new `test_health_returns_ok_when_ready`). The `patch` is a no-op and will mislead future readers.

   Tests that do NOT need readiness wiring (they return 400 before touching the indexer): `test_search_missing_query_returns_400`, `test_duplicates_missing_file_returns_400`. Leave these untouched.

   For `test_reindex_post` specifically: the new `reindex` handler calls `_indexer.rebuild_index()` through `run_in_threadpool`, so the mock must have a `rebuild_index` attribute (MagicMock provides it automatically) and `meta` set post-reindex. No extra wiring needed beyond the readiness gate.

   Add `import asyncio` at the top of `tests/test_http_server.py` if not present.

10. **Strict mypy compliance.** The new module-level `_indexer_ready: asyncio.Event = asyncio.Event()` is typed explicitly. `_indexer_error: str | None = None` is typed explicitly. The `combined_lifespan` async context manager must be typed `AsyncIterator[None]`. `contextlib.asynccontextmanager` preserves the type. If mypy complains about `app: Starlette` in `combined_lifespan` not being used, keep the parameter (required by Starlette's lifespan protocol) and do NOT add `# type: ignore`. Run `make typecheck` locally to confirm.

11. **Update `CHANGELOG.md`** — `## Unreleased` already exists at the top of the file. Append a new bullet under it (do NOT add a second `## Unreleased` header, do NOT overwrite the existing bullets that may be present for the cache-dir change):

    ```
    - fix: HTTP server now binds its port immediately on startup and builds the initial vault index in a background task. `/health` returns `{"status":"indexing","ready":false,...}` during the build and `{"status":"ok","ready":true,...}` once done. `/search`, `/duplicates`, and `/reindex` return HTTP 503 with a `Retry-After: 5` header while the indexer is not yet ready. Fixes connection-refused / hung requests during cold start on large vaults.
    ```

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass after the updates described in requirement 9.
- Do NOT change the JSON response shape of the ready-path `/health` (adding the new `ready: true` field is acceptable; removing `status`, `paths`, or `indexed_files` is NOT).
- Do NOT change the JSON response shape of `/search` or `/duplicates` on the happy path — only the new 503 branch has a different shape.
- Do NOT change the MCP tool surface in `src/semantic_search/server.py`. This prompt only touches `http_server.py`, `tests/test_http_server.py`, and `CHANGELOG.md`.
- Do NOT change any CLI flag or argument parsing in `main()`.
- Do NOT use `asyncio.run(...)`, `loop.run_until_complete(...)`, or `threading.Thread(...)` to launch the build — use `asyncio.create_task` + `asyncio.to_thread` from inside the lifespan.
- Do NOT call `_indexer_ready.set()` from the worker thread — only from the lifespan coroutine's `finally` block (which runs on the loop thread).
- Do NOT introduce new top-level dependencies — `asyncio`, `contextlib`, `collections.abc` are stdlib; `starlette` is already a direct dep.
- Follow strict mypy typing — no new `type: ignore` comments without a justification comment on the same line.
- Repo-relative paths only.
- Out of scope: indexing progress percentage, cancelling in-flight indexing from a client, persisting readiness across restarts.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Specifically confirm:
- `tests/test_http_server.py::TestHealthEndpoint::test_health_returns_indexing_status_when_not_ready` passes.
- `tests/test_http_server.py::TestHealthEndpoint::test_health_returns_ok_when_ready` passes.
- `tests/test_http_server.py::TestSearchEndpoint::test_search_returns_503_when_not_ready` passes.
- All existing HTTP server tests still pass after the readiness-gate updates in requirement 9 (`test_health_returns_ok`, `test_search_with_query`, `test_search_runs_in_threadpool`, `test_duplicates_with_file`, `test_duplicates_runs_in_threadpool`, `test_duplicates_indexer_returns_error_dict_returns_400`, `test_reindex_post`, `test_mcp_endpoint_returns_400_for_bare_get_not_404`).
- `make test` (full suite) passes.
- `make typecheck` passes with zero mypy errors.
- `make lint` passes with zero ruff errors.
- Manual smoke check (not required to pass in CI but useful to note): `uvx --from . semantic-search-http --port 18321` followed immediately by `curl http://127.0.0.1:18321/health` should return a JSON response with `status: "indexing"` on a cold cache, and `status: "ok"` once the background build finishes.
</verification>
