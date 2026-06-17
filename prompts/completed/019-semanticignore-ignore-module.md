---
status: completed
spec: [003-semanticignore-support]
summary: Created VaultIgnore module with pathspec-based gitignore pattern matching, full error handling, and 17-test suite covering all specified scenarios.
container: semantic-search-semanticignore-exec-019-semanticignore-ignore-module
dark-factory-version: v0.181.0
created: "2026-06-17T00:00:00Z"
queued: "2026-06-17T10:26:28Z"
started: "2026-06-17T10:26:30Z"
completed: "2026-06-17T10:37:15Z"
branch: dark-factory/semanticignore-support
---

## Summary

- Adds a new dependency that understands gitignore-style pattern matching
- Introduces a self-contained "ignore" component: given a vault root, it loads that root's `.semanticignore` file (if present) and answers "should this path be ignored?"
- Matching follows gitignore semantics: simple patterns, directory patterns, double-star, negation, anchoring
- The `.semanticignore` file itself is always reported as ignored
- A missing or empty `.semanticignore` means "index everything" — nothing is ignored
- Bad input is handled gracefully: an unreadable file or an oversized file (> 1 MiB) falls back to "accept everything" with an ERROR log; a single malformed pattern line is logged at ERROR (with line number) and skipped while the rest still apply
- No indexer wiring happens in this prompt — this is the standalone module plus its full unit-test suite

## Objective

Create a new, fully unit-tested ignore module for the semantic-search package that loads a vault's `.semanticignore` file and exposes a predicate deciding whether a given path should be excluded from indexing. No indexer/watcher integration yet — that lands in later prompts.

## Context

Read these files before making changes:

- `/workspace/CLAUDE.md` — project conventions (Python 3.13+, `uv`, strict mypy, src/ layout at `src/semantic_search/`)
- `/workspace/docs/dod.md` — Definition of Done (docstrings on public functions/classes, full type hints, no `print`, no bare `except Exception`, coverage rules)
- `/workspace/pyproject.toml` — runtime `dependencies` list (lines 11-21) and the `dev` optional-deps list; mypy is `strict = true`
- `/workspace/src/semantic_search/indexer.py` — note `logger = logging.getLogger(__name__)` at module top (line 23); follow the same module-level logger pattern in the new module
- `/workspace/tests/conftest.py` — shared fixtures (`temp_vault`, `multi_vaults`, `_isolated_indexer_cache`); note tests are fully type-annotated and class-based (`class TestFoo:` with `def test_x(self, ...) -> None:`)
- `/workspace/tests/test_indexer.py` — existing test style for reference
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-logging-guide.md` — logging conventions
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-project-structure.md` — module placement

## Requirements

1. **Add `pathspec` to runtime dependencies.** In `/workspace/pyproject.toml`, add `"pathspec"` to the `[project]` `dependencies` array (lines 11-21) — NOT to `[project.optional-dependencies].dev`. Place it alphabetically or at the end of the list; keep valid TOML. After this, `grep -n '^\s*"pathspec"' pyproject.toml` must return a line inside the runtime `dependencies` block. Then run `uv sync` (or `uv pip install -e '.[dev]'`) so `pathspec` is importable for tests.

2. **Create the new module** `/workspace/src/semantic_search/ignore.py` with a module-level logger `logger = logging.getLogger(__name__)`.

3. **Public class `VaultIgnore`** in that module. Constructor and the predicate must match these signatures exactly:

   ```python
   class VaultIgnore:
       """Loads a vault root's .semanticignore file and decides which paths to exclude.

       Patterns use gitignore (gitwildmatch) semantics and are matched relative to
       the vault root using POSIX-style forward slashes. A missing or empty
       .semanticignore means "ignore nothing". The .semanticignore file itself is
       always ignored.
       """

       def __init__(self, vault_root: str | Path) -> None:
           ...

       def is_ignored(self, path: str | Path) -> bool:
           """Return True iff the given path should be excluded from indexing.

           The path may be absolute or relative; it is interpreted relative to the
           vault root. Paths outside the vault root are treated as NOT ignored.
           """
           ...

       def reload(self) -> None:
           """Re-read .semanticignore from disk and rebuild the compiled matcher.

           Used when the file changes at runtime. Replaces the internal matcher
           atomically (build the new one fully, then assign).
           """
           ...
   ```

4. **Loading logic (factor into a private method called by both `__init__` and `reload`):**
   - The ignore file lives at `vault_root / ".semanticignore"`.
   - If the file does not exist → compiled matcher matches nothing (ignore nothing). This is the no-op default. Do NOT log an error for a missing file.
   - Before reading, check file size via `Path.stat().st_size`. If size > `1 * 1024 * 1024` (1 MiB), log at ERROR (include the file path) and fall back to the accept-all (ignore-nothing) matcher. Do not read the file.
   - Read the file as UTF-8 with `errors="replace"`. If reading raises `OSError`, log at ERROR (include the file path and the exception) and fall back to the accept-all matcher.
   - Parse line-by-line. For each non-empty, non-comment line, attempt to compile it into a `pathspec` pattern. If a single line is malformed (raises an exception from `pathspec`), log at ERROR naming the **1-based line number** and the file path, skip that line, and continue compiling the remaining lines. Build the final matcher from all successfully-compiled lines.

5. **Use `pathspec` with `gitwildmatch` syntax.** Verified API (grep `pathspec` module source under `$(uv run python -c "import pathspec, os; print(os.path.dirname(pathspec.__file__))")` before writing if unsure):
   - `import pathspec`
   - To validate/compile a single pattern line and catch malformed patterns: `pathspec.patterns.GitWildMatchPattern(line)` — wrap in try/except to detect malformed lines per requirement 4. (Grep `class GitWildMatchPattern` in the pathspec source to confirm the constructor; if the exact name differs in the installed version, use the documented `pathspec.PathSpec.from_lines("gitwildmatch", [line])` form to test a single line and `pathspec.PathSpec.from_lines("gitwildmatch", good_lines)` for the final matcher.)
   - Final matcher: build `pathspec.PathSpec` from the list of successfully-compiled pattern objects (or good lines). Match with `spec.match_file(relative_posix_path)` which returns a bool honoring negation, `**`, anchoring, and directory semantics.
   - **Verify the chosen call exists**: run `grep -rn "from_lines\|match_file\|GitWildMatchPattern" $(uv run python -c "import pathspec, os; print(os.path.dirname(pathspec.__file__))")` and use only symbols that appear.

6. **`is_ignored` path handling:**
   - Resolve the candidate path relative to the vault root. Compute the path relative to `vault_root` using `Path.relative_to` semantics; if the path is not under the vault root, return `False` (not ignored).
   - Convert the relative path to POSIX form (`as_posix()`) before calling the matcher, so matching uses forward slashes regardless of OS.
   - **The `.semanticignore` file itself is always ignored**: if the relative POSIX path equals `.semanticignore`, return `True` immediately, regardless of compiled patterns.

7. **Atomic reload:** in `reload` and the shared loader, construct the new matcher object completely, then assign it to the instance attribute in one statement. Do not mutate a live matcher in place.

8. **Follow DoD:** docstrings on the class and all public methods, full type hints on every parameter and return, no `print`, no bare `except Exception` (catch the specific exception types — `OSError` for I/O, and the specific exception `pathspec` raises for malformed patterns; if unsure of the exact pathspec exception type, catch the narrowest type that grep of the pathspec source shows it raising, e.g. `pathspec.patterns.GitWildMatchPatternError` if present, otherwise `ValueError`).

9. **Write `/workspace/tests/test_ignore.py`** as a class-based, fully type-annotated pytest suite (`package_test` not required for Python; follow existing test file conventions). Use `tmp_path: Path` and write `.semanticignore` files directly. Cover ALL of these cases (each as its own test method):
   - **missing file** → `is_ignored` returns False for every path
   - **empty file** → returns False for every path
   - **simple pattern** (e.g. `secret.md`) → that file ignored, others not
   - **directory pattern** (e.g. `archive/`) → `archive/old.md` ignored, `a.md` not
   - **double-star** (e.g. `**/draft.md` or `notes/**`) → matches nested paths
   - **negation** (e.g. `archive/` then `!archive/keep.md`) → `archive/keep.md` NOT ignored, `archive/old.md` ignored
   - **anchored pattern** (e.g. leading `/` like `/root-only.md`) → only the root-level file matches, a nested same-name file does not
   - **the `.semanticignore` file itself is always ignored** → `is_ignored(vault_root / ".semanticignore")` returns True even with an empty patterns file
   - **malformed pattern line** (AC10): a file with a line `pathspec` rejects (e.g. an unmatched-bracket pattern such as `[invalid`) plus a valid line. Use `caplog` at ERROR level: assert an ERROR record is captured whose message names the offending **line number**, AND assert the predicate still works for the unaffected valid pattern (the valid pattern still matches; the malformed line does not crash loading).
   - **`pathspec` boundary contract**: add a separate test that pins the upstream behavior the malformed-line case depends on — call `pathspec.PathSpec.from_lines("gitwildmatch", ["[invalid"])` (or `pathspec.patterns.GitWildMatchPattern("[invalid")`) at test time and assert it raises (catch `Exception` broadly; the test only cares that *something* is raised). If `pathspec` ever stops rejecting `[invalid`, this test fails loudly and tells you to pick a new sentinel — preventing silent regression of the skip-and-continue path.
   - **oversized file** (constraint): monkeypatch or write a `.semanticignore` whose `stat().st_size` exceeds 1 MiB (write > 1 MiB of pattern text). Assert an ERROR is logged and the matcher accepts everything (ignores nothing).
   - **unreadable file**: simulate `OSError` on read (e.g. `monkeypatch` `Path.read_text`/`open` to raise `OSError`, or point the loader at a path whose open raises). Assert ERROR logged and matcher ignores nothing.
   - **reload picks up new patterns**: construct `VaultIgnore`, assert a path is not ignored, write a new pattern into `.semanticignore`, call `reload()`, assert the path is now ignored.

10. **Coverage:** `tests/test_ignore.py` must drive `src/semantic_search/ignore.py` to ≥ 80% statement coverage (all branches above are exercised, so this is satisfied by the listed tests).

## Constraints

[Copied from spec — the executing agent has no memory of the spec.]

- Pattern matching MUST follow gitignore semantics via the `pathspec` library with `gitwildmatch` syntax.
- Paths MUST be matched **relative to the vault root**, using POSIX-style forward slashes.
- `pathspec` MUST be added to `pyproject.toml` runtime dependencies (not dev-only).
- File size cap: 1 MiB when reading `.semanticignore`; on overflow log ERROR and treat as accept-all.
- `.semanticignore` unreadable → log ERROR, treat vault filter as "accept all".
- Malformed pattern line → log ERROR naming line number, skip that line, compile the rest.
- Must satisfy project DoD: docstrings on public functions/classes, full type hints, no `print`, `make precommit` clean (ruff + mypy strict + pytest).
- Do NOT wire this module into the indexer or watcher in this prompt — that is prompts 2 and 3.
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass.

## Verification

```bash
cd /workspace && uv sync
cd /workspace && grep -n '"pathspec"' pyproject.toml      # must be inside runtime dependencies
cd /workspace && uv run pytest tests/test_ignore.py -v    # all pass, exit 0
cd /workspace && uv run pytest -q                         # full suite still green
cd /workspace && make precommit                           # final: ruff + mypy strict + pytest, exit 0
```
