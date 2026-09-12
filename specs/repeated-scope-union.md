---
status: draft
created: 2026-09-12
---

## Summary

- A URL with two `?scope=` parameters is silently collapsed to one, and the two surfaces collapse it in opposite directions
- `/search` (REST) takes the LAST duplicate; `/mcp` takes the FIRST — the same URL gives opposite answers depending on which surface a client hits
- Repeated scope should instead resolve to the **union** of the named scopes' roots, so a client can span several vaults in one request
- A union is a requested capability, not only a divergence fix: the operator asked for a single MCP config spanning multiple scopes on 2026-09-12
- Unknown names must reject the whole request rather than serving the valid subset

## Problem

Today a repeated `?scope=` is silently collapsed to one value, and the two surfaces collapse it in opposite directions. `/search` takes the LAST duplicate via Starlette's `request.query_params.get("scope")`; the MCP mount takes the FIRST via `parse_qs(...).get("scope", [None])[0]` in the middleware. Verified live on 2026-09-12: `?scope=brogrammers&scope=personal` returns **Personal** on `/search` but **Brogrammers** on `/mcp`, with single-scope controls confirming both. Neither unions them — the other value is silently discarded, so a URL that reads like two scopes is really one, with no error.

Measured 2026-09-12: repeated scope produces **no union at all**. At both `top_k=5` and `top_k=20` the result is byte-identical to `scope=personal` alone, with 0 paths under `Brogrammers/` at `top_k=20`. This is last-wins with no merge, not a union masked by truncation.

The shipped shape is not dangerous (one scope per URL is the documented pattern and no MCP config repeats the parameter), but it is a latent trap: the identical URL string gives opposite answers depending on which surface a client hits. Fixing that divergence and adding the union capability are the same change, because one shared resolution point subsumes both.

## Goal

A request naming two or more scopes searches the union of their roots and returns results from every named scope, on both the REST routes and the MCP mount, with a single resolution point that both surfaces call. A request naming any scope that is not declared fails closed with HTTP 400 and serves nothing. A request naming one scope behaves exactly as it does today.

## Non-goals

- Bracket syntax `?scope[]=a&scope[]=b` and comma syntax `?scope=a,b` — deliberately rejected; they already fail closed with 400 and stay that way
- Ad-hoc unions that bypass the scope map — a union of named scopes only; scope names still come from the map
- Any change to the index build, the cache key, or the embedding path — the index is built once over the map's union at startup and request-level scope never reaches it
- Replacing the named-scope model — named scopes remain the recommended, auditable way to give a client a fixed view
- Per-call MCP scope — scope binds at session creation and stays bound for the session's life

## Acceptance Criteria

- [ ] **Union membership.** A repeated-scope request returns a result set containing ≥1 path under EACH named scope's roots, and that set is a strict superset of the same request with either scope alone — evidence: a probe over a two-scope fixture where each scope holds a document matching the query, asserting per-scope path counts > 0 and `set(union) ⊋ set(single_a)` and `set(union) ⊋ set(single_b)`. Count alone is NOT evidence: `search()` returns the moment it holds `top_k` in-scope hits, so a union and a last-wins collapse both return exactly `top_k` paths
- [ ] **Single-scope regression lock.** A single-scope request returns byte-identical results — same paths, same order, same scores — to the pre-change behaviour — evidence: a single-scope request diffed against an **independently built single-scope index** over that scope's own roots (own process, own cache), comparing the same path set, the same ordering, **and** the same scores. Zero tolerance: one extra path, one missing path, a reordering, or a differing score fails. The frozen pre-change baseline is a **retired oracle** and must not be used: it was captured from processes whose `PYTHONHASHSEED`-randomised tag ordering made each process's answer arbitrary, the live `VaultWatcher` has since moved the corpus under it, and the five originals now reproduce only **57/70** of their own recording — no new process could ever match it. `scripts/replay-scope-fixture.py` is additionally path-only (`:223` compares `entry["path"]`, never scores), so it is structurally incapable of observing a score change even against a valid baseline
- [ ] **Fail-closed on any unknown name.** A repeated-scope request naming one unknown value returns HTTP 400 with body `{"error": "UNKNOWN_SCOPE"}`, returns no results, and names the offending value in the raised `UnknownScopeError` message (and its log line) — evidence: the curl's status code, its response body, the absence of a `results` key in that body, and the exception message naming the bad value. The response body is frozen to `{"error": "<TOKEN>"}` by Constraints and carries no value; the value is named in the error message, not the body. The repeated form is required — `?scope=personal&bogus` is a separate unnamed parameter the handler ignores, so it returns 200. Note this probe does **not** distinguish the union from a last-wins collapse: both reject `bogus` (last-wins makes it the effective scope; the union rejects it as an unknown member). AC1, AC4 and AC5 carry that discrimination; this one asserts only that an unknown name fails closed
- [ ] **Both surfaces resolve identically, on the union.** The same repeated-scope URL probed through REST and through an MCP session returns the same vault set, and that set contains ≥1 path under each named scope — evidence: both probes' outputs and a diff of their path sets (empty), with per-scope counts > 0 in both. Agreement alone is NOT evidence: making both surfaces take the FIRST (or both the LAST) duplicate satisfies agreement while still not unioning
- [ ] **Order-independence.** `?scope=a&scope=b` and `?scope=b&scope=a` return identical result sets, both non-empty and both containing paths under both scopes' roots — evidence: both outputs and an empty diff of their path sets, with both sets non-empty
- [ ] **No leakage beyond the union.** For every fixture query, the union returns ≥1 path under EACH named scope's roots — proving the union is actually exercised — and 0 paths outside all named roots — evidence: a leakage sweep over all 14 baseline fixture queries reporting per-scope path counts (all > 0) and an out-of-scope total of 0. The positive clause is required: an all-empty sweep also reports 0 out-of-scope paths
- [ ] **Rejected forms stay rejected.** `?scope[]=a&scope[]=b` returns HTTP 400 `MISSING_SCOPE` and `?scope=a,b` returns HTTP 400 `UNKNOWN_SCOPE` — evidence: both curls' status codes and response bodies
- [ ] **One resolution point.** Both surfaces resolve scope through the same multi-value function, and no call site extracts a single scope value — evidence: a test asserting the resolver is invoked from all four call sites (the three REST routes and the MCP middleware), plus `grep -c 'resolve_scope(' src/semantic_search/http_server.py` returning ≥ 4, plus `grep -c '\.get("scope", \[None\])\[0\]' src/semantic_search/http_server.py` returning 0. The middleware legitimately parses the raw query string itself — it is pure ASGI by design — so the assertion is about single-value *extraction*, not about parsing

## Verification

### Container-executable (runs inside the YOLO container at prompt time)

- `make precommit` — format, lint, typecheck (strict mypy) clean
- `make test` — unit + integration suite passes, including the new multi-value resolution tests and the existing single-scope tests unchanged
- `grep -n 'def resolve_scope' src/semantic_search/scopes.py` — the resolution function accepts a sequence of values, not a single string

### Operator-executable (runs on the host after PR merge, spec verification ladder)

- A single-scope request (`?scope=personal`) diffed against an independently built single-scope index over `Personal`'s own roots — identical paths, ordering and scores (the regression lock; do **not** use the frozen fixture replay, see AC2)
- `curl -sS "http://127.0.0.1:8321/search?q=Boss+vault&top_k=20&scope=brogrammers&scope=personal"` — the result set contains paths under both `Obsidian/Brogrammers/` and `Obsidian/Personal/`
- `curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:8321/search?q=x&scope=personal&scope=bogus"` — prints `400` (the second `scope=` is required; a bare `&bogus` is an ignored unknown parameter and would return 200)
- `curl -sS "http://127.0.0.1:8321/search?q=Boss+vault&top_k=20&scope=personal&scope=brogrammers"` compared against the reversed parameter order — the two path sets are identical
- An MCP session opened against `http://127.0.0.1:8321/mcp?scope=brogrammers&scope=personal`, calling `search_related` — returns the same vault set as the equivalent REST request
- `semantic-search-http --version` — matches the release tag installed by `uv tool upgrade semantic-search`

## Desired Behavior

1. A request naming two or more declared scopes resolves to the deduplicated union of those scopes' roots, and search returns results drawn from every named scope.
2. A request naming one declared scope behaves exactly as before — same paths, same order, same scores.
3. A request naming no scope returns HTTP 400 `MISSING_SCOPE`.
4. A request naming any undeclared scope returns HTTP 400 `UNKNOWN_SCOPE` and serves no results — the whole request is rejected, never the valid subset. The offending value is named in the `UnknownScopeError` message and its log line; the response body carries only the token.
5. A repeated identical scope name is deduplicated, not an error.
6. Scope order does not affect the result set: `?scope=a&scope=b` and `?scope=b&scope=a` are equivalent.
7. Both the REST routes and the MCP mount resolve scope through one shared function, so the two surfaces cannot disagree.
8. Bracket syntax and comma syntax continue to return HTTP 400.

## Constraints

- `resolve_scope` in `src/semantic_search/scopes.py` changes from a single-string signature to a multi-value one. This is a shared interface: a queued sibling task (defaulting the scope-map path to the user config path) builds on this module, so the new signature must be named in the change summary.
- The error tokens `MISSING_SCOPE` and `UNKNOWN_SCOPE` and the response shape `{"error": "<TOKEN>"}` with status 400 stay unchanged.
- `UnknownScopeError` must name the offending value.
- The index build, the cache key, and the embedding path are untouched — scope reaches `search()` as the `roots` argument and never reaches the indexer's construction.
- MCP scope continues to bind at session creation, not per call.
- Existing single-scope tests must pass unmodified; a test that must change to accommodate this work is a regression signal, not a test to update.
- `make precommit` and `make test` must pass.
- `docs/design/per-vault-scoping.md` documents the scope contract this change extends, and its "Fail-closed" section currently reads as one-scope-per-URL ("An omitted or unknown scope returns HTTP 400 … never the union index"). That doc needs a union update after merge — specs die, docs live.

## Failure Modes

| Trigger | Expected behavior | Recovery | Detection |
|---|---|---|---|
| Repeated scope with one unknown name | HTTP 400 `UNKNOWN_SCOPE`, no results served | Client corrects the name | Response body names the offending value |
| Repeated scope where all names are known but the union matches nothing | HTTP 200 with an empty result list — an empty union is not an error | Re-run with a broader query and confirm `count > 0` | Response `count` is 0 |
| A duplicate name repeated three or more times | Deduplicated to one; same result as naming it once | — | Result set identical to the single-name request |
| MCP session opened with a repeated scope | The session binds to the union at creation and stays bound for its life | Client re-opens the session with a different scope | Session's calls return the union set |
| A scope name that is valid but whose root was deleted after startup | Unchanged from today — `validate_scope_map` runs at startup, so a deleted root surfaces as an empty result set, not a 400 | Operator restarts with a corrected map | Result set shrinks; no error token |

## Security / Abuse Cases

The union capability widens what a client can see, so the boundary is the scope map and nothing else.

- A client can only name scopes declared in the map. An undeclared name is rejected outright, and there is no syntax that names an arbitrary filesystem path.
- A partially-valid list must never degrade to "serve what you can" — the whole request is rejected. This is the failure this spec guards against most directly, because a filter that silently drops the bad name looks successful while leaking nothing and hiding a misconfiguration.
- The union of two declared scopes is bounded by those scopes' roots; it cannot exceed the map's own union, which is already the index's build input.
- The leakage sweep asserts 0 paths outside the named roots across every fixture query, so a resolution bug that widens beyond the named scopes is caught rather than shipped.

## Suggested Decomposition

Prompts should be generated in this order — each row is a single prompt with a clear scope.

| # | Prompt focus | Covers DBs | Covers ACs | Depends on |
|---|---|---|---|---|
| 1 | Multi-value resolution point in `scopes.py` (dedup, order-independence, unknown-name rejection) + unit tests | 1, 2, 3, 4, 5, 6 | 1, 3, 5, 7 | — |
| 2 | Wire both surfaces to the shared resolver (REST routes + MCP middleware) + integration tests covering both surfaces and the single-scope regression | 7, 8 | 2, 4, 8 | prompt 1 |
| 3 | Fixture sweep: leakage assertion and per-scope non-vacuity across all 14 baseline queries | — | 6 | prompts 1, 2 |

Rationale: prompt 1 establishes the resolution contract that everything else calls. Prompt 2 is the surface wiring and can only be written once that contract exists. Prompt 3 is the broad-coverage assertion on top of both — it needs the union working on both surfaces before its per-scope counts can be non-zero.

This spec exceeds the size heuristic (8 DBs × 8 ACs = 64, above the threshold of 50) and is deliberately kept whole: the three prompts above are each independently scoped, the decomposition is complete with DB/AC mapping, and splitting would duplicate the Problem and Constraints sections across two specs for no research benefit. The heuristic's own remedy — a decomposition the prompt-creator does not have to re-derive — is already satisfied.

## Do-Nothing Option

Leaving this alone costs nothing today, because no shipped config repeats the parameter. The cost is deferred and silent: the identical URL string continues to give opposite answers on the two surfaces, and any client that writes a repeated scope — a plausible mistake, since the URL reads as if it should work — gets one arbitrary scope with no error. The capability the operator asked for also stays missing, so spanning several vaults from one MCP session remains impossible. Doing nothing is defensible only if multi-scope is genuinely unwanted; it is not, so the divergence is worth closing even setting the union aside.
