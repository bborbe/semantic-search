---
status: completed
summary: 'Added show_progress_bar=False to _embed_text in indexer.py, pinned behavior with TestEmbedNoProgressBar test, and created ## Unreleased section in CHANGELOG.md.'
container: semantic-search-009-disable-tqdm-progress-bar-in-encode
dark-factory-version: v0.111.2
created: "2026-04-16T11:30:00Z"
queued: "2026-04-16T18:03:28Z"
started: "2026-04-16T18:04:38Z"
completed: "2026-04-16T18:05:49Z"
---

<summary>
- Embedding calls no longer auto-enable a tqdm progress bar, eliminating a thread-safety race that surfaced in production
- Concurrent encode calls from the watcher thread and the HTTP search handler stop crashing with `'tqdm' object has no attribute 'sp'`
- The fix is a single keyword argument on one `model.encode(...)` call; all other encode paths already route through that method
- A new unit test locks in the behavior so a future refactor cannot silently re-enable the progress bar
- Residual risk during cold-start rebuild and tombstone compaction is eliminated without changing any public API, response shape, or dependency
- Search / duplicates / reindex behavior on the happy path is unchanged — only the internal encode call gains one kwarg
</summary>

<objective>
Eliminate the `'tqdm' object has no attribute 'sp'` race that struck 153 times in production between 2026-04-15 and 2026-04-16 08:34. Root cause: `sentence_transformers.SentenceTransformer.encode()` defaults `show_progress_bar` to `None`, which auto-enables a tqdm progress bar whose internal state is not thread-safe. When the `VaultWatcher` thread and an HTTP `/search` handler call `model.encode()` concurrently, tqdm's singleton gets corrupted. Fix: explicitly pass `show_progress_bar=False` in the single internal `_embed_text` method that all embed paths funnel through. Pin the behavior with a unit test.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.14+, `uv`, strict mypy, src/ layout, dark-factory workflow, tests in `tests/test_*.py`, pytest with pytest-asyncio, `unittest.mock.patch`/`Mock`).

Read these files before making changes:

- `src/semantic_search/indexer.py` — `VaultIndexer._embed_text` (around line 244) is the single chokepoint every embed call goes through. `VaultIndexer.search` (around line 360) and `VaultIndexer.find_duplicates` (around line 403) both call `self._embed_text(...)` for the query vector, so fixing `_embed_text` covers every production path including the watcher-thread incremental add.
- `tests/test_indexer.py` — existing test style: one `class Test<Feature>:` per behavior, `patch("semantic_search.indexer.SentenceTransformer")` to stub the model, `mock_model.encode.return_value = np.array([[0.1] * 384])`, `temp_vault` fixture for a throwaway vault path. `Mock` and `patch` are already imported: `from unittest.mock import Mock, patch`. `numpy as np` is already imported. The new test class goes at the end of the file.
- `CHANGELOG.md` — the current top entry is `## v0.8.1`. There is NO `## Unreleased` section yet. The agent must create a new `## Unreleased` section above `## v0.8.1` and put the new bullet under it.

**Why this fix and not a lock or env var:**

- Wrapping `encode` in a `threading.Lock` would serialize search and watcher-thread embeds — measurable latency regression.
- `TQDM_DISABLE=1` as an env var is process-wide and a foot-gun for anyone running the CLI interactively who still wants progress bars elsewhere.
- Passing `show_progress_bar=False` is the single documented, stable knob on `SentenceTransformer.encode` (the signature is `encode(sentences, ..., show_progress_bar: bool | None = None, ...)`) and exactly matches what we want: no tqdm, ever, inside `_embed_text`.
- `_embed_text` is the only method that calls `self.model.encode(...)` on the hot path. `search` and `find_duplicates` both call `_embed_text` for the query vector (verified lines 360 and 403 in the current file). `add_file_to_index` / `rebuild_index` / tombstone compaction all route through `_embed_text` as well. Single point of change = complete coverage.

**Current verbatim code that must change** in `src/semantic_search/indexer.py` (around line 244):

```python
def _embed_text(self, text: str) -> np.ndarray:
    """Generate embedding vector for text."""
    vec = self.model.encode([text], normalize_embeddings=True)
    return vec.astype("float32")
```
</context>

<requirements>

1. **Change `_embed_text` in `src/semantic_search/indexer.py`.** Replace the body verbatim (the method around line 244) with:

   ```python
   def _embed_text(self, text: str) -> np.ndarray:
       """Generate embedding vector for text.

       `show_progress_bar=False` is load-bearing: sentence-transformers defaults
       to `None` which auto-enables tqdm, whose internal state is not thread-safe.
       Concurrent encode() calls (watcher thread + HTTP search handler) were
       racing on tqdm's `.sp` attribute and raising in production.
       """
       vec = self.model.encode(
           [text], normalize_embeddings=True, show_progress_bar=False
       )
       return vec.astype("float32")
   ```

   Notes:
   - Do NOT change the method signature (`self, text: str) -> np.ndarray`).
   - Do NOT pass any kwarg other than `show_progress_bar=False` (no `batch_size`, no `convert_to_numpy`, no `device`).
   - Do NOT touch `search` (around line 360) or `find_duplicates` (around line 403) — they already delegate to `_embed_text`. Verify by grepping `self.model.encode(` in `src/semantic_search/indexer.py`: the only call on the hot path after this change must be the one inside `_embed_text`.

2. **Add a new test class `TestEmbedNoProgressBar`** at the end of `tests/test_indexer.py`. Append exactly:

   ```python
   class TestEmbedNoProgressBar:
       """Ensure _embed_text disables tqdm to avoid the threading race.

       sentence_transformers.encode() defaults to show_progress_bar=None which
       auto-enables tqdm — not thread-safe. We always pass False.
       """

       def test_embed_text_passes_show_progress_bar_false(
           self, temp_vault: Path
       ) -> None:
           """_embed_text must pass show_progress_bar=False to model.encode()."""
           with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
               mock_model = Mock()
               mock_model.get_sentence_embedding_dimension.return_value = 384
               mock_model.encode.return_value = np.array([[0.1] * 384])
               mock_st.return_value = mock_model

               from semantic_search.indexer import VaultIndexer

               indexer = VaultIndexer(str(temp_vault))

               # Clear any calls from __init__/rebuild
               mock_model.encode.reset_mock()

               # Trigger an embed
               indexer._embed_text("hello world")

               # Assert last call had show_progress_bar=False
               assert mock_model.encode.called
               call_kwargs = mock_model.encode.call_args.kwargs
               assert call_kwargs.get("show_progress_bar") is False, (
                   f"encode() must be called with show_progress_bar=False "
                   f"to avoid the tqdm threading race; got kwargs={call_kwargs}"
               )
   ```

   Notes:
   - `Mock` and `patch` are already imported at the top of `tests/test_indexer.py` (`from unittest.mock import Mock, patch`). Do not re-import.
   - `numpy as np` is already imported. Do not re-import.
   - `Path` is already imported (`from pathlib import Path`). Do not re-import.
   - `temp_vault` is an existing pytest fixture used by other test classes in this file — do not redefine it.
   - Place the class at the very end of the file, after the last existing test class, separated by two blank lines per PEP 8.

3. **Update `CHANGELOG.md`.** The file currently has no `## Unreleased` section — the top non-header entry is `## v0.8.1`. Insert a new `## Unreleased` section immediately above `## v0.8.1` with one bullet:

   ```
   ## Unreleased

   - fix: Disable `tqdm` progress bar in `sentence-transformers` encode calls — eliminates `'tqdm' object has no attribute 'sp'` race condition that occurred when the watcher thread and HTTP search handler called `model.encode()` concurrently. 153 production errors pre-v0.8.1; residual risk during cold-start rebuild / tombstone compaction now eliminated.
   ```

   Keep one blank line before `## v0.8.1`. Do not modify any existing entry.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Do NOT change the public signature of `_embed_text` (it stays `(self, text: str) -> np.ndarray`).
- Do NOT pass any new kwarg to `encode()` other than `show_progress_bar=False`.
- Do NOT add a `threading.Lock` around `encode()`, `_embed_text`, or `search`.
- Do NOT set `TQDM_DISABLE` or any other tqdm env var.
- Do NOT modify `search` (around line 360) or `find_duplicates` (around line 403) — they already route through `_embed_text`.
- Do NOT modify any file other than `src/semantic_search/indexer.py`, `tests/test_indexer.py`, and `CHANGELOG.md`.
- Do NOT change the embedding model, embedding dimension, or FAISS index type.
- Existing tests must still pass — many existing tests use `mock_model.encode.return_value = np.array([[0.1] * 384])` without asserting kwargs; adding the kwarg does not break them because `Mock.encode` accepts any kwargs by default.
- Follow strict mypy typing. `show_progress_bar=False` is a concrete `bool`; no annotation changes needed.
- Repo-relative paths only.
- Out of scope: wrapping locks, env-var tqdm disabling, changing the embedding model, pinning sentence-transformers to a tqdm-free release.
</constraints>

<verification>
Run `make precommit` — must pass (0 ruff errors, 0 mypy errors, all pytest tests green).

Specifically confirm:
- `tests/test_indexer.py::TestEmbedNoProgressBar::test_embed_text_passes_show_progress_bar_false` passes.
- All existing indexer tests still pass (`make test` or `uv run pytest tests/test_indexer.py`).
- `make typecheck` passes with zero mypy errors.
- `make lint` passes with zero ruff errors.
- Grep sanity check: `grep -n "self.model.encode(" src/semantic_search/indexer.py` returns exactly one hit, inside `_embed_text`, and that hit includes `show_progress_bar=False`.
- Manual post-install smoke (not required to pass in CI, useful to note): after restarting the HTTP server, `grep "tqdm.*sp" /tmp/semantic-search-http*.log | awk '{print $1,$2}'` should return no new entries dated after the restart timestamp.
</verification>
