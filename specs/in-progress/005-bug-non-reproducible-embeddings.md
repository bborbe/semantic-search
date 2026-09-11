---
status: approved
approved: "2026-09-11T19:42:53Z"
branch: dark-factory/bug-non-reproducible-embeddings
---

## Summary

- A document's embedding changes between process restarts even when the document has not changed
- The tag block fed into the embedding is assembled from an unordered collection, and that collection's iteration order differs in every process
- The tag block is embedded at double weight, so the variation is large enough to reorder search results
- Any index rebuild therefore produces different rankings than the previous build, from identical content
- The fix makes the tag block's order deterministic while preserving the order the author wrote

## Problem

Semantic search results are not reproducible across restarts. Two servers running identical code over the same roots rank the same query differently, and the difference is large enough to swap two documents in and out of the top 20. Because the index is rebuilt on cache miss, on compaction, and on `/reindex`, the ranking a client sees silently changes over the life of the service. This also makes any frozen-baseline acceptance test unachievable: the five pre-consolidation daemons fail their own captured baseline on 12 of 70 query/port pairs, because the baseline was itself produced by an affected process.

## Reproduction

Self-contained — no dependency on mutable repo or vault content. Run from the repo root:

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

Observed on 2026-09-11 at `e9ee09c` (dirty tree), three seeds, three different texts — the second column is the tag block extracted from the prepared text:

```
seed=1  fa8307e8fdc2b1ea  epsilon beta delta gamma alpha
seed=2  3dba4767f1ceb1f4  beta delta gamma alpha epsilon
seed=3  8ce7cb2105da8028  gamma beta delta epsilon alpha
```

The same five tags appear in three different orders, producing three different prepared-text hashes.

**Control** — rewrite the *same* file with no tags, keeping the `fixture.md` name (the prepared text embeds the filename stem three times, so the name must not change or the hashes are not comparable):

```bash
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
done
```

```
seed=1  503e924daafabde1
seed=2  503e924daafabde1
seed=3  503e924daafabde1
```

One identical hash under every seed. That control is the point of the fixture: it isolates the tag block as the sole cause, and it is why this fixture carries five tags rather than two — with a two-element set, all three seeds agree by chance one time in four, so a revert check against it would flake.

End-to-end confirmation, from the same investigation: two servers built over identical roots, differing only in the breadth of the index behind them, returned different orderings on 12 of 14 fixture queries, with per-document score deltas up to `6.5e-4` on an unchanged file (`0.509065` vs `0.509712`). Both servers were individually deterministic across repeated identical queries, so the divergence was between processes, not between calls.

These hashes depend only on the fixture content and the fixed seed, so a correct run reproduces them exactly on any machine.

## Expected vs Actual

**Expected.** `_prepare_text_for_embedding` is a pure function of `(file_path, content)`. Two processes given the same file produce the same text, therefore the same embedding, therefore the same stored vector and the same ranking.

**Actual.** The text depends on the process's hash seed, so the stored vector for an unchanged file differs per process, and rankings drift across restarts.

## Why this is a bug

`_prepare_text_for_embedding` documents its own contract as a weighted concatenation of filename, metadata title, tags, first heading, and body. Every component except the tags is derived in a deterministic order. The tags are not: they are collected into a `set` and converted with `list()`, and CPython randomises string hashing per process unless `PYTHONHASHSEED` is fixed. A documented-pure function is therefore not pure.

## Goal

`_prepare_text_for_embedding` returns byte-identical text for identical inputs regardless of which process calls it, and the tag block preserves the order in which tags were authored.

## Non-goals

- Changing component weights, the 500-word body truncation, or any other part of the embedding strategy
- Changing which components are lowercased or deduplicated — today's semantics are preserved exactly; only the *ordering* source changes
- Changing the index cache key, currently derived from the root set alone and therefore shared between any two servers with the same roots
- Adding `PYTHONHASHSEED` to any plist, wrapper script, or environment file — pinning the seed would mask the defect rather than fix it
- Invalidating or rebuilding existing index caches. **A running server keeps its pre-fix vectors** until a rebuild is triggered by a cache miss, a compaction, a file change, or `/reindex`; the fix changes what future builds produce, not what a live process already holds.

## Acceptance Criteria

- [ ] The tag block is assembled in a deterministic order — evidence: the reproduction above, run against the fixture it creates, prints one identical sha256 for all three seeds.
- [ ] Authored order is preserved, not alphabetised — evidence: the same run's `repr` column shows `alpha` before `beta` before `gamma` before `delta` before `epsilon` under every seed.
- [ ] Today's lowercasing and deduplication semantics are unchanged — evidence: for a fixture with frontmatter `tags: [Foo, foo]` and body `#FOO`, the tag block contains `foo` exactly once; and for a fixture with `tags: [a]` and `aliases: [B]`, the emitted tag text is `a B` — the alias keeps its original case and sits after the tags, exactly as today.
- [ ] Untagged files are unaffected — evidence: the control above prints `503e924daafabde1` under all three seeds, and the pre-fix revision (obtained by `git stash`-ing the fix and re-running the control command) prints that same hash.
- [ ] The regression test crosses the process boundary rather than asserting on source text — evidence: `grep -o PYTHONHASHSEED tests/test_*.py | wc -l` returns ≥ 3, and `uv run pytest -s <new test file>` shows the distinct seed values it ran under. The `-s` matters: pytest captures stdout for passing tests, so the plain `make test` run does not surface it.
- [ ] The regression test genuinely spawns processes — evidence: replacing the subprocess spawn with an in-process call makes the test exit non-zero, because the test asserts the runs came from distinct processes (distinct PIDs, or distinct *effective* hash seeds rather than the seeds they were told) — something three in-process calls cannot produce.
- [ ] The regression test detects the original defect — evidence: reverting the fix and re-running the new test makes it exit non-zero.
- [ ] No test regressions — evidence: `make precommit` exits 0.

## Verification

### Container-executable (runs inside the YOLO container at prompt time)

- `make precommit` — lint, typecheck, and the full test suite pass
- `make test` — the new determinism test is collected and passes
- `grep -o PYTHONHASHSEED tests/test_*.py | wc -l` — returns ≥ 3

### Operator-executable (runs on the host after PR merge, spec verification ladder)

- Re-run the reproduction from `## Reproduction` against the fixed revision. All three seeds must print one identical hash. This is the original-reproduction replay that `kind: bug` requires; passing tests alone do not satisfy it.
- Build two indexes over the same roots in two separate processes without pinning `PYTHONHASHSEED`, then query both and diff:

```bash
for i in 1 2; do
  SEMANTIC_SCOPE_MAP=scopes.yaml uv run semantic-search-http --host 127.0.0.1 --port 835$i &
done
# wait for both /health to report "ready": true, then:
diff <(curl -fsS 'http://127.0.0.1:8351/search?q=Boss%20vault&top_k=20&scope=personal' | jq -r '.results[].path') \
     <(curl -fsS 'http://127.0.0.1:8352/search?q=Boss%20vault&top_k=20&scope=personal' | jq -r '.results[].path')
```

The diff must be empty. The cache must be cold before **both** builds: `index_dir` is `~/Library/Caches/semantic-search/<content-hash>/`, keyed on the root set alone, so a warm cache makes the second server load the first's index and the check proves nothing. Delete that directory before starting each server. Do **not** instead give the two servers different root sets — different corpora legitimately produce different rankings, which would make the diff non-empty for a reason unrelated to this bug.

## Desired Behavior

1. `_prepare_text_for_embedding` is a pure function of `(file_path, content)`: two processes with different hash seeds produce byte-identical output for the same inputs.
2. The tag block's tag-union portion is ordered by first appearance — frontmatter `tags` first, then inline body tags — with later duplicates dropped rather than reordered.
3. Component order and normalization are unchanged from today: the tag union is lowercased and deduplicated, then aliases are appended after it, with their original case, not deduplicated against the tags.
4. A test exists that fails against the pre-fix implementation and passes against the fixed one, and it does so by spawning subprocesses with distinct `PYTHONHASHSEED` values rather than by asserting on source text; replacing that spawn with an in-process call makes the test fail.

## Constraints

- Python only; no new dependencies.
- `_prepare_text_for_embedding` keeps its current signature and stays a method on `VaultIndexer`.
- Docstrings on all functions, type hints on all signatures, no `print()` in library code, no broad `except Exception` — per `docs/dod.md`.
- Existing tests must still pass unchanged.
- Do NOT commit — dark-factory handles git.
- The embedding model is already deterministic across processes; do not add seeding, thread pinning, or model-configuration changes. Verified 2026-09-11: three separate processes produced an identical embedding hash for a fixed string.

## Failure Modes

| Trigger | Expected behavior | Recovery |
|---|---|---|
| A file's frontmatter `tags` is a bare string rather than a list | Treated as a single tag, as today | None — existing branch preserved |
| A file has inline tags but no frontmatter tags | Inline tags form the tag block in body order | None |
| Frontmatter YAML fails to parse | The existing warning is logged; the block falls back to inline tags only | None — existing behavior |
| The test runs where a spawned subprocess cannot take an overridden `PYTHONHASHSEED` | The test fails loudly rather than silently passing | Operator runs `make test` on the host and confirms it exits 0 |
| A long-running server still holds pre-fix vectors after the fix ships | Results remain non-reproducible until a rebuild | Trigger `/reindex`. Compaction alone is not a reliable recovery — it only fires above a 20% tombstone ratio. Expected and bounded. |

## Suggested Decomposition

| # | Prompt focus | Covers DBs | Covers ACs | Depends on |
|---|---|---|---|---|
| 1 | Deterministic, order-preserving tag block in `_prepare_text_for_embedding` | 1, 2, 3 | 1, 2, 3, 4 | — |
| 2 | Cross-process determinism regression test | 4 | 5, 6, 7, 8 | prompt 1 (the test asserts the fixed behavior) |

Rationale: prompt 1 is the behavior change and carries the ordering and dedup assertions. Prompt 2 adds the subprocess-based test that crosses the process boundary, which can only assert the fixed contract once prompt 1 has landed.

## Related

- `specs/in-progress/004-per-vault-scoping.md` — the consolidation work that surfaced this. Its acceptance replay compares against a frozen baseline captured *before* this fix, so that baseline carries pre-fix drift; the fix removes future drift but does not make the old oracle match. That spec's replay may need re-capture, which is its own decision and is out of scope here.
- `docs/design/weighted-embedding-strategy.md` — the weighting contract this fix restores.
