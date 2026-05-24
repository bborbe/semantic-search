---
status: completed
spec: [001-content-fetch-endpoint]
summary: Implemented VaultIndexer.get_content() with path validation, full/snippet modes, and query-based best-line scoring
container: semantic-search-exec-012-content-fetch-core
dark-factory-version: v0.171.1-3-gd94f1fa
created: "2026-05-24T21:00:00Z"
queued: "2026-05-24T21:07:05Z"
started: "2026-05-24T21:07:07Z"
completed: "2026-05-24T21:08:59Z"
branch: dark-factory/content-fetch-endpoint
---

## Summary

- Add a `get_content` capability to the indexer for retrieving file content by path
- Validate paths against indexed vault roots; reject anything outside (including symlink escapes)
- Support three modes: full file, snippet around best query match, snippet of file head
- Clamp `context_lines` to safe bounds and return a structured result with `path`, `content`, `mode`

## Objective

Implement the core content-fetch logic in `VaultIndexer` — path validation, file reading, snippet extraction, and all three modes. This becomes the shared implementation that both the HTTP endpoint and the MCP tool delegate to.

## Context

Read these files before making changes:

- `/workspace/src/semantic_search/indexer.py` — the `VaultIndexer` class, especially `_read_file()` (line 137), `vault_paths` (line 41), and the path-resolution patterns used in `find_duplicates`
- `/workspace/tests/conftest.py` — shared fixtures (`temp_vault`, `_isolated_indexer_cache`)
- `/workspace/tests/test_indexer.py` — existing test patterns for VaultIndexer
- `/workspace/docs/dod.md` — DoD checklist (docstrings, type hints, no `print()`, no `except Exception`)

## Requirements

1. In `src/semantic_search/indexer.py`, add a new public method to `VaultIndexer`:

   ```python
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
           query: Search string used to find the best-matching line (only meaningful when snippet=True)
           context_lines: Number of lines before and after the best match to include

       Returns:
           Dict with keys: "path" (resolved absolute path), "content" (string), "mode" ("full" | "snippet")

       Raises:
           ValueError: If path resolves outside the indexed vault roots
           FileNotFoundError: If path is inside roots but file does not exist
       """
   ```

2. **Path validation logic**: Resolve the path using `Path(path).resolve()` (follows symlinks). Check whether the resolved path is inside any `vault_paths` directory. If not, raise `ValueError("path not in indexed roots")` — do NOT read the file.

3. **Existence check (must happen BEFORE reading)**: After path validation passes, check `resolved_path.exists()`. If False, raise `FileNotFoundError(f"file not found: {path}")`. This must be distinct from the path-validation error. The existence check MUST run before `_read_file()` because `_read_file()` would propagate `FileNotFoundError` from `open()` and conflate it with this case.

4. **File reading**: Only if existence check passes, call `self._read_file(resolved_path)` to get the file content. If it returns `None` (encoding failure — `_read_file` tried utf-8, latin-1, cp1252 and all failed), raise `RuntimeError("could not read file")`.

5. **Full mode** (`snippet=False`): Return `{"path": resolved_path_str, "content": file_content, "mode": "full"}`.

6. **Snippet mode with query** (`snippet=True`, `query` is non-empty string):
   - Split `file_content` into lines
   - Tokenize the query: `tokens = query.lower().split()` (whitespace split, case-folded)
   - For each line, compute `score = sum(1 for tok in tokens if tok in line.lower())` — count of DISTINCT query tokens that appear as case-insensitive substrings anywhere in the line
   - Pick the line with the highest score (ties broken by first occurrence — use `max` with a key or iterate in order)
   - If the best score is `0` (no line contains any token), fall back to the file-head behavior (Requirement 7)
   - Return `"\n".join(lines[max(0, best_idx - context_lines): best_idx + context_lines + 1])` (clamped to file bounds)
   - `mode` is always `"snippet"`

7. **Snippet mode without query** (`snippet=True`, `query` is `None` or empty string): Return the first `2 * context_lines + 1` lines of the file. `mode` is `"snippet"`.

8. **`context_lines` clamping**: If `context_lines` is negative, use `0`. If `context_lines * 2 + 1` exceeds the file's total line count, return all lines. No error — proceed with clamped values.

9. **Return path field**: The `"path"` field in the returned dict must be the resolved absolute path as a string, not the original input.

10. Follow `docs/dod.md` — add docstring, type hints on all params and return, no `print()` in library code, no bare `except Exception`.

## Constraints

- Reuse `_read_file()` for all file reads — do not open files directly
- Use only stdlib for snippet logic (no new dependencies)
- `search_related` response shape is frozen — do not change it
- Tests for this method are written in prompt 4; do not add ad-hoc tests in this prompt

## Verification

Run `make test` after each change (fast feedback loop). When complete, run `make precommit` as final validation.

```bash
cd /workspace && make test
# Then after all changes:
cd /workspace && make precommit
```