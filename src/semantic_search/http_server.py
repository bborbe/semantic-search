"""Unified HTTP server: REST endpoints + MCP-over-HTTP on one port."""

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ._version import __version__
from .factory import create_indexer, declare_index_roots
from .indexer import VaultIndexer
from .scopes import (
    MissingScopeError,
    ScopeConfigError,
    ScopeMap,
    ScopeRequestError,
    UnknownScopeError,
    load_scope_map,
    mark_http_transport,
    reset_request_roots,
    resolve_scope,
    scope_map_path_from_env,
    set_request_roots,
    validate_scope_map,
)
from .server import mcp  # reuse the existing FastMCP instance with tools registered

logger = logging.getLogger(__name__)

MISSING_SCOPE = "MISSING_SCOPE"
UNKNOWN_SCOPE = "UNKNOWN_SCOPE"

_indexer: VaultIndexer | None = None
_indexer_ready: asyncio.Event = asyncio.Event()
_indexer_error: str | None = None


def get_indexer() -> VaultIndexer:
    """Return the ready indexer instance, or raise RuntimeError if not ready.

    The indexer is built in a background task launched from the Starlette
    lifespan (see `_build_indexer_in_background`). Handlers MUST gate on
    `_indexer_ready.is_set()` before calling this.
    """
    if _indexer is None:
        raise RuntimeError("Indexer not initialized yet")
    return _indexer


async def _build_indexer_in_background(roots: tuple[Path, ...]) -> None:
    """Build the VaultIndexer over the union roots in a worker thread, then mark ready.

    Called from the Starlette lifespan so the server can bind its port
    immediately while the (slow, blocking) initial embedding pass runs.

    Args:
        roots: The union of every declared scope's roots, in union order.
    """
    global _indexer, _indexer_error
    if _indexer_ready.is_set():
        return
    root_strs = [str(p) for p in roots]
    logger.info(f"Indexer build starting in background for paths: {root_strs}")
    try:
        _indexer = await asyncio.to_thread(create_indexer, root_strs)
        logger.info(f"Indexer build complete: {len(_indexer.meta)} files indexed")
    except Exception as e:
        _indexer_error = str(e)
        logger.exception("Indexer build failed")
    finally:
        _indexer_ready.set()


def _not_ready_response() -> JSONResponse:
    """503 response returned while the initial index build is in flight."""
    return JSONResponse(
        {"error": "indexing in progress", "ready": False},
        status_code=503,
        headers={"Retry-After": "5"},
    )


def _scope_error_token(exc: ScopeRequestError) -> str:
    """Return the HTTP refusal token for a scope resolution failure."""
    if isinstance(exc, MissingScopeError):
        return MISSING_SCOPE
    if isinstance(exc, UnknownScopeError):
        return UNKNOWN_SCOPE
    raise TypeError(f"unhandled scope error type: {type(exc).__name__}")


class _MCPScopeMiddleware:
    """Pure ASGI middleware that scopes every request to the MCP mount.

    The MCP mount is served at `/mcp` and any sub-path of it; every other path
    (the REST routes) passes through untouched, and so does every non-HTTP
    scope — Starlette's lifespan travels through the same middleware stack as
    a scope with no `"path"` key, so the type check must come first.

    For an MCP request the `?scope=` query parameter is resolved against the
    injected scope map *before* the MCP protocol layer runs: a request naming
    no scope is refused with HTTP 400 (`MISSING_SCOPE`), a request naming an
    unknown scope is refused with HTTP 400 (`UNKNOWN_SCOPE`), and a valid
    scope's roots are bound via `set_request_roots` for the downstream app.
    The binding is a context variable, so the value a tool body reads is the
    one bound on the request that established its MCP session, and two
    concurrent sessions never observe each other's roots.
    """

    def __init__(self, app: ASGIApp, scope_map: ScopeMap) -> None:
        self.app = app
        self.scope_map = scope_map

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path != "/mcp" and not path.startswith("/mcp/"):
            await self.app(scope, receive, send)
            return
        raw_scope = parse_qs(scope.get("query_string", b"").decode("latin-1")).get("scope", [None])[
            0
        ]
        try:
            roots = resolve_scope(self.scope_map, raw_scope)
        except ScopeRequestError as exc:
            response = JSONResponse({"error": _scope_error_token(exc)}, status_code=400)
            await response(scope, receive, send)
            return
        token = set_request_roots(roots)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_roots(token)


async def reindex(request: Request) -> JSONResponse:
    """Handle /reindex endpoint.

    Blocks until the reindex completes only on the branch that actually
    performs the rebuild (when no rebuild is in flight). If a rebuild is
    already running — an automatic compaction or an earlier manual reindex —
    returns HTTP 409 immediately with REINDEX_IN_PROGRESS rather than
    starting a second one or holding the connection open for minutes.

    Returns 503 if the initial index build is still running — the client
    cannot meaningfully reindex something that has not finished indexing
    the first time.
    """
    global _indexer
    if not _indexer_ready.is_set() or _indexer is None:
        return _not_ready_response()
    try:
        logger.info("Forcing reindex...")
        rebuilt = await run_in_threadpool(_indexer.force_rebuild)
        if not rebuilt:
            return JSONResponse(
                {
                    "error": {
                        "code": "REINDEX_IN_PROGRESS",
                        "message": (
                            "Another index rebuild is already in progress; retry once it completes"
                        ),
                    }
                },
                status_code=409,
            )
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


def build_app(scope_map: ScopeMap) -> Starlette:
    """Build the unified Starlette app with REST routes and MCP mount.

    Args:
        scope_map: The committed scope map. The routes close over it: each
            request to /search, /duplicates, or /content has its `scope`
            parameter resolved against it, and a request naming no scope or
            an unknown scope is refused with HTTP 400 (MISSING_SCOPE /
            UNKNOWN_SCOPE) before the readiness gate. A valid scope's roots
            are passed into the indexer call as a per-request argument, so
            each read path answers only from that scope's roots.
    """
    mark_http_transport()
    mcp_app = mcp.http_app(path="/mcp")
    union_root_strs = [str(p) for p in scope_map.union_roots]

    async def health(request: Request) -> JSONResponse:
        """Handle /health endpoint. Never blocks on indexer construction.

        Reports the union root set and stays scopeless: a `scope` parameter,
        if present, does not change the response.
        """
        if _indexer_error is not None:
            return JSONResponse(
                {
                    "status": "error",
                    "ready": False,
                    "error": _indexer_error,
                    "paths": union_root_strs,
                },
                status_code=500,
            )
        if not _indexer_ready.is_set() or _indexer is None:
            return JSONResponse(
                {
                    "status": "indexing",
                    "ready": False,
                    "paths": union_root_strs,
                }
            )
        return JSONResponse(
            {
                "status": "ok",
                "ready": True,
                "paths": union_root_strs,
                "indexed_files": len(_indexer.meta),
            }
        )

    async def search(request: Request) -> JSONResponse:
        """Handle /search endpoint."""
        try:
            q = request.query_params.get("q")
            if not q:
                return JSONResponse({"error": "Missing 'q' parameter"}, status_code=400)

            # scope gate: resolve the scope (refusing a missing or unknown
            # scope) before the readiness gate, then pass its roots through
            # to the search as a per-request argument.
            try:
                roots = resolve_scope(scope_map, request.query_params.get("scope"))
            except ScopeRequestError as e:
                return JSONResponse({"error": _scope_error_token(e)}, status_code=400)

            # gate on readiness before touching the indexer
            if not _indexer_ready.is_set() or _indexer is None:
                return _not_ready_response()

            top_k = int(request.query_params.get("top_k", "5"))
            indexer = get_indexer()
            results: list[Any] = await run_in_threadpool(indexer.search, q, top_k, roots)
            return JSONResponse({"query": q, "results": results, "count": len(results)})
        except Exception as e:
            logger.exception("Error handling /search request")
            return JSONResponse({"error": str(e)}, status_code=500)

    async def duplicates(request: Request) -> JSONResponse:
        """Handle /duplicates endpoint."""
        try:
            file_path = request.query_params.get("file")
            if not file_path:
                return JSONResponse({"error": "Missing 'file' parameter"}, status_code=400)

            # scope gate: resolve the scope (refusing a missing or unknown
            # scope) before the readiness gate, then pass its roots through
            # to the duplicate check as a per-request argument.
            try:
                roots = resolve_scope(scope_map, request.query_params.get("scope"))
            except ScopeRequestError as e:
                return JSONResponse({"error": _scope_error_token(e)}, status_code=400)

            # gate on readiness before touching the indexer
            if not _indexer_ready.is_set() or _indexer is None:
                return _not_ready_response()

            threshold = float(request.query_params.get("threshold", "0.85"))
            indexer = get_indexer()
            indexer.duplicate_threshold = threshold
            results = await run_in_threadpool(indexer.find_duplicates, file_path, roots)

            if isinstance(results, dict) and "error" in results:
                return JSONResponse({"error": str(results["error"])}, status_code=400)

            return JSONResponse(
                {
                    "file": file_path,
                    "threshold": threshold,
                    "duplicates": results,
                    "count": len(results),
                }
            )
        except Exception as e:
            logger.exception("Error handling /duplicates request")
            return JSONResponse({"error": str(e)}, status_code=500)

    async def content(request: Request) -> JSONResponse:
        """Handle /content endpoint.

        Returns file content for a given path, optionally as a snippet around
        the best-matching line for the given query.
        """
        # Step 1: parse path (must be first)
        path = request.query_params.get("path")
        if not path:
            return JSONResponse(
                {"error": {"code": "MISSING_PATH", "message": "Missing 'path' parameter"}},
                status_code=400,
            )

        # Step 2: scope gate (resolve the scope and pass its roots through)
        try:
            roots = resolve_scope(scope_map, request.query_params.get("scope"))
        except ScopeRequestError as e:
            return JSONResponse({"error": _scope_error_token(e)}, status_code=400)

        # Step 3: gate on readiness
        if not _indexer_ready.is_set() or _indexer is None:
            return _not_ready_response()

        # Step 4: parse remaining params
        snippet_str = request.query_params.get("snippet", "false")
        snippet = snippet_str.lower() == "true"

        query = request.query_params.get("query")
        if query is not None and query.strip() == "":
            query = None

        context_lines_str = request.query_params.get("context_lines", "20")
        try:
            context_lines = int(context_lines_str)
        except ValueError:
            return JSONResponse(
                {
                    "error": {
                        "code": "INVALID_CONTEXT_LINES",
                        "message": "Invalid 'context_lines' parameter",
                    }
                },
                status_code=400,
            )

        # Step 5: call get_content in threadpool
        indexer = get_indexer()
        try:
            result = await run_in_threadpool(
                indexer.get_content, path, snippet, query, context_lines, roots
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

    @contextlib.asynccontextmanager
    async def combined_lifespan(app: Starlette) -> AsyncIterator[None]:
        # Launch the indexer build as a background task — do NOT await it.
        # The server binds its port as soon as this lifespan yields.
        task = asyncio.create_task(_build_indexer_in_background(scope_map.union_roots))
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
        Route("/content", content, methods=["GET"]),
        Route("/search", search, methods=["GET"]),
        Route("/duplicates", duplicates, methods=["GET"]),
        Route("/reindex", reindex, methods=["GET", "POST"]),
        Mount("/", app=mcp_app),
    ]
    return Starlette(
        routes=routes,
        lifespan=combined_lifespan,
        middleware=[Middleware(_MCPScopeMiddleware, scope_map=scope_map)],
    )


def main() -> None:
    """Entry point for semantic-search-http binary.

    The composition root: reads and validates the scope map named by
    SEMANTIC_SCOPE_MAP, declares the union root set, then builds the app.
    A missing or unusable scope map logs an ERROR (naming the scope and the
    root) and exits non-zero before any port is bound. `--version` exits 0
    without touching the scope map.
    """
    import uvicorn

    from .logging_setup import configure_logging

    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))

    parser = argparse.ArgumentParser(
        prog="semantic-search-http",
        description="Unified HTTP server: REST endpoints + MCP-over-HTTP on one port",
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"semantic-search-http v{__version__}",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8321, help="Port to bind (default: 8321)")
    args = parser.parse_args()

    try:
        scope_map_path = scope_map_path_from_env()
        scope_map = load_scope_map(scope_map_path)
        validate_scope_map(scope_map)
    except ScopeConfigError as e:
        logger.error(f"Invalid scope map: {e}")
        sys.exit(1)

    declare_index_roots([str(p) for p in scope_map.union_roots])
    logger.info(f"Index will be built over union roots: {[str(p) for p in scope_map.union_roots]}")

    app = build_app(scope_map)
    logger.info(f"Serving REST + MCP on http://{args.host}:{args.port}")
    logger.info("  GET  /health")
    logger.info("  GET  /content?path=...&snippet=...&query=...&context_lines=...")
    logger.info("  GET  /search?q=...&top_k=5&scope=...")
    logger.info("  GET  /duplicates?file=...&threshold=0.85&scope=...")
    logger.info("  GET/POST /reindex")
    logger.info("  MCP  /mcp  (streamable HTTP transport)")
    uvicorn.run(app, host=args.host, port=args.port)
