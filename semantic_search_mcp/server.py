"""MCP server for semantic search."""

import os

from fastmcp import FastMCP

from .indexer import VaultIndexer, VaultWatcher

# Configuration from environment
VAULT_PATH = os.environ.get("VAULT_PATH", "./vault")

# MCP server instance
mcp = FastMCP("semantic-search-mcp")

# Lazy initialization
_indexer = None
_watcher = None


def get_indexer() -> VaultIndexer:
    """Get or create the indexer instance."""
    global _indexer, _watcher
    if _indexer is None:
        _indexer = VaultIndexer(VAULT_PATH)
        _watcher = VaultWatcher(_indexer)
        _watcher.start(background=True)
    return _indexer


@mcp.tool
def search_related(query: str, top_k: int = 5) -> list[dict]:
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
def check_duplicates(file_path: str) -> list[dict]:
    """Find notes that are potential duplicates of the given file.

    Args:
        file_path: Path to the file (absolute or relative to vault)

    Returns:
        List of similar notes with path and similarity score
    """
    indexer = get_indexer()
    return indexer.find_duplicates(file_path)


def run():
    """Run the MCP server."""
    print("[INFO] Starting MCP server (fastmcp)")
    mcp.run()
