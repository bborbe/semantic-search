---
status: completed
spec: [006-repeated-scope-union]
summary: Added tests/test_union_scope_sweep.py, a fourteen-query repeated-scope sweep asserting per-scope non-vacuity and zero leakage, with both clauses proven load-bearing
execution_id: semantic-search-exec-035-spec-006-union-leakage-sweep
dark-factory-version: v0.193.0
created: "2026-09-12T09:03:10Z"
queued: "2026-09-12T09:22:11Z"
started: "2026-09-12T09:31:54Z"
completed: "2026-09-12T09:34:44Z"
branch: dark-factory/repeated-scope-union
---

# Sweep every fixture query for union leakage and per-scope coverage

<summary>
- A fourteen-query sweep drives a repeated-scope request over a three-root corpus and reports what came back from each scope
- Every query must return at least one path under each named scope — a sweep that returns nothing fails instead of passing quietly
- No query may return a single path from outside the named scopes, including from a third declared scope the request never names
- The sweep proves its own leakage clause is load-bearing: an unscoped search on the same index does return the unnamed scope's documents
- The sweep mirrors the acceptance baseline's shape — fourteen queries at the baseline's result cap — while the corpus is built locally
- The sweep runs against a real app over real temporary roots, with the deterministic encoder, so results are comparable between runs
- The existing single-scope tests, the union probes, and the rest of the suite keep passing untouched
- No production code changes: this prompt adds the broad-coverage assertion on top of the resolution point and the surface wiring
</summary>

<objective>
Prove, across a fourteen-query sweep rather than a single probe, that a repeated-scope request returns results drawn from every scope it names and nothing from outside them. The single-query probes already shipped assert membership and the fail-closed paths; this sweep is the broad-coverage assertion that a resolution bug which widens beyond the named scopes — or which silently drops a scope — is caught rather than shipped.
</objective>

<context>
Read `CLAUDE.md` for project conventions and `docs/dod.md` for the Definition of Done.

Prompts 1 and 2 have already landed:

- `resolve_scope(scope_map, raw_values)` in `src/semantic_search/scopes.py` takes every scope value a request carried, ignores blank values, rejects the whole request when any name is undeclared, and otherwise returns the deduplicated union of the named scopes' roots.
- The three REST routes call it with `request.query_params.getlist("scope")` and the MCP middleware calls it with `parse_qs(...).get("scope", [])`, so a request carrying `?scope=scope_a&scope=scope_b` searches the union of both scopes' roots.

Read these files before writing the sweep:

- `tests/test_scoping.py` — the pattern this file follows: the autouse `_reset_process_state` fixture (`factory.reset()` **plus** `http_server._indexer = None`, `http_server._indexer_error = None`, `http_server._indexer_ready = asyncio.Event()`), `_wait_for_ready(client)`, `build_app(scope_map)`, and `patch("semantic_search.indexer.SentenceTransformer", deterministic_sentence_transformer)`. Its existing tests must stay unmodified.
- `docs/design/per-vault-scoping.md` — the scope contract the sweep asserts against ("Fail-closed", "The union index"). Read-only: the union update to it is a direct edit after merge.
- `tests/conftest.py` — the `deterministic_sentence_transformer` fixture and the fixed vocabulary its encoder counts: `alpha`, `beta`, `gamma`, `delta`, `epsilon`, `zeta`. A document or query's similarity comes from how many of those tokens it contains, so every query and every document in this sweep must be built from those six tokens.
- `src/semantic_search/http_server.py` — `build_app(scope_map)`, `get_indexer()`, and the `/health` readiness contract the sweep waits on. Do not change it.

**Verified facts you can rely on (re-measured on this tree):**

- The acceptance baseline's fourteen queries are **not readable from inside the container**. They exist only in the host vault fixture (`80 Attachments/semantic-search-consolidation-baseline-2026-09-11.json` in the Personal vault); there is no copy in the repository and none in the git history. The fixture's `top_k` is 20. So this sweep builds its own fourteen-query set over a locally built corpus, mirroring the baseline's cardinality and result cap, and the operator's real-fixture replay stays on the spec's verification ladder (spec 004's replay tool) rather than in this prompt.
- The frozen baseline is a **retired oracle** and must not be used here: it was captured from processes whose `PYTHONHASHSEED`-randomised tag ordering made each process's answer arbitrary, the live `VaultWatcher` has since moved the corpus under it, and the five originals now reproduce only **57/70** of their own recording — no new process could ever match it. `scripts/replay-scope-fixture.py` is additionally path-only (it compares `results[].path` lists, never scores), so it is structurally incapable of observing a score change even against a valid baseline.
- A declared scope's root is part of the index's build input: the index is built once over the union of every declared scope's roots. So a third scope that the request never names is still indexed, and its documents would appear in the response if resolution ever widened to the whole map — which is what makes the leakage clause below load-bearing.
- The `?scope[]=` and `?scope=a,b` forms are refused before any of this and stay refused; the sweep covers the repeated form only.

**The evidence discipline this sweep must satisfy (from the spec's acceptance criteria):**

- For every fixture query the union request must return **at least one path under EACH named scope's roots** — proving the union is actually exercised — and **0 paths outside all named roots**.
- **The positive clause is required: an all-empty sweep also reports 0 out-of-scope paths.** A sweep that returns nothing for every query must fail, not pass.

Pattern guides (in-container paths):

- `/home/node/.claude/plugins/marketplaces/coding/docs/test-pyramid-triggers.md` — why this is an integration test (a real app over real temp roots) and why the assertion belongs there rather than in a mocked unit test.
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-architecture-patterns.md` — the app's process-wide singletons and why a probe resets them before and after.
</context>

<requirements>

1. **Create `tests/test_union_scope_sweep.py`.** The module docstring states what the sweep proves (per-scope non-vacuity plus zero leakage across the whole query set) and why the query set is built locally — the baseline's fourteen queries live only in the host fixture and are not readable in the container, so this sweep mirrors the baseline's cardinality and result cap instead.

2. **Module constants**, so the sweep's shape is visible at the top of the file:

   - `BASELINE_QUERY_COUNT = 14` — the acceptance baseline's query count.
   - `TOP_K = 20` — the acceptance baseline's `top_k`.
   - `QUERIES: list[str]` — exactly fourteen distinct query strings, every one built from the deterministic encoder's six tokens (the six single tokens plus eight two-token combinations is one way to get there).
   - A module-level `assert len(QUERIES) == BASELINE_QUERY_COUNT` so a query added or dropped later fails loudly instead of silently changing the sweep's coverage.

3. **Build the corpus and the app inside a fixture** in this file, in the style of `tests/test_scoping.py`:

   - five temp roots: the three sibling corpus roots `root_a`, `root_b`, and `root_c`, plus two empty roots `root_empty_a` and `root_empty_b` holding no `.md` files;
   - each root holds two documents, every one of them containing all six probe tokens (for example `"alpha beta gamma delta epsilon zeta\n" * 5`), so every query matches a document in every root;
   - a scope map declaring five scopes — `scope_a` → `root_a`, `scope_b` → `root_b`, `scope_c` → `root_c`, `scope_empty_a` → `root_empty_a`, `scope_empty_b` → `root_empty_b`. `scope_c` is declared so its root is in the index's build input, and the sweep request never names it; the two empty scopes exist only for the empty-union probe in requirement 6;
   - reuse the process-state reset discipline (`factory.reset()` plus the `http_server._indexer` / `_indexer_error` / `_indexer_ready` trio) before and after, and wait for `/health` to report ready before issuing any request;
   - patch `deterministic_sentence_transformer` into `semantic_search.indexer.SentenceTransformer` for the app's lifetime.

4. **The sweep itself** — one request per query, over the app's own indexer:

   - For each query, first assert the leakage clause is load-bearing: an unscoped `http_server.get_indexer().search(query, top_k=TOP_K)` returns at least one path under `root_c`. Without this, an out-of-scope count of zero could just mean the index never held the unnamed scope's documents.
   - Then issue `GET /search` with `params=[("q", query), ("top_k", str(TOP_K)), ("scope", "scope_a"), ("scope", "scope_b")]` — the repeated form, built as a list of pairs so the parameter really is repeated and the query is URL-encoded for you. Assert the response is HTTP 200.
   - From `results[].path`, count: paths under `root_a`, paths under `root_b`, and paths under neither (resolve each path before comparing, the way the existing probes do).
   - Print one line per query in exactly this shape, so the evidence is visible under `pytest -s` and countable by a shell check:
     ```
     sweep <query>: scope_a=<n> scope_b=<n> outside=<n>
     ```
   - Collect the per-query counts into a report.

5. **Assert on the whole report, not on the last query:**

   - every query's `scope_a` count is greater than 0 **and** every query's `scope_b` count is greater than 0 — an all-empty sweep fails here;
   - the sum of every query's `outside` count is exactly 0 — and the assertion message carries the totals, so a failure names the offending query and its counts rather than just "assertion failed";
   - `len(report) == BASELINE_QUERY_COUNT`, so the sweep cannot shrink without failing.

6. **Assert the empty union is not an error.** One extra request over the same app — naming two declared scopes whose roots hold no `.md` files — returns HTTP 200 with `count == 0` and an empty `results` list: a valid repeated scope whose union matches nothing is an empty result, not an error (the spec's failure-modes table). Do **not** print a `sweep ` line for it — the shell check counts exactly 14. Note: an out-of-vocabulary query does not produce this case (the deterministic encoder returns a zero vector and `search()` then returns arbitrary zero-score documents), so empty roots are what make the result set empty.

7. **Strict typing and style.** Full annotations, docstrings on every function, no new dependency, no broad `except Exception`, ruff format/check clean at the repo's 100-column limit.

**Self-check before finishing:** re-run `<verification>` and confirm every step reports PASS. Then prove the sweep is not vacuous, twice:

- **The positive clause is load-bearing.** Temporarily issue the sweep's request with `scope_a` only (drop `scope_b` from the parameter list), re-run `uv run pytest tests/test_union_scope_sweep.py -q`, and confirm the per-scope assertion fails on every query — that is the evidence that an all-empty sweep cannot pass.
- **The leakage clause is load-bearing.** Temporarily make the resolver ignore the request's values and return `scope_map.union_roots`, re-run the same command, and confirm the out-of-scope assertion fails with a non-zero total.

Restore both, re-run `uv run pytest tests/test_union_scope_sweep.py -q` and `make precommit`, and report both observations in the completion summary.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- This prompt adds tests only. Do NOT change production code — no edit to `src/semantic_search/`, `Makefile`, or `pyproject.toml`. If the sweep exposes a real defect, do NOT weaken the sweep to make it pass: fix it only if the correction is confined to the union construction in `resolve_scope` or the two multi-value extraction call sites, and otherwise report the failing query and its counts in the completion report.
- Do NOT use the frozen pre-change baseline fixture as an oracle, and do NOT edit `scripts/replay-scope-fixture.py`. The baseline's fourteen queries are not readable in the container; this sweep builds its own corpus and query set.
- Existing tests must pass unmodified; a test that must change to accommodate this work is a regression signal, not a test to update. Do not edit any existing test file.
- The error tokens `MISSING_SCOPE` and `UNKNOWN_SCOPE` and the response shape `{"error": "<TOKEN>"}` with status 400 stay unchanged.
- `UnknownScopeError` must name the offending value.
- The index build, the cache key, and the embedding path are untouched — scope reaches `search()` as the `roots` argument and never reaches the indexer's construction.
- MCP scope continues to bind at session creation, not per call.
- Do NOT add a comma syntax, a bracket syntax, or any other way to name a scope that is not a key of the map.
- Tests must build their own temp content roots: the container has no vault content mounted, and the committed `scopes.yaml` names host directories that do not exist inside it. Never point a test at the committed map.
- Do NOT edit anything under `docs/`, `commands/`, `agents/`, or `scenarios/` — those are direct edits handled outside the prompt pipeline.
- No new dependency. `make precommit` must be green: ruff format/check clean, `mypy src` strict, full suite green.
</constraints>

<verification>
Run `make precommit` — must pass (sync + format + test + lint + typecheck).

Then confirm each of these. Every step shells out to `uv`; a `127` from any step means the command never ran, which is a verification failure — never read it as a pass.

```bash
# Guard: if `uv` is missing, every step below reports 127 and nothing was actually verified
command -v uv >/dev/null || echo "FAIL: uv is not in PATH — every step below would report 127"

# Keep uv's cache on its mounted path
export UV_CACHE_DIR="${UV_CACHE_DIR:-/home/node/.cache/uv}"

# 1. The sweep exists and carries the baseline's cardinality as a named constant
test -f tests/test_union_scope_sweep.py && echo "PASS: the sweep file exists" || echo "FAIL: tests/test_union_scope_sweep.py is missing"
count="$(grep -c 'BASELINE_QUERY_COUNT' tests/test_union_scope_sweep.py)"
if [ "$count" -ge 2 ]; then echo "PASS: BASELINE_QUERY_COUNT is defined and used ($count occurrences)"; else echo "FAIL: expected >= 2 occurrences of BASELINE_QUERY_COUNT, got $count"; fi

# 2. Run the sweep with its report visible, and count the per-query lines
uv run pytest tests/test_union_scope_sweep.py -q -s > /tmp/union-sweep.txt 2>&1
cat /tmp/union-sweep.txt
swept="$(grep -c '^sweep ' /tmp/union-sweep.txt)"
if [ "$swept" = "14" ]; then
  echo "PASS: the sweep reported 14 queries"
else
  echo "FAIL: expected 14 sweep lines, got $swept — a shrunken sweep cannot prove the same coverage"
fi
if grep -q '^FAILED\|^ERROR' /tmp/union-sweep.txt || ! grep -q 'passed' /tmp/union-sweep.txt; then
  echo "FAIL: the sweep did not pass — read the report above"
else
  echo "PASS: the sweep passed"
fi

# 3. The whole suite, and the full gate
if uv run pytest -q; then echo "PASS: the full suite passed"; else echo "FAIL: the full suite did not pass — read the output above"; fi
```
</verification>
