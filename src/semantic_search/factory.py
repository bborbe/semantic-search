"""Composition root for dependency wiring."""

import logging
from collections.abc import Sequence
from threading import Lock

from .indexer import VaultIndexer, VaultWatcher

logger = logging.getLogger(__name__)

_indexer: VaultIndexer | None = None
_watcher: VaultWatcher | None = None
_indexer_lock = Lock()
_declared_roots: list[str] | None = None


def create_indexer(content_paths: list[str]) -> VaultIndexer:
    """Get or create the indexer instance (thread-safe singleton).

    Once declare_index_roots has been called, the declared root set wins over
    the caller's content_paths: whichever caller creates the index first, it
    is built over the declared roots. Before any declaration, content_paths
    is used as-is, exactly as before.

    Args:
        content_paths: List of directory paths to index

    Returns:
        VaultIndexer instance with watcher started in background
    """
    global _indexer, _watcher
    with _indexer_lock:
        if _indexer is None:
            logger.debug("Initializing vault indexer")
            roots = _declared_roots if _declared_roots is not None else content_paths
            _indexer = VaultIndexer(roots)
            _watcher = VaultWatcher(_indexer)
            _watcher.start(background=True)
    return _indexer


def declare_index_roots(roots: Sequence[str]) -> None:
    """Declare this process's index root set; the HTTP entry point calls this once, before serving.

    Once declared, the process index is built over the declared roots whichever
    caller creates it first — including the FastMCP tools, which call
    create_indexer(CONTENT_PATHS) in the same process. Before any declaration,
    the roots passed to create_indexer are used, exactly as today.

    This is a process-scoped root set, not the per-request scope, which must
    never be stored here or anywhere else.
    """
    global _declared_roots
    with _indexer_lock:
        _declared_roots = list(roots)


def reset() -> None:
    """Drop the singleton and the declared root set, so the next create_indexer starts clean.

    Test support: stops the existing watcher if there is one, then clears the
    indexer, the watcher, and the declared root set. A stale indexer or a
    stale declaration left behind by an earlier test would otherwise decide
    what a later test indexes.
    """
    global _indexer, _watcher, _declared_roots
    with _indexer_lock:
        if _watcher is not None:
            _watcher.stop()
        _indexer = None
        _watcher = None
        _declared_roots = None
