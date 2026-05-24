"""Core indexer for semantic search over markdown files."""

import hashlib
import json
import logging
import re
import tempfile
import threading
import time
from pathlib import Path
from threading import Thread
from typing import Any

import faiss
import numpy as np
import yaml
from platformdirs import user_cache_dir
from sentence_transformers import SentenceTransformer
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

logger = logging.getLogger(__name__)

# Extract inline markdown tags: #project, #team-a/sub
INLINE_TAG_PATTERN = re.compile(r"(?<!\w)#([\w\-/]+)")


class VaultIndexer:
    """Indexes markdown files and provides semantic search."""

    def __init__(
        self,
        vault_paths: str | list[str],
        embedding_model: str = "all-MiniLM-L6-v2",
        duplicate_threshold: float = 0.85,
    ):
        # Support both single path (str) and multiple paths (list)
        if isinstance(vault_paths, str):
            vault_paths = [vault_paths]
        self.vault_paths = [Path(p).expanduser() for p in vault_paths]
        self.embedding_model = embedding_model
        self.duplicate_threshold = duplicate_threshold

        # Store index in the OS-appropriate user cache directory (stable across reboots,
        # not subject to macOS /var/folders/.../T/ auto-cleanup). platformdirs returns:
        #   macOS: ~/Library/Caches/semantic-search
        #   Linux: $XDG_CACHE_HOME/semantic-search or ~/.cache/semantic-search
        #   Windows: %LOCALAPPDATA%/semantic-search/Cache
        paths_str = ",".join(str(p.resolve()) for p in self.vault_paths)
        content_hash = hashlib.md5(paths_str.encode()).hexdigest()[:8]
        self.index_dir = Path(user_cache_dir("semantic-search", appauthor=False)) / content_hash
        self.index_file = self.index_dir / "vector_index.faiss"
        self.meta_file = self.index_dir / "index_meta.json"

        self._migrate_from_tempdir(content_hash)
        self.model = SentenceTransformer(embedding_model)
        self.meta: dict[str, dict[str, str]] = {}  # {idx: {"path": ...}}
        self.index: Any = None  # faiss.IndexFlatIP
        self._path_to_idx: dict[str, int] = {}  # reverse lookup: path -> index position
        self._tombstones: set[int] = set()  # logically-deleted idx positions
        self._index_lock = threading.Lock()  # protects all FAISS index operations
        self._load_index()

    def _migrate_from_tempdir(self, content_hash: str) -> None:
        """Best-effort one-time move of cache files from the pre-0.6.3 tempdir
        location into the new user cache dir.

        Runs only when the old directory has a cached index AND the new directory
        does not. Any OSError is swallowed — the worst case is _load_index sees no
        cache and rebuilds from scratch, which is the same behavior as before.
        """
        old_dir = Path(tempfile.gettempdir()) / "semantic-search" / content_hash
        old_meta = old_dir / "index_meta.json"
        old_faiss = old_dir / "vector_index.faiss"
        new_meta = self.meta_file
        new_faiss = self.index_file

        if not old_meta.exists():
            return  # nothing to migrate
        if new_meta.exists():
            return  # new cache already present, do not overwrite

        try:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            logger.info("[Indexer] migrating index cache from tempdir to user cache dir")
            if old_meta.exists():
                old_meta.replace(new_meta)
            if old_faiss.exists():
                old_faiss.replace(new_faiss)
        except OSError as e:
            logger.warning(f"[Indexer] cache migration failed ({e}); will rebuild instead")

    def _load_index(self) -> None:
        """Load existing index or build new one."""
        self.index_dir.mkdir(parents=True, exist_ok=True)

        if self.index_file.exists() and self.meta_file.exists():
            with self._index_lock:
                self.index = faiss.read_index(str(self.index_file))
                with open(self.meta_file) as f:
                    data = json.load(f)
                # Handle both old (bare dict) and new ({"meta": ..., "tombstones": ...}) formats
                if isinstance(data, dict) and "meta" in data:
                    self.meta = data["meta"]
                    self._tombstones = set(data.get("tombstones", []))
                else:
                    self.meta = data
                    self._tombstones = set()
                self._path_to_idx = {v["path"]: int(k) for k, v in self.meta.items()}
            logger.info(
                f"[Indexer] Loaded index with {len(self.meta)} entries, "
                f"{len(self._tombstones)} tombstones"
            )
        else:
            with self._index_lock:
                self.index = faiss.IndexFlatIP(self.model.get_sentence_embedding_dimension())
                self.meta = {}
                self._path_to_idx = {}
            logger.info("[Indexer] No existing index found. Building initial index...")
            self.rebuild_index()

    def save_index(self) -> None:
        """Persist index to disk."""
        with self._index_lock:
            faiss.write_index(self.index, str(self.index_file))
            with open(self.meta_file, "w") as f:
                json.dump(
                    {
                        "meta": self.meta,
                        "tombstones": sorted(self._tombstones),
                    },
                    f,
                )
        logger.info("[Indexer] Index saved")

    def _read_file(self, file_path: Path) -> str | None:
        """Read file with encoding fallback."""
        encodings = ["utf-8", "latin-1", "cp1252"]
        for encoding in encodings:
            try:
                with open(file_path, encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        logger.warning(f"[Indexer] Could not decode {file_path} with any encoding")
        return None

    def _prepare_text_for_embedding(self, file_path: Path, content: str) -> str:
        """Prepare weighted text for embedding by repeating important components.

        Components and weights:
        - Filename (no extension, separators → spaces): 3x
        - Metadata title: 3x
        - Metadata tags/aliases: 2x
        - First H1 heading: 2x
        - Body (first 500 words, frontmatter removed): 1x
        """
        parts = []

        # 1. Filename processing (3x)
        filename = file_path.stem  # Remove .md extension
        filename_text = filename.replace("-", " ").replace("_", " ")
        parts.extend([filename_text] * 3)

        # 2. Extract frontmatter and parse YAML metadata
        frontmatter_data: dict[str, Any] = {}
        content_without_frontmatter = content

        if content.startswith("---"):
            try:
                # Find the second --- marker
                end_marker = content.find("---", 3)
                if end_marker != -1:
                    frontmatter_text = content[3:end_marker].strip()
                    content_without_frontmatter = content[end_marker + 3 :].strip()

                    # Parse YAML frontmatter
                    frontmatter_data = yaml.safe_load(frontmatter_text) or {}
            except yaml.YAMLError as e:
                logger.warning(f"[Indexer] Failed to parse frontmatter in {file_path}: {e}")
            except Exception as e:
                logger.warning(f"[Indexer] Error processing frontmatter in {file_path}: {e}")

        # 3. Metadata title (3x)
        if frontmatter_data.get("title"):
            title = str(frontmatter_data["title"])
            parts.extend([title] * 3)

        # 4. Metadata tags and aliases (2x)
        tags_aliases = []

        # Frontmatter tags
        if frontmatter_data.get("tags"):
            tags = frontmatter_data["tags"]
            if isinstance(tags, list):
                tags_aliases.extend([str(t) for t in tags])
            else:
                tags_aliases.append(str(tags))

        # Inline #tags from body
        inline_tags = self._extract_inline_tags(content_without_frontmatter)

        # Merge and dedupe (lowercase)
        all_tags = {t.lower() for t in tags_aliases} | {t.lower() for t in inline_tags}
        tags_aliases = list(all_tags)

        # Aliases
        if frontmatter_data.get("aliases"):
            aliases = frontmatter_data["aliases"]
            if isinstance(aliases, list):
                tags_aliases.extend([str(a) for a in aliases])
            else:
                tags_aliases.append(str(aliases))

        if tags_aliases:
            tags_text = " ".join(tags_aliases)
            parts.extend([tags_text] * 2)

        # 5. First H1 heading (2x)
        for line in content_without_frontmatter.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                heading = line[2:].strip()
                parts.extend([heading] * 2)
                break

        # 6. Body content (first 500 words, 1x)
        words = content_without_frontmatter.split()
        body_words = words[:500]
        if body_words:
            parts.append(" ".join(body_words))

        # Join all parts with newlines for readability
        return "\n".join(parts)

    def _extract_inline_tags(self, content: str) -> list[str]:
        """Extract inline #tags from markdown content.

        Returns tags without # prefix (e.g., ["project", "team-a/sub"])
        """
        return INLINE_TAG_PATTERN.findall(content)

    def _embed_text(self, text: str) -> np.ndarray:
        """Generate embedding vector for text.

        `show_progress_bar=False` is load-bearing: sentence-transformers defaults
        to `None` which auto-enables tqdm, whose internal state is not thread-safe.
        Concurrent encode() calls (watcher thread + HTTP search handler) were
        racing on tqdm's `.sp` attribute and raising in production.
        """
        vec = self.model.encode([text], normalize_embeddings=True, show_progress_bar=False)
        return vec.astype("float32")

    def add_file_to_index(self, file_path: str | Path) -> None:
        """Add a new file or update an existing one in the index.

        On update: tombstone the old idx, append a new embedding, update lookups.
        Never triggers a full rebuild on the hot path.
        """
        file_path = Path(file_path)
        if not file_path.exists() or file_path.suffix != ".md":
            return

        content = self._read_file(file_path)
        if content is None:
            return

        weighted_text = self._prepare_text_for_embedding(file_path, content)
        vec = self._embed_text(weighted_text)

        path_str = str(file_path)
        with self._index_lock:
            # Tombstone the old entry if this path is already indexed
            old_idx = self._path_to_idx.get(path_str)
            if old_idx is not None:
                self._tombstones.add(old_idx)
                self.meta.pop(str(old_idx), None)

            new_idx = self.index.ntotal  # next row position before add
            self.index.add(vec)
            self.meta[str(new_idx)] = {"path": path_str}
            self._path_to_idx[path_str] = new_idx

        self.save_index()
        self._maybe_compact()
        logger.info(f"[Indexer] Indexed {file_path} (idx={new_idx})")

    def remove_file_from_index(self, file_path: str | Path) -> None:
        """Remove a file from the index by tombstoning its entry.

        No-op if the path is not currently indexed.
        """
        path_str = str(Path(file_path))
        with self._index_lock:
            old_idx = self._path_to_idx.pop(path_str, None)
            if old_idx is None:
                return
            self._tombstones.add(old_idx)
            self.meta.pop(str(old_idx), None)
        self.save_index()
        self._maybe_compact()
        logger.info(f"[Indexer] Removed {file_path} (idx={old_idx})")

    def _maybe_compact(self) -> None:
        """Rebuild the index if tombstone ratio exceeds 20%.

        Compaction drops tombstoned vectors and reclaims memory / search cost.
        Must be called without holding self._index_lock.
        """
        with self._index_lock:
            live = len(self.meta)
            dead = len(self._tombstones)
        total = live + dead
        if total == 0:
            return
        if dead > 0.2 * total:
            logger.info(
                f"[Indexer] Compacting: {dead} tombstones / {total} total (> 20%), rebuilding index"
            )
            self.rebuild_index()

    def rebuild_index(self) -> None:
        """Rebuild entire index from all vault paths."""
        # Build new index and metadata outside the lock (embedding is slow)
        new_index = faiss.IndexFlatIP(self.model.get_sentence_embedding_dimension())
        new_meta: dict[str, dict[str, str]] = {}
        new_path_to_idx: dict[str, int] = {}
        idx = 0
        for vault_path in self.vault_paths:
            for file_path in vault_path.rglob("*.md"):
                # Skip files in .semantic-search directory
                if ".semantic-search" in str(file_path):
                    continue
                try:
                    content = self._read_file(file_path)
                    if content is None:
                        continue

                    # Prepare weighted text for embedding
                    weighted_text = self._prepare_text_for_embedding(file_path, content)
                    vec = self._embed_text(weighted_text)

                    new_index.add(vec)
                    new_meta[str(idx)] = {"path": str(file_path)}
                    new_path_to_idx[str(file_path)] = idx
                    idx += 1
                    if idx % 100 == 0:
                        logger.info(f"[Indexer] Indexed {idx} files...")
                except Exception as e:
                    logger.error(f"[Indexer] Failed to index {file_path}: {e}")
        # Swap atomically under the lock
        with self._index_lock:
            self.index = new_index
            self.meta = new_meta
            self._path_to_idx = new_path_to_idx
            self._tombstones = set()
        self.save_index()
        logger.info(f"[Indexer] Rebuilt index with {len(self.meta)} files")

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Search for related notes, skipping tombstoned entries."""
        if len(self.meta) == 0:
            return []

        vec = self._embed_text(query)
        with self._index_lock:
            # Oversample to account for tombstoned rows we will skip
            oversample = min(top_k * 4, self.index.ntotal)
            if oversample == 0:
                return []
            distances, indices = self.index.search(vec, oversample)
            meta_snapshot = dict(self.meta)
            tombstones_snapshot = set(self._tombstones)

        results: list[dict[str, Any]] = []
        for score, idx in zip(distances[0], indices[0], strict=True):
            if idx < 0:  # FAISS returns -1 for missing slots when k > ntotal
                continue
            if int(idx) in tombstones_snapshot:
                continue
            if str(idx) not in meta_snapshot:
                continue
            results.append({"path": meta_snapshot[str(idx)]["path"], "score": float(score)})
            if len(results) >= top_k:
                break
        return results

    def get_content(
        self,
        path: str,
        snippet: bool = False,
        query: str | None = None,
        context_lines: int = 20,
    ) -> dict[str, str]:
        """Return content for the given path, optionally as a snippet around the best-matching line.

        Args:
            path: File path (absolute or relative to a vault root)
            snippet: If True, return a snippet instead of the full file
            query: Search string used to find the best-matching line
                (only meaningful when snippet=True)
            context_lines: Number of lines before and after the best match to include

        Returns:
            Dict with keys: "path" (resolved absolute path),
                "content" (string), "mode" ("full" | "snippet")

        Raises:
            ValueError: If path resolves outside the indexed vault roots
            FileNotFoundError: If path is inside roots but file does not exist
        """
        # Resolve the path (follows symlinks)
        resolved_path = Path(path).resolve()

        # Path validation: check if resolved path is inside any vault root
        is_inside = any(resolved_path.is_relative_to(vp) for vp in self.vault_paths)
        if not is_inside:
            raise ValueError("path not in indexed roots")

        # Existence check (must happen BEFORE reading)
        if not resolved_path.exists():
            raise FileNotFoundError(f"file not found: {path}")

        # Read the file using _read_file
        file_content = self._read_file(resolved_path)
        if file_content is None:
            raise RuntimeError("could not read file")

        resolved_path_str = str(resolved_path)

        if not snippet:
            return {"path": resolved_path_str, "content": file_content, "mode": "full"}

        # Snippet mode
        if context_lines < 0:
            context_lines = 0

        lines = file_content.split("\n")
        total_lines = len(lines)

        if query and query.strip():
            # Snippet mode with query: find best-matching line
            tokens = query.lower().split()
            best_idx = 0
            best_score = 0
            for idx, line in enumerate(lines):
                line_lower = line.lower()
                score = sum(1 for tok in tokens if tok in line_lower)
                if score > best_score:
                    best_score = score
                    best_idx = idx

            if best_score == 0:
                # No match, fall back to file-head behavior
                end_idx = min(context_lines * 2 + 1, total_lines)
                snippet_content = "\n".join(lines[:end_idx])
            else:
                start_idx = max(0, best_idx - context_lines)
                end_idx = min(best_idx + context_lines + 1, total_lines)
                snippet_content = "\n".join(lines[start_idx:end_idx])
        else:
            # Snippet mode without query: return first 2 * context_lines + 1 lines
            end_idx = min(context_lines * 2 + 1, total_lines)
            snippet_content = "\n".join(lines[:end_idx])

        return {"path": resolved_path_str, "content": snippet_content, "mode": "snippet"}

    def find_duplicates(self, file_path: str | Path) -> list[dict[str, Any]] | dict[str, str]:
        """Find potential duplicates of a file."""
        file_path = Path(file_path) if isinstance(file_path, str) else file_path
        if not file_path.is_absolute():
            # Try each vault path for relative paths
            for vault_path in self.vault_paths:
                candidate = vault_path / file_path
                if candidate.exists():
                    file_path = candidate
                    break

        if not file_path.exists():
            return {"error": f"File not found: {file_path}"}

        content = self._read_file(file_path)
        if content is None:
            return {"error": f"Could not read file: {file_path}"}

        # Prepare weighted text for embedding
        weighted_text = self._prepare_text_for_embedding(file_path, content)
        vec = self._embed_text(weighted_text)

        if len(self.meta) == 0:
            return []

        with self._index_lock:
            distances, indices = self.index.search(vec, self.index.ntotal)
            meta_snapshot = dict(self.meta)
            tombstones_snapshot = set(self._tombstones)
        duplicates = []
        for score, idx in zip(distances[0], indices[0], strict=True):
            if idx < 0:
                continue
            if int(idx) in tombstones_snapshot:
                continue
            if (
                str(idx) in meta_snapshot
                and score > self.duplicate_threshold
                and Path(meta_snapshot[str(idx)]["path"]).resolve() != file_path.resolve()
            ):
                duplicates.append({"path": meta_snapshot[str(idx)]["path"], "score": float(score)})
        return duplicates


class VaultWatcher:
    """Watches vault for file changes and updates index."""

    def __init__(self, indexer: VaultIndexer):
        self.indexer = indexer
        self._observer: BaseObserver | None = None
        self._thread: Thread | None = None

    def start(self, background: bool = True) -> None:
        """Start watching all vault paths."""
        handler = _VaultEventHandler(self.indexer)
        self._observer = Observer()
        for vault_path in self.indexer.vault_paths:
            self._observer.schedule(handler, str(vault_path), recursive=True)
            logger.info(f"[Watcher] Watching vault at {vault_path}")
        self._observer.start()

        if background:
            self._thread = Thread(target=self._run_loop, daemon=True)
            self._thread.start()
        else:
            self._run_loop()

    def _run_loop(self) -> None:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()


class _VaultEventHandler(FileSystemEventHandler):
    DEBOUNCE_DELAY: float = 2.0

    def __init__(self, indexer: VaultIndexer):
        self.indexer = indexer
        self._pending: dict[str, float] = {}
        self._pending_deletes: set[str] = set()
        self._debounce_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule_flush(self) -> None:
        """Cancel any existing timer and schedule a new flush."""
        if self._debounce_timer is not None:
            self._debounce_timer.cancel()
        self._debounce_timer = threading.Timer(self.DEBOUNCE_DELAY, self._flush)
        self._debounce_timer.daemon = True
        self._debounce_timer.start()

    @staticmethod
    def _is_path_indexable(path: str) -> bool:
        """Return True iff this path should trigger an incremental update.

        Rejects:
        - paths that do not end with .md
        - paths containing any dotfile segment (.git, .obsidian, .semantic-search,
          .DS_Store, .tempfile, etc.)
        """
        p = Path(path)
        if p.suffix != ".md":
            return False
        return not any(part.startswith(".") for part in p.parts)

    @staticmethod
    def _is_indexable_event(event: FileSystemEvent) -> bool:
        """Return True iff this event should trigger an incremental update.

        Rejects:
        - directory events
        - paths that do not end with .md
        - paths containing any dotfile segment (.git, .obsidian, .semantic-search,
          .DS_Store, etc.)
        """
        if event.is_directory:
            return False
        return _VaultEventHandler._is_path_indexable(str(event.src_path))

    def _flush(self) -> None:
        """Process all pending changes incrementally (no full rebuild)."""
        with self._lock:
            adds = list(self._pending.keys())
            deletes = list(self._pending_deletes)
            self._pending.clear()
            self._pending_deletes.clear()
            self._debounce_timer = None

        if not adds and not deletes:
            return

        logger.info(f"[EventHandler] Flushing {len(adds)} add/update(s), {len(deletes)} delete(s)")
        # Process deletes first so a rename (delete + create of new path) is correct
        for path in deletes:
            try:
                self.indexer.remove_file_from_index(path)
            except Exception:
                logger.exception(f"[EventHandler] Failed to remove {path}")
        for path in adds:
            try:
                self.indexer.add_file_to_index(path)
            except Exception:
                logger.exception(f"[EventHandler] Failed to index {path}")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not self._is_indexable_event(event):
            return
        with self._lock:
            self._pending[str(event.src_path)] = time.time()
            self._schedule_flush()

    def on_created(self, event: FileSystemEvent) -> None:
        if not self._is_indexable_event(event):
            return
        with self._lock:
            self._pending[str(event.src_path)] = time.time()
            self._schedule_flush()

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not self._is_indexable_event(event):
            return
        with self._lock:
            self._pending_deletes.add(str(event.src_path))
            self._schedule_flush()

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle file rename / move.

        Watchdog delivers ``FileMovedEvent`` to this method. The base class
        ``FileSystemEventHandler.on_moved`` is a no-op, so without this override
        atomic-replace writes (Obsidian, obsidian-git) silently drop their
        indexable destinations and the index decays.

        Routes the event through the same _pending / _pending_deletes queues
        as create / delete so the existing _flush logic handles the work.
        """
        if event.is_directory:
            return

        src_path = str(event.src_path)
        dest_path_attr = getattr(event, "dest_path", None)
        dest_path = str(dest_path_attr) if dest_path_attr is not None else None

        src_indexable = self._is_path_indexable(src_path)
        dest_indexable = dest_path is not None and self._is_path_indexable(dest_path)

        if not src_indexable and not dest_indexable:
            return

        with self._lock:
            if src_indexable:
                self._pending_deletes.add(src_path)
            if dest_indexable and dest_path is not None:
                self._pending[dest_path] = time.time()
            self._schedule_flush()
