---
status: verifying
approved: "2026-09-11T11:20:35Z"
generating: "2026-09-11T11:43:08Z"
prompted: "2026-09-11T11:43:08Z"
verifying: "2026-09-11T13:27:12Z"
branch: dark-factory/per-vault-scoping
---

## Summary

- Five always-on search daemons each build their own embedding index over heavily overlapping content — 10.3 GB combined memory footprint against a ~90 MB embedding model, because the indexes are five separate copies of nearly the same vaults.
- This spec makes one server answer every client's query from a single shared index, filtered to a named scope, so each client sees exactly the paths it sees today.
- Scope is a named allowlist of roots. The five existing path sets are not derivable from any path-prefix rule, so a prefix filter cannot reproduce them.
- A request that names no scope, or a scope the server does not know, is refused. It never falls back to the full index.
- The acceptance oracle is already frozen: a pre-change baseline of 14 queries against all five ports. The scoped server must replay it with an empty diff — same path set, same ordering, per scope.

## Problem

Five `launchd` daemons run continuously on the laptop, each holding its own embedding index over content that overlaps heavily with its siblings: 10.3 GB combined footprint measured 2026-09-11, on a machine that also runs the voice stack, several Claude Code sessions, and the trading tooling. The embedding model is ~90 MB, so the footprint is duplicated index data rather than duplicated model weights. Every other reclaim candidate ranked below this one is smaller. The obstacle to merging the daemons is not the merge — it is that each daemon's indexed path set is the only thing distinguishing its clients, and that path set is read once at process start from an environment variable, with no way for a request to ask for a narrower view. Merging without per-request scoping would hand every client the union of all five path sets, which is a visibility change no client asked for and several would notice.

## Goal

One server process holds one index covering the union of the five path sets, and every request names the scope it wants. A scoped request returns exactly the result set the corresponding single-purpose daemon returns today — same paths, same ordering — for search, duplicate detection, and content fetch, over both the REST surface and the HTTP-mounted MCP surface. Requests naming no scope, or a scope the server does not know, are refused rather than answered from the full index. With the four redundant daemons retired, the surviving process measures at or below 4 GB footprint at ≥24 h uptime — at least 6 GB below the five-daemon baseline.

## Non-goals

- Do NOT change what any client can see. The five scopes reproduce the five existing path sets exactly; no scope gains a vault and no scope loses one.
- Do NOT collapse the five MCP server names into one. Each client keeps its own server name and tool prefix; only the URL that name points at changes.
- Do NOT touch the stdio MCP transport or the `semantic-search` CLI. Both keep `CONTENT_PATH` as their root source and gain no scope handling (see Constraints).
- Do NOT index content that no daemon indexes today — OctopusAgent, DataAssistant, and every vault outside the five path sets.
- Do NOT shrink the index itself: chunking, embedding-model swap, quantization. This work merges five indexes; it does not make one smaller.
- Do NOT add lazy or on-demand index loading. If the merged instance measures above the 4 GB ceiling, that is a separate spec with its own measurement.
- Do NOT add a scope parameter to `/health` or `/reindex`. Both are scopeless: `/health` is the readiness probe and `/reindex` rebuilds the whole union index.
- Do NOT add a configuration knob that disables scoping or restores union-wide answers. Fail-closed is invariant; a deployment that needs unscoped answers is a different product.
- Do NOT route the `docs/` and `commands/` markdown rewrites through this spec's prompts. They are direct edits handled outside the prompt pipeline (see Constraints).
- Do NOT regenerate the acceptance fixture. It is the oracle; a fixture captured after the change proves nothing.
- Do NOT add a per-scope memory or result-count limit. Visibility equivalence is the only scoping contract.

## Acceptance Criteria

**Container-executable (verified at prompt time inside the YOLO container):**

- [ ] `make precommit` exits 0 in the repo root — evidence: exit code.
- [ ] A scoped search leaks nothing and orders identically to a sub-index search. Two probes over one temp content root split into sibling directories A and B: (a) `GET /search?q=<term present in both>&scope=<A>&top_k=20` returns ≥1 result, and every `results[].path` is under A; (b) the same query against a second server built over only A's roots returns an equal `[r["path"] for r in results]` list, non-empty because the term is present in A. Evidence: assertion that (a) returns ≥1 result and on every returned path; list equality for (b).
- [ ] A scoped search is not truncated when the union's nearest documents are out of scope. Probe: temp content whose documents closest to the probe query live entirely in scope B; `scope=A` still returns exactly `top_k` results, all under A. Evidence: assertion on result count and on every returned path.
- [ ] Fail-closed on omitted scope and on an unknown scope name. Two probes: `GET /search?q=<term>` with no `scope` returns HTTP 400 with a body containing the token `MISSING_SCOPE`; `GET /search?q=<term>&scope=does-not-exist` returns HTTP 400 with a body containing the token `UNKNOWN_SCOPE`. Both probes assert that a path known to be in the union is absent from the response body. Evidence: HTTP status plus body token plus absence assertion.
- [ ] Duplicate detection returns no out-of-scope path. Probe: temp content holding two near-identical files in scope A and a near-identical file in scope B; `GET /duplicates?file=<one of the A files>&scope=A` returns a body with ≥1 entry in `duplicates[]`, whose every `path` is under A. Evidence: assertion that ≥1 duplicate is returned plus an assertion on every returned duplicate path.
- [ ] Content fetch refuses an out-of-scope path with the route's existing rejection code. Probe: `GET /content?path=<path under B>&scope=A` returns HTTP 400 with `error.code == "PATH_OUTSIDE_ROOTS"`. Evidence: HTTP status plus nested error-code equality.
- [ ] The MCP search tool is scoped. Probe: `search_related`, invoked with a scope-bearing request whose query has a known in-scope match, returns ≥1 path, all under that scope. Evidence: assertion that ≥1 path is returned plus an assertion on every returned path.
- [ ] Fail-closed on the HTTP-mounted MCP surface. Two probes: an MCP `tools/call` request for `search_related` POSTed to `/mcp` with no scope returns HTTP 400 with a body containing the token `MISSING_SCOPE`; the same request to `/mcp?scope=does-not-exist` returns HTTP 400 with a body containing the token `UNKNOWN_SCOPE`. Neither body contains a path known to be in the union. Evidence: HTTP status plus body token plus absence assertion, for both probes.
- [ ] Interleaved requests with different scopes do not contaminate each other. Probe: two requests for the same query, scopes A and B, held in flight at the same time with the handler paused between scope resolution and the index read, so the second request's scope resolves while the first is still in flight; each response contains only its own scope's paths and the two path lists are disjoint. Evidence: two in-scope assertions plus a disjointness check, against the held-open interleave — a scope stored on the shared indexer fails this probe deterministically.
- [ ] Scope definitions are validated at startup. Probe: start the server with a scope whose root list names a root that is not present in the built index — nonexistent, unreadable, or not among the declared roots; the process exits non-zero and writes a log line containing both the scope name and the offending root path. Evidence: exit code plus captured log line.
- [ ] `/health` reports the union root set and ignores a scope parameter. Probe: `GET /health` returns 200 with `status: "ok"`, `ready: true`, and a `paths` list equal to the union of all declared scope roots; `GET /health?scope=<A>` returns the same body rather than an error. Evidence: assertion on the response body for both probes.
- [ ] A committed replay tool compares a live server against the frozen fixture and exits non-zero on any difference. Two probes: (a) against a correctly scoped server, `uv run python scripts/replay-scope-fixture.py --fixture <path> --base-url <url>` prints one line per scope and exits 0; (b) against a deliberately perturbed copy of the fixture — one path deleted from one query in one scope — the same command exits non-zero and names the scope and query that differ. Evidence: both exit codes, plus the (a) per-scope stdout lines and the (b) failure line.
- [ ] `docs/design/per-vault-scoping.md` documents the scope parameter: its name, the five accepted scope names, the fail-closed default, and one worked `curl` example. Evidence: `grep -c 'scope' docs/design/per-vault-scoping.md` returns ≥1 and the file contains the parameter name, all five scope names, the fail-closed sentence, and a `curl` line. **Direct edit — not produced by this spec's prompts.**
- [ ] The stdio MCP transport and the `semantic-search` CLI keep `CONTENT_PATH` as their root source and gain no scope handling. Probe: (a) `uv run pytest tests/test_server.py` exits 0, and `grep -c 'CONTENT_PATHS' tests/test_server.py` returns ≥4 — the stdio tools are still driven through the patched content path; (b) `env -u CONTENT_PATH uv run semantic-search search kubernetes` exits 1 with stderr containing `CONTENT_PATH environment variable not set`; (c) a tool invoked over the stdio transport with no scope answers from `CONTENT_PATH` instead of refusing: with `CONTENT_PATH` pointed at a temp root, a probe that spawns `semantic-search-mcp serve` and sends a `tools/call` for `search_related` over stdio returns ≥1 result whose path resolves under that root, and the captured stdout contains neither `MISSING_SCOPE` nor `UNKNOWN_SCOPE`. Evidence: pytest exit code, the `CONTENT_PATHS` grep count, the CLI exit code plus its stderr line, and the stdio probe's returned paths plus its refusal-token absence check.

**Post-Deploy (verified on the host against the running daemon, after merge and `uv tool upgrade`):** the `deploy_check` below is behavioral on purpose, not a version token — a version comparison would pass against the pre-fix daemon and gate nothing, because `git describe --tags --abbrev=0` returns `v0.19.0` and the installed binary already reports `semantic-search-http v0.19.0` today, before any of this work exists. The probe asserts the new fail-closed contract instead: an unscoped `/search` returns 400, which only a post-fix daemon does.

- [ ] **Post-Deploy (Rung-2):** the deployed server replays the frozen fixture with an empty diff, per scope — evidence: `uv run python scripts/replay-scope-fixture.py --fixture "$HOME/Documents/Obsidian/Personal/80 Attachments/semantic-search-consolidation-baseline-2026-09-11.json" --base-url http://127.0.0.1:8321` exits 0 and prints a matching line for each of the five scopes; zero extra paths, zero missing paths, zero reorderings.
  - `deploy_check:` `curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8321/search?q=probe'`
  - `deploy_target:` `400`
- [ ] **Post-Deploy (Rung-2):** the deployed server refuses an unscoped request — evidence: `curl -s -o /tmp/noscope.json -w '%{http_code}' 'http://127.0.0.1:8321/search?q=semantic%20search'` prints `400`, and `grep -c MISSING_SCOPE /tmp/noscope.json` returns 1 while `grep -c 'Obsidian' /tmp/noscope.json` returns 0.
  - `deploy_check:` `curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8321/search?q=probe'`
  - `deploy_target:` `400`
- [ ] **Post-Deploy (Rung-2):** a real Claude Code session reaches the deployed server through an MCP URL that carries the scope and gets scoped results — evidence: an MCP server configured with the URL `http://127.0.0.1:8321/mcp?scope=personal` answers `search_related(query="semantic search embedding index", top_k=5)` from a live session with ≥1 result, and every returned `path` resolves under one of the `personal` scope's nine roots. If the client strips the query string, the named contingency is the `X-Semantic-Scope` request header: the header form then becomes the required transport and this AC is re-run against it.
  - `deploy_check:` `curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8321/search?q=probe'`
  - `deploy_target:` `400`
- [ ] **Post-Deploy (Rung-2):** exactly one search daemon survives — evidence: `launchctl list | grep semantic-search-http` prints exactly one line, and `lsof -i :8322`, `lsof -i :8323`, `lsof -i :8324`, `lsof -i :8325` each print nothing.
  - `deploy_check:` `curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8321/search?q=probe'`
  - `deploy_target:` `400`
- [ ] **Post-Deploy (Rung-2):** the surviving process measures ≤ 4 GB at ≥24 h uptime — evidence: `footprint -p <pid>` prints a `phys_footprint` value ≤ 4 GB, with the uptime and the value recorded against the 10.3 GB five-daemon baseline in the task's `# Progress`.
  - `deploy_check:` `curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8321/search?q=probe'`
  - `deploy_target:` `400`

**Scenario coverage**: NO new scenario. Scoping is reachable by integration tests over a real Starlette app and real FastMCP in-process (AC 2-11), which is where the pyramid puts it. The fixture replay needs the host's real vaults and a running daemon, so it lives in the operator rung as a one-shot migration oracle, not in the re-runnable scenario suite — the five-port baseline it compares against ceases to exist once the daemons are consolidated, and a scenario that can never be re-run is not a regression lock. Four existing scenarios start a server through `scenarios/helper/start-http-server.sh` and are updated in place as direct edits: `scenarios/002-http-rest-search-returns-json.md`, `scenarios/004-http-content-fetch-happy-path.md`, and `scenarios/005-http-content-fetch-error-responses.md` are invalidated by the scope contract, while `scenarios/006-http-reindex-concurrency.md` keeps every assertion it has — its requests are `/reindex` only, which stays scopeless — and changes only its Start step to pass the scope-map override; the helper's unscoped `/search` readiness probe breaks all four, not only the three that assert on scoped routes (see Constraints). `scenarios/001-mcp-stdio-no-stdout-pollution.md` (stdio) and `scenarios/003-cli-search-prints-results.md` (CLI) are unaffected because both entry points keep `CONTENT_PATH` and neither starts a server through the helper.

## Verification

### Container-executable (runs inside the YOLO container at prompt time)

```bash
make precommit                      # format + test + lint + typecheck — must exit 0
make test                           # pytest suite green
grep -rn 'MISSING_SCOPE\|UNKNOWN_SCOPE' src/    # both fail-closed tokens present
grep -rn 'scope' tests/ | wc -l     # scoped-behavior tests present (non-zero)
ls docs/design/per-vault-scoping.md scripts/replay-scope-fixture.py   # both artifacts exist
```

The container has no vault content mounted, so every scoping test builds its own temp roots. The fixture replay cannot run here.

### Operator-executable (runs on the host; spec-verification ladder)

```bash
# 1. EARLY GATE — run this BEFORE the merge, against a scratch server on a spare port.
#    Question: does the Claude Code MCP HTTP client preserve a query string on the configured URL?
#    Configure a scratch MCP server with url http://127.0.0.1:18321/mcp?scope=personal and call
#    search_related from a real session.
#      in-scope results returned  -> query-string transport holds; ship the design as specified
#      empty or union-wide results -> the client stripped the query string; implement the
#                                     X-Semantic-Scope header fallback and re-run this gate
#    This gate is load-bearing: the MCP surface is unreachable without a transport that carries scope.

# 2. Release freshness (must hold before any Rung-2 AC is trusted)
semantic-search-http --version                                  # must equal: git describe --tags --abbrev=0
curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8321/search?q=probe'   # must print 400

# 3. The acceptance oracle — fixture replay, per scope, against the deployed server
cd ~/Documents/workspaces/semantic-search
uv run python scripts/replay-scope-fixture.py \
  --fixture "$HOME/Documents/Obsidian/Personal/80 Attachments/semantic-search-consolidation-baseline-2026-09-11.json" \
  --base-url http://127.0.0.1:8321
# must exit 0 and print one matching line per scope: personal, brogrammers, boss, openbrain, starcitzen

# 4. Consolidation state
launchctl list | grep semantic-search-http                      # exactly one line
for p in 8322 8323 8324 8325; do lsof -i :$p; done              # all four print nothing

# 5. Footprint, at ≥24 h uptime (a cold reading is not comparable to the baseline)
footprint -p "$(pgrep -f 'semantic-search-http' | head -1)"     # phys_footprint must be ≤ 4 GB
```

The five accepted scope names are fixed by the fixture's port labels: `personal` (8321), `brogrammers` (8322), `boss` (8323), `openbrain` (8324), `starcitzen` (8325). The scope-to-roots mapping is fixed by the fixture's `ports[*].health.paths` — 15 distinct roots across the five scopes, with `Personal`, `Trading`, `Family`, `OpenClaw` and four `workspaces/*/docs` directories appearing in more than one scope.

## Desired Behavior

1. **A request declares its scope, and the server resolves it against a named allowlist — or refuses.** The scope map is a single committed configuration artifact: one entry per scope name mapping to an ordered list of absolute roots, the five entries reproducing the fixture's five path sets exactly. No prefix rule, no vault-name derivation, no per-request root list. A request to a content-serving route (`/search`, `/duplicates`, `/content`) or to any MCP tool over the HTTP-mounted surface that names no scope returns HTTP 400 with the token `MISSING_SCOPE`; one that names an unknown scope returns HTTP 400 with the token `UNKNOWN_SCOPE`. Neither ever returns a result set drawn from the full index. Scope resolution happens before the readiness gate, so an unscoped request gets 400 whether or not the index is ready.
2. **One index covers the union of all declared scope roots.** The server builds exactly one index per process, over the union of the scope map's roots (15 distinct roots), and every scope answers from it. `/health` reports that union as `paths` and the union's file count as `indexed_files`. The first-caller-wins behaviour where the index is created once for whichever path list arrived first no longer decides what is indexed.
3. **A scoped search returns the same result set as an index built over only that scope's roots** — same paths, same ordering — including when the union's nearest neighbours for the query all live outside the requested scope. The retrieval window is derived from `top_k` and bounded by the index size, so a scope never returns fewer than `top_k` results because out-of-scope documents crowded the window.
4. **Duplicate detection is scoped.** A file in scope A is compared only against documents in scope A; a near-identical file in scope B is not returned.
5. **Content fetch is scoped.** A path is served only when it resolves inside the requested scope's roots. A path that resolves inside the union but outside the requested scope is refused with the route's existing `PATH_OUTSIDE_ROOTS` rejection — the same shape the route already returns for a path outside all roots.
6. **The HTTP-mounted MCP surface carries scope per session.** The surface resolves the scope when the MCP session is established, from the transport that carried it, and all three tools — `search_related`, `check_duplicates`, `get_content` — answer within it. The scope of one session is never observable by another: no shared mutable scope state on the indexer, no module global, no post-construction mutation. The stdio MCP transport is not scoped and keeps reading `CONTENT_PATH` (see Constraints). Measured against the pinned `fastmcp 3.2.4`: a contextvar bound by an outer ASGI middleware is visible inside a sync tool body but carries the value bound on the request that *created* the session, not the one being answered — the session's receive-loop task snapshots its context at creation. `fastmcp`'s own `get_http_request()` *is* per-request, and is deliberately not used: resolution stays in the HTTP layer. Session semantics are accepted because the frozen transport is one MCP URL per client, so session scope equals request scope in the deployed shape.
7. **Startup validation of the scope map.** A scope definition source that is missing or empty and has no usable default, or a scope naming a root that is not present in the built index — nonexistent, unreadable, or not among the declared roots — stops the process with a non-zero exit and a log line naming the scope and the offending root. The server never starts in a state where a scope can only return an empty result set.
8. **A committed replay tool reproduces the acceptance comparison.** `scripts/replay-scope-fixture.py` reads the frozen fixture, replays every query against a live server per scope with the fixture's `top_k`, compares `results[].path` lists in order, prints one line per scope, and exits non-zero on any difference. It uses the standard library only.

## Constraints

- **Frozen external contract:** the scope is declared as the query parameter `?scope=<name>` on `/search`, `/duplicates`, and `/content`, and in the MCP config URL (`http://127.0.0.1:<port>/mcp?scope=<name>`). The parameter name and the five scope names are frozen — clients hardcode them.
- **Frozen fallback:** if the MCP client strips the query string, the contingency transport is the `X-Semantic-Scope` request header. No third transport is added.
- **Frozen fail-closed form:** HTTP 400 with the tokens `MISSING_SCOPE` / `UNKNOWN_SCOPE`. An empty result set is not an acceptable substitute — it is indistinguishable from a legitimate no-match, and the fixture replay would read it as missing paths.
- **Frozen oracle:** `80 Attachments/semantic-search-consolidation-baseline-2026-09-11.json` in the Personal vault. Zero tolerance: one extra path, one missing path, or one reordering fails. The fixture is never regenerated.
- **Frozen rejection code:** `/content` reuses `PATH_OUTSIDE_ROOTS` for an out-of-scope path. No new error code for that case.
- **Frozen response shape:** for a request that passes the scope check, `/search` and `/duplicates` bodies keep their existing field names and types. Scoping narrows the result set; it does not reshape the envelope.
- **Scope is injected, never parked:** the scope must reach the read path as a value passed in — never stored on the shared indexer, never a module global, never assigned after construction. On the REST surface it is resolved per request; on the HTTP-mounted MCP surface it is resolved per session (see DB 6). The existing per-request threshold assignment on the shared indexer, set by the duplicates handler for the duration of one request, is the pattern this constraint exists to prevent repeating.
- **Where the refusal lives.** The refusal tokens `MISSING_SCOPE` / `UNKNOWN_SCOPE` are raised in the HTTP layer (`src/semantic_search/http_server.py`), which serves the REST routes and mounts `/mcp` over the same FastMCP instance that `src/semantic_search/server.py` defines — the module the stdio entry point also imports. The tools in `server.py` read a scope already resolved for the request they are answering and never resolve or refuse one themselves; on the stdio transport, where no scope exists, they answer from `CONTENT_PATH` as today. The shared module therefore legitimately stays free of both refusal tokens, and whether the token strings appear in it is not an acceptance criterion — the stdio behavior is verified behaviorally (see the stdio/CLI acceptance criterion). The `/mcp` refusal applies to every request to the mount that carries no scope or an invalid scope, and it fires before the MCP handshake — the HTTP layer answers 400 ahead of the MCP protocol layer, whose own rejection of a sessionless non-`initialize` POST carries neither refusal token.
- **Scope definitions are committed to the repo**, not left only on the operator's host. The filename and syntax of that artifact are chosen at implementation time and documented in `docs/design/per-vault-scoping.md` in the same change — agent decides at impl time, because no external consumer reads the file (unlike the frozen `?scope=` parameter name).
- **Backwards compatibility — the boundary is entry-point-specific.** `CONTENT_PATH` feeds three entry points today and continues to feed two of them: the merged HTTP daemon (`semantic-search-http`) stops using it as its index root source and uses the committed scope map instead, while the stdio MCP transport (`semantic-search-mcp`) and the `semantic-search` CLI keep `CONTENT_PATH` unchanged and unscoped. Rationale: the consolidation targets the five HTTP daemons — that is the 10.3 GB — and stdio spawns a per-client process that is not part of that footprint, so leaving both untouched is the minimum blast radius.
- **Direct edits, not prompt work:** the surviving launchd plist and any client bound to a retired port; `README.md` — its MCP `url` example and its `CONTENT_PATH` example are both wrong once this ships, and `docs/dod.md` requires a README update when usage changes — together with the MCP sentence in the `## Unreleased` CHANGELOG entry; `commands/search.md` and `commands/research.md` (their REST fallback calls `/search` with no scope and will receive 400), `commands/configure.md`, `docs/launchd-service.md`, `docs/releasing-semantic-search.md`, and `docs/systemd-user-service.md`; and the scope-contract fallout in the scenario suite (next bullet). The fixture-schema contract currently lives only inside prompt 4 — it belongs in `docs/design/per-vault-scoping.md` so the next reader of the oracle need not re-derive it from the vault.
- **The four helper-driven scenarios stay active and are updated in place, not retired.** `scenarios/002-http-rest-search-returns-json.md` (`/search?q=...` unscoped now returns 400), `scenarios/004-http-content-fetch-happy-path.md` (`/content?path=...` unscoped now returns 400), and `scenarios/005-http-content-fetch-error-responses.md` (which expects `PATH_OUTSIDE_ROOTS` / `FILE_NOT_FOUND` but now gets `MISSING_SCOPE` first) keep `status: active` and gain the scope parameter. `scenarios/006-http-reindex-concurrency.md` also keeps `status: active` and keeps every assertion it has: its requests are `/reindex` only, and `/reindex` stays scopeless. Marking any of them `outdated` is rejected: 002/004/005 are the only real-socket coverage of the HTTP contract, and that socket-level guarantee is exactly what this change alters; 006 is the only wire-level coverage of the `409 REINDEX_IN_PROGRESS` response shape. `make precommit` does not run scenarios, so nothing catches this at prompt time — the release gate's scenario walk is where it surfaces. `scenarios/001-mcp-stdio-no-stdout-pollution.md` (stdio) and `scenarios/003-cli-search-prints-results.md` (CLI) are unaffected — both keep `CONTENT_PATH` and neither starts a server through the helper.
- **The scenario helper changes in two ways — both direct edits, made once prompt 1 has landed.** First, `scenarios/helper/start-http-server.sh` gains a scope-map override whose env var name follows prompt 1's artifact decision, and all four helper-driven scenarios pass it, so the test server indexes their own corpus instead of the real vaults. Without that override a scenario run indexes the real vaults from the committed map — the hazard 006 walks into directly, because it passes only `CONTENT_PATH` (a no-op for `semantic-search-http` after this change) and would embed the real vaults instead of its 1500-file corpus. Second, the helper's readiness probe stops being an unscoped `curl` against `/search?q=test&top_k=1`: post-change that request returns 400, `curl -f` exits 22, and the helper never reports ready and exits 1 after `READY_TIMEOUT`. That breaks every scenario that starts a server through the helper — 002, 004, 005 and 006 — not only the three whose assertions touch scoped routes. Required form: probe `GET /health` and require `"ready": true` in the body; `/health` stays scopeless (see Non-goals) and already answers 200 with `"ready": false` while the initial build is in flight, so a bare status-code check would report ready too early.
- **Unchanged:** the MCP server names and tool prefixes, the launchd plist label convention, port discovery, the `/mcp` mount path, the readiness gate and its `503` + `Retry-After: 5` response, the `/reindex` concurrency contract (`409 REINDEX_IN_PROGRESS`), and the `/content` error codes other than the scope case.
- **Repo DoD (`docs/dod.md`) applies:** docstrings on every function, type hints on every signature, no `print()` in library code, no broad `except Exception`. `make precommit` = format + test + lint + typecheck; mypy strict, ruff clean.
- **No new runtime dependencies.** The replay tool and the scope resolution use the standard library plus what is already declared.
- **`CHANGELOG.md` gets an `## Unreleased` entry** describing the scope parameter and the fail-closed default — this is a user-facing API change.

## Failure Modes

| Trigger | Expected behavior | Recovery | Detection | Reversibility | Concurrency |
|---------|-------------------|----------|-----------|---------------|-------------|
| Request names no scope | HTTP 400, token `MISSING_SCOPE`; no result set returned | Client adds `?scope=` to its URL or config; the next request returns 200 | Response body token; the request never reaches the index | Reversible — client-side config | n/a |
| Request names a scope not in the map | HTTP 400, token `UNKNOWN_SCOPE`; no result set returned | Client corrects the name; `docs/design/per-vault-scoping.md` lists the five; the next request returns 200 | Response body token | Reversible | n/a |
| Union's nearest neighbours for a query are all out of scope | Retrieval window widens until `top_k` in-scope results are found; the scope returns exactly `top_k` | None needed — deterministic | Fixture replay prints missing paths for that scope | n/a | n/a |
| A scope names a root that is not present in the built index | Process exits non-zero; log line names the scope and the root | Operator fixes the scope map, reloads the service | Non-zero exit code; `launchctl list` shows the job not running | Reversible — config-only | n/a |
| Scope definition source missing or empty and no usable default | Process refuses to start; never indexes nothing and never serves the union unscoped | Operator restores the artifact or the default, reloads | Non-zero exit code plus log line naming the path tried | Reversible | n/a |
| Two requests with different scopes in flight simultaneously | Each sees only its own scope; scopes never cross | None needed | The interleaved-request test; a leak would surface as an out-of-scope path in one response | n/a | Real — the indexer is shared across threads; scope is per-request state |
| MCP client strips the query string from the configured URL | Detected before the merge by the early gate; the `X-Semantic-Scope` header becomes the transport and the gate is re-run | Re-point clients at the header form; the next request returns 200 | The early gate in Verification rung 2 returns empty or union-wide results | Reversible before merge; a live leak after merge | n/a |
| Index still building when a scoped request arrives | Existing `503` + `Retry-After: 5` for a request that names a valid scope; `400` for one that names none or an unknown one | Client retries after the build; the retry returns 200 once `/health` reports `ready: true` | Response status; the existing readiness log lines | Reversible | Real — the build runs in a background task while requests are served |
| A retired plist is still loaded after consolidation | Two daemons contend for the port range; ports 8322-8325 must refuse connections | Boot the plist out, archive it under `~/Library/LaunchAgents/disabled/` (launchd does not scan subdirectories, so it cannot be re-loaded) | `lsof -i :8322` through `:8325` print something | Reversible — plists are archived, not deleted | Real — two processes on one port fail to bind |
| Merged instance measures above 4 GB at ≥24 h uptime | The 4 GB ceiling is missed; the merge is not the memory win it was scoped to be | Separate spec for index-load strategy (see Non-goals); the measurement is recorded before that spec is written | `footprint -p` at ≥24 h uptime against the 10.3 GB baseline | Reversible — the four plists are archived and can be restored | n/a |
| Footprint measured before 24 h uptime | The reading is a cold reading and is not comparable to the baseline | Re-measure after the window; record uptime next to the value | The recorded uptime alongside the reading | n/a | n/a |
| The single daemon restarts | All five clients lose search at once, where previously one vault's client did | `launchd` restarts the job; clients retry against the readiness gate | `/health` reports `ready: false` while the restarted process rebuilds the index | Reversible | Real — this is the availability cost the consolidation accepts |

## Security / Abuse Cases

- **Attacker controls:** the `scope` value on any route reachable over HTTP, the `path` and `file` values on `/content` and `/duplicates`, and the MCP request that carries the scope. All four arrive from the network on a server bound to `127.0.0.1` by default.
- **Trust boundary crossed:** the HTTP/MCP boundary into the filesystem. Scoping must be exactly as narrow as today's five-daemon layout — never wider. A filter that fails open is a cross-vault disclosure, and it is the single highest-consequence bug this change can introduce.
- **Scope value validation:** a scope name is looked up in the declared map and never used as a filesystem path, never interpolated into a path, and never passed to a shell. An unknown name produces the `UNKNOWN_SCOPE` refusal, not a filesystem lookup.
- **Path validation under scope:** the out-of-scope check resolves the requested path (following symlinks) before comparing it against the requested scope's roots — the same resolve-then-compare discipline the existing out-of-roots check uses. A string-prefix comparison on the unresolved path is bypassable via `..` or a symlink and is not acceptable.
- **Result filtering:** filtering happens on the path returned by the index, after resolution, so a symlink inside a scope that points into another scope cannot smuggle an out-of-scope document into a result set.
- **Resource exhaustion via the retrieval window:** the window is derived from `top_k`, which the client already controls and which is already capped by the request, and is bounded by the index size. A request cannot force an unbounded scan.
- **Denial of service via scope churn:** an attacker cannot cause per-scope index construction — there is one index, built once at startup. Unknown scope names cost one map lookup.
- **Logging:** the scope name is attacker-influenced and is logged as data, never as a format string or a shell fragment. Log lines naming an offending root on startup come from the committed map, not from the request.
- **Information disclosure:** the scoped surface is the same surface five separate daemons expose today. No scope reaches a root outside the five path sets, and no scope gains a root.

## Suggested Decomposition

Prompts are generated in this order — each row is one prompt with a clear scope. This is a multi-layer spec (scope resolution, index construction, three read paths, MCP transport), so the decomposition is mandatory rather than optional.

| # | Prompt focus | Covers DBs | Covers ACs | Depends on |
|---|---|---|---|---|
| 1 | Scope map artifact, union index root set, fail-closed resolution, startup validation; stdio and CLI stay on `CONTENT_PATH` | 1, 2, 7 | 1, 4, 10, 11, 14 | — |
| 2 | Scoped read paths: search (bounded over-fetch), duplicates, content | 3, 4, 5 | 1, 2, 3, 5, 6 | prompt 1 |
| 3 | HTTP-mounted MCP surface carries scope per session, including the fail-closed refusal; no cross-session leakage | 6 | 1, 7, 8, 9 | prompts 1, 2 |
| 4 | Fixture replay tool | 8 | 1, 12 | prompts 1, 2 |
| 5 (contingency) | `X-Semantic-Scope` request-header transport for the HTTP-mounted MCP surface — generated ONLY if the early gate fails | 6 | 7, 8 | prompt 3 + the early-gate result |
| — | `docs/design/per-vault-scoping.md` (including the fixture-schema contract, which otherwise survives only inside prompt 4); `README.md` and the `## Unreleased` CHANGELOG MCP sentence; scenarios 002/004/005/006 + `scenarios/helper/start-http-server.sh` updated for the scope contract | — | 13 | **direct edits, no prompt** |
| — | Host consolidation, client repointing, fixture replay, footprint measurement | — | 15-19 | **operator work after merge, no prompt** |

Rationale: prompt 1 establishes the scope map and the one-index-per-process invariant that every later prompt reads from; prompts 2 and 3 are the two surfaces that must both be scoped before the daemons can be consolidated, and prompt 2 is the one that carries the leak risk; prompt 3 also carries the MCP-side fail-closed proof (AC 7 and 8) — the refusal tokens live in the HTTP layer, but only the HTTP-mounted MCP surface shows that an unscoped MCP call is refused rather than answered from the full index; prompt 4 is a verification tool that only makes sense once a scoped server answers. The Post-Deploy ACs are deliberately not covered by any prompt — they are the operator's merge gate, and a prompt that claimed them would be claiming work no container can perform. Prompt 1 also owns AC 14: it is the prompt that moves the HTTP daemon's index root source, and the stdio transport and the CLI are exactly what a careless refactor of the shared composition root breaks — the tools it must leave alone are defined in the very module the HTTP server mounts at `/mcp`. Row 5 is conditional — the early gate in Verification decides whether the query-string transport holds, and if it does the row is never generated; without it, a failed gate would leave the MCP surface's only remaining transport unassigned while the merge waits on it. In that case the operator's client-repointing step covers the header form.

**Why this is one spec rather than two.** The scope check fires on the arithmetic (8 desired behaviors × 19 acceptance criteria = 152, and four code layers). Every candidate split was rejected on evidence: the only clean seam is code-versus-host-consolidation, and the host half produces no code — it is `launchctl` and plist work already tracked as Success Criteria in the originating task, with the markdown fallout handled as direct edits. Splitting the code by surface would strand the load-bearing fixture-replay AC away from the code it proves, because the replay is only possible once the union index and all scoped surfaces exist. A split that separates the acceptance evidence from the implementation is worse than a large spec.

## Do-Nothing Option

Do nothing and the five daemons keep running as they are: 10.3 GB of footprint, on a laptop whose remaining headroom is the binding constraint on the voice stack, the Claude Code sessions, and the trading tooling. The layout is stable and every client works today — this is not a broken system. But the reclaim is the largest software lever left after the OrbStack VM, and it is unavailable for exactly one reason: no request can ask for a narrower view than the process's startup path set. Without that capability the merge cannot happen at all, because a merged daemon hands every client the union of all five vault sets. The cost of doing nothing is therefore not "slightly worse memory" — it is that the 6 GB stays spent, indefinitely, and every future vault added to any client's view grows all five indexes again.
