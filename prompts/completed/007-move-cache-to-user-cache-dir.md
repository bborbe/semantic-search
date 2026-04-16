---
status: completed
summary: Relocated FAISS index cache from OS tempdir to platformdirs user cache dir, with one-time best-effort migration; added platformdirs>=4.0 dependency and four new tests covering the new path and migration logic.
container: semantic-search-007-move-cache-to-user-cache-dir
dark-factory-version: v0.111.2
created: "2026-04-16T11:30:00Z"
queued: "2026-04-16T11:12:36Z"
started: "2026-04-16T11:14:03Z"
completed: "2026-04-16T11:16:48Z"
---

<summary>
- Move the persistent FAISS index cache out of the OS temp directory (which macOS auto-cleans) into the proper per-OS user cache directory
- macOS users get `~/Library/Caches/semantic-search/<hash>/` instead of the strange `/var/folders/.../T/semantic-search/<hash>/` path
- Linux users get `~/.cache/semantic-search/<hash>/` (or `$XDG_CACHE_HOME/semantic-search/<hash>/` when set)
- Adds `platformdirs` as a direct dependency — the canonical cross-platform library for user cache/config/data directories
- One-time best-effort migration: on startup, if an old tempdir cache exists for the same vault hash and no new cache is present, move the two cache files to the new location instead of re-embedding from scratch
- Cache format is unchanged (`index_meta.json` + `vector_index.faiss`); content hash algorithm unchanged; no new CLI flags
- Three new tests cover the new cache path, successful migration, and the "do not migrate when new cache already exists" guard
</summary>

<objective>
Relocate the on-disk FAISS index cache from `tempfile.gettempdir()/semantic-search/<hash>/` to a proper OS-appropriate user cache directory via the `platformdirs` library, so the cache is stable across reboots and not subject to macOS's periodic `/var/folders/.../T/` cleanup. Existing users with an old tempdir cache get a one-time, best-effort migration on first startup — no forced re-embed.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.14+, `uv`, strict mypy, src/ layout, dark-factory workflow — no direct coding, changes go through dark-factory prompts).

Read these files before making changes:

- `src/semantic_search/indexer.py` — contains `VaultIndexer.__init__`, which currently computes `self.index_dir = Path(tempfile.gettempdir()) / "semantic-search" / content_hash` (around the line with `hashlib.md5(paths_str.encode()).hexdigest()[:8]`). This is the single place that decides where the cache lives.
- `pyproject.toml` — the `[project].dependencies` list is where `platformdirs` must be added. Existing deps: `fastmcp>=3.2.0`, `starlette`, `uvicorn`, `sentence-transformers`, `faiss-cpu`, `watchdog`, `numpy`, `pyyaml`. Follow the same formatting (one dep per line, trailing-comma discipline of the existing list).
- `tests/test_indexer.py` — follow the existing `unittest.mock.patch` style: `patch("semantic_search.indexer.SentenceTransformer")`, 384-dim vectors via `np.array([[0.1] * 384])`. The `TestVaultIndexerInit` class is a good home for the new path test; new migration tests can live in a new `TestCacheMigration` class.
- `tests/conftest.py` — provides `temp_vault` fixture (single vault with one `test-note.md`). Reuse it.
- `prompts/completed/005-fix-indexer-runaway-rebuild.md` — prior prompt that last touched the cache path (removed the PID segment); read for style reference.

**Why `platformdirs` is the right choice:**

`platformdirs` (https://pypi.org/project/platformdirs/) is the maintained successor to `appdirs` and is the de facto standard Python library for OS-appropriate user directories. It is already installed transitively by many common dependencies (pytest, black, etc.), so the real install footprint of adding it as a direct dep is zero on most systems. Its `user_cache_dir(appname, appauthor=False)` returns:

- macOS: `~/Library/Caches/<appname>`
- Linux: `$XDG_CACHE_HOME/<appname>` if set, else `~/.cache/<appname>`
- Windows: `%LOCALAPPDATA%/<appname>/Cache` (flat because `appauthor=False`)

This is exactly what the user asked for. Do NOT roll your own `sys.platform` switch — `platformdirs` handles the XDG spec, fallback logic, and Windows quirks correctly.

**Migration rationale:**

Users with an existing cache in the old tempdir location should not be forced to re-embed thousands of files on next startup just because we changed the cache path. A best-effort migration — detect the old directory, move the two known cache files, log once, continue — is cheap, safe, and invisible when there's nothing to migrate. Any OSError during migration must be swallowed: the worst case is that `_load_index` sees no cache and rebuilds, which is the same behavior as before this change.

**Out of scope:**

- Do NOT change the cache format (`index_meta.json` + `vector_index.faiss`).
- Do NOT change the content_hash algorithm (still `hashlib.md5(paths_str.encode()).hexdigest()[:8]`).
- Do NOT add CLI flags to override the cache path.
- Do NOT touch `src/semantic_search/http_server.py`, `src/semantic_search/__main__.py`, or `src/semantic_search/factory.py`.
- Do NOT delete the old tempdir cache after migration — leave cleanup to the OS.
</context>

<requirements>

1. **Add `platformdirs>=4.0` to `pyproject.toml`.**

   In the `[project].dependencies` list, add `"platformdirs>=4.0"` at the end (after `"pyyaml"`). Match the existing formatting style exactly (the list items do not have trailing commas on the last line in the current file — follow whatever style is already in place; do not reformat other lines). After the edit, the dependencies block should read:

   ```toml
   dependencies = [
       "fastmcp>=3.2.0",
       "starlette",
       "uvicorn",
       "sentence-transformers",
       "faiss-cpu",
       "watchdog",
       "numpy",
       "pyyaml",
       "platformdirs>=4.0"
   ]
   ```

   Do NOT put it in `[project.optional-dependencies].dev` — it's a runtime dependency.

2. **Add the `platformdirs` import in `src/semantic_search/indexer.py`.**

   Add `from platformdirs import user_cache_dir` to the third-party import block. Let `ruff check --fix` handle ordering. Do NOT remove the existing `import tempfile` — it's still needed for the migration path (requirement 4).

3. **Replace the cache path computation in `VaultIndexer.__init__`.**

   Locate these lines (around the existing comment `# Store index in OS temp directory with content hash ...`):

   ```python
   # Store index in OS temp directory with content hash (no PID so cache survives restart)
   paths_str = ",".join(str(p.resolve()) for p in self.vault_paths)
   content_hash = hashlib.md5(paths_str.encode()).hexdigest()[:8]
   self.index_dir = Path(tempfile.gettempdir()) / "semantic-search" / content_hash
   self.index_file = self.index_dir / "vector_index.faiss"
   self.meta_file = self.index_dir / "index_meta.json"
   ```

   Replace with:

   ```python
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
   ```

   `appauthor=False` is load-bearing — it keeps the Windows path flat (no author subdirectory) and is a no-op on macOS/Linux. Keep the `content_hash` line and algorithm exactly as they are.

4. **Add a one-time, best-effort migration helper.**

   Add a new private method `_migrate_from_tempdir` on `VaultIndexer`, placed directly above `_load_index` (the method that is called at the end of `__init__`):

   ```python
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
   ```

   Key points the agent must get right:

   - Use `Path.replace` (not `shutil.move` or `os.rename`) because `replace` is atomic-within-filesystem and clobbers any partial file at the destination without raising.
   - Swallow `OSError` (covers `PermissionError`, `FileNotFoundError`, cross-device moves, etc.) — never propagate to the caller.
   - Emit exactly ONE INFO-level log line when migration actually runs: `"migrating index cache from tempdir to user cache dir"` (this exact substring; tests assert on it).
   - Do NOT delete the old directory after moving; leave it as an empty shell for OS cleanup.
   - Do NOT migrate if `new_meta.exists()` — the new location wins.

5. **Call the migration helper from `__init__`.**

   In `VaultIndexer.__init__`, immediately AFTER the `self.meta_file = self.index_dir / "index_meta.json"` line and BEFORE the existing `self.model = SentenceTransformer(embedding_model)` line, insert a single call:

   ```python
   self._migrate_from_tempdir(content_hash)
   ```

   Rationale: migration must happen before `_load_index` runs, otherwise `_load_index` would see "no cache at new path" and trigger a full rebuild, defeating the migration.

6. **Add a test `test_index_dir_uses_user_cache_dir`** in `tests/test_indexer.py` under the existing `TestVaultIndexerInit` class:

   ```python
   def test_index_dir_uses_user_cache_dir(
       self, temp_vault: Path, tmp_path: Path
   ) -> None:
       """index_dir must live under platformdirs.user_cache_dir, not tempdir."""
       fake_cache_root = tmp_path / "fake_user_cache" / "semantic-search"

       with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
           mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
           mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

           with patch(
               "semantic_search.indexer.user_cache_dir",
               return_value=str(fake_cache_root),
           ):
               from semantic_search.indexer import VaultIndexer

               indexer = VaultIndexer(str(temp_vault))

           # index_dir = <fake_cache_root>/<8-char-hash>
           assert str(indexer.index_dir).startswith(str(fake_cache_root))
           assert indexer.index_dir.parent == fake_cache_root
           assert len(indexer.index_dir.name) == 8  # md5 truncated to 8 chars
   ```

7. **Add a new test class `TestCacheMigration` at the end of `tests/test_indexer.py`** with three tests:

   ```python
   class TestCacheMigration:
       """Tests for one-time migration of the cache from tempdir to user cache dir."""

       def test_cache_migration_from_tempdir(
           self, temp_vault: Path, tmp_path: Path
       ) -> None:
           """Old tempdir cache is moved to the new location on first startup.

           Seed the OLD tempdir location with index_meta.json + vector_index.faiss
           for the expected content hash, then construct VaultIndexer and assert
           both files now live at the new location and the old ones are gone.
           """
           import hashlib
           import tempfile

           fake_cache_root = tmp_path / "fake_user_cache" / "semantic-search"

           # Reproduce the same hash the indexer will compute for temp_vault
           paths_str = str(temp_vault.resolve())
           content_hash = hashlib.md5(paths_str.encode()).hexdigest()[:8]

           old_dir = Path(tempfile.gettempdir()) / "semantic-search" / content_hash
           old_dir.mkdir(parents=True, exist_ok=True)
           old_meta = old_dir / "index_meta.json"
           old_faiss = old_dir / "vector_index.faiss"
           # Minimal valid meta JSON (empty index) — matches the format
           # _load_index writes via save_index.
           old_meta.write_text('{"meta": {}, "tombstones": []}')
           old_faiss.write_bytes(b"\x00\x01\x02\x03FAKE_FAISS")

           try:
               with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
                   mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
                   mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

                   # Mock faiss.read_index so we don't try to parse the fake bytes
                   with patch("semantic_search.indexer.faiss.read_index") as mock_read:
                       mock_read.return_value = Mock(ntotal=0)
                       with patch(
                           "semantic_search.indexer.user_cache_dir",
                           return_value=str(fake_cache_root),
                       ):
                           from semantic_search.indexer import VaultIndexer

                           indexer = VaultIndexer(str(temp_vault))

               new_meta = fake_cache_root / content_hash / "index_meta.json"
               new_faiss = fake_cache_root / content_hash / "vector_index.faiss"
               assert new_meta.exists(), "meta should have been migrated to new dir"
               assert new_faiss.exists(), "faiss file should have been migrated"
               assert not old_meta.exists(), "old meta should have been moved away"
               assert not old_faiss.exists(), "old faiss should have been moved away"
               # The migrated meta file drove _load_index
               assert indexer.meta == {}
           finally:
               # Clean up in case the test fails before migration
               for p in (old_meta, old_faiss):
                   if p.exists():
                       p.unlink()
               if old_dir.exists():
                   old_dir.rmdir()

       def test_no_migration_when_new_cache_present(
           self, temp_vault: Path, tmp_path: Path
       ) -> None:
           """If the new cache dir already has index_meta.json, old tempdir files
           are left untouched — the new location wins."""
           import hashlib
           import tempfile

           fake_cache_root = tmp_path / "fake_user_cache" / "semantic-search"

           paths_str = str(temp_vault.resolve())
           content_hash = hashlib.md5(paths_str.encode()).hexdigest()[:8]

           # Seed OLD location
           old_dir = Path(tempfile.gettempdir()) / "semantic-search" / content_hash
           old_dir.mkdir(parents=True, exist_ok=True)
           old_meta = old_dir / "index_meta.json"
           old_meta.write_text('{"meta": {"old": {"path": "/old"}}, "tombstones": []}')

           # Seed NEW location too (pre-existing newer cache)
           new_dir = fake_cache_root / content_hash
           new_dir.mkdir(parents=True, exist_ok=True)
           new_meta = new_dir / "index_meta.json"
           new_meta.write_text('{"meta": {"new": {"path": "/new"}}, "tombstones": []}')
           new_faiss = new_dir / "vector_index.faiss"
           new_faiss.write_bytes(b"NEWFAISS")

           try:
               with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
                   mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
                   mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

                   with patch("semantic_search.indexer.faiss.read_index") as mock_read:
                       mock_read.return_value = Mock(ntotal=1)
                       with patch(
                           "semantic_search.indexer.user_cache_dir",
                           return_value=str(fake_cache_root),
                       ):
                           from semantic_search.indexer import VaultIndexer

                           VaultIndexer(str(temp_vault))

               # Old file must still be there — migration skipped
               assert old_meta.exists(), "old meta must be left untouched"
               # New file must be unchanged
               assert new_meta.read_text().startswith(
                   '{"meta": {"new":'
               ), "new meta must not be overwritten"
           finally:
               for p in (old_meta,):
                   if p.exists():
                       p.unlink()
               if old_dir.exists():
                   old_dir.rmdir()

       def test_migration_swallows_oserror(
           self, temp_vault: Path, tmp_path: Path
       ) -> None:
           """Migration is best-effort: an OSError during replace must not
           propagate. The indexer must still construct successfully and fall
           back to the normal rebuild path."""
           import hashlib
           import tempfile

           fake_cache_root = tmp_path / "fake_user_cache" / "semantic-search"

           paths_str = str(temp_vault.resolve())
           content_hash = hashlib.md5(paths_str.encode()).hexdigest()[:8]

           old_dir = Path(tempfile.gettempdir()) / "semantic-search" / content_hash
           old_dir.mkdir(parents=True, exist_ok=True)
           old_meta = old_dir / "index_meta.json"
           old_meta.write_text('{"meta": {}, "tombstones": []}')

           try:
               with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
                   mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
                   mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

                   # Force Path.replace to blow up
                   def boom(self: Path, target: Path) -> Path:
                       raise OSError("simulated cross-device link")

                   with patch.object(Path, "replace", boom):
                       with patch(
                           "semantic_search.indexer.user_cache_dir",
                           return_value=str(fake_cache_root),
                       ):
                           from semantic_search.indexer import VaultIndexer

                           # Must not raise
                           indexer = VaultIndexer(str(temp_vault))
                           assert indexer.index_dir.parent == fake_cache_root
           finally:
               if old_meta.exists():
                   old_meta.unlink()
               if old_dir.exists():
                   old_dir.rmdir()
   ```

   Add the required imports at the top of `tests/test_indexer.py`. The file currently has `from unittest.mock import patch`; change it to `from unittest.mock import Mock, patch`.

8. **Update `CHANGELOG.md`** under `## Unreleased`:

   - `feat: Move persistent index cache from OS temp directory to platformdirs user cache dir (macOS: ~/Library/Caches/semantic-search/, Linux: ~/.cache/semantic-search/, Windows: %LOCALAPPDATA%/semantic-search/Cache/). macOS no longer auto-cleans the cache.`
   - `feat: One-time best-effort migration of existing tempdir cache to the new user cache location on first startup — no forced re-embed for existing users.`

9. **Strict mypy compliance.** The new `_migrate_from_tempdir` method must be fully typed (`content_hash: str`, return `-> None`). `user_cache_dir` returns `str`; wrap it in `Path(...)`. Do NOT add a `# type: ignore` unless mypy complains on the `platformdirs` import — `platformdirs` ships a `py.typed` marker, so the import should be clean. If it isn't, add the module to the existing `[[tool.mypy.overrides]]` pattern in `pyproject.toml` with a comment explaining why.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass unchanged.
- Do NOT change the cache file names (`index_meta.json`, `vector_index.faiss`).
- Do NOT change the content_hash algorithm or the 8-character truncation.
- Do NOT add any CLI flag, environment variable, or constructor argument to override the cache path.
- Do NOT touch `src/semantic_search/http_server.py`, `src/semantic_search/__main__.py`, or `src/semantic_search/factory.py`.
- Do NOT delete the old tempdir cache after migration (leave to OS cleanup).
- Do NOT use `shutil.move` or `os.rename` for migration — use `Path.replace`.
- Do NOT remove the `import tempfile` line from `indexer.py` — `_migrate_from_tempdir` still needs it.
- Migration errors are best-effort: catch `OSError` (and only `OSError`), log at WARNING, continue. Never let a migration error crash indexer construction.
- `platformdirs>=4.0` goes in `[project].dependencies`, NOT `[project.optional-dependencies].dev`.
- Follow strict mypy typing. All new code must have full annotations.
- Repo-relative paths only in code and tests — no absolute user paths, no home-relative paths, no `/Users/...` in source or tests.
- Do NOT introduce a `sys.platform` branch in the code — `platformdirs` is the single source of truth for the path.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Specifically confirm:
- `tests/test_indexer.py::TestVaultIndexerInit::test_index_dir_uses_user_cache_dir` passes.
- `tests/test_indexer.py::TestCacheMigration::test_cache_migration_from_tempdir` passes (old files moved, new files present).
- `tests/test_indexer.py::TestCacheMigration::test_no_migration_when_new_cache_present` passes (old files untouched).
- `tests/test_indexer.py::TestCacheMigration::test_migration_swallows_oserror` passes (construction succeeds despite OSError).
- All existing indexer/watcher/http/main/imports tests still pass.
- `make test` (full suite) passes.

Manual post-install sanity (NOT required to pass automated verification, but document it as the acceptance scenario for the user):

- After `uv sync` and `uv run semantic-search-http` (or equivalent):
  - On macOS, `ls ~/Library/Caches/semantic-search/` shows `<hash>/index_meta.json` and `<hash>/vector_index.faiss` after a cold start.
  - On second startup, logs show `[Indexer] Loaded index with N entries` — no full rebuild.
- If a user had a previous cache under `/var/folders/.../T/semantic-search/<hash>/`, the first startup under this version logs `[Indexer] migrating index cache from tempdir to user cache dir` once, and subsequent startups load directly from the new path.
</verification>
