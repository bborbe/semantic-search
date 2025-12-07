"""Core indexer for semantic search over markdown files."""

import json
from pathlib import Path
from threading import Thread
import time

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class VaultIndexer:
    """Indexes markdown files and provides semantic search."""

    def __init__(self, vault_path: str, embedding_model: str = "all-MiniLM-L6-v2",
                 duplicate_threshold: float = 0.85):
        self.vault_path = Path(vault_path)
        self.embedding_model = embedding_model
        self.duplicate_threshold = duplicate_threshold

        # Store index in vault's .semantic-search directory
        self.index_dir = self.vault_path / ".semantic-search"
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
            with open(self.meta_file, "r") as f:
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
                with open(file_path, "r", encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        print(f"[WARN] Could not decode {file_path} with any encoding")
        return None

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
        vec = self._embed_text(content)
        idx = len(self.meta)
        self.index.add(vec)
        self.meta[str(idx)] = {"path": str(file_path), "content": content}
        print(f"[INFO] Indexed {file_path}")

    def rebuild_index(self):
        """Rebuild entire index from vault."""
        self.index = faiss.IndexFlatIP(self.model.get_sentence_embedding_dimension())
        new_meta = {}
        idx = 0
        for file_path in self.vault_path.rglob("*.md"):
            # Skip files in .semantic-search directory
            if ".semantic-search" in str(file_path):
                continue
            try:
                content = self._read_file(file_path)
                if content is None:
                    continue
                vec = self._embed_text(content)
                self.index.add(vec)
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
        D, I = self.index.search(vec, k)
        results = []
        for score, idx in zip(D[0], I[0]):
            if str(idx) in self.meta:
                results.append({
                    "path": self.meta[str(idx)]["path"],
                    "score": float(score)
                })
        return results

    def find_duplicates(self, file_path: str) -> list[dict]:
        """Find potential duplicates of a file."""
        file_path = Path(file_path)
        if not file_path.is_absolute():
            file_path = self.vault_path / file_path

        if not file_path.exists():
            return {"error": f"File not found: {file_path}"}

        content = self._read_file(file_path)
        if content is None:
            return {"error": f"Could not read file: {file_path}"}
        vec = self._embed_text(content)

        if len(self.meta) == 0:
            return []

        D, I = self.index.search(vec, len(self.meta))
        duplicates = []
        for score, idx in zip(D[0], I[0]):
            if str(idx) in self.meta and score > self.duplicate_threshold:
                # Skip the file itself
                if Path(self.meta[str(idx)]["path"]).resolve() != file_path.resolve():
                    duplicates.append({
                        "path": self.meta[str(idx)]["path"],
                        "score": float(score)
                    })
        return duplicates


class VaultWatcher:
    """Watches vault for file changes and updates index."""

    def __init__(self, indexer: VaultIndexer):
        self.indexer = indexer
        self._observer = None
        self._thread = None

    def start(self, background: bool = True):
        """Start watching the vault."""
        handler = _VaultEventHandler(self.indexer)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.indexer.vault_path), recursive=True)
        self._observer.start()
        print(f"[INFO] Watching vault at {self.indexer.vault_path}")

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
            print(f"[INFO] File removed, rebuilding index...")
            self.indexer.rebuild_index()
