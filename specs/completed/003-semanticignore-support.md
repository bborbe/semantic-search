---
status: completed
tags:
    - dark-factory
    - spec
approved: "2026-06-17T09:58:22Z"
generating: "2026-06-17T10:08:09Z"
prompted: "2026-06-17T10:16:48Z"
verifying: "2026-06-17T10:47:11Z"
completed: "2026-06-17T11:28:15Z"
branch: dark-factory/semanticignore-support
---

## Summary

- Each vault root may contain a `.semanticignore` file with gitignore-style patterns; matching paths are excluded from indexing.
- Patterns are loaded per vault root and matched relative to that root, with correct gitignore semantics (negation, `**`, anchoring, directory matches).
- Exclusion applies in three places: full rebuild, single-file add/update, and file-watcher events.
- The `.semanticignore` file itself is always excluded from the index. Missing `.semanticignore` means "index everything" — no behavior change for existing vaults.
- Changes to `.semanticignore` at runtime cause patterns for that vault to reload; subsequent events use the new rules.

## Problem

Users who run semantic-search over an Obsidian vault have no way to keep operational, generated, or sensitive markdown out of the index. Today every `*.md` under a vault root is embedded and stored, regardless of whether it represents notes the user wants to search. This wastes embedding compute, pollutes search results with archive/scratch/template noise, and offers no escape hatch short of moving files out of the vault. Git users already maintain `.gitignore`; a vault-local `.semanticignore` with the same syntax is the least-surprising way to filter.

## Goal

After this work, placing a `.semanticignore` file at any vault root causes the indexer to skip every path matching its patterns — during the initial full rebuild, during incremental add/update of single files, and during file-watch events triggered while the service is running. Vaults without a `.semanticignore` behave exactly as before.

## Non-goals

- Do NOT support global / per-user ignore files outside the vault (no `~/.semanticignore_global`). One file, at each vault root.
- Do NOT implement a custom gitignore parser — use a maintained library (`pathspec`).
- Do NOT add a runtime opt-out flag to disable `.semanticignore` processing — invariant; if a future consumer demands variation, that's a separate spec.
- Do NOT support nested `.semanticignore` files in subdirectories of a vault — only at vault roots. If multi-level support is needed later, that's a separate spec.
- Do NOT change the on-disk index format, search API, or HTTP/CLI surface. Excluded files simply do not get embedded.
- Do NOT retroactively remove already-indexed entries that newly match an ignore pattern in this spec — purge-on-rule-change is out of scope. (Next full `rebuild_index` call drops them, which is acceptable.)

## Desired Behavior

1. On indexer startup, for each configured vault root, the indexer loads `<vault_root>/.semanticignore` if present and compiles its patterns. If the file is absent, the filter for that vault accepts all paths.
2. During `rebuild_index`, every `*.md` path discovered under a vault root is checked against that vault's filter; non-matching paths are embedded as today, matching paths are skipped and not added to the index.
3. During single-file add/update (the incremental path used by file-watch events), the path is checked against the filter for the vault root it belongs to; matching paths are skipped (not embedded, not added) and any previously-indexed entry for that path is removed.
4. During file-watch events (create / modify / move-to / move-from / delete), each event's path is filtered before the indexer processes it; ignored paths produce no embedding work.
5. The `.semanticignore` file itself is always excluded from indexing, regardless of what its patterns say.
6. When the `.semanticignore` file at a vault root is itself created, modified, or deleted at runtime, the in-memory filter for that vault is reloaded; subsequent file events use the new rules. Already-indexed files are not retroactively re-evaluated until the next `rebuild_index`.
7. Each skipped path is logged at DEBUG; a one-line INFO summary per `rebuild_index` reports total skipped count per vault.

## Constraints

- Must NOT change the existing `Indexer` public API (constructor signature may gain one optional parameter, but existing callers without ignore support must keep working).
- Must NOT change index file format or on-disk metadata schema.
- Must preserve the existing hardcoded skip for paths containing `.semantic-search` (the index storage dir) — that exclusion is independent of `.semanticignore`.
- Pattern matching must follow gitignore semantics as implemented by the `pathspec` library with the `gitwildmatch` syntax (negation via `!`, double-star `**`, leading `/` anchoring, trailing `/` for directories).
- Paths are matched **relative to the vault root**, using POSIX-style forward slashes (per `pathspec` convention), even on case-insensitive filesystems where the OS would otherwise normalise.
- Must satisfy project DoD (`docs/dod.md`): docstrings on public functions/classes, full type hints, no `print` (use `logging`), `make precommit` clean (ruff + mypy strict + pytest).
- `pathspec` must be added to `pyproject.toml` runtime dependencies; it has a `py.typed` marker and is mypy-compatible (per python-architecture-patterns.md guidance).

## Failure Modes

| Trigger | Expected behavior | Recovery | Detection |
|---------|-------------------|----------|-----------|
| `.semanticignore` is unreadable (permission denied, I/O error) | Log ERROR with vault path and OS error; treat vault's filter as "accept all" for this session | Operator fixes permissions; next reload of `.semanticignore` (its own edit) picks up the patterns | ERROR log line containing the vault path |
| `.semanticignore` contains a syntactically malformed pattern that `pathspec` rejects | Log ERROR naming the vault path and the offending line number; skip only that line, compile the rest | Operator edits the file; the watch reload picks up the corrected file | ERROR log line citing line number |
| `.semanticignore` is created, modified, or deleted at runtime | The filter for that vault is rebuilt (deletion → accept-all); INFO log records the reload | None needed — automatic | INFO log line `reloaded .semanticignore for <vault>` |
| File event arrives between deletion of `.semanticignore` and the reload completing | Filter swap is atomic under a per-vault lock; concurrent readers observe either the old fully-compiled filter or the new one — never a partially built one | None needed — outcome is one of two consistent states | No symptom; covered by concurrency invariant in tests |
| Vault root itself is deleted or unreadable | Existing indexer behavior — outside this spec | n/a | n/a |
| Pattern matches every file in a vault (`*`) | Vault contributes zero documents to the index; INFO summary reports skipped count = total `.md` count | Operator narrows the pattern | INFO log line shows non-zero skipped count |

## Security / Abuse Cases

- `.semanticignore` is read from disk; treat it as untrusted text. Cap file size at 1 MiB to prevent a runaway file from exhausting memory; on overflow, log ERROR and treat as accept-all.
- Patterns are compiled by `pathspec` — no shell-out, no `eval`. The library is the trust boundary.
- Symlink behaviour follows the existing indexer behaviour (no change). A `.semanticignore` that is a symlink is read normally; the spec does not introduce new symlink traversal.
- No user-controllable path is logged without being passed through `repr()` / `str()` of `Path` — no log injection via crafted filenames.

## Acceptance Criteria

- [ ] AC1 — `pathspec` appears in `pyproject.toml` runtime dependencies (not dev/test); evidence: `grep -n '^pathspec' pyproject.toml` returns a line in the runtime deps section.
- [ ] AC2 — A new ignore module exists with a class/function that accepts a vault root, loads `.semanticignore` if present, and exposes a "should this path be ignored" predicate; evidence: `pytest tests/test_ignore.py -v` exits 0 with passing unit tests covering all of: missing file, empty file, simple pattern, directory pattern, double-star, negation, anchored pattern, and the `.semanticignore` file itself being ignored.
- [ ] AC3 — In a vault containing `a.md`, `b.md`, `archive/old.md`, and a `.semanticignore` with `archive/`, running `rebuild_index` produces an index with exactly `a.md` and `b.md`; evidence: integration test in `tests/test_indexer.py` asserts `set(meta paths) == {a.md, b.md}` and exits 0.
- [ ] AC4 — In the same vault, calling the single-file add/update entry point with path `archive/old.md` is a no-op (no embedding, no index growth); evidence: integration test asserts `index.ntotal` is unchanged before/after the call.
- [ ] AC5 — A file-watcher `on_created` / `on_modified` event for an ignored path does not add it to the index; evidence: integration test fires a synthetic event and asserts the path is absent from `meta`.
- [ ] AC6 — Editing `.semanticignore` at runtime (adding a new pattern) causes the next file event for a path matching the new pattern to be ignored; evidence: integration test mutates the file, waits for reload (deterministic via direct call or a watcher event for the file), fires an event, asserts ignore.
- [ ] AC7 — The `.semanticignore` file itself never appears in the index after a rebuild, regardless of patterns; evidence: integration test rebuilds and asserts `.semanticignore` is not in `meta`.
- [ ] AC8 — Multiple vault roots each load their own `.semanticignore`; a pattern in vault A does not affect vault B; evidence: integration test with two vault roots and different patterns asserts cross-vault independence.
- [ ] AC9 — On `rebuild_index` completion, an INFO log record per vault reports skipped count in a stable, greppable form; evidence: `caplog` captures an INFO record whose `message` matches `r'rebuild_index skipped (\d+) files for vault .+'` and the captured vault path equals the vault root passed in.
- [ ] AC10 — A malformed pattern line (e.g. unmatched bracket that `pathspec` rejects) is logged at ERROR with the file path and line number, and the remaining lines still compile; evidence: unit test using `caplog` asserts an ERROR record naming the line number, and the predicate behaves correctly for unaffected patterns.
- [ ] AC11 — `make precommit` exits 0 in the repo root after all changes; evidence: exit code 0 from `make precommit`.
- [ ] AC12 — README.md documents the feature (one section: location, syntax pointer to gitignore, behaviour summary, runtime reload note); evidence: `grep -n '.semanticignore' README.md` returns ≥ 1 line.
- [ ] AC13 — CHANGELOG.md has an entry under `## Unreleased` describing the feature; evidence: `grep -nA2 '## Unreleased' CHANGELOG.md` shows a bullet mentioning `.semanticignore`.

Scenario coverage: NO new scenario. Unit + integration tests in `tests/test_ignore.py` and `tests/test_indexer.py` cover every observable behavior; there is no E2E surface (no Docker, no `gh`, no cluster) that integration tests cannot reach.

## Verification

Run from repo root (the container's CWD):

```
make precommit
pytest tests/test_ignore.py tests/test_indexer.py -v
```

Expected: `make precommit` exits 0; the two test files together show all ignore-related cases passing.

## Suggested Decomposition

| # | Prompt focus | Covers DBs | Covers ACs | Depends on |
|---|---|---|---|---|
| 1 | Add `pathspec` dep + new ignore module (loader + predicate) with full unit-test coverage; no indexer wiring yet | 1, 5 (predicate-level), 7 (filter behaviour) | AC1, AC2, AC10 | — |
| 2 | Wire the filter into `Indexer` via constructor injection; apply in `rebuild_index` and single-file add/update; integration tests with real vaults | 2, 3, 5, 7, 8 | AC3, AC4, AC7, AC8, AC9 | prompt 1 |
| 3 | Wire ignore into the watchdog event handler and add runtime reload of `.semanticignore`; integration tests with synthetic events | 4, 6 | AC5, AC6 | prompt 2 |
| 4 | Docs (README + CHANGELOG) and final `make precommit` sweep | — | AC11, AC12, AC13 | prompt 3 |

Rationale: layer 1 ships the pure module so the predicate is fully tested before any indexer change; layer 2 lands the synchronous code paths (`rebuild_index`, single-file add) which are easiest to test deterministically; layer 3 takes on the trickier watcher + reload concurrency only after the foundation is proven; layer 4 closes out docs in a small, low-risk PR.

## Do-Nothing Option

If we ship nothing, users must keep moving unwanted notes out of the vault or accept polluted search results and wasted embedding cost. This is a recurring papercut — every new archive/scratch/template directory the user creates is another silent regression in result quality. The cost of doing this work is bounded (a few hundred lines plus tests, one dependency); the cost of not doing it grows with vault size. Recommend: build it.

## Verification Result

**Verified:** 2026-06-17T11:10:59Z (HEAD 9a0e39e)
**Binary:** /Users/bborbe/Documents/workspaces/go/bin/dark-factory (v0.181.0)
**Scenario:** No scenario file (spec uses inline verification via unit+integration tests). Walked all 13 ACs with fresh test runs + grep evidence against worktree HEAD 9a0e39e.
**Evidence:**
- AC1: `pyproject.toml` L19 `pathspec` inside `dependencies = [...]` block (runtime, not dev)
- AC2: `pytest tests/test_ignore.py -v` → 17 passed including missing/empty/simple/directory/double-star/negation/anchored/self-ignored
- AC3,4,7,8,9: `pytest tests/test_indexer.py -v` → `TestVaultIgnoreIntegration::test_ac{3,4,7,8,9}_*` all PASSED (46 passed total)
- AC5,6: `pytest tests/test_watcher.py -v` → `TestVaultIgnoreGate::test_ignored_path_not_queued_on_{created,modified}` + `test_runtime_reload_on_semanticignore_modified` PASSED (26 passed total)
- AC10: `tests/test_ignore.py::TestVaultIgnoreMalformedPattern::test_malformed_pattern_logs_error_with_line_number` PASSED
- AC11: `make precommit` → 135 passed, ruff clean, mypy strict clean, exit 0
- AC12: `grep -n '.semanticignore' README.md` → 4 hits incl. section header at L231 "Excluding Files with `.semanticignore`"
- AC13: `grep -nA5 '## Unreleased' CHANGELOG.md` → 3 feat bullets at L13-15 mentioning `.semanticignore`
**Verdict:** PASS
