# Per-vault scoping

Reference for the scope contract introduced by spec `004-per-vault-scoping`. The
spec is the behavioral contract; this document is the implementation context — the
parameter names, the artifact shapes, and the measured facts behind the design.

## Why scopes exist

Five launchd daemons each built their own embedding index over overlapping content:
**10.3 GB** combined `phys_footprint` (measured 2026-09-11) against ~490 MB RSS. The
embedding model is only ~90 MB, so almost all of it was five separately-built indexes
over the same material. One daemon holding one union index, with a per-request scope
filter, reclaims ≥6 GB — and the scope filter is what keeps each client seeing exactly
what it sees today.

## The scope contract

### Declaring a scope

`?scope=<name>` on the scoped REST routes — `/search`, `/duplicates`, `/content` — and
in the MCP client config URL:

```json
{"semantic-search": {"type": "http", "url": "http://127.0.0.1:8321/mcp?scope=personal"}}
```

`/health` and `/reindex` stay **scopeless**: the first reports the union root set, the
second rebuilds the whole index.

### The five scope names

`personal` · `brogrammers` · `boss` · `openbrain` · `starcitzen`

Each maps to an ordered list of absolute roots in `scopes.yaml` at the repo root. The
committed file reproduces the five `CONTENT_PATH` sets the daemons ran with, so the
scoped view is identical to the per-port view it replaces.

### Fail-closed

An omitted or unknown scope returns **HTTP 400** with `MISSING_SCOPE` or
`UNKNOWN_SCOPE` — never the union index. An empty result set was rejected as the
failure mode: it is indistinguishable from a legitimate no-match, so a filter that
silently degraded to "no filter" would look like a working query returning nothing.

### Worked example

```bash
curl -s "http://127.0.0.1:8321/search?q=dark-factory%20spec&top_k=3&scope=personal"
curl -s "http://127.0.0.1:8321/search?q=dark-factory%20spec&top_k=3&scope=brogrammers"
# same query, different path sets — that difference is the whole contract

curl -s "http://127.0.0.1:8321/search?q=dark-factory%20spec&top_k=3"
# {"error": ... "MISSING_SCOPE"} with HTTP 400
```

## The scope map artifact

`scopes.yaml`, named by the `SEMANTIC_SCOPE_MAP` environment variable when it is set, and
otherwise read from the user config default `~/.config/semantic-search/config.yaml`:

```yaml
scopes:
  personal:
    - /Users/bborbe/Documents/Obsidian/Personal
    - /Users/bborbe/Documents/workspaces/coding/docs
  starcitzen:
    - /Users/bborbe/Documents/Obsidian/StarCitizen
```

`src/semantic_search/scopes.py` owns `load_scope_map`, `validate_scope_map`,
`resolve_scope`, and `ScopeMap.union_roots`. The map is validated at startup: a scope
naming a root that is not present in the built index exits non-zero with a log line
naming both the scope and the offending root.

## Why an allowlist, not a path prefix

The obvious design — scope by vault directory prefix — cannot reproduce the existing
views. The live plists show:

| scope | roots |
|---|---|
| `personal` | 5 vaults + 4 workspace `docs` dirs (9) |
| `brogrammers` | Brogrammers + `{dark-factory,sm-octopus,coding}/docs` (4) |
| `boss` | Personal's 9 paths + `Boss` (9) |
| `openbrain` | OpenBrain + Brogrammers + Personal + 3 `docs` dirs (6) |
| `starcitzen` | StarCitizen (1) |

Three of five index more than one vault, and four index more than one root. `boss`
is a strict superset of `personal` plus one vault; `openbrain` spans three vaults. No
prefix rule produces those sets, so scope is a named allowlist of roots.

## The union index

`factory.declare_index_roots(roots)` makes the union win regardless of which caller
creates the indexer first. This matters because `factory.create_indexer` was a
first-caller-wins singleton: without the declaration, a FastMCP tool call racing the
background build would decide what gets indexed. `main()` declares
`[str(p) for p in scope_map.union_roots]` before building the app; `build_app` must
**not** declare, because every HTTP test calls it and the declaration is process-global
state that outlives the test that set it.

## Index load is eager — measured, not assumed

The union index is built **eagerly** at startup: `main()` declares the scope map's
`union_roots` and the indexer loads or rebuilds the whole set before the app serves a
request. A lazy alternative — loading a scope's roots on first request — was considered
and **rejected on measurement** rather than on principle.

Measured on the deployed launchd service (`v0.22.0`, 15 roots / ~19,600 files, macOS
arm64), across a full `/reindex`:

| Reading | `phys_footprint` | RSS |
|---|---|---|
| Settled, after the reindex | **1,555 MB** | 243 MB |
| Peak, during the reindex | **1,787 MB** | 333 MB |

The five instances this replaces measured **10.3 GB** combined. Eager load therefore
lands at roughly a seventh of that, and far below the 4 GB ceiling the consolidation
set for itself. Lazy loading would add a per-scope load path, a second cache-key story
and a cold-request latency spike, to save memory that is not scarce.

Two properties make eager the right shape here specifically:

- **The scope filter is a read-path concern, not a load-path one.** One index over
  `union_roots`, narrowed per request, is what makes a scope a *view* rather than a
  separate corpus. A per-scope load would reintroduce exactly the duplicated-index
  cost the consolidation removed.
- **`/reindex` is union-wide and scopeless by design** — it rebuilds the whole index,
  so there is no partial-load state for a lazy path to keep consistent.

This is a single-host observation, not a benchmark. It is recorded because the decision
is otherwise re-litigable from first principles, and the answer here is "measured, and
the margin is large" — which is a different claim from "lazy would be wrong".

## Scope is injected, never parked

The scope reaches the read paths as a value passed in — never stored on the shared
indexer, never a module global, never assigned after construction. The existing
per-request threshold assignment on the shared indexer, set by the duplicates handler
for the duration of one request, is the pattern this rule exists to prevent repeating:
one request's scope leaking into the next is exactly the bug the feature introduces.

## MCP is session-scoped, and that is deliberate

The REST surface resolves scope per request. The HTTP-mounted MCP surface resolves it
**per session**, from the URL that established the session.

Measured against the pinned `fastmcp 3.2.4`: a contextvar bound by an outer ASGI
middleware **is** visible inside a sync tool body, but it carries the value bound on the
request that *created* the session — the session's receive-loop task snapshots its
context at creation. So a `tools/call` carrying `?scope=b` on a session initialised with
`?scope=a` is answered from `a`, silently.

This is accepted rather than fixed: the deployed transport is one MCP URL per client, so
session scope equals request scope in the shape that ships. `fastmcp`'s own
`get_http_request()` *is* genuinely per-request and is deliberately **not** used —
resolution stays in the HTTP layer, and the tools read a scope already resolved for
them. A probe pins the semantics so it stays a decision rather than an accident.

## The entry-point boundary

`CONTENT_PATH` feeds three entry points and continues to feed two of them:

| entry point | index roots |
|---|---|
| `semantic-search-http` | the scope map |
| `semantic-search-mcp` (stdio) | `CONTENT_PATH`, unscoped |
| `semantic-search` (CLI) | `CONTENT_PATH`, unscoped |

Only the HTTP daemons are being consolidated — that is the 10.3 GB — and stdio spawns a
per-client process outside that footprint, so leaving both untouched is the minimum
blast radius. The refusal tokens live in `http_server.py`; `server.py`'s tools read an
already-resolved scope and never resolve or refuse one themselves, which keeps the
shared module free of scope state.

## The acceptance oracle

The migration is proved against a frozen fixture captured **before** any code change:

`~/Documents/Obsidian/Personal/80 Attachments/semantic-search-consolidation-baseline-2026-09-11.json`

Schema: `captured_at`, `purpose`, `top_k`, `ports` (keyed `8321`–`8325`, each with
`label` and `health`), `queries` (14), and `search` — a map of query → port → block,
where each block is `{query, results, count}` and each result is `{path, score}`.

Replaying it per scope must produce a **zero diff**: same path set, same ordering. One
extra path, one missing path, or a reordering fails. Every query returns 20 results
(the `top_k` cap), so the signal is path set and ordering, not count. The fixture is
read-only — the replay tool never rewrites its oracle — and it cannot be regenerated
after the merge, because the five-port baseline it compares against ceases to exist.
