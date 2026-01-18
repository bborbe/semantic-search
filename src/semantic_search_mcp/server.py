"""MCP server for semantic search."""

import logging
import os
from threading import Lock

from fastmcp import FastMCP

from .indexer import VaultIndexer, VaultWatcher

logger = logging.getLogger(__name__)

# Configuration from environment - supports comma-separated paths
_raw_paths = os.environ.get("CONTENT_PATH", "./content")
CONTENT_PATHS = [p.strip() for p in _raw_paths.split(",") if p.strip()]

# MCP server instance
mcp = FastMCP("semantic-search-mcp")

# Lazy initialization
_indexer = None
_watcher = None
_indexer_lock = Lock()


def get_indexer() -> VaultIndexer:
    """Get or create the indexer instance."""
    global _indexer, _watcher
    with _indexer_lock:
        if _indexer is None:
            _indexer = VaultIndexer(CONTENT_PATHS)
            _watcher = VaultWatcher(_indexer)
            _watcher.start(background=True)
    return _indexer


@mcp.tool
def search_related(query: str, top_k: int = 5) -> list[dict[str, str | float]]:
    """Search for notes semantically related to the query text.

    Args:
        query: The text to search for
        top_k: Number of results to return (default 5)

    Returns:
        List of matching notes with path and similarity score
    """
    indexer = get_indexer()
    return indexer.search(query, top_k)


@mcp.tool
def check_duplicates(file_path: str) -> list[dict[str, str | float]] | dict[str, str]:
    """Find notes that are potential duplicates of the given file.

    Args:
        file_path: Path to the file (absolute or relative to content directory)

    Returns:
        List of similar notes with path and similarity score, or error dict
    """
    indexer = get_indexer()
    return indexer.find_duplicates(file_path)


def run() -> None:
    """Run the MCP server."""
    logger.info("[Server] Starting MCP server (fastmcp)")
    mcp.run()
