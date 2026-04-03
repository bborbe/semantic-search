"""Semantic search MCP server for Obsidian vaults."""

__version__ = "0.1.0"

from .indexer import VaultIndexer, VaultWatcher

__all__ = ["VaultIndexer", "VaultWatcher", "__version__"]
