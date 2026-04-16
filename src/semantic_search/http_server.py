"""Unified HTTP server: REST endpoints + MCP-over-HTTP on one port."""

import logging
import os
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


def get_indexer() -> VaultIndexer:
    """Get or create the indexer instance."""
    global _indexer
    if _indexer is None:
        logger.info(f"Creating indexer for paths: {CONTENT_PATHS}")
        _indexer = create_indexer(CONTENT_PATHS)
    return _indexer


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


async def search(request: Request) -> JSONResponse:
    """Handle /search endpoint."""
    try:
        q = request.query_params.get("q")
        if not q:
            return JSONResponse({"error": "Missing 'q' parameter"}, status_code=400)

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
    """Handle /reindex endpoint."""
    global _indexer
    try:
        logger.info("Forcing reindex...")
        _indexer = None
        indexer = get_indexer()
        return JSONResponse(
            {
                "status": "ok",
                "message": "Reindex complete",
                "indexed_files": len(indexer.meta),
            }
        )
    except Exception as e:
        logger.exception("Error handling /reindex request")
        return JSONResponse({"error": str(e)}, status_code=500)


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
