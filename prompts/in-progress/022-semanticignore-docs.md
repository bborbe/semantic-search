---
status: approved
spec: [003-semanticignore-support]
created: "2026-06-17T00:00:00Z"
queued: "2026-06-17T10:26:28Z"
branch: dark-factory/semanticignore-support
---

## Summary

- Documents the `.semanticignore` feature for end users in the README
- Records the feature in the changelog under the Unreleased section
- Runs the final full validation sweep to confirm the whole feature lands clean

## Objective

Document the `.semanticignore` feature (README + CHANGELOG) and run the final `make precommit` sweep confirming all of prompts 1-3 are integrated and green.

## Context

Read these files before making changes:

- `/workspace/CLAUDE.md` — project conventions and changelog rules
- `/workspace/README.md` — find a logical place for a new section (e.g. near indexing/configuration content). Confirm `.semanticignore` is not yet documented (`grep -n '.semanticignore' README.md` currently returns nothing).
- `/workspace/CHANGELOG.md` — top entries are version-headed (`## v0.17.0`, etc.). There is no `## Unreleased` section yet; you will add one at the top, directly under the preamble and above the first version heading.
- `/workspace/src/semantic_search/ignore.py` and `indexer.py` — confirm prompts 1-3 are in place. If `ignore.py` is missing or `VaultIndexer` lacks `self._ignores`, STOP and report `Status: failed` with `"ignore feature implementation not yet deployed (prompts 1-3)"`.
- `/home/node/.claude/plugins/marketplaces/coding/docs/changelog-guide.md` — entry format and style
- `/home/node/.claude/plugins/marketplaces/coding/docs/readme-guide.md` — README conventions

## Requirements

1. **README section (AC12).** Add one new section to `/workspace/README.md` documenting `.semanticignore`. It MUST cover all four points:
   - **Location** — a `.semanticignore` file at the root of each vault; rules are per vault root and matched relative to that root.
   - **Syntax** — gitignore-style patterns (point readers to gitignore semantics: negation with `!`, `**`, directory patterns ending `/`, leading-`/` anchoring). Mention it is powered by the `pathspec` library's `gitwildmatch` syntax.
   - **Behaviour summary** — matching paths are excluded from indexing across full rebuild, single-file add/update, and watcher events; the `.semanticignore` file itself is never indexed; a missing file means "index everything".
   - **Runtime reload note** — editing `.semanticignore` while the watcher is running reloads that vault's rules; subsequent file events use the new patterns.
   - Include a short fenced example block of a `.semanticignore` file (e.g. `archive/`, `**/draft.md`, `!archive/keep.md`).
   - After this, `grep -n '.semanticignore' README.md` MUST return ≥ 1 line.

2. **CHANGELOG entry (AC13).** Add an `## Unreleased` section at the TOP of `/workspace/CHANGELOG.md` (after the preamble lines, before `## v0.17.0`). Under it add a bullet using the required `feat:` prefix that mentions `.semanticignore`. Example:
   ```
   ## Unreleased

   - feat: support per-vault `.semanticignore` files (gitignore-style patterns) to exclude paths from indexing across rebuild, single-file add/update, and watcher events; rules reload at runtime when the file changes
   ```
   After this, `grep -nA2 '## Unreleased' CHANGELOG.md` MUST show a bullet mentioning `.semanticignore`.

3. **Do NOT** modify Python source or tests in this prompt — this is docs-only plus the final validation sweep. If the validation sweep surfaces a code defect, report it in `## Improvements` (category PROMPT, naming the prompt) and set `Status: partial` rather than patching code here.

## Constraints

[Copied from spec — the executing agent has no memory of the spec.]

- README must document the feature in one section: location, syntax pointer to gitignore, behaviour summary, runtime reload note.
- CHANGELOG must have an entry under `## Unreleased` describing the feature, mentioning `.semanticignore`, with a valid changelog prefix (`feat:`).
- `make precommit` must exit 0 in the repo root after all changes.
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass.

## Verification

```bash
cd /workspace && grep -n '.semanticignore' README.md          # ≥ 1 line
cd /workspace && grep -nA2 '## Unreleased' CHANGELOG.md        # bullet mentioning .semanticignore
cd /workspace && uv run pytest tests/test_ignore.py tests/test_indexer.py tests/test_watcher.py -v
cd /workspace && make precommit                                # final: must exit 0
```
