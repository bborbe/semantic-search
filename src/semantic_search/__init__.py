"""Semantic search MCP server for Obsidian vaults."""

try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"

from .indexer import VaultIndexer, VaultWatcher

__all__ = ["VaultIndexer", "VaultWatcher", "__version__"]
