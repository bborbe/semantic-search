"""Semantic search MCP server for Obsidian vaults."""

from .indexer import VaultIndexer, VaultWatcher

__all__ = ["VaultIndexer", "VaultWatcher"]
