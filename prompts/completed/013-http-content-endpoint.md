---
status: completed
spec: [001-content-fetch-endpoint]
summary: Added GET /content REST endpoint with path validation, readiness gating, snippet/query/context_lines params, threadpool execution, and nested error responses
container: semantic-search-exec-013-http-content-endpoint
dark-factory-version: v0.171.1-3-gd94f1fa
created: "2026-05-24T21:00:00Z"
queued: "2026-05-24T21:07:05Z"
started: "2026-05-24T21:09:03Z"
completed: "2026-05-24T21:10:17Z"
branch: dark-factory/content-fetch-endpoint
---

## Summary

- Add a new `GET /content` REST endpoint that accepts a path plus optional snippet, query, and context-line parameters
- Returns JSON with the file's path, content, and mode (full or snippet)
- Returns the "indexer not ready" 503 response when the index is still building, matching the existing pattern
- Delegates content reading to the core indexer method added in prompt 1, running it in the threadpool like the other endpoints
- Returns structured nested error responses (`{"error": {"code": "...", "message": "..."}}`) on validation failures

## Objective

Add the REST endpoint that exposes content-fetch capability over HTTP. The MCP tool (`get_content`) is a separate prompt. This prompt is HTTP-only.

## Context

Read these files before making changes:

- `/workspace/src/semantic_search/http_server.py` — existing endpoints (`search`, `duplicates`, `_not_ready_response`), the `run_in_threadpool` pattern for sync-to-async, and the readiness-gating pattern
- `/workspace/src/semantic_search/indexer.py` — the new `get_content()` method added in prompt 1
- `/workspace/tests/test_http_server.py` — existing test patterns for HTTP endpoints, including the readiness-gate tests and the module-level global patching pattern used to mock `_indexer`
- `/workspace/docs/dod.md` — DoD checklist

## Requirements

1. In `src/semantic_search/http_server.py`, add an `async def content(request: Request) -> JSONResponse:` handler function.

2. **Step ordering** — the handler MUST proceed in this exact order:
   a. Parse `path` from query params; if missing, return 400 with `{"error": {"code": "MISSING_PATH", "message": "Missing 'path' parameter"}}`. Do this FIRST.
   b. Gate on readiness; if not ready, return `_not_ready_response()`.
   c. Parse `snippet` (bool from `"true"`/`"false"`, default `False`), `query` (str or None), and `context_lines` (int, default `20`). On `int()` parse failure for `context_lines`, return 400 with `{"error": {"code": "INVALID_CONTEXT_LINES", "message": "..."}}` — do NOT let the ValueError bubble into the `get_content` error branch.
   d. Get the indexer via `get_indexer()` and call `get_content` inside `try`/`except` (see Requirement 3).

3. **Readiness gate** (between steps 2a and 2c):
   ```python
   if not _indexer_ready.is_set() or _indexer is None:
       return _not_ready_response()
   ```
   This returns 503 with `Retry-After: 5` — same pattern as `/search` and `/duplicates`.

4. **Call and error-handling skeleton** — use exactly this structure (the `try` wraps the `await`):
   ```python
   try:
       result = await run_in_threadpool(
           indexer.get_content, path, snippet, query, context_lines
       )
   except ValueError:
       logger.warning("path not in indexed roots: %s", path)
       return JSONResponse(
           {"error": {"code": "PATH_OUTSIDE_ROOTS", "message": "path not in indexed roots"}},
           status_code=400,
       )
   except FileNotFoundError:
       logger.info("file not found: %s", path)
       return JSONResponse(
           {"error": {"code": "FILE_NOT_FOUND", "message": f"file not found: {path}"}},
           status_code=404,
       )
   except RuntimeError:
       return JSONResponse(
           {"error": {"code": "UNREADABLE_FILE", "message": f"could not read file: {path}"}},
           status_code=422,
       )
   return JSONResponse(result, status_code=200)
   ```
   Note: `run_in_threadpool` propagates exceptions back to the awaiter — the `try/except` MUST wrap the `await`, never sit after it.

5. **Error response shape is locked**: `{"error": {"code": "<CODE>", "message": "..."}}` (nested). The spec acceptance criteria assert `data["error"]["code"] == "PATH_OUTSIDE_ROOTS"` etc. Do NOT flatten to `{"error": "...", "code": "..."}`.

6. Add the route to the Starlette routes list in `build_app()`:
   ```python
   Route("/content", content, methods=["GET"]),
   ```
   Place it alongside the other routes (after `/health`, before `/search`).

7. Update the `main()` logging to list the new endpoint:
   ```
   logger.info("  GET  /content?path=...&snippet=...&query=...&context_lines=...")
   ```

8. Follow `docs/dod.md` — docstring on the handler, type hints, no `print()`, no bare `except Exception`.

## Constraints

- Must use `run_in_threadpool` (same as `/search`, `/duplicates`) — no sync handlers
- Use exact error code strings: `"PATH_OUTSIDE_ROOTS"`, `"FILE_NOT_FOUND"`, `"UNREADABLE_FILE"` — these are referenced in acceptance criteria
- Return the same `_not_ready_response()` shape as other readiness-gated routes

## Verification

Run `make test` after each change. When complete, run `make precommit`.

```bash
cd /workspace && make test
cd /workspace && make precommit
```