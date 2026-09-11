---
status: approved
spec: [005-bug-non-reproducible-embeddings]
created: "2026-09-11T19:50:17Z"
queued: "2026-09-11T20:09:00Z"
---

# Add a cross-process embedding determinism regression test

<summary>
- A new test proves the same unchanged file yields the same embedding input from three separate processes started with three different hash seeds
- It spawns real subprocesses, so an in-process shortcut cannot pass it — the runs must come from distinct process ids
- Each child reports a fingerprint of its own effective string-hash seed, so the test fails loudly when the seed override never took effect instead of passing vacuously
- It also pins that the tag block every child produced is in authored order
- An untagged file is probed as a control, so a failure points at the tag block rather than at everything
- The test prints one line per run naming its seed, pid and digest, so a `-s` run shows which seeds actually ran
- The test is proven to detect the defect: reverting the fix makes it fail, and that revert check is part of this prompt
- No production code changes land — the test file is the only addition; a temporary revert proves the test detects the defect, then is restored
- Runtime stays bounded: one child per seed, three seeds
</summary>

<objective>
Add the regression test that makes prompt 1's determinism fix enforceable: it must fail against the pre-fix implementation and pass against the fixed one, and it must do so by crossing a real process boundary — the defect only exists between processes, so no single-process test can see it.
</objective>

<context>
Read `CLAUDE.md` for project conventions and `docs/dod.md` for the Definition of Done.

Read these files before making changes:

- `src/semantic_search/indexer.py` — `_prepare_text_for_embedding(self, file_path: Path, content: str) -> str` is the function under test. It must be exercised through `VaultIndexer.__new__(VaultIndexer)`; never through `VaultIndexer(...)`.
- `tests/test_server.py` — the repo's existing subprocess-probe pattern: a child program kept as a module-level string constant (`_STUB_SENTENCE_TRANSFORMERS`), spawned with `sys.executable`, plus `_terminate_and_read_stderr` for timeout handling. Your probe follows the same shape, with `subprocess.run(..., capture_output=True, text=True)`.
- `tests/test_replay_scope_fixture.py` — the repo's `subprocess.run([sys.executable, <script>, ...])` shape and how it asserts on a child's return code, stdout, and stderr.
- `tests/conftest.py` — the autouse `_isolated_indexer_cache` fixture and the deterministic-encoder fixtures. Your test needs neither: it never constructs a real indexer.
- `tests/test_indexer.py` — prompt 1's in-process tag-block assertions live here. Your test is the process-boundary layer; do not restate those in-process assertions.
- `docs/design/weighted-embedding-strategy.md` § "Metadata Extraction" — the authored-order contract the tag-line assertion pins.

**Verified facts you can rely on:**

- `semantic_search` resolves to the working tree: `uv run python -c "import semantic_search; print(semantic_search.__file__)"` prints `.../src/semantic_search/__init__.py` (editable install), so a child spawned as `[sys.executable, ...]` imports the code under test and not an installed copy. No `PYTHONPATH` juggling is needed.
- The container cannot load `all-MiniLM-L6-v2`. The child must therefore call `VaultIndexer.__new__(VaultIndexer)` and then `_prepare_text_for_embedding` — `__init__` is what loads the model, and the prepared-text path needs no instance state. Importing `semantic_search.indexer` (and therefore `sentence_transformers`) is unavoidable and costs a few seconds per child; that is why the test uses exactly three children.
- `PYTHONHASHSEED` genuinely changes a child's effective string-hash secret: three children with seeds `1`, `2`, `3` printed `hash("semantic-search-hash-probe")` as `201182457203691922`, `8507505509590056905`, `-8233807107485993974` on this tree today.
- Today the tagged fixture below produces three different digests under seeds `1|2|3` (`fa8307e8fdc2b1ea`, `3dba4767f1ceb1f4`, `8ce7cb2105da8028`), while the untagged control produces `503e924daafabde1` under all three. After prompt 1's fix the tagged fixture produces one identical digest under all three seeds.

Pattern guides (in-container paths):

- `/home/node/.claude/plugins/marketplaces/coding/docs/test-pyramid-triggers.md` — why this assertion belongs at the process boundary rather than one layer down: the defect is invisible to any single-process test, so this is the "real out-of-process dependency" trigger, not an E2E defaulted up the pyramid.
</context>

<requirements>

1. **Create `tests/test_embedding_determinism.py`** — the only file this prompt adds. Standard library plus pytest; no new dependency.

2. **The probe child.** Keep its source as a module-level string constant in the test file (the `_STUB_SENTENCE_TRANSFORMERS` shape in `tests/test_server.py`), write it to `tmp_path` at test time, and run it as `[sys.executable, str(probe_path), str(tagged_fixture), str(untagged_fixture)]`. It must:
   - read both fixture paths from `argv`, read each file's text, and call `VaultIndexer._prepare_text_for_embedding` on an instance built with `VaultIndexer.__new__(VaultIndexer)` — never `VaultIndexer(...)`, which loads the embedding model the container does not have;
   - print exactly one line of JSON on stdout with these keys:
     - `pid` — `os.getpid()`, the child's process id;
     - `hash_fingerprint` — `str(hash("semantic-search-hash-probe"))`, the child's effective string-hash secret made observable (use this literal — the values in `<context>` were measured with it); `argv[1]` is the tagged fixture, `argv[2]` the untagged one;
     - `tagged_sha256` — `hashlib.sha256(prepared.encode()).hexdigest()` for the tagged fixture;
     - `tagged_tag_line` — the tagged fixture's tag block, `prepared.split("\n")[3]`;
     - `untagged_sha256` — the same digest for the untagged fixture;
   - exit non-zero if anything goes wrong, so a broken probe is a loud failure rather than an empty stdout that a lenient parse could read as agreement.

3. **The parent test** — one `Test*` class in the file. Spawn three children, one per seed, with `env={**os.environ, "PYTHONHASHSEED": seed}` for three distinct non-zero seeds (`"1"`, `"2"`, `"3"` are fine), using `subprocess.run(..., capture_output=True, text=True, timeout=180)`, parsing the child's **last non-empty stdout line** (never the whole blob) and including raw stdout and stderr in any failure message. A non-zero return code must fail the test with the child's stderr in the failure message — the child imports a heavy module and can fail for an environmental reason, which must never read as a pass. Print one line per run in exactly this shape, so a `-s` run shows the distinct seeds it ran under:

   ```
   PYTHONHASHSEED=<seed> pid=<pid> fingerprint=<hash_fingerprint> tagged_sha256=<digest>
   ```

4. **Assertions — each one is load-bearing; weaken none of them:**
   - the three runs report **three distinct `pid` values** — the runs really are separate processes. This is the assertion that fails when the spawn is replaced with in-process calls, which is exactly the property the spec demands;
   - the three runs report **three distinct `hash_fingerprint` values** — the `PYTHONHASHSEED` override genuinely took effect. If it did not, this fails loudly instead of letting the determinism assertion pass vacuously;
   - the three runs report **one identical `tagged_sha256`** — the defect is fixed;
   - every run's `tagged_tag_line` equals `alpha beta gamma delta epsilon` — authored order, not alphabetised, under every seed;
   - the three runs report **one identical `untagged_sha256`** — the control. Untagged files were already deterministic; a failure here points somewhere other than the tag block, and the assertion is what makes a tagged failure diagnosable.

5. **Fixtures.** Write each fixture into its own directory under `tmp_path`, both named `fixture.md`, so the two differ only in their tag block (the filename stem is embedded three times — a different name would move the digest for a reason unrelated to this bug). Tagged:

   ```
   ---
   tags: [alpha, beta, gamma, delta, epsilon]
   ---

   # Fixture

   body text
   ```

   Control — identical but with an empty frontmatter block:

   ```
   ---
   ---

   # Fixture

   body text
   ```

6. **No escape hatches.** No `pytest.skip`/`skipif`, no retry loop, no `try`/`except` around the subprocess call that could turn a failure into a pass, and no fallback to an in-process call. The spec's failure mode for this test is "fails loudly rather than silently passing".

7. **The literal `PYTHONHASHSEED` must occur at least three times in the file.** The spec's evidence command counts occurrences, not lines (`grep -o PYTHONHASHSEED tests/test_*.py | wc -l` must be ≥ 3). Natural occurrences: the env write in the spawn helper, the module or class docstring explaining why the seed is overridden, and the printed run line or a failure message. Do not pad the file with repeats in a comment.

8. **Prove the test detects the defect (revert check), then restore.** This is the spec's acceptance criterion for the test itself and must be completed before the final `<verification>` run:
   - copy `src/semantic_search/indexer.py` to a scratch path outside the repo (e.g. `/tmp/indexer.fixed.py`);
   - put the pre-fix tag-union assembly back — `all_tags = {t.lower() for t in tags_aliases} | {t.lower() for t in inline_tags}` followed by `tags_aliases = list(all_tags)` — in place of prompt 1's ordered merge;
   - run `uv run pytest tests/test_embedding_determinism.py -q` and confirm it **exits non-zero**;
   - restore the copy over `src/semantic_search/indexer.py` and prove the restore is byte-identical (`sha256sum` before the revert and after the restore must match);
   - confirm the restore: the new test passes again, and `make precommit` is green.

   Never leave the reverted implementation in the tree. If the test passes while the pre-fix code is in place, the test is wrong — fix the test, not the assertion.

9. **Self-check before finishing:** re-run `<verification>` and confirm every step reports PASS, including the reproduction step, which prints one identical digest only while prompt 1's fix is in place.

</requirements>

<constraints>
- Python only; no new dependencies — the test uses the standard library plus pytest.
- This prompt adds a test, not a behaviour change: do NOT ship any edit under `src/` — prompt 1 landed the fix. The single exception is requirement 8's temporary revert, which exists only to prove the test detects the defect and must be restored before you finish. If the new test fails against the shipped implementation, do not weaken its assertions and do not adjust the implementation — report the failure instead.
- Do NOT commit — dark-factory handles git.
- Existing tests must still pass unchanged.
- Do NOT add `PYTHONHASHSEED` to any plist, wrapper script, environment file, or the service's process environment. The only legitimate override is the test's own child-process environment.
- Do NOT skip the test on a slow or missing subprocess, and do not mark it `slow`/`skipif` — a silently skipped determinism test is worse than no test.
- Do NOT assert on source text (never read `indexer.py` and grep for a pattern) — the spec requires the test to cross the process boundary, not to inspect the source.
- Keep the child count at three, and do not add an in-process repeat: every child imports `sentence_transformers`, so the suite's runtime is this test's cost.
- Keep `make precommit` green: `ruff format`/`ruff check` clean at the repo's 100-character limit (tests are linted and format-checked), full suite green.
- Repo-relative paths in the test; fixture paths under `tmp_path` only.
</constraints>

<verification>
Run `make precommit` — must pass (sync + format + test + lint + typecheck).

Then confirm each of these. Every step shells out to `uv`; a `127` from any step means the command never ran, which is a verification failure — never read it as a pass.

```bash
# Guard: if `uv` is missing, every step below reports 127 and nothing was actually verified
command -v uv >/dev/null || echo "FAIL: uv is not in PATH — every step below would report 127"

# 1. The new test exists, passes, and names the seeds it ran under
#    (-s matters: pytest captures stdout for passing tests, so a plain run hides it)
test -f tests/test_embedding_determinism.py || echo "FAIL: tests/test_embedding_determinism.py is missing"
out="$(mktemp)"
uv run pytest -s tests/test_embedding_determinism.py > "$out" 2>&1
code=$?
[ "$code" -eq 0 ] && echo "PASS: the new test exits 0" || echo "FAIL: the new test exited $code (127 = the command never ran)"
seeds_seen=$(grep -c 'PYTHONHASHSEED=' "$out")
[ "$seeds_seen" -ge 3 ] && echo "PASS: the -s run named $seeds_seen runs under distinct seeds" || echo "FAIL: the -s run named only $seeds_seen runs (expected >= 3)"
grep -qE 'pid=[0-9]+' "$out" && echo "PASS: the -s run reported child pids" || echo "FAIL: no child pid in the -s output"

# 2. The spec's evidence command: the test really overrides the seed, in >= 3 places
n=$(grep -o PYTHONHASHSEED tests/test_*.py | wc -l | tr -d ' ')
[ "$n" -ge 3 ] && echo "PASS: PYTHONHASHSEED occurs $n times in tests/test_*.py" || echo "FAIL: PYTHONHASHSEED occurs $n times (expected >= 3)"
nf=$(grep -o PYTHONHASHSEED tests/test_embedding_determinism.py | wc -l | tr -d ' ')
[ "$nf" -ge 3 ] && echo "PASS: PYTHONHASHSEED occurs $nf times in the new file" || echo "FAIL: PYTHONHASHSEED occurs $nf times in the new file (expected >= 3)"

# 3. Prompt 1's fix survived the revert check: one identical digest across three seeds
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
distinct_hashes=$(awk '{print $1}' "$tmp/tagged.txt" | sort -u | wc -l | tr -d ' ')
[ "$distinct_hashes" = "1" ] && echo "PASS: fix still in place — one identical digest across three seeds" || echo "FAIL: $distinct_hashes distinct digests — the fix is missing or was left reverted"
ordered=$(grep -c "alpha beta gamma delta epsilon" "$tmp/tagged.txt")
[ "$ordered" = "3" ] && echo "PASS: authored order under all three seeds" || echo "FAIL: authored order in $ordered of 3 runs"

# 4. The whole suite
make test
```
</verification>
