---
status: completed
container: semantic-search-004-add-semantic-search-http-binary
dark-factory-version: v0.110.0
created: "2026-04-15T00:00:00Z"
queued: "2026-04-15T15:47:15Z"
started: "2026-04-15T15:47:20Z"
completed: "2026-04-15T15:53:55Z"
---

<summary>
- A new binary serves both MCP-over-HTTP and REST endpoints from a single process on one port
- Multiple Claude Code sessions can share one warm indexer instead of each spawning its own stdio MCP process
- Memory usage drops from roughly N times the model footprint to one shared footprint across all sessions
- The stdio MCP binary remains available unchanged for backwards compatibility
- The old dual-mode `--mode mcp|rest` flag is removed — server mode is now chosen by picking a binary, not a flag
- The standalone REST server module is removed; its handler logic migrates into the new unified HTTP server
- The one-shot CLI binary is unchanged
- The `fastmcp` dependency is upgraded from 2.x to 3.x (no source changes required — current usage is fully compatible)
- New tests cover the unified server's health, search, and MCP mount endpoints
- README explains the three-binary matrix and shows a Claude Code HTTP MCP config example
</summary>

<objective>
Introduce a new `semantic-search-http` binary that runs a single Starlette/uvicorn app on one port (default 8321) serving both the REST endpoints (`/search`, `/duplicates`, `/health`, `/reindex`) and MCP-over-HTTP at `/mcp`. Remove the `--mode`/`--port` flags from `semantic-search-mcp serve` so it only runs stdio MCP, and delete `rest_server.py` (its logic moves into the new module). Both protocols share the existing thread-safe warmed `VaultIndexer` singleton from `factory.py`, enabling multiple Claude Code sessions to connect to one long-running HTTP process instead of each spawning its own model-loading stdio subprocess. As part of this change, upgrade `fastmcp` from 2.x to 3.x so the new binary is built against the current major version from the start.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.14+, `uv`, strict mypy, src/ layout, dark-factory workflow).

Read these files before making changes:
- `src/semantic_search/rest_server.py` — existing REST handlers (`_handle_search`, `_handle_duplicates`, `_handle_health`, `_handle_reindex`) and their JSON response shapes. Response shapes MUST be preserved exactly. Pay particular attention to the `_handle_duplicates` branch near L112-114 that returns `JSONResponse({"error": ...}, status_code=400)` when `indexer.find_duplicates(...)` returns a dict containing an `"error"` key — that branch MUST be preserved in the new implementation AND exercised by a dedicated named test (see step 9).
- `src/semantic_search/server.py` — existing stdio MCP server. Defines `mcp = FastMCP("semantic-search")` with `@mcp.tool search_related` and `@mcp.tool check_duplicates`. Tool logic must be shared with the new HTTP server.
- `src/semantic_search/factory.py` — `create_indexer(content_paths)` is already a thread-safe singleton. Both protocols in the new binary MUST use this same function so they share one indexer.
- `src/semantic_search/__main__.py` — `main()` dispatches subcommands; `_serve()` currently parses `--mode`/`--port` and dispatches to MCP or REST.
- `src/semantic_search/logging_setup.py` — `configure_logging(level)` configures stdout logging.
- `pyproject.toml` — `[project.scripts]` currently has `semantic-search-mcp` and `semantic-search` entries. Dependencies list. `fastmcp>=2.12.4` is currently pinned; this prompt bumps it to `>=3.2.0` (see step 1).
- `tests/test_rest_server.py` — shows the test pattern (mock `get_indexer` via `patch`, construct handler, call `do_GET`/`do_POST`, parse JSON). Reuse the same mocking patterns.
- `tests/test_main.py` — shows CLI subcommand tests using `monkeypatch.setattr(sys, "argv", [...])`.
- `tests/test_imports.py` — contains `test_fastmcp_import` which simply imports `FastMCP`. It must continue to pass on fastmcp 3.x.
- `README.md` — has sections "Server Modes" (MCP Mode, REST Mode), "CLI Commands", "Two Binaries". These sections need updating to a three-binary matrix.
- `CHANGELOG.md` — add entry under `## Unreleased`.

Verified facts about the environment:
- On fastmcp 3.x, `FastMCP("name").http_app(path="/mcp")` still returns a `Starlette`-based app (`StarletteWithLifespan`) that can be mounted via `starlette.routing.Mount`. The `http_app(path=...)` API is unchanged from 2.x.
- `starlette` and `uvicorn` arrive transitively via `fastmcp`, but they are NOT listed as direct dependencies in `pyproject.toml`. Add them as direct dependencies — this binary depends on them explicitly.
- On fastmcp 3.x streamable-http transport, a plain `GET /mcp` without the required MCP handshake headers (no `Accept: text/event-stream`, no session init) does NOT return 404 — the route is mounted. It typically returns `406 Not Acceptable` (missing SSE accept header) or `400 Bad Request` (malformed/unrecognized request). A bare `POST /mcp` without a valid JSON-RPC body likewise returns `400`. Importantly, it never returns `405 Method Not Allowed` (both GET and POST are accepted by the transport). Tests MUST assert on a specific expected status, not a negative `!= 404`.

**fastmcp 2.x → 3.x compatibility — why this upgrade is safe for this codebase:**

The source code currently uses only the following fastmcp APIs, all of which are unchanged in 3.x:
- `from fastmcp import FastMCP`
- `FastMCP("semantic-search")` — positional name argument only, no constructor kwargs. (The fastmcp 3.x breaking change that removed certain constructor kwargs does not apply here.)
- `@mcp.tool` used purely as a decorator. The return value is never inspected — we do not read `.name` or `.description` on the decorated result, so the 3.x change to the decorator's return type has no effect.
- `mcp.run()` called with no arguments (stdio default).
- `mcp.http_app(path="/mcp")` — still supported in 3.x.

None of the known fastmcp 2→3 breaking changes apply to this codebase:
- async context / `ctx` state changes — not used
- removal of `get_tools()` / `get_resources()` / `get_prompts()` — not used
- `WSTransport` removal — not used (we use stdio and streamable HTTP)
- OAuth environment autoload — no OAuth config
- removal of `tool.enable()` / `tool.disable()` — not used
- prompts API change — no prompts registered

Therefore the upgrade requires only a version bump in `pyproject.toml` plus a lockfile refresh — no source changes.
</context>

<requirements>
1. **Bump `fastmcp` to 3.x FIRST, before writing any new code.** This keeps the lockfile clean and ensures the new `http_server.py` is built and tested against the upgraded version from the first line.

   - In `pyproject.toml`, under `[project.dependencies]`, change `"fastmcp>=2.12.4"` to `"fastmcp>=3.2.0"`.
   - Refresh the lockfile so `uv.lock` records a fastmcp 3.x resolution. Run:
     ```bash
     uv lock --upgrade-package fastmcp
     ```
     If that command is unavailable or fails, fall back to `uv sync --upgrade-package fastmcp` (or regenerate with `uv lock` after confirming the pin). The goal: `uv.lock` must resolve `fastmcp` to a 3.x version.
   - Verify by running `make test` (or at minimum `uv run pytest tests/test_imports.py::test_fastmcp_import`) and confirming `test_fastmcp_import` still passes.
   - Do NOT modify `src/semantic_search/server.py` for this upgrade — the existing usage (`FastMCP("semantic-search")`, `@mcp.tool`, `mcp.run()`) is fully compatible with 3.x. See the context block for the full compatibility analysis.
   - If any existing test fails after the bump, stop and investigate — do NOT edit source code to paper over a real incompatibility without auditing against the breaking-change list in the context block.

2. **Create `src/semantic_search/http_server.py`** — new module that unifies REST + MCP-over-HTTP on one Starlette app. Structure:

   ```python
   """Unified HTTP server: REST endpoints + MCP-over-HTTP on one port."""

   import json
   import logging
   import os
   from typing import Any

   from starlette.applications import Starlette
   from starlette.requests import Request
   from starlette.responses import JSONResponse
   from starlette.routing import Mount, Route

   from .factory import create_indexer
   from .indexer import VaultIndexer
   from .server import mcp  # reuse the existing FastMCP instance with tools registered

   logger = logging.getLogger(__name__)

   _raw_paths = os.environ.get("CONTENT_PATH", "./content")
   CONTENT_PATHS = [p.strip() for p in _raw_paths.split(",") if p.strip()]

   _indexer: VaultIndexer | None = None


   def get_indexer() -> VaultIndexer:
       global _indexer
       if _indexer is None:
           logger.info(f"Creating indexer for paths: {CONTENT_PATHS}")
           _indexer = create_indexer(CONTENT_PATHS)
       return _indexer
   ```

   Implement Starlette route handlers that preserve the exact JSON shapes from `rest_server.py`:

   - `async def health(request: Request) -> JSONResponse` returns `{"status": "ok", "paths": CONTENT_PATHS, "indexed_files": len(indexer.meta)}`.
   - `async def search(request: Request) -> JSONResponse` reads `q` (required) and `top_k` (default `5`) from `request.query_params`; on missing `q` return `JSONResponse({"error": "Missing 'q' parameter"}, status_code=400)`; on success return `{"query": q, "results": results, "count": len(results)}`.
   - `async def duplicates(request: Request) -> JSONResponse` reads `file` (required) and `threshold` (default `0.85`) from `request.query_params`; on missing `file` return a 400 error with the same shape as above; set `indexer.duplicate_threshold = threshold`; if `indexer.find_duplicates(file_path)` returns a dict containing `"error"`, return that as a 400 JSON error (this preserves the existing `rest_server.py` L112-114 behavior — do NOT silently drop this branch); otherwise return `{"file": file_path, "threshold": threshold, "duplicates": results, "count": len(results)}`.
   - `async def reindex(request: Request) -> JSONResponse` — resets the module-level `_indexer` to `None`, calls `get_indexer()` to rebuild, returns `{"status": "ok", "message": "Reindex complete", "indexed_files": len(indexer.meta)}`. Handle both GET and POST on the same route.

   Each handler MUST wrap its body in `try/except Exception` that logs via `logger.exception(...)` and returns `JSONResponse({"error": str(e)}, status_code=500)` — matching the existing `do_GET`/`do_POST` error behavior in `rest_server.py`.

3. **Build the combined app** in `http_server.py` via a `build_app()` factory function:

   ```python
   def build_app() -> Starlette:
       mcp_app = mcp.http_app(path="/mcp")
       routes = [
           Route("/health", health, methods=["GET"]),
           Route("/search", search, methods=["GET"]),
           Route("/duplicates", duplicates, methods=["GET"]),
           Route("/reindex", reindex, methods=["GET", "POST"]),
           Mount("/", app=mcp_app),
       ]
       # IMPORTANT: pass the MCP app's lifespan so FastMCP's session manager starts.
       return Starlette(routes=routes, lifespan=mcp_app.lifespan)
   ```

   Mount order matters: explicit REST `Route`s come first so they take precedence over the catch-all `Mount("/")`. The `Mount("/")` makes the MCP app handle `/mcp` (and any sub-paths FastMCP requires). Using `lifespan=mcp_app.lifespan` is REQUIRED — without it FastMCP's session manager never initializes and the `/mcp` endpoint returns 500 errors.

4. **Add the `main()` entry point** in `http_server.py`:

   ```python
   def main() -> None:
       import argparse
       import uvicorn

       from .logging_setup import configure_logging

       configure_logging(os.environ.get("LOG_LEVEL", "INFO"))

       parser = argparse.ArgumentParser(
           prog="semantic-search-http",
           description="Unified HTTP server: REST endpoints + MCP-over-HTTP on one port",
       )
       parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
       parser.add_argument("--port", type=int, default=8321, help="Port to bind (default: 8321)")
       args = parser.parse_args()

       logger.info(f"Building index for: {CONTENT_PATHS}")
       get_indexer()  # pre-warm the singleton before accepting requests

       app = build_app()
       logger.info(f"Serving REST + MCP on http://{args.host}:{args.port}")
       logger.info("  GET  /health")
       logger.info("  GET  /search?q=...&top_k=5")
       logger.info("  GET  /duplicates?file=...&threshold=0.85")
       logger.info("  GET/POST /reindex")
       logger.info("  MCP  /mcp  (streamable HTTP transport)")
       uvicorn.run(app, host=args.host, port=args.port)
   ```

   Default host is `127.0.0.1` (loopback only — this is a per-user local service; do not default to `0.0.0.0`).

5. **Update `pyproject.toml` (beyond the fastmcp bump in step 1):**
   - In `[project.dependencies]`, add `"starlette"` and `"uvicorn"` as explicit direct dependencies (they arrive transitively today but this binary uses them directly).
   - In `[project.scripts]`, add one line: `semantic-search-http = "semantic_search.http_server:main"`. Keep existing `semantic-search-mcp` and `semantic-search` entries unchanged.

6. **Delete `src/semantic_search/rest_server.py`** entirely. Also delete `tests/test_rest_server.py` entirely — its tests are replaced by new ones in step 9. Do not leave a backwards-compat shim. When migrating these tests, you MUST explicitly preserve coverage of the duplicate-error-dict 400 branch (see step 9's `test_duplicates_indexer_returns_error_dict_returns_400`) — this is not a generic "missing file" case, it's the branch where `indexer.find_duplicates(...)` returns `{"error": "..."}` and the handler must forward it as a 400 JSON response.

7. **Modify `src/semantic_search/__main__.py`:**
   - In `_serve()`, remove the `--mode` and `--port` argparse arguments. The function body should now be simply:
     ```python
     def _serve() -> None:
         """Start the stdio MCP server."""
         from .server import run as run_mcp
         run_mcp()
     ```
     (No argparse, no imports of `rest_server`.)
   - In `_print_usage()`, remove all references to `--mode`, `--port`, and the `semantic-search-mcp serve --mode rest*` examples. Replace with a single `serve` command description. Add one line mentioning the three binaries so users can discover `semantic-search-http`. Example:
     ```
     Note: this binary runs stdio MCP only. For HTTP (REST + MCP-over-HTTP),
     use `semantic-search-http`. For one-shot CLI, use `semantic-search`.
     ```
   - Keep `main()`, `main_cli()`, and the `search`/`duplicates` subcommand dispatch intact.

8. **Error paths and failure handling** (all must be implemented):
   - If `CONTENT_PATH` is unset, the existing default `"./content"` applies — no change needed, but verify `CONTENT_PATHS` is non-empty before using it. If empty, log a warning and let `create_indexer` handle it (existing behavior).
   - If `uvicorn.run` fails to bind (port already in use), let the exception propagate; the `configure_logging` formatter will surface it. Do not catch `OSError` in `main()`.
   - Handle `KeyboardInterrupt` gracefully by letting uvicorn handle its own shutdown — no custom signal handling needed (uvicorn installs SIGINT/SIGTERM handlers).
   - Each REST handler catches `Exception` (step 2) and returns a 500 JSON response; this mirrors the existing `rest_server.py` behavior and prevents handler crashes from tearing down the server.

9. **Add `tests/test_http_server.py`** using Starlette's `TestClient` (imported from `starlette.testclient`, which uses `httpx` under the hood — already a transitive dep via fastmcp). Required tests:

   ```python
   """Tests for unified HTTP server."""

   from unittest.mock import MagicMock, patch

   from starlette.testclient import TestClient

   from semantic_search.http_server import build_app


   class TestHealthEndpoint:
       def test_health_returns_ok(self) -> None:
           with patch("semantic_search.http_server.get_indexer") as mock_get:
               mock_indexer = MagicMock()
               mock_indexer.meta = {"0": {}, "1": {}}
               mock_get.return_value = mock_indexer
               with TestClient(build_app()) as client:
                   resp = client.get("/health")
           assert resp.status_code == 200
           data = resp.json()
           assert data["status"] == "ok"
           assert "paths" in data
           assert data["indexed_files"] == 2


   class TestSearchEndpoint:
       def test_search_missing_query_returns_400(self) -> None:
           with TestClient(build_app()) as client:
               resp = client.get("/search")
           assert resp.status_code == 400
           assert "Missing 'q' parameter" in resp.json()["error"]

       def test_search_with_query(self) -> None:
           with patch("semantic_search.http_server.get_indexer") as mock_get:
               mock_indexer = MagicMock()
               mock_indexer.search.return_value = [
                   {"path": "a.md", "score": 0.9},
                   {"path": "b.md", "score": 0.8},
               ]
               mock_get.return_value = mock_indexer
               with TestClient(build_app()) as client:
                   resp = client.get("/search?q=test+query&top_k=3")
           assert resp.status_code == 200
           data = resp.json()
           assert data["query"] == "test query"
           assert data["count"] == 2
           mock_indexer.search.assert_called_once_with("test query", 3)


   class TestDuplicatesEndpoint:
       def test_duplicates_missing_file_returns_400(self) -> None:
           with TestClient(build_app()) as client:
               resp = client.get("/duplicates")
           assert resp.status_code == 400
           assert "Missing 'file' parameter" in resp.json()["error"]

       def test_duplicates_with_file(self) -> None:
           with patch("semantic_search.http_server.get_indexer") as mock_get:
               mock_indexer = MagicMock()
               mock_indexer.find_duplicates.return_value = [
                   {"path": "similar.md", "score": 0.95}
               ]
               mock_get.return_value = mock_indexer
               with TestClient(build_app()) as client:
                   resp = client.get("/duplicates?file=note.md&threshold=0.9")
           assert resp.status_code == 200
           data = resp.json()
           assert data["file"] == "note.md"
           assert data["threshold"] == 0.9
           assert data["count"] == 1

       def test_duplicates_indexer_returns_error_dict_returns_400(self) -> None:
           """Preserves rest_server.py L112-114: when indexer.find_duplicates returns
           a dict with an 'error' key (e.g., file not indexed), the handler must
           forward it as a 400 JSON response, not a 200 success.
           """
           with patch("semantic_search.http_server.get_indexer") as mock_get:
               mock_indexer = MagicMock()
               mock_indexer.find_duplicates.return_value = {
                   "error": "File not found in index: missing.md"
               }
               mock_get.return_value = mock_indexer
               with TestClient(build_app()) as client:
                   resp = client.get("/duplicates?file=missing.md")
           assert resp.status_code == 400
           data = resp.json()
           assert "error" in data
           assert "missing.md" in data["error"]


   class TestReindexEndpoint:
       def test_reindex_post(self) -> None:
           with patch("semantic_search.http_server.get_indexer") as mock_get:
               mock_indexer = MagicMock()
               mock_indexer.meta = {}
               mock_get.return_value = mock_indexer
               with TestClient(build_app()) as client:
                   resp = client.post("/reindex")
           assert resp.status_code == 200
           assert resp.json()["status"] == "ok"


   class TestMcpMount:
       def test_mcp_endpoint_returns_400_for_bare_get_not_404(self) -> None:
           """MCP endpoint must be mounted and handled by fastmcp's streamable-http
           transport — NOT routed to Starlette's 404 handler.

           Expected behavior on fastmcp 3.x streamable-http: a bare `GET /mcp`
           without the required MCP handshake headers (no Accept: text/event-stream,
           no session init) is rejected by the MCP transport with HTTP 400
           (Bad Request) or 406 (Not Acceptable) — never 404, and never 405
           (both GET and POST are accepted by the transport).

           We assert the response is one of {400, 406} to tolerate minor version
           differences in the exact status code chosen by fastmcp, while proving
           the route is mounted and reaching the MCP handler.
           """
           with TestClient(build_app()) as client:
               resp = client.get("/mcp")
           assert resp.status_code in {400, 406}, (
               f"Expected 400 or 406 from mounted MCP handler, got {resp.status_code}. "
               f"A 404 means the route is not mounted; a 405 means the transport "
               f"rejected the method, which contradicts fastmcp streamable-http behavior."
           )
   ```

   Important: the `TestMcpMount` test MUST use `with TestClient(build_app()) as client:` (context manager form) so Starlette's lifespan runs and FastMCP initializes its session manager. Without the `with` block, `/mcp` may return 500 "session manager not initialized".

10. **Update `tests/test_main.py`** — add a test that `_serve()` no longer accepts `--mode` or `--port`. Since `_serve()` is now argument-less and calls `run_mcp()`, add the following test. Note the subtlety: `_serve()` does `from .server import run as run_mcp`, which re-binds the name `run_mcp` locally at call time. Patching the local name in `__main__` (e.g. `monkeypatch.setattr("semantic_search.__main__.run_mcp", ...)`) will NOT work — the local import inside `_serve()` re-fetches the attribute from `semantic_search.server` on every call. The only reliable patch target is the source attribute `semantic_search.server.run`:

    ```python
    import sys
    from unittest.mock import patch


    def test_serve_invokes_stdio_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
        """`serve` subcommand must call the stdio MCP run function with no args.

        `_serve()` does `from .server import run as run_mcp` at call time, so we
        patch `semantic_search.server.run` (the SOURCE attribute) rather than any
        name in `__main__` — patching in `__main__` would be clobbered by the
        local re-import inside `_serve()`.
        """
        import semantic_search.__main__ as main_module

        monkeypatch.setattr(sys, "argv", ["semantic-search-mcp", "serve"])

        with patch("semantic_search.server.run") as mock_run:
            main_module.main()

        mock_run.assert_called_once_with()
    ```

    Do NOT use `monkeypatch.setattr("semantic_search.server.run", fake_run)` — while monkeypatch can also target that path, the deliberate use of `unittest.mock.patch` as a context manager here documents the intent (patch the source module attribute, scoped to the `main()` call) and matches the pattern used elsewhere in the suite for patching cross-module imports.

    Do NOT add tests that pass `--mode` or `--port` — those flags no longer exist.

11. **Update `README.md`:**
    - Replace the opening "Supports two server modes" bullet list with a three-binary description mentioning stdio MCP, HTTP (REST + MCP-over-HTTP), and one-shot CLI.
    - Replace the "Server Modes" section with two subsections:
      - `### Stdio MCP (one-off spawned per session)` — shows the existing `claude mcp add` command with `semantic-search-mcp serve`.
      - `### HTTP Server (shared across sessions — recommended)` — shows starting `CONTENT_PATH=/path/to/vault semantic-search-http --port 8321` and the Claude Code MCP config snippet:
        ```json
        {
          "mcpServers": {
            "semantic-search": {
              "type": "http",
              "url": "http://127.0.0.1:8321/mcp"
            }
          }
        }
        ```
        Explain the memory benefit: one warm process shared by N sessions vs N copies of torch+sentence-transformers.
    - In the REST endpoints table, update the example URLs to `http://127.0.0.1:8321/...` and note that the same server exposes `/mcp` for MCP-over-HTTP.
    - Replace the "Two Binaries" section with a "Three Binaries" matrix:

      | Binary | Purpose |
      |--------|---------|
      | `semantic-search-mcp` | Stdio MCP server (one per Claude Code session) |
      | `semantic-search-http` | Unified HTTP server: REST + MCP-over-HTTP on one port (shared across sessions) |
      | `semantic-search` | One-shot CLI — `search` and `duplicates` |

12. **Update `CHANGELOG.md`** — under `## Unreleased`, add:
    - `feat: Add "semantic-search-http" binary serving REST and MCP-over-HTTP on a single port (default 8321), enabling multiple Claude Code sessions to share one warm indexer process.`
    - `breaking: Remove "--mode" and "--port" flags from "semantic-search-mcp serve". The stdio MCP binary now only runs stdio MCP; use "semantic-search-http" for HTTP/REST.`
    - `refactor: Remove src/semantic_search/rest_server.py — its handlers migrated into the unified http_server module.`
    - `deps: Upgrade fastmcp from >=2.12.4 to >=3.2.0. No source changes required — existing usage (FastMCP constructor, @mcp.tool decorator, mcp.run(), mcp.http_app) is fully compatible with fastmcp 3.x.`

13. **Type checking compliance:** strict mypy is enabled. All new functions must have full type annotations. The `search`/`duplicates`/`health`/`reindex` handlers must be typed `async def name(request: Request) -> JSONResponse`. `build_app` returns `Starlette`. `main` returns `None`. `get_indexer` returns `VaultIndexer`. The existing mypy overrides in `pyproject.toml` (`faiss.*`, `sentence_transformers.*`) are unrelated and should not be touched.
</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass (except for `tests/test_rest_server.py` which is deleted in step 6).
- `tests/test_imports.py::test_fastmcp_import` MUST continue to pass after the fastmcp 2→3 bump.
- Do not change `src/semantic_search/server.py` (the stdio MCP module) — the new HTTP module imports `mcp` from it and reuses the registered tools. The fastmcp 3.x upgrade requires no changes to this file (see context block).
- Do not change `src/semantic_search/factory.py` — `create_indexer` is already thread-safe and shared.
- Do not change the JSON response shapes of any existing REST endpoint. Consumers (OpenClaw, scripts) rely on exact field names and nesting.
- Do not change the `semantic-search` CLI binary or its subcommands.
- Do not introduce new top-level dependencies beyond `starlette`, `uvicorn`, and the fastmcp version bump.
- Do not default the HTTP server to `0.0.0.0` — this is a loopback per-user service; default `127.0.0.1`.
- Do not use absolute or home-relative paths anywhere in code or tests (repo-relative only).
- Follow strict mypy typing — every new function must have full annotations.
- Preserve the existing error-handling pattern (`logger.exception` + 500 JSON) from `rest_server.py` in the new handlers.
- Preserve the existing `rest_server.py` L112-114 branch where `indexer.find_duplicates` returning a dict with `"error"` is forwarded as a 400 — and exercise it with a dedicated named test (`test_duplicates_indexer_returns_error_dict_returns_400`), not a generic "missing file" case.
- When mounting the MCP app, the Starlette app MUST pass `lifespan=mcp_app.lifespan` or FastMCP's session manager will not initialize and `/mcp` requests will fail.
- The `/mcp` mount test must assert on a specific expected status (400 or 406), not a negative `!= 404`. Assertions like `status_code != 404` pass for server crashes (500), proxy errors (502), etc. and do not prove the MCP handler is actually reached.
- When patching `semantic_search.server.run` for the `_serve()` test, patch the SOURCE attribute (`semantic_search.server.run`) via `unittest.mock.patch`, not a name in `__main__` — `_serve()` re-imports at call time and any `__main__`-scoped patch will be bypassed.
- Bump `fastmcp` BEFORE writing new code (step 1) so the new module is built against 3.x from the start and the lockfile contains exactly one coherent resolution.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Specifically confirm:
- `uv.lock` resolves `fastmcp` to a 3.x version (e.g., grep `uv.lock` for `name = "fastmcp"` and confirm the adjacent `version = "3.x.y"`).
- `tests/test_imports.py::test_fastmcp_import` passes.
- All new tests in `tests/test_http_server.py` pass, including the dedicated `test_duplicates_indexer_returns_error_dict_returns_400` and the specific-status `test_mcp_endpoint_returns_400_for_bare_get_not_404`.
- `tests/test_main.py::test_serve_invokes_stdio_mcp` passes with the `patch("semantic_search.server.run")` target (source-module patching).
- `make test` (full suite) passes.

Manual smoke test (optional, not part of automated verification):

```bash
CONTENT_PATH=./content uv run semantic-search-http --port 8321 &
sleep 3
curl -s http://127.0.0.1:8321/health | grep -q '"status": "ok"'
curl -s 'http://127.0.0.1:8321/search?q=test' | grep -q '"results"'
curl -sI http://127.0.0.1:8321/mcp | head -1  # should NOT be 404
kill %1
```
</verification>
