"""Core indexer for semantic search over markdown files."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from threading import Thread

import faiss
import numpy as np
import yaml
from sentence_transformers import SentenceTransformer
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


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
        self.vault_paths = [Path(p) for p in vault_paths]
        self.embedding_model = embedding_model
        self.duplicate_threshold = duplicate_threshold

        # Store index in OS temp directory with content hash and PID
        paths_str = ",".join(str(p.resolve()) for p in self.vault_paths)
        content_hash = hashlib.md5(paths_str.encode()).hexdigest()[:8]
        self.index_dir = (
            Path(tempfile.gettempdir()) / "semantic-search" / content_hash / str(os.getpid())
        )
        self.index_file = self.index_dir / "vector_index.faiss"
        self.meta_file = self.index_dir / "index_meta.json"

        self.model = SentenceTransformer(embedding_model)
        self.meta = {}  # {idx: {"path": ..., "content": ...}}
        self.index = None
        self._load_index()

    def _load_index(self):
        """Load existing index or build new one."""
        self.index_dir.mkdir(parents=True, exist_ok=True)

        if self.index_file.exists() and self.meta_file.exists():
            self.index = faiss.read_index(str(self.index_file))
            with open(self.meta_file) as f:
                self.meta = json.load(f)
            print(f"[INFO] Loaded index with {len(self.meta)} entries.")
        else:
            self.index = faiss.IndexFlatIP(self.model.get_sentence_embedding_dimension())
            self.meta = {}
            print("[INFO] No existing index found. Building initial index...")
            self.rebuild_index()

    def save_index(self):
        """Persist index to disk."""
        faiss.write_index(self.index, str(self.index_file))
        with open(self.meta_file, "w") as f:
            json.dump(self.meta, f)
        print("[INFO] Index saved.")

    def _read_file(self, file_path: Path) -> str | None:
        """Read file with encoding fallback."""
        encodings = ["utf-8", "latin-1", "cp1252"]
        for encoding in encodings:
            try:
                with open(file_path, encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        print(f"[WARN] Could not decode {file_path} with any encoding")
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
        frontmatter_data = {}
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
                print(f"[WARN] Failed to parse frontmatter in {file_path}: {e}")
            except Exception as e:
                print(f"[WARN] Error processing frontmatter in {file_path}: {e}")

        # 3. Metadata title (3x)
        if "title" in frontmatter_data and frontmatter_data["title"]:
            title = str(frontmatter_data["title"])
            parts.extend([title] * 3)

        # 4. Metadata tags and aliases (2x)
        tags_aliases = []
        if "tags" in frontmatter_data and frontmatter_data["tags"]:
            tags = frontmatter_data["tags"]
            if isinstance(tags, list):
                tags_aliases.extend([str(t) for t in tags])
            else:
                tags_aliases.append(str(tags))

        if "aliases" in frontmatter_data and frontmatter_data["aliases"]:
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

    def _embed_text(self, text: str) -> np.ndarray:
        """Generate embedding vector for text."""
        vec = self.model.encode([text], normalize_embeddings=True)
        return vec.astype("float32")

    def index_file(self, file_path):
        """Add or update a single file in the index."""
        file_path = Path(file_path)
        if not file_path.exists() or file_path.suffix != ".md":
            return
        content = self._read_file(file_path)
        if content is None:
            return

        # Prepare weighted text for embedding
        weighted_text = self._prepare_text_for_embedding(file_path, content)
        vec = self._embed_text(weighted_text)

        idx = len(self.meta)
        self.index.add(vec)
        # Store original content in metadata for display
        self.meta[str(idx)] = {"path": str(file_path), "content": content}
        print(f"[INFO] Indexed {file_path}")

    def rebuild_index(self):
        """Rebuild entire index from all vault paths."""
        self.index = faiss.IndexFlatIP(self.model.get_sentence_embedding_dimension())
        new_meta = {}
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

                    self.index.add(vec)
                    # Store original content in metadata for display
                    new_meta[str(idx)] = {"path": str(file_path), "content": content}
                    idx += 1
                    if idx % 100 == 0:
                        print(f"[INFO] Indexed {idx} files...")
                except Exception as e:
                    print(f"[WARN] Failed to index {file_path}: {e}")
        self.meta = new_meta
        self.save_index()
        print(f"[INFO] Rebuilt index with {len(self.meta)} files.")

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Search for related notes."""
        if len(self.meta) == 0:
            return []

        vec = self._embed_text(query)
        k = min(top_k, len(self.meta))
        distances, indices = self.index.search(vec, k)
        results = []
        for score, idx in zip(distances[0], indices[0], strict=True):
            if str(idx) in self.meta:
                results.append({"path": self.meta[str(idx)]["path"], "score": float(score)})
        return results

    def find_duplicates(self, file_path: str) -> list[dict]:
        """Find potential duplicates of a file."""
        file_path = Path(file_path)
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

        distances, indices = self.index.search(vec, len(self.meta))
        duplicates = []
        for score, idx in zip(distances[0], indices[0], strict=True):
            if str(idx) in self.meta and score > self.duplicate_threshold:
                # Skip the file itself
                if Path(self.meta[str(idx)]["path"]).resolve() != file_path.resolve():
                    duplicates.append({"path": self.meta[str(idx)]["path"], "score": float(score)})
        return duplicates


class VaultWatcher:
    """Watches vault for file changes and updates index."""

    def __init__(self, indexer: VaultIndexer):
        self.indexer = indexer
        self._observer = None
        self._thread = None

    def start(self, background: bool = True):
        """Start watching all vault paths."""
        handler = _VaultEventHandler(self.indexer)
        self._observer = Observer()
        for vault_path in self.indexer.vault_paths:
            self._observer.schedule(handler, str(vault_path), recursive=True)
            print(f"[INFO] Watching vault at {vault_path}")
        self._observer.start()

        if background:
            self._thread = Thread(target=self._run_loop, daemon=True)
            self._thread.start()
        else:
            self._run_loop()

    def _run_loop(self):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, indexer: VaultIndexer):
        self.indexer = indexer

    def on_modified(self, event):
        if not event.is_directory:
            self.indexer.index_file(event.src_path)
            self.indexer.save_index()

    def on_created(self, event):
        if not event.is_directory:
            self.indexer.index_file(event.src_path)
            self.indexer.save_index()

    def on_deleted(self, event):
        if not event.is_directory:
            print("[INFO] File removed, rebuilding index...")
            self.indexer.rebuild_index()
