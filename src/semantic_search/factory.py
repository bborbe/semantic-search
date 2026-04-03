"""Composition root for dependency wiring."""

import logging
from threading import Lock

from .indexer import VaultIndexer, VaultWatcher

logger = logging.getLogger(__name__)

_indexer: VaultIndexer | None = None
_watcher: VaultWatcher | None = None
_indexer_lock = Lock()


def create_indexer(content_paths: list[str]) -> VaultIndexer:
    """Get or create the indexer instance (thread-safe singleton).

    Args:
        content_paths: List of directory paths to index

    Returns:
        VaultIndexer instance with watcher started in background
    """
    global _indexer, _watcher
    with _indexer_lock:
        if _indexer is None:
            logger.debug("Initializing vault indexer")
            _indexer = VaultIndexer(content_paths)
            _watcher = VaultWatcher(_indexer)
            _watcher.start(background=True)
    return _indexer
