"""Unified HTTP server: REST endpoints + MCP-over-HTTP on one port."""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
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


async def _build_indexer_in_background() -> None:
    """Build the VaultIndexer in a worker thread, then mark ready.

    Called from the Starlette lifespan so the server can bind its port
    immediately while the (slow, blocking) initial embedding pass runs.
    """
    global _indexer, _indexer_error
    logger.info(f"Indexer build starting in background for paths: {CONTENT_PATHS}")
    try:
        _indexer = await asyncio.to_thread(create_indexer, CONTENT_PATHS)
        logger.info(f"Indexer build complete: {len(_indexer.meta)} files indexed")
    except Exception as e:
        _indexer_error = str(e)
        logger.exception("Indexer build failed")
    finally:
        _indexer_ready.set()


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


def _not_ready_response() -> JSONResponse:
    """503 response returned while the initial index build is in flight."""
    return JSONResponse(
        {"error": "indexing in progress", "ready": False},
        status_code=503,
        headers={"Retry-After": "5"},
    )


async def search(request: Request) -> JSONResponse:
    """Handle /search endpoint."""
    try:
        q = request.query_params.get("q")
        if not q:
            return JSONResponse({"error": "Missing 'q' parameter"}, status_code=400)

        # gate on readiness before touching the indexer
        if not _indexer_ready.is_set() or _indexer is None:
            return _not_ready_response()

        top_k = int(request.query_params.get("top_k", "5"))
        indexer = get_indexer()
        results: list[Any] = await run_in_threadpool(indexer.search, q, top_k)
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

        # gate on readiness before touching the indexer
        if not _indexer_ready.is_set() or _indexer is None:
            return _not_ready_response()

        threshold = float(request.query_params.get("threshold", "0.85"))
        indexer = get_indexer()
        indexer.duplicate_threshold = threshold
        results = await run_in_threadpool(indexer.find_duplicates, file_path)

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


def main() -> None:
    """Entry point for semantic-search-http binary."""
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

    logger.info(f"Indexer will build in background for: {CONTENT_PATHS}")
    app = build_app()
    logger.info(f"Serving REST + MCP on http://{args.host}:{args.port}")
    logger.info("  GET  /health")
    logger.info("  GET  /search?q=...&top_k=5")
    logger.info("  GET  /duplicates?file=...&threshold=0.85")
    logger.info("  GET/POST /reindex")
    logger.info("  MCP  /mcp  (streamable HTTP transport)")
    uvicorn.run(app, host=args.host, port=args.port)
