---
status: completed
spec: [006-repeated-scope-union]
summary: 'Changed resolve_scope to the multi-value signature `resolve_scope(scope_map: ScopeMap, raw_values: Sequence[str] | str | None) -> tuple[Path, ...]` in src/semantic_search/scopes.py — it now returns the deduplicated union of every named scope''s roots in first-appearance order, ignores blank values, raises MissingScopeError when no non-blank value remains, and rejects the whole request with UnknownScopeError naming the first undeclared value; the bare-str and None legacy forms are retained so the three existing single-scope tests in TestResolveScope pass unmodified, and 18 new unit tests pin the union, order-independence, de-duplication, and every rejection path.'
execution_id: semantic-search-exec-033-spec-006-multi-value-resolution
dark-factory-version: v0.193.0
created: "2026-09-12T09:03:10Z"
queued: "2026-09-12T09:22:10Z"
started: "2026-09-12T09:22:12Z"
completed: "2026-09-12T09:28:30Z"
branch: dark-factory/repeated-scope-union
---

# Resolve a repeated scope to the union of the named scopes' roots

<summary>
- A request that names several scopes resolves to the combined roots of all of them, each root appearing once
- The order the scopes are named in does not change which roots are resolved
- Naming the same scope twice is not an error — it resolves exactly as naming it once does
- A request naming any scope that is not declared is rejected whole: the error names the offending value, and no partial set of roots is returned
- A request naming no scope is still refused exactly as it is today
- A value containing a comma stays one scope name, never two
- Every current caller keeps working unchanged, including one that passes a single scope as a plain string
- The resolution function becomes the single place where a request's scope names turn into roots — the shared point the next prompt wires both HTTP surfaces to
- Unit tests pin the union, the de-duplication, the order-independence, and every rejection path
- No server behaviour changes yet — this prompt changes the resolution function only; the surfaces are wired in the next prompt
</summary>

<objective>
Turn the scope resolver into a multi-value one: given every scope value a request carried, return the deduplicated union of the named scopes' roots, and reject the whole request — naming the offending value — when any name is not declared. This is the shared resolution point that the REST routes and the MCP mount will both call, so it is the contract the next two prompts build on; today it takes a single string, which is why the two surfaces collapse a repeated parameter in opposite directions.
</objective>

<context>
Read `CLAUDE.md` for project conventions and `docs/dod.md` for the Definition of Done. Read `docs/design/per-vault-scoping.md` for the scope contract this change extends — it owns the fail-closed rule and names `resolve_scope`, `load_scope_map`, `validate_scope_map`, and `ScopeMap.union_roots`. Do not edit it: the union update to its "Fail-closed" section is a direct edit after merge.

Read these files before making changes:

- `src/semantic_search/scopes.py` — the change lands in `resolve_scope`, which today reads `def resolve_scope(scope_map: ScopeMap, raw: str | None) -> tuple[Path, ...]`, raises `MissingScopeError("request named no scope")` for `None` or a blank string, and raises `UnknownScopeError(f"unknown scope {raw!r}")` for an undeclared name. `ScopeMap.union_roots` documents the de-duplication rule this change reuses: "Deduplicated union of every scope's roots, in first-appearance order". `ScopeMap`, `load_scope_map`, `validate_scope_map`, and the request-roots context variable are out of scope — do not change them.
- `tests/test_scopes.py` — `TestResolveScope` is the style anchor **and** the compatibility pin. It calls the resolver with a bare string (`resolve_scope(scope_map, "one")`, `resolve_scope(scope_map, "does-not-exist")`) and with `None`, `""`, and `"   "` (the parametrized `test_missing_scope`). All three of its tests must keep passing unmodified — requirement 1 says how the new signature accommodates them.
- `src/semantic_search/http_server.py` — the four call sites the next prompt will feed (do **not** edit this file here): `_MCPScopeMiddleware.__call__` and the `search`, `duplicates`, and `content` handlers. Each currently extracts one value and hands it to the resolver.

**Verified facts you can rely on (re-measured on this tree):**

- `starlette.requests.QueryParams.getlist("scope")` returns a `list[str]` — empty when the parameter is absent, and it keeps blank entries. `QueryParams.get("scope")` returns the **last** value, while `urllib.parse.parse_qs(...).get("scope", [...])[0]` returns the **first**. That is the divergence this spec closes.
- `resolve_scope` is called from exactly four places, all in `http_server.py`. No other module imports it.
- The three tests in `TestResolveScope` pass a bare `str` and `None`. The spec's Constraints require existing single-scope tests to pass unmodified, and a `Sequence[str]`-only signature would break all three of them: a bare string would be iterated character by character, and `None` would raise `TypeError`. So the new signature accepts the sequence as its primary contract and keeps tolerating both legacy forms.
- The return type must stay `tuple[Path, ...]`: five call-shape assertions in `tests/test_http_server.py` compare the resolved roots against a tuple — `assert_called_once_with` in the `/search` test and the three `/content` tests, plus one `assert_called_with` — e.g. `mock_indexer.search.assert_called_once_with("test query", 3, (root_a, root_b))`.

Pattern guides (in-container paths):

- `/home/node/.claude/plugins/marketplaces/coding/docs/python-architecture-patterns.md` — the resolver is a pure function over its arguments; keep it that way (no logging, no module state).
- `/home/node/.claude/plugins/marketplaces/coding/docs/test-pyramid-triggers.md` — why this contract is pinned at the unit layer: it is a pure input → output function with branches and error paths.
</context>

<requirements>

1. **Change `resolve_scope` to the multi-value signature** in `src/semantic_search/scopes.py`:

   ```python
   def resolve_scope(
       scope_map: ScopeMap, raw_values: Sequence[str] | str | None
   ) -> tuple[Path, ...]:
   ```

   - `Sequence[str]` is the primary contract: every scope value the request carried, in request order. A bare `str` is one value; `None` is no value. Both are retained so the existing single-scope callers and tests keep working unmodified.
   - The return type stays `tuple[Path, ...]` — a list would break the three call-shape assertions in `tests/test_http_server.py`.
   - Normalise once at the top so the rest of the body works on a single shape:
     ```python
     if raw_values is None:
         values: Sequence[str] = ()
     elif isinstance(raw_values, str):
         values = (raw_values,)
     else:
         values = raw_values
     ```
     The `isinstance` branch is load-bearing: without it a bare string is iterated character by character and `"one"` raises `UnknownScopeError` naming `'o'`.
   - Add `Sequence` to the existing `from collections.abc import Mapping` import line.

2. **Pin the resolution contract.** The body is a small loop over the values; these rules are what it must satisfy:

   - Blank values are ignored (`value.strip() == ""`), so `["", "personal"]` resolves `personal`. That matches what `parse_qs` already produces for the MCP surface, which keeps the two surfaces agreeing on a query string such as `?scope=&scope=personal`.
   - When no non-blank value is left — an empty sequence, `None`, `""`, `"   "`, or `[""]` — raise `MissingScopeError("request named no scope")`: the same exception and the same message as today.
   - Every non-blank value is looked up in `scope_map.scopes`, in request order. The **first** value that is not declared raises `UnknownScopeError(f"unknown scope {name!r}")` — the same message format as today, naming the offending value. The whole request is rejected: never return the roots of the values that were declared.
   - Otherwise return the deduplicated union of the named scopes' roots, in first-appearance order — the rule `ScopeMap.union_roots` documents. Naming the same scope more than once is de-duplication, not an error.
   - Values are never split or interpreted: `"a,b"` is one literal name, so it raises `UnknownScopeError` naming `'a,b'`. There is no comma syntax and no bracket syntax.
   - Update the docstring to state the multi-value contract, the blank rule, and both raises.

3. **Add unit tests to `tests/test_scopes.py`** — extend `TestResolveScope` or add a sibling class in the same style (class-based, plain `assert`, `pytest.raises`, `tmp_path`, maps built directly as `ScopeMap(scopes={...})`). Cover at minimum:

   - the union of two disjoint scopes is every root of both, in first-appearance order;
   - order-independence: `["a", "b"]` and `["b", "a"]` resolve the same **set** of roots, and every root of each scope appears in both results;
   - a root shared by two named scopes appears exactly once;
   - a repeated identical name (`["a", "a", "a"]`) resolves exactly as `["a"]` does;
   - an unknown name anywhere in the list raises `UnknownScopeError` and `str(exc)` contains the offending value — parametrize over more than one bad value, including one that is a prefix of a declared name, and add a case with two undeclared values (`["bogus", "worse"]`) asserting the first is the one named;
   - the whole request is rejected: `["a", "bogus"]` raises rather than returning `a`'s roots;
   - `["a,b"]` raises `UnknownScopeError` and `str(exc)` contains `a,b` (no comma splitting);
   - blank handling: `["", "a"]` resolves `a`'s roots; `[""]` raises `MissingScopeError`.

   **Do not modify the three existing tests in `TestResolveScope`.** They are the compatibility pin: if one of them fails, the signature is wrong, not the test.

4. **Name the new signature in the change summary.** This module is a shared interface that prompts 2 and 3 of this spec build on, so the completion report's summary must state the new `resolve_scope` signature explicitly — the parameter name and its type, and that it returns `tuple[Path, ...]`.

5. **Strict typing and style.** Full annotations, docstrings on every function, no new dependency, no `print()` in `src/`, no broad `except Exception`, ruff format/check clean at the repo's 100-column limit, `mypy src` strict.

**Self-check before finishing:** re-run `<verification>` and confirm every step reports PASS. Then prove the new assertions are load-bearing rather than incidental: temporarily make the resolver return only the **last** named scope's roots (`scope_map.scopes[values[-1]]`), re-run `uv run pytest tests/test_scopes.py -q`, and confirm the union, order-independence and de-duplication tests **fail** while the existing single-scope tests still pass — that pair of observations is the proof that the new tests discriminate a union from a last-wins collapse. Restore the union implementation and re-run `make precommit`.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- `resolve_scope` changes from a single-string signature to a multi-value one. The new signature must be named in the change summary — it is a shared interface that a sibling task builds on.
- The error tokens `MISSING_SCOPE` and `UNKNOWN_SCOPE`, and the response shape `{"error": "<TOKEN>"}` with status 400, stay unchanged. Do not touch them in this prompt — they are wired in the next prompt.
- `UnknownScopeError` must name the offending value.
- Existing single-scope tests must pass unmodified; a test that must change to accommodate this work is a regression signal, not a test to update. That covers every test in `tests/test_scopes.py`, `tests/test_scoping.py`, `tests/test_http_server.py`, and `tests/test_mcp_scoping.py`.
- The index build, the cache key, and the embedding path are untouched — scope reaches `search()` as the `roots` argument and never reaches the indexer's construction.
- Do NOT edit `src/semantic_search/http_server.py`, `src/semantic_search/indexer.py`, `src/semantic_search/server.py`, or `src/semantic_search/factory.py` — the surfaces are wired in the next prompt.
- Do NOT change `load_scope_map`, `validate_scope_map`, `ScopeMap` (including `union_roots`), or the request-roots context variable.
- Do NOT add a comma syntax, a bracket syntax, or any other way to name a scope that is not a key of the map. An undeclared name is always an error.
- Do NOT edit anything under `docs/`, `commands/`, `agents/`, or `scenarios/`, and do NOT edit `scripts/replay-scope-fixture.py` — those are direct edits handled outside the prompt pipeline.
- No new dependency. `make precommit` must be green: ruff format/check clean, `mypy src` strict, full suite green.
- No CHANGELOG entry in this prompt — it makes no user-facing change; the union capability's entry lands in prompt 2.
</constraints>

<verification>
Run `make precommit` — must pass (sync + format + test + lint + typecheck).

Then confirm each of these. Every step shells out to `uv`; a `127` from any step means the command never ran, which is a verification failure — never read it as a pass.

```bash
# Guard: if `uv` is missing, every step below reports 127 and nothing was actually verified
command -v uv >/dev/null || echo "FAIL: uv is not in PATH — every step below would report 127"

# Keep uv's cache on its mounted path
export UV_CACHE_DIR="${UV_CACHE_DIR:-/home/node/.cache/uv}"

# 1. The signature is multi-value, and Sequence is imported
grep -n 'def resolve_scope' -A 2 src/semantic_search/scopes.py
grep -n 'from collections.abc import' src/semantic_search/scopes.py

# 2. The resolution contract, exercised directly — independent of the test suite
uv run python - <<'PY' > /tmp/union-resolver.txt 2>&1
from pathlib import Path

from semantic_search.scopes import ScopeMap, ScopeRequestError, resolve_scope

a, b, c = Path("/tmp/union-a"), Path("/tmp/union-b"), Path("/tmp/union-c")
m = ScopeMap(scopes={"a": (a,), "b": (b,), "shared": (a, c)})


def check(label: str, got: object, want: object) -> None:
    print(f"{'PASS' if got == want else 'FAIL'}: {label} -> {got!r} (want {want!r})")


def outcome(raw: object) -> str:
    """Resolve, or describe the failure — a wrong signature must report, not crash."""
    try:
        return repr(resolve_scope(m, raw))
    except ScopeRequestError as e:
        return f"{type(e).__name__}: {e}"
    except (AttributeError, TypeError) as e:
        return f"WRONG SIGNATURE: {type(e).__name__}: {e}"


def root_set(raw: object) -> object:
    """The resolved roots as a set, or the failure description."""
    try:
        return set(resolve_scope(m, raw))
    except ScopeRequestError as e:
        return f"{type(e).__name__}: {e}"
    except (AttributeError, TypeError) as e:
        return f"WRONG SIGNATURE: {type(e).__name__}: {e}"


check("union of a and b", outcome(["a", "b"]), repr((a, b)))
check("order-independent (same root set)", root_set(["b", "a"]), root_set(["a", "b"]))
check("order-independent resolves both scopes", root_set(["b", "a"]), {a, b})
check("repeated name deduplicated", outcome(["a", "a", "a"]), repr((a,)))
check("shared root deduplicated", outcome(["a", "shared"]), repr((a, c)))
check("bare string is one value", outcome("a"), repr((a,)))
check("blank value ignored", outcome(["", "a"]), repr((a,)))

check("unknown name rejected and named", outcome(["a", "bogus"]), "UnknownScopeError: unknown scope 'bogus'")
check("comma value not split", outcome(["a,b"]), "UnknownScopeError: unknown scope 'a,b'")
check("missing: None", outcome(None), "MissingScopeError: request named no scope")
check("missing: empty sequence", outcome([]), "MissingScopeError: request named no scope")
check("missing: blank only", outcome([""]), "MissingScopeError: request named no scope")
check("missing: blank string", outcome("   "), "MissingScopeError: request named no scope")
PY
cat /tmp/union-resolver.txt
if grep -qE '^FAIL|Traceback' /tmp/union-resolver.txt; then
  echo "FAIL: a resolution assertion failed or the script crashed — read the lines above"
elif [ "$(grep -c '^PASS' /tmp/union-resolver.txt)" -ne 13 ]; then
  echo "FAIL: expected 13 PASS lines, saw $(grep -c '^PASS' /tmp/union-resolver.txt) — the script did not run to completion"
else
  echo "PASS: every resolution assertion passed"
fi

# 3. The unit tests, then the whole suite
uv run pytest tests/test_scopes.py -q
uv run pytest -q
```
</verification>
