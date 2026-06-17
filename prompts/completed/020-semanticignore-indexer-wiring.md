---
status: completed
spec: [003-semanticignore-support]
summary: 'Wired VaultIgnore into VaultIndexer: rebuild_index and add_file_to_index now skip files matched by each vault''s .semanticignore, with per-vault skip-count INFO log; 7 new integration tests covering AC3/AC4/AC7/AC8/AC9 and backward compatibility.'
container: semantic-search-semanticignore-exec-020-semanticignore-indexer-wiring
dark-factory-version: v0.181.0
created: "2026-06-17T00:00:00Z"
queued: "2026-06-17T10:26:28Z"
started: "2026-06-17T10:37:16Z"
completed: "2026-06-17T10:40:38Z"
branch: dark-factory/semanticignore-support
---

## Summary

- Teaches the indexer to respect each vault root's `.semanticignore` file
- Full rebuilds now skip any markdown file matched by the owning vault's ignore rules
- Single-file add/update becomes a no-op when the file is ignored — no embedding, no index growth
- The `.semanticignore` file is never indexed, regardless of patterns
- Each vault root uses its own ignore rules; rules in one vault never affect another
- After a rebuild, one INFO log line per vault reports how many files were skipped, in a stable greppable form
- Indexer public API stays backward-compatible: callers that pass no ignore support keep working unchanged

## Objective

Wire the `VaultIgnore` filter from prompt 1 into `VaultIndexer` so that `rebuild_index` and the single-file `add_file_to_index` entry point exclude ignored paths, with per-vault rule isolation and a per-vault skip-count INFO log. The watcher and runtime reload are handled in prompt 3.

## Context

Read these files before making changes:

- `/workspace/CLAUDE.md` — project conventions
- `/workspace/docs/dod.md` — Definition of Done (docstrings, type hints, no `print`, no bare `except Exception`, coverage)
- `/workspace/src/semantic_search/ignore.py` — the `VaultIgnore` class created in prompt 1. Key surface (verify by reading): `VaultIgnore(vault_root: str | Path)`, `is_ignored(path: str | Path) -> bool`, `reload() -> None`. If this file does NOT exist, STOP and report `Status: failed` with `".semanticignore ignore module not yet deployed (prompt 1)"` — do NOT create it here.
- `/workspace/src/semantic_search/indexer.py` — the `VaultIndexer` class. Relevant points:
  - `__init__(self, vault_paths: str | list[str], embedding_model: str = "all-MiniLM-L6-v2", duplicate_threshold: float = 0.85)` at lines 32-63. `self.vault_paths` is a `list[Path]` of `expanduser()`-ed roots (line 41).
  - `rebuild_index(self)` at lines 323-359 — iterates `for vault_path in self.vault_paths: for file_path in vault_path.rglob("*.md"):`, currently skips only paths containing `.semantic-search` (lines 332-334), logs `[Indexer] Rebuilt index with {len(self.meta)} files` (line 359).
  - `add_file_to_index(self, file_path: str | Path)` at lines 255-287 — currently guards on `file_path.exists()` and `.suffix != ".md"` (line 262).
- `/workspace/tests/conftest.py` — fixtures `temp_vault`, `multi_vaults`, autouse `_isolated_indexer_cache`.
- `/workspace/tests/test_indexer.py` — existing VaultIndexer test patterns; reuse the `patch("semantic_search.indexer.SentenceTransformer")` + `mock_st.return_value.get_sentence_embedding_dimension.return_value = 384` + `mock_st.return_value.encode.return_value = np.array([[0.1] * 384])` idiom so the real model is never loaded.
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-logging-guide.md` — logging conventions

## Requirements

1. **Import** `VaultIgnore` from the ignore module into `indexer.py` (`from semantic_search.ignore import VaultIgnore` — match the import style of the existing in-package imports; verify the correct absolute import path used elsewhere in the file/package).

2. **Build the per-vault ignore map unconditionally.** Do NOT add any new constructor parameter. The signature stays exactly as today (`vault_paths`, `embedding_model`, `duplicate_threshold`) — every existing positional caller keeps working unchanged. After `self.vault_paths` is set (line 41), build a per-root ignore map:

   ```python
   self._ignores: dict[Path, VaultIgnore] = {vp: VaultIgnore(vp) for vp in self.vault_paths}
   ```

   The spec's Non-goals explicitly forbid a runtime opt-out flag (`.semanticignore` filtering is invariant). The constraint "constructor signature may gain one optional parameter" is backward-compatibility headroom, not a license to introduce an opt-out — appending nothing already preserves every existing call site.

   Note: `_load_index` (called at the end of `__init__`, line 63) may invoke `rebuild_index`, so `self._ignores` MUST be assigned BEFORE `self._load_index()` is called.

3. **Add a private helper** `_is_ignored(self, vault_root: Path, file_path: Path) -> bool` on `VaultIndexer`:
   - Look up the `VaultIgnore` for `vault_root` in `self._ignores`. If absent (defensive; should not happen given req 2), return `False`.
   - Return `vault_ignore.is_ignored(file_path)`.
   - Add a docstring and full type hints.

4. **Apply in `rebuild_index`** (lines 323-359):
   - In the loop, track a per-vault skipped counter. Initialize `skipped = 0` at the start of each `for vault_path in self.vault_paths:` iteration.
   - Keep the existing `.semantic-search` skip (lines 332-334) unchanged.
   - After the `.semantic-search` check, add: `if self._is_ignored(vault_path, file_path): skipped += 1; continue`. The `.semanticignore` file itself is matched as ignored by `VaultIgnore`, so it is naturally excluded (it is not a `.md` file and would not be matched by `rglob("*.md")` anyway — but the helper guarantees correctness for any path).
   - After finishing the inner loop for that vault (still inside the `for vault_path` loop), emit exactly one INFO log per vault in this **stable, greppable** form (AC9): the message MUST match the regex `r'rebuild_index skipped (\d+) files for vault .+'` and the vault path printed MUST equal the vault root being processed. Use:
     ```python
     logger.info(f"rebuild_index skipped {skipped} files for vault {vault_path}")
     ```
     Place this log so it fires once per vault even when `skipped == 0`.
   - Leave the existing final `logger.info(f"[Indexer] Rebuilt index with {len(self.meta)} files")` (line 359) unchanged.

5. **Apply in `add_file_to_index`** (lines 255-287): after the existing `.suffix != ".md"` / `exists()` guard (line 262) and before reading content, determine which vault root owns `file_path` and short-circuit if ignored:
   - Find the owning vault root: the first `vp` in `self.vault_paths` such that the resolved `file_path` is relative to `vp` (use `Path.is_relative_to`, consistent with `get_content` at line 420 which uses `resolved_path.is_relative_to(vp.resolve())`). Resolve both sides for symlink consistency.
   - If an owning root is found and `self._is_ignored(owning_root, file_path)` is True, `return` immediately (no embedding, no `self.index.add`, no `self.meta` mutation, no `save_index`). This makes the call a true no-op so `index.ntotal` is unchanged (AC4).
   - If no owning root is found (path outside all vaults), keep the existing behavior (do not add ignore handling — the existing flow already handles it).

6. **Do NOT change** `remove_file_from_index`, `search`, `get_content`, `find_duplicates`, the index file format, or the on-disk metadata schema.

7. **Follow DoD:** docstrings/type hints on the new helper, no `print`, no bare `except Exception`.

8. **Add integration tests to `/workspace/tests/test_indexer.py`** (real vaults on `tmp_path`, mocked `SentenceTransformer` per the idiom above). Cover:
   - **AC3** — Build a vault with `a.md`, `b.md`, `archive/old.md`, and `.semanticignore` containing `archive/`. Construct `VaultIndexer(str(vault))`, call `rebuild_index()`, assert `{Path(v["path"]).name for v in indexer.meta.values()} == {"a.md", "b.md"}` (or assert the full path set equals `{vault/a.md, vault/b.md}`). `archive/old.md` must be absent.
   - **AC4** — In a vault with `.semanticignore` containing `archive/` and a file `archive/old.md`, after an initial `rebuild_index`, capture `indexer.index.ntotal`, call `indexer.add_file_to_index(str(vault / "archive" / "old.md"))`, assert `indexer.index.ntotal` is unchanged and `archive/old.md` is not in `meta`.
   - **AC7** — After `rebuild_index`, assert no entry in `meta` has a path basename equal to `.semanticignore` (test both: an empty `.semanticignore`, and one with patterns).
   - **AC8** — Use two vault roots. Vault A's `.semanticignore` contains `secret.md`; vault B has its own different/empty `.semanticignore`. Put `secret.md` in BOTH vaults. After `rebuild_index`, assert vault A's `secret.md` is excluded but vault B's `secret.md` is present (cross-vault independence). Build with `VaultIndexer([str(vaultA), str(vaultB)])`.
   - **AC9** — Use `caplog.at_level(logging.INFO)` around `rebuild_index`. Assert at least one captured record's `message` matches `re.search(r'rebuild_index skipped (\d+) files for vault .+', record.message)`, and that a record exists whose message ends with the vault root path passed in. Assert the count captured equals the number of files actually skipped (e.g. 1 when `archive/old.md` is ignored).
   - **Backward compatibility** — Assert the original 3-positional-arg construction `VaultIndexer(str(vault))` still works (no signature change), and that calling with `embedding_model=` and `duplicate_threshold=` keywords still works.

9. **Coverage:** changed/added code paths in `indexer.py` (the helper, the rebuild skip branch + log, the add-file short-circuit) must all be exercised by the tests above; target ≥ 80% on changed code.

## Constraints

[Copied from spec — the executing agent has no memory of the spec.]

- Must NOT change the existing `Indexer` public API signature. The `.semanticignore` filter is invariant (no runtime opt-out flag, per spec Non-goals).
- Must NOT change index file format or on-disk metadata schema.
- Must preserve the existing hardcoded skip for paths containing `.semantic-search`.
- Paths matched **relative to the vault root**, using POSIX-style forward slashes (handled inside `VaultIgnore`).
- The `.semanticignore` file itself is always excluded from the index.
- Missing `.semanticignore` means "index everything" — no behavior change for existing vaults.
- The INFO skip-log per vault must match `r'rebuild_index skipped (\d+) files for vault .+'`.
- Must satisfy project DoD: docstrings, full type hints, no `print`, `make precommit` clean.
- Do NOT wire the watcher / runtime reload in this prompt — that is prompt 3.
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass.

## Verification

```bash
cd /workspace && uv run pytest tests/test_indexer.py tests/test_ignore.py -v   # all pass, exit 0
cd /workspace && uv run pytest -q                                              # full suite green
cd /workspace && make precommit                                                # ruff + mypy strict + pytest, exit 0
```
