---
status: completed
spec: [005-bug-non-reproducible-embeddings]
summary: Made the embedding tag block deterministic and authored-ordered by replacing the set-based tag union with a first-appearance dedup loop, added exact tag-line tests, and recorded the fix in the changelog
execution_id: semantic-search-exec-030-spec-005-deterministic-tag-block
dark-factory-version: dev
created: "2026-09-11T19:50:17Z"
queued: "2026-09-11T20:09:00Z"
started: "2026-09-11T20:09:41Z"
completed: "2026-09-11T20:12:02Z"
---

# Make the embedding tag block deterministic and authored-order

<summary>
- A document's embedding no longer changes when the process reading it restarts — identical content produces an identical vector in every process
- The tags that feed the embedding are emitted in the order the author wrote them: frontmatter tags first, then tags found in the body
- A tag that appears more than once is emitted once, at its first appearance
- Lowercasing, deduplication, and the position of frontmatter aliases are unchanged from today
- Documents without tags produce byte-identical text to what the current code produces
- Index rebuilds now reproduce the previous build's vectors, so search rankings stop drifting across restarts
- The change is confined to how the tag block is ordered — no weight, truncation, or cache behaviour changes
- Tests pin the exact tag text the embedder receives, including the branches around it
- No new dependency and no signature change
- A changelog entry records the fix
</summary>

<objective>
Make `_prepare_text_for_embedding` a pure function of `(file_path, content)`, so two processes reading the same unchanged file produce the same text, the same vector, and the same ranking. Today the tag block is assembled from a `set`, and CPython randomises string hashing per process, so the identical file is embedded differently in every process and the ranking a client sees silently changes on every rebuild.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.13+, `uv`, `src/` layout, strict mypy, never code directly) and `docs/dod.md` for the Definition of Done.

Read these files before making changes:

- `src/semantic_search/indexer.py` — the change is inside `_prepare_text_for_embedding(self, file_path: Path, content: str) -> str`. The defect is the tag-union assembly:

  ```python
        # Merge and dedupe (lowercase)
        all_tags = {t.lower() for t in tags_aliases} | {t.lower() for t in inline_tags}
        tags_aliases = list(all_tags)
  ```

  Everything else in the method — filename 3x, `title` 3x, tag block 2x, first H1 2x, first 500 body words 1x, joined with `"\n"` — is already deterministic and stays byte-for-byte as it is. `_extract_inline_tags(content)` returns inline tags in document order via `INLINE_TAG_PATTERN.findall`.
- `docs/design/weighted-embedding-strategy.md` § "Metadata Extraction" — the authored-order contract this fix restores, verbatim: "the frontmatter `tags` unioned with the inline body tags, lowercased, deduplicated with the first occurrence winning and ordered by first appearance — then the frontmatter `aliases` appended after them, keeping their original case." It already states this; do not edit it.
- `tests/test_indexer.py` — class `TestVaultIndexerInlineTags` is where the tag-block tests live and is the style anchor for the new ones: class-based `Test*` classes, plain `assert`, `tmp_path`, and `patch("semantic_search.indexer.SentenceTransformer")` when a real `VaultIndexer` is constructed. Read the existing tests first — they already cover inline extraction, bare-string tags, and case-insensitive deduplication, so the new assertions must add coverage rather than restate it.
- `CHANGELOG.md` — the `## Unreleased` section. Every released section is frozen.

**Verified facts you can rely on (re-measured on this tree):**

- The container cannot load `all-MiniLM-L6-v2`, so a test that constructs a real `VaultIndexer` must patch `SentenceTransformer` as the existing tests do. When only the prepared text is needed, the cheaper route is `VaultIndexer.__new__(VaultIndexer)` — it skips `__init__` (which is what loads the model) and `_prepare_text_for_embedding` needs no instance state. The reproduction below uses that route.
- With no frontmatter `title`, the tag block is the 4th line of the prepared text: `prepared.split("\n")[3]`.
- Today, three processes with `PYTHONHASHSEED=1|2|3` produce three different digests for one unchanged tagged file (`fa8307e8fdc2b1ea`, `3dba4767f1ceb1f4`, `8ce7cb2105da8028`) and three different tag orders (`epsilon beta delta gamma alpha`, `beta delta gamma alpha epsilon`, `gamma beta delta epsilon alpha`).
- The untagged control fixture below digests to `503e924daafabde1` under every seed today, and must still after your change.

Reproduction (run from the repo root; the fixture's name matters — the filename stem is embedded three times, so the name must stay `fixture.md`):

```bash
tmp="$(mktemp -d)"
printf -- '---\ntags: [alpha, beta, gamma, delta, epsilon]\n---\n\n# Fixture\n\nbody text\n' > "$tmp/fixture.md"
for seed in 1 2 3; do
  PYTHONHASHSEED=$seed uv run python -c "
import hashlib, sys
from pathlib import Path
from semantic_search.indexer import VaultIndexer
t = Path(sys.argv[1])
ix = VaultIndexer.__new__(VaultIndexer)
prep = VaultIndexer._prepare_text_for_embedding(ix, t, t.read_text())
print(hashlib.sha256(prep.encode()).hexdigest()[:16], repr(prep.split(chr(10))[3][:60]))
" "$tmp/fixture.md"
done
```

After the fix this prints one identical digest three times, with the tag line `alpha beta gamma delta epsilon` in every run.

Pattern guides (in-container paths — read the one you need before writing):

- `/home/node/.claude/plugins/marketplaces/coding/docs/changelog-guide.md` — the `- fix: ...` bullet convention and the frozen-preamble rule.
- `/home/node/.claude/plugins/marketplaces/coding/docs/test-pyramid-triggers.md` — the unit-test triggers that apply to this change.
</context>

<requirements>

1. **Replace the tag-union assembly** in `_prepare_text_for_embedding` (`src/semantic_search/indexer.py`). The contract:
   - The union of frontmatter `tags` and inline body tags is lowercased, deduplicated with the **first occurrence winning**, and ordered by **first appearance**: frontmatter `tags` in authored order first, then inline tags in document order (`_extract_inline_tags` already returns them in body order).
   - A later duplicate is dropped, never moved.
   - Frontmatter `aliases` are appended after that union, unchanged: original case, not lowercased, not deduplicated against the tags — exactly as today.
   - Alphabetising is not acceptable. `sorted(...)` and `list(set(...))` both fail the authored-order requirement, and `sorted` additionally changes which tag the ranking favours.
   - The frontmatter-tags branch above it (`isinstance(tags, list)` → extend, else append as a single tag) is unchanged, as is the aliases branch below it.

   Old:

   ```python
        # Merge and dedupe (lowercase)
        all_tags = {t.lower() for t in tags_aliases} | {t.lower() for t in inline_tags}
        tags_aliases = list(all_tags)
   ```

   Verified replacement — adopt it verbatim or use any mechanism that satisfies the contract above:

   ```python
        # Merge and dedupe (lowercase), preserving first-appearance order:
        # frontmatter tags first, then inline body tags.
        seen: set[str] = set()
        ordered_tags: list[str] = []
        for tag in [*tags_aliases, *inline_tags]:
            lowered = tag.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            ordered_tags.append(lowered)
        tags_aliases = ordered_tags
   ```

   Note the `seen` set is used for membership only — it is never iterated, so it cannot reintroduce the nondeterminism. Iterating it would reintroduce exactly the bug being fixed.

2. **Change nothing else in the method.** The signature `_prepare_text_for_embedding(self, file_path: Path, content: str) -> str` stays, the method stays on `VaultIndexer`, and every other component keeps its current content and weight: filename (stem, `-`/`_` → spaces) 3x, `title` 3x, tag block 2x, first `# ` heading 2x, first 500 body words 1x, joined with `"\n"`. Update the docstring's tag line so it states the authored-order rule; change no other documented behaviour. No change to component weights, the 500-word truncation, the lowercasing rule, the index cache key, or the embedding call.

3. **Keep the surrounding branches exactly as they are.** These are the spec's failure modes and each keeps its current behaviour:
   - frontmatter `tags` as a bare string → treated as one tag;
   - inline tags with no frontmatter tags → the tag block is the inline tags in body order;
   - frontmatter YAML that fails to parse → the existing warning is logged and the block falls back to inline tags only. Do not restructure that `try`/`except`, do not add a new broad catch, and do not remove the existing clauses.

4. **Add unit tests to `tests/test_indexer.py`** in the file's existing class-based style. Extend `TestVaultIndexerInlineTags` or add a sibling class in the same file. Assert the tag line **exactly** (`prepared.split("\n")[3]` for a fixture with no frontmatter `title`), never with a substring check. Each fixture below was measured against the fixed implementation; the expected value is the whole tag line:
   - authored order: `tags: [alpha, beta, gamma, delta, epsilon]` → `alpha beta gamma delta epsilon`. This is the alphabetisation guard: a `sorted` implementation yields `alpha beta delta epsilon gamma` and must fail here;
   - lowercase dedup unchanged: `tags: [Foo, foo]` with body `#FOO` → `foo` (exactly once);
   - aliases unchanged: `tags: [a]` with `aliases: [B]` → `a B` (the alias keeps its case and sits after the tags);
   - cross-source dedup: `tags: [alpha, beta]` with body `#beta #gamma` → `alpha beta gamma` (the inline duplicate is dropped, not moved);
   - bare-string tag: `tags: single-tag` with body `#inline-tag` → `single-tag inline-tag`;
   - inline-only: no frontmatter `tags`, body `#alpha #beta` → `alpha beta`;
   - malformed frontmatter: `---\ntags: [unclosed\n---\nbody #alpha #beta\n` → the tag line is `alpha beta` and no exception escapes (the warning is the existing one);
   - untagged file: `---\n---\n\n# Fixture\n\nbody text\n` written as `fixture.md` → the full prepared text is exactly `"fixture\nfixture\nfixture\nFixture\nFixture\n# Fixture body text"`. The heading appears twice because the body component still carries the heading line — that is today's behaviour and must not change.

5. **Add a CHANGELOG entry** — one `- fix: ...` bullet under `## Unreleased` in `CHANGELOG.md`, naming the user-visible effect: an unchanged file no longer gets a different embedding per process, so rankings are reproducible across restarts and index rebuilds. Keep `## Unreleased` where it is and leave every released section untouched.

6. **Self-check before finishing:** re-run `<verification>` and confirm every step reports PASS; then walk the spec's acceptance criteria (deterministic tag block, authored order, unchanged lowercasing/dedup/alias semantics, untagged files unaffected) against the change and state the outcome in your report.

</requirements>

<constraints>
- Python only; no new dependencies.
- `_prepare_text_for_embedding` keeps its current signature and stays a method on `VaultIndexer`.
- Docstrings on all functions, type hints on all signatures, no `print()` in library code, no new broad `except Exception` — per `docs/dod.md`.
- Existing tests must still pass unchanged. Add tests; do not edit or delete existing assertions in `tests/test_indexer.py`.
- Do NOT change component weights, the 500-word body truncation, which components are lowercased or deduplicated, or the index cache key.
- Do NOT add `PYTHONHASHSEED` to any plist, wrapper script, environment file, or the service's process environment. Pinning the seed would mask the defect instead of fixing it — the fix is the ordering, not the seed. (Overriding the seed for the lifetime of a one-off child process — the reproduction in `<verification>` below, and the cross-process regression test in the next prompt — is the only legitimate use.)
- Do NOT invalidate, delete, migrate, or rebuild existing index caches, and do not add a rebuild trigger. A running server keeps its pre-fix vectors until a rebuild is triggered by a cache miss, a compaction, a file change, or `/reindex`; this change governs what future builds produce and nothing else. Reindexing a live server is an operator step outside this prompt.
- Do NOT edit `docs/design/weighted-embedding-strategy.md` (it already states the contract), `README.md`, `CLAUDE.md`, `scopes.yaml`, or anything under `src/` other than the tag-block assembly in `indexer.py`.
- The embedding model is already deterministic across processes (three separate processes produced an identical embedding hash for a fixed string, verified 2026-09-11). Do not add seeding, thread pinning, or model-configuration changes.
- Do NOT commit — dark-factory handles git.
- Keep `make precommit` green: `ruff format`/`ruff check` clean at the repo's 100-column limit, `mypy src` strict, full suite green.
</constraints>

<verification>
Run `make precommit` — must pass (sync + format + test + lint + typecheck).

Then confirm each of these. Every step shells out to `uv`; a `127` from any step means the command never ran, which is a verification failure — never read it as a pass.

```bash
# Guard: if `uv` is missing, every step below reports 127 and nothing was actually verified
command -v uv >/dev/null || echo "FAIL: uv is not in PATH — every step below would report 127"

# 1. Reproduction: three seeds, one identical digest, authored tag order
tmp="$(mktemp -d)"
printf -- '---\ntags: [alpha, beta, gamma, delta, epsilon]\n---\n\n# Fixture\n\nbody text\n' > "$tmp/fixture.md"
for seed in 1 2 3; do
  PYTHONHASHSEED=$seed uv run python -c "
import hashlib, sys
from pathlib import Path
from semantic_search.indexer import VaultIndexer
t = Path(sys.argv[1])
ix = VaultIndexer.__new__(VaultIndexer)
prep = VaultIndexer._prepare_text_for_embedding(ix, t, t.read_text())
print(hashlib.sha256(prep.encode()).hexdigest()[:16], repr(prep.split(chr(10))[3][:60]))
" "$tmp/fixture.md"
done > "$tmp/tagged.txt"
cat "$tmp/tagged.txt"
runs=$(wc -l < "$tmp/tagged.txt" | tr -d ' ')
[ "$runs" = "3" ] && echo "PASS: three runs produced output" || echo "FAIL: $runs of 3 runs produced output (a seed crashed — nothing was verified)"
distinct_hashes=$(awk '{print $1}' "$tmp/tagged.txt" | sort -u | wc -l | tr -d ' ')
[ "$distinct_hashes" = "1" ] && echo "PASS: one identical digest across three seeds" || echo "FAIL: $distinct_hashes distinct digests across three seeds (expected 1)"
ordered=$(grep -c "alpha beta gamma delta epsilon" "$tmp/tagged.txt")
[ "$ordered" = "3" ] && echo "PASS: authored order under all three seeds" || echo "FAIL: authored order in $ordered of 3 runs (expected 3)"

# 2. Control: the untagged fixture, same name, unchanged digest under every seed
printf -- '---\n---\n\n# Fixture\n\nbody text\n' > "$tmp/fixture.md"
for seed in 1 2 3; do
  PYTHONHASHSEED=$seed uv run python -c "
import hashlib, sys
from pathlib import Path
from semantic_search.indexer import VaultIndexer
t = Path(sys.argv[1])
ix = VaultIndexer.__new__(VaultIndexer)
print(hashlib.sha256(VaultIndexer._prepare_text_for_embedding(ix, t, t.read_text()).encode()).hexdigest()[:16])
" "$tmp/fixture.md"
done > "$tmp/control.txt"
cat "$tmp/control.txt"
control_distinct=$(sort -u "$tmp/control.txt" | wc -l | tr -d ' ')
if [ "$control_distinct" = "1" ] && grep -q '^503e924daafabde1$' "$tmp/control.txt"; then
  echo "PASS: untagged control digests to 503e924daafabde1 under every seed"
else
  echo "FAIL: untagged control did not print one identical 503e924daafabde1 (untagged files must be byte-identical to the pre-fix output)"
fi

# 3. The tag-block tests exist and pass
uv run pytest tests/test_indexer.py -q

# 4. The whole suite
make test
```
</verification>
