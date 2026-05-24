"""MCP server for semantic search."""

import logging
import os

from fastmcp import FastMCP

from .factory import create_indexer

logger = logging.getLogger(__name__)

# Configuration from environment - supports comma-separated paths
_raw_paths = os.environ.get("CONTENT_PATH", "./content")
CONTENT_PATHS = [p.strip() for p in _raw_paths.split(",") if p.strip()]

# MCP server instance
mcp = FastMCP("semantic-search")


@mcp.tool
def search_related(query: str, top_k: int = 5) -> list[dict[str, str | float]]:
    """Search for notes semantically related to the query text.

    Args:
        query: The text to search for
        top_k: Number of results to return (default 5)

    Returns:
        List of matching notes with path and similarity score
    """
    indexer = create_indexer(CONTENT_PATHS)
    return indexer.search(query, top_k)


@mcp.tool
def check_duplicates(file_path: str) -> list[dict[str, str | float]] | dict[str, str]:
    """Find notes that are potential duplicates of the given file.

    Args:
        file_path: Path to the file (absolute or relative to content directory)

    Returns:
        List of similar notes with path and similarity score, or error dict
    """
    indexer = create_indexer(CONTENT_PATHS)
    return indexer.find_duplicates(file_path)


@mcp.tool
def get_content(
    path: str,
    snippet: bool = False,
    query: str | None = None,
    context_lines: int = 20,
) -> dict[str, str]:
    """Fetch the content of a file from the indexed vault.

    Args:
        path: File path (absolute or relative to an indexed root)
        snippet: If True, return a snippet around the best-matching line instead of the full file
        query: Search string to find the best-matching line (only used when snippet=True)
        context_lines: Number of lines before and after the match to include (default 20)

    Returns:
        Dict with keys: "path" (resolved absolute path),
            "content" (string), "mode" ("full" | "snippet")

    Raises:
        ValueError: If path resolves outside the indexed vault roots
        FileNotFoundError: If path is inside roots but file does not exist
    """
    indexer = create_indexer(CONTENT_PATHS)
    return indexer.get_content(path, snippet, query, context_lines)


def run() -> None:
    """Run the MCP server."""
    logger.info("[Server] Starting MCP server (fastmcp)")
    mcp.run()
