"""REST server for semantic search."""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .factory import create_indexer
from .indexer import VaultIndexer

logger = logging.getLogger(__name__)

# Configuration from environment - supports comma-separated paths
_raw_paths = os.environ.get("CONTENT_PATH", "./content")
CONTENT_PATHS = [p.strip() for p in _raw_paths.split(",") if p.strip()]

# Global indexer instance (created once, reused)
_indexer: VaultIndexer | None = None


def get_indexer() -> VaultIndexer:
    """Get or create the indexer instance."""
    global _indexer
    if _indexer is None:
        logger.info(f"Creating indexer for paths: {CONTENT_PATHS}")
        _indexer = create_indexer(CONTENT_PATHS)
    return _indexer


class SemanticSearchHandler(BaseHTTPRequestHandler):
    """HTTP request handler for semantic search."""

    def _send_json(self, data: Any, status: int = 200) -> None:
        """Send JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_error(self, message: str, status: int = 400) -> None:
        """Send error response."""
        self._send_json({"error": message}, status)

    def do_GET(self) -> None:
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        try:
            if path == "/search":
                self._handle_search(params)
            elif path == "/duplicates":
                self._handle_duplicates(params)
            elif path == "/health":
                self._handle_health()
            elif path == "/reindex":
                self._handle_reindex()
            else:
                self._send_error(f"Unknown endpoint: {path}", 404)
        except Exception as e:
            logger.exception("Error handling request")
            self._send_error(str(e), 500)

    def do_POST(self) -> None:
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/reindex":
                self._handle_reindex()
            else:
                self._send_error(f"Unknown endpoint: {path}", 404)
        except Exception as e:
            logger.exception("Error handling request")
            self._send_error(str(e), 500)

    def _handle_search(self, params: dict[str, list[str]]) -> None:
        """Handle search request."""
        query_list = params.get("q", [])
        if not query_list:
            self._send_error("Missing 'q' parameter")
            return

        query = query_list[0]
        top_k = int(params.get("top_k", ["5"])[0])

        indexer = get_indexer()
        results = indexer.search(query, top_k)

        self._send_json({
            "query": query,
            "results": results,
            "count": len(results)
        })

    def _handle_duplicates(self, params: dict[str, list[str]]) -> None:
        """Handle duplicates check request."""
        file_list = params.get("file", [])
        if not file_list:
            self._send_error("Missing 'file' parameter")
            return

        file_path = file_list[0]
        threshold = float(params.get("threshold", ["0.85"])[0])

        indexer = get_indexer()
        # Update threshold if needed
        indexer.duplicate_threshold = threshold
        results = indexer.find_duplicates(file_path)

        if isinstance(results, dict) and "error" in results:
            self._send_error(str(results["error"]), 400)
            return

        self._send_json({
            "file": file_path,
            "threshold": threshold,
            "duplicates": results,
            "count": len(results)
        })

    def _handle_health(self) -> None:
        """Handle health check."""
        indexer = get_indexer()
        self._send_json({
            "status": "ok",
            "paths": CONTENT_PATHS,
            "indexed_files": len(indexer.meta)
        })

    def _handle_reindex(self) -> None:
        """Force reindex."""
        global _indexer
        logger.info("Forcing reindex...")
        _indexer = None
        indexer = get_indexer()
        self._send_json({
            "status": "ok",
            "message": "Reindex complete",
            "indexed_files": len(indexer.meta)
        })

    def log_message(self, format: str, *args: Any) -> None:
        """Override to use our logger."""
        logger.info("%s - %s", self.address_string(), format % args)


def run(port: int = 8321) -> None:
    """Run the REST server."""
    # Pre-create indexer to build index at startup
    logger.info(f"Building index for: {CONTENT_PATHS}")
    get_indexer()

    server = HTTPServer(("0.0.0.0", port), SemanticSearchHandler)
    logger.info(f"REST server listening on http://0.0.0.0:{port}")
    logger.info("Endpoints:")
    logger.info("  GET  /search?q=...&top_k=5")
    logger.info("  GET  /duplicates?file=...&threshold=0.85")
    logger.info("  GET  /health")
    logger.info("  POST /reindex")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()
