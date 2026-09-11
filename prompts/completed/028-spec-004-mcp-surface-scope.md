---
status: completed
spec: [004-per-vault-scoping]
summary: 'Carried the scope onto the HTTP-mounted MCP surface: a pure ASGI middleware in http_server.py refuses unscoped/unknown /mcp requests with HTTP 400 (MISSING_SCOPE/UNKNOWN_SCOPE) before the MCP protocol layer and binds the resolved roots as context-scoped state (scopes.py context variable + HTTP-transport marker) that the server.py tools read session-scoped, with a reset seam in conftest.py, a rewritten TestMcpMount, and 8 new real-socket probes in tests/test_mcp_scoping.py; the interleave probe was proven load-bearing — it failed red under a temporary store-transport mutation (the first response carried the second scope''s paths) and went green again after restoring the context-variable transport. make precommit exits 0.'
execution_id: semantic-search-exec-028-spec-004-mcp-surface-scope
dark-factory-version: dev
created: "2026-09-11T11:27:52Z"
queued: "2026-09-11T12:32:41Z"
started: "2026-09-11T13:08:51Z"
completed: "2026-09-11T13:20:59Z"
---

# Carry the scope on the HTTP-mounted MCP surface

<summary>
- A client's MCP URL names its scope, and all three MCP tools answer inside that scope
- The scope a session answers from is fixed when that session is established — the deployed shape is one MCP URL per client, so this is the same thing as scoping every request that client makes
- A request to the MCP endpoint which names no scope is refused with HTTP 400 and `MISSING_SCOPE`, before the MCP handshake ever starts
- A request which names an unknown scope is refused with HTTP 400 and `UNKNOWN_SCOPE`, also before the handshake
- The refusal never carries a result set, so an unscoped MCP call can never be answered from the full index
- Two MCP clients with different scopes, in flight at the same time, each see only their own scope; one client's scope is never observable by another
- The tools themselves never resolve or refuse a scope and never look at the HTTP request — they read the scope the HTTP layer bound for their session
- A tool invocation that has no resolved scope while the process is serving the HTTP endpoint fails loudly instead of falling back to the full index
- The stdio MCP transport is untouched: with no scope in play it still answers from its content path and never refuses
- Tests can clear the process-wide HTTP marker between cases, so the stdio tests keep seeing the stdio transport inside the same test run
</summary>

<objective>
Make the MCP-over-HTTP surface carry a scope — the third and last surface that must be scoped before the five daemons can be merged into one. The refusal tokens stay in the HTTP layer, the tools stay free of both tokens, and the scope travels as context-scoped state the HTTP layer binds, never as anything stored on the shared indexer. The scope a tool answers from is the one bound when its MCP session was established; that is deliberate, and the frozen transport (one MCP URL per client) makes session scope and request scope the same thing in the deployed shape.
</objective>

<context>
Read `CLAUDE.md` for project conventions and `docs/dod.md` for the Definition of Done.

Prompts 1 and 2 have already landed. Their contract, which this prompt builds on:

- `src/semantic_search/scopes.py` — `ScopeMap`, `union_roots`, `load_scope_map`, `validate_scope_map`, `resolve_scope` (raising `MissingScopeError` / `UnknownScopeError`), `SCOPE_MAP_ENV`. This module contains neither `MISSING_SCOPE` nor `UNKNOWN_SCOPE`.
- `src/semantic_search/http_server.py` — `build_app(scope_map: ScopeMap) -> Starlette`, module constants for both refusal tokens, one helper mapping `MissingScopeError` / `UnknownScopeError` to the 400 refusal body, the scope gate on the three REST content routes, and `/health` / `/reindex` scopeless.
- `src/semantic_search/factory.py` — `declare_index_roots(roots)`, called from `main()` only and never from `build_app` (prompt 1 owns that edit), so the HTTP process indexes the union root set whichever caller creates the indexer first; and `reset()`, which clears the indexer, the watcher, and the declared root set. Use `factory.reset()` wherever this prompt resets the singleton — never assign `factory._indexer` / `factory._watcher` directly.
- `src/semantic_search/indexer.py` — `search(query, top_k=5, roots=None)`, `find_duplicates(file_path, roots=None)`, `get_content(path, snippet=False, query=None, context_lines=20, roots=None)`. `roots=None` means "every indexed root".

Read these files before making changes:

- `src/semantic_search/http_server.py` — `build_app` builds `mcp_app = mcp.http_app(path="/mcp")` and mounts it with `Mount("/", app=mcp_app)`, inside a `combined_lifespan` that also launches the background index build. The MCP mount is therefore reached at the ASGI path `/mcp` (and any sub-path of it). `build_app` is called directly by the tests as well as by `main()`, so anything it does at construction happens in the test process too.
- `src/semantic_search/server.py` — the FastMCP instance `mcp` and the three tools `search_related(query, top_k=5)`, `check_duplicates(file_path)`, `get_content(path, snippet=False, query=None, context_lines=20)`, each currently `create_indexer(CONTENT_PATHS)` followed by an indexer call. This module is imported by the stdio entry point as well, so nothing in it may assume an HTTP request exists.
- `tests/test_http_server.py` — `TestMcpMount::test_mcp_endpoint_returns_400_for_bare_get_not_404`, which asserts a bare `GET /mcp` returns 400 or 406 and exists to prove the route is mounted rather than 404. After this prompt an unscoped `GET /mcp` is answered by the new guard, so that test must carry a scope to keep proving what it was written to prove.
- `tests/test_server.py` — the stdio tool tests, which call the tools directly with `CONTENT_PATHS` patched and the factory singleton reset. The tools must keep working exactly like that.
- `tests/conftest.py` — `_isolated_indexer_cache` (autouse) redirects `user_cache_dir` into a per-test tmp dir. Prompt 2 adds the deterministic fake `SentenceTransformer` fixture `deterministic_sentence_transformer` to this same file; it is the shared fake this prompt's probes use. **Request it by name** in the test signature — a pytest fixture is resolved by name, and a literal `from tests.conftest import deterministic_sentence_transformer` yields the decorated fixture function itself (calling it raises). Do not define or cross-import a second copy. It returns a **class**, so it is passed straight to `patch("semantic_search.indexer.SentenceTransformer", deterministic_sentence_transformer)`.
- `tests/test_scoping.py` (from prompt 2) — the temp-root scope-map fixture and the app-driving pattern; reuse both here.

Pattern guides (in-container paths):

- `/home/node/.claude/plugins/marketplaces/coding/docs/python-architecture-patterns.md` — constructor injection and the composition root.
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-ioc-guide.md` — "Avoid Global Singleton Dependencies" (why the scope is context-bound state, not a module attribute) and "Never Inject Framework Objects into Services".
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-logging-guide.md` — log-line conventions; the scope name is attacker-influenced and is logged as data, never as a format string or a shell fragment.

**Verify the installed framework API before writing code.** The locked version is `fastmcp 3.2.4` (from `uv.lock`; `pyproject.toml` declares only `>=3.2.0`, so read the lockfile, not the constraint). Locate and read the installed package — `uv run python -c "import fastmcp, pathlib; print(pathlib.Path(fastmcp.__file__).parent)"` (typically `.venv/lib/python*/site-packages/fastmcp/`) — for two things: (a) whether a value bound by an outer ASGI middleware's context variable is visible inside a sync tool body, and *which request's* value it carries; (b) how a client issues a `tools/call` — the handshake, the session header, the request shape. Do not write either from memory. **The tools must not read the HTTP request themselves.** `fastmcp.server.dependencies.get_http_request()` exists in this version and is genuinely per-request — which is exactly why it is out of bounds here: resolving the scope inside a tool from the live request is the resolution-in-the-tool this design forbids. Resolution stays in the HTTP layer; `server.py` only reads the value that layer bound.

**Documentation is not prompt work.** `README.md` — its MCP `url` example and its `CONTENT_PATH` example — and the CHANGELOG's MCP sentence are owned by the spec's direct-edit list, not by this prompt: after this change the client-facing MCP URL must carry the scope and `semantic-search-http` no longer takes `CONTENT_PATH` as its index root source, so both README examples are stale, and `docs/dod.md` requires a README update on a usage change. Per `/home/node/.claude/plugins/marketplaces/dark-factory/docs/choosing-a-flow.md`, markdown docs are direct edits. Do not edit `README.md` or `CHANGELOG.md` here; the constraint list below already keeps `docs/` and `commands/` out of scope.
</context>

<requirements>

1. **Add the scope transport to `src/semantic_search/scopes.py`** — a context variable plus its accessors, a marker for the HTTP-serving process, and a test seam for that marker:

   ```python
   def set_request_roots(roots: tuple[Path, ...]) -> contextvars.Token[tuple[Path, ...] | None]:
       """Bind the roots resolved for the request being served; return the reset token."""

   def reset_request_roots(token: contextvars.Token[tuple[Path, ...] | None]) -> None:
       """Undo a previous `set_request_roots`."""

   def current_request_roots() -> tuple[Path, ...] | None:
       """Return the roots bound for the MCP session being served, or None when nothing is bound."""

   def mark_http_transport() -> None:
       """Mark this process as serving the HTTP mount (called once, at app construction)."""

   def http_transport() -> bool:
       """Return True when this process serves the HTTP mount."""

   def reset_http_transport() -> None:
       """Clear the HTTP-transport marker. Test seam: pytest shares one process across modules."""
   ```

   A context variable is the correct primitive precisely because it is per-task: two MCP sessions in flight at once each see their own value, and the value a tool body observes is the one bound on the request that established its session (see requirement 3). Do not use a module attribute, a thread local, or an attribute on a shared object. These functions carry no refusal token and no scope *name* — only resolved roots.

   **The HTTP marker is process-global and is never cleared in production** — a process either serves the HTTP mount or it does not, and `build_app` sets it once at construction. In the test process it must be cleared, because pytest collects every module into a single process: `tests/test_http_server.py` and the new `tests/test_mcp_scoping.py` both construct an app (setting the marker) and both sort before `tests/test_server.py`, whose direct tool calls must keep seeing the stdio transport. So `tests/conftest.py` gains an autouse fixture that calls `reset_http_transport()` before each test — that seam exists for no other reason. Do not resolve this by weakening the guard in requirement 3 or by dropping `mark_http_transport()` from `build_app`: the fail-closed unit test in requirement 4 calls `mark_http_transport()` itself, after the autouse reset has run.

2. **Guard the MCP mount in `src/semantic_search/http_server.py`** with a pure ASGI middleware (not `BaseHTTPMiddleware`, whose request-scope handling is a needless variable here), applied inside `build_app` so it wraps only the app it builds:

   - It acts on requests whose ASGI path is `/mcp` or a sub-path of it. Every other path passes through untouched — `/health`, `/search`, `/duplicates`, `/content`, and `/reindex` are served by the REST routes and must not be affected.
   - **Non-HTTP scopes pass through untouched as well.** Starlette's lifespan travels through the same middleware stack as an ASGI scope whose `"type"` is `"lifespan"`, with no `"path"` key — reading `scope["path"]` unconditionally raises `KeyError` and crashes app startup. Branch on `scope["type"]` first and forward every scope that is not `"http"` to the wrapped app unchanged.
   - For an MCP request it resolves `?scope=` with `resolve_scope` and the injected scope map:
     - no scope named → HTTP 400, body containing `MISSING_SCOPE`;
     - unknown scope name → HTTP 400, body containing `UNKNOWN_SCOPE`;
     - valid scope → bind the resolved roots with `set_request_roots`, call the downstream app, and reset the token in a `finally`.
   - The refusal is produced **before** the MCP protocol layer runs, so a request with no scope is answered 400 rather than by the MCP transport's own sessionless-request rejection — which carries neither token.
   - It applies to every method and every request to the mount, including the handshake: the client's configured URL is `http://127.0.0.1:<port>/mcp?scope=<name>`, so the handshake carries the scope too. That initialize request is the one that decides what every tool call on that session answers from (requirement 3).
   - `build_app` calls `mark_http_transport()` once at construction. Keep `build_app(scope_map)`'s signature.
   - `build_app` does **not** declare the index root set: `declare_index_roots` is called from `main()` only (prompt 1 owns that edit). Leave it that way.

3. **Make the tools read the resolved scope** in `src/semantic_search/server.py`:

   - Add one private helper used by all three tools, e.g.

     ```python
     def _request_roots() -> tuple[Path, ...] | None:
         """Return the roots the HTTP layer bound for this MCP session, or None on the stdio transport."""
     ```

     It returns `current_request_roots()` when there is one — **the value bound when this session was established, not the value of the request currently being answered** (see the semantics note below). When there is none **and** `http_transport()` is true, it raises `RuntimeError` — the same exception type `http_server.get_indexer()` raises for its not-initialized state — with a message naming the missing scope binding. The HTTP mount is unreachable without a scope, so a tool that finds itself without one must fail loudly rather than answer from the union index. When there is none and the process is the stdio transport, it returns `None` and the tools behave exactly as today.
   - Each tool passes that value as the trailing argument of its indexer call: `indexer.search(query, top_k, _request_roots())`, `indexer.find_duplicates(file_path, _request_roots())`, `indexer.get_content(path, snippet, query, context_lines, _request_roots())`.
   - The tools **never resolve a scope name and never produce a refusal token**, and they never read the HTTP request — no `get_http_request()`, no request object, no header parsing in this module. `MISSING_SCOPE` and `UNKNOWN_SCOPE` must not appear in `server.py` at all; the `RuntimeError` in `_request_roots` is an internal invariant failure, not a scope refusal, and it is not part of the frozen contract.
   - `CONTENT_PATHS` and the three `create_indexer(CONTENT_PATHS)` calls stay: on the stdio transport they are still the root source, and in the HTTP process the factory's declared union roots win (prompt 1).
   - `server.py`'s tool signatures must not change — clients call them by name with their existing arguments.

   **The semantics are session-scoped, and that is deliberate.** Measured against the locked `fastmcp 3.2.4` (real uvicorn, real ASGI middleware, hand-rolled MCP handshake over a socket): a context variable set by an outer ASGI middleware **is** visible inside the sync tool body, but it carries the value bound on the request that **created the MCP session** — the initialize POST — not the value bound on the request being answered, because the session's receive-loop task snapshots the context at session creation. A `tools/call` carrying `?scope=B` on a session initialized with `?scope=A` is therefore answered from **A**, silently. That outcome is accepted, not accidental: the frozen transport is one MCP URL per client, so the scope a client binds at session creation is the scope every request it makes carries. Re-binding the scope mid-session is out of scope, and resolving it inside the tool from the live HTTP request is the design this prompt forbids. Requirement 4 pins the session-scoped outcome with a probe.

4. **Create `tests/test_mcp_scoping.py`.** These probes need a real socket and the real MCP layer, so start the app with `uvicorn` in a background thread on a free port (build the app with `build_app(scope_map)` from a temp scope map over temp content roots, wait until the server is up, shut it down via `server.should_exit = True` and join the thread), and drive it with a real MCP client. Read the installed `fastmcp` for the client API of the locked version; where the client helper cannot express the probe — the session probe below needs one session header reused across two different query strings, and one `Client` holds one URL — speak the JSON-RPC handshake by hand with `httpx` (`initialize`, capture the `mcp-session-id` response header, `notifications/initialized`, then `tools/call`). Use the deterministic fake `SentenceTransformer` fixture `deterministic_sentence_transformer` from `tests/conftest.py` (prompt 2's; **request it by name** in the test signature — do not define or cross-import a second copy; it returns a **class**, passed straight to `patch("semantic_search.indexer.SentenceTransformer", deterministic_sentence_transformer)`) so the corpus geometry is controllable. Between tests, reset **all four** pieces of process-wide state — `semantic_search.factory.reset()`, `http_server._indexer = None`, `http_server._indexer_ready = asyncio.Event()`, `http_server._indexer_error = None` — exactly the quartet `tests/test_scoping.py` uses (prompt 2, requirement 7). **`factory.reset()` alone is insufficient**: `_build_indexer_in_background` early-returns while the module-level `_indexer_ready` event is set, and earlier modules in the same pytest process leave it set — `tests/test_http_server.py` constructs `build_app()` unpatched (e.g. `test_search_missing_query_returns_400`, and `TestMcpMount`, which this prompt edits), and the build task's `finally: _indexer_ready.set()` runs even when the lifespan cancels that task on exit; a stale `_indexer` or `_indexer_error` survives the same way. Measured on this tree after that module: `_indexer_ready.is_set()` is `True` while `_indexer` is still `None`. Without the full quartet this app's lifespan skips its own build, `/health` answers `{"status": "indexing", "ready": false}` forever, and the readiness poll never sees `"ready": true`. Nothing needs to declare index roots here: the app's lifespan builds the indexer over the scope map's union roots (prompt 1), and the tools' `create_indexer(CONTENT_PATHS)` returns that same instance.

   **No probe may hang.** Give every wait an explicit timeout: the readiness poll (poll `GET /health` until the body reports `"ready": true`, with a deadline of a few seconds and a clear assertion failure when it is not reached — never drive a tool before the background index build has finished), every MCP client call (`Client(..., timeout=...)`, plus `httpx` client timeouts on the hand-rolled path), and the `threading.Event` hold (`event.wait(timeout=...)`). A regression must fail red, not stall the suite until the runner's own timeout.

   - **The MCP search tool is scoped** (AC 7): a `tools/call` for `search_related` on a session carrying `?scope=<a>`, whose query has a known in-scope match, returns at least one result and every returned `path` resolves under that scope's roots.
   - **Fail-closed on the HTTP-mounted MCP surface** (AC 8): a `tools/call` POSTed to `/mcp` with no scope returns HTTP 400 with a body containing `MISSING_SCOPE`; the same request to `/mcp?scope=does-not-exist` returns HTTP 400 with a body containing `UNKNOWN_SCOPE`. Neither body contains a path known to be in the union. Send these as genuine MCP requests (the same headers a real client sends) so the probe proves the guard fires ahead of the protocol layer.
   - **The scope is fixed at session creation, and this probe pins it.** Establish one MCP session with `?scope=<a>` (initialize, capture the `mcp-session-id` response header, send `notifications/initialized`), then send a `tools/call` for `search_related` with that **same** session header but `?scope=<b>` on the request, where `<b>` is a second valid scope whose roots hold a known match for the query. Assert the call is answered from `<a>`: at least one result comes back, every returned `path` resolves under `<a>`'s roots, and no path under `<b>`'s roots appears in the body. This is the documented, deliberate session-scoped outcome from requirement 3 — a valid but different scope on a later request of an established session does not re-scope it. No other probe sees this case.
   - **Interleaved requests do not contaminate each other** (AC 9): two requests for the same query, scopes A and B, genuinely in flight at the same time, with the first held between its scope resolution and its index read. **Use two separate MCP clients — two separate MCP sessions, one per scope.** Two sessions are mandatory — not because a session serializes its requests (it does not: the SDK dispatches every message within a session concurrently via `tg.start_soon`, `mcp/server/lowlevel/server.py:673-683`, so a same-session pair would not deadlock), but because a session answers only from the scope bound when that session was created (requirement 3). A same-session probe therefore cannot exercise two different scopes at all: the second request would be answered from the first scope's roots, so it would fail red against the *correct* implementation and prove nothing about interleaving. The hold must sit between the guard's scope resolution and the tool's scope read — a fake encoder that blocks inside `indexer.search` does **not** work: the tool evaluates `_request_roots()` as an argument to `search`, so a held request has already captured its roots and a stored scope could never reach its response. Patch the tool's own `create_indexer` call instead (`semantic_search.server.create_indexer`, which the tool body runs before it reads the scope) with a wrapper that blocks on a `threading.Event` for the first probe call only, with an explicit `event.wait(timeout=...)`. A scope stored on the shared indexer is then overwritten by the second request *before* the first request reads it, so the first response comes back with the second scope's paths. Assert both responses are in-scope for their own scope and that the two path lists are disjoint. The probe must be deterministic: no sleeps racing against each other.
   - **The fail-closed guard**: with `mark_http_transport()` called and no request roots bound, the tool helper raises instead of returning union results. Assert the exception **type** (`RuntimeError`), not merely "some error". A unit-level test is enough here; keep it in the same file.
   - **All three tools are scoped**, not just `search_related`: cover `check_duplicates` and `get_content` over the mount as well, each asserting that every returned path is in scope (for `get_content`, a path in scope returns its content and a path in another scope is refused).

5. **Update `TestMcpMount` in `tests/test_http_server.py`** so its bare-`GET /mcp` probe carries a valid scope (the app is built from a temp scope map, as the rest of that file now does) — otherwise the new guard answers it and the test no longer proves the mount is reachable. Add a sibling assertion that an unscoped `GET /mcp` returns 400 with `MISSING_SCOPE`.

6. **Strict typing and style.** Full annotations (`contextvars.Token[...]` included), docstrings everywhere, no broad `except Exception` in new code, no `print()` in `src/`, no new dependency. `make precommit` must be clean.

**Self-check before finishing:** re-run `<verification>` and confirm every command behaves as stated; then walk each requirement against the change. In particular, prove the interleave probe is load-bearing — and make the mutation a **transport swap**, not a store. `VaultIndexer` has no `__slots__` (`src/semantic_search/indexer.py`), so `indexer.scope = roots` is silently accepted, but the tools read `current_request_roots()`, so storing alone changes nothing, the probe stays green, and the required "confirm it fails" observation is unmakeable. Do this instead: (a) inside the `/mcp` guard, store the resolved roots on the shared indexer (`http_server.get_indexer().scope = roots`) **and** make `_request_roots()` return that attribute when it is set, falling back to `current_request_roots()` only when it is not; (b) re-run the interleave test and confirm it **fails** — the second request overwrites the attribute before the first request reads its scope, so the first response comes back with the second scope's paths; (c) restore the context-variable transport (drop the store and the attribute branch), re-run the file, and confirm it is green again, then re-run `make precommit` — a botched restore must not ship. Report that observation in the completion summary.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Do NOT implement the `X-Semantic-Scope` request header. The query string is the frozen transport; the header is a contingency that is generated only if the operator's early gate shows the MCP client strips the query string. No third transport. `scopes.yaml` and `SEMANTIC_SCOPE_MAP` are frozen too — this prompt does not rename, reshape, or extend them.
- Do NOT put `MISSING_SCOPE` or `UNKNOWN_SCOPE` in `src/semantic_search/server.py`. The refusal tokens live in the HTTP layer; whether they appear in the shared scope module is not an acceptance criterion, but they must not be raised by the tools.
- Do NOT read the HTTP request inside a tool: no `get_http_request()`, no request object, no header parsing in `server.py`. The HTTP layer resolves; the tool only reads the bound value.
- Do NOT change the tool signatures or add a `scope` argument to a tool — clients call `search_related(query, top_k)`, `check_duplicates(file_path)`, and `get_content(path, snippet, query, context_lines)` exactly as they do today.
- Do NOT store the scope on the indexer, on the app, or in a module global, and do NOT assign it after construction. It is context-bound state; the interleave probe exists to catch a violation.
- Do NOT build a per-scope index and do NOT load an index lazily. There is exactly one index per process, built once at startup over the union of the declared roots.
- Do NOT change the readiness gate (`503` + `Retry-After: 5`) on the REST routes, the `/reindex` concurrency contract (`409 REINDEX_IN_PROGRESS`), or the `/content` error codes.
- Do NOT make `/health` or `/reindex` require a scope, and do NOT make the MCP guard apply to any path other than the MCP mount.
- Do NOT change what the stdio MCP transport reads: it keeps `CONTENT_PATH` as its root source, gains no scope handling, and never refuses. The stdio tests in `tests/test_server.py` must keep passing unchanged — that is what the marker-reset seam in requirement 1 protects, not a licence to relax the fail-closed guard.
- Do NOT edit `README.md` or `CHANGELOG.md` — the README's MCP `url` example and its `CONTENT_PATH` example, and the CHANGELOG's MCP sentence, are direct edits on the spec's list, not prompt work.
- Do NOT create or edit `docs/design/per-vault-scoping.md`, any other file under `docs/`, the `commands/*.md` files, or anything under `scenarios/` — those are direct edits handled outside the prompt pipeline.
- Log the scope name as data, never as a format string, and never pass it to a shell or interpolate it into a filesystem path.
- Tests must build their own temp content roots; the container has no vault content mounted, and the committed `scopes.yaml` names host directories that do not exist inside it.
- Keep `make precommit` green: format + test + lint + typecheck, mypy strict, ruff clean.
- Repo-relative paths everywhere except the roots inside `scopes.yaml`.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Then confirm each of these:

```bash
# The refusal tokens stay in the HTTP layer, and the tools stay free of them
grep -c 'MISSING_SCOPE\|UNKNOWN_SCOPE' src/semantic_search/http_server.py   # must print >= 2
! grep -q 'MISSING_SCOPE\|UNKNOWN_SCOPE' src/semantic_search/server.py      # must be silent (absent)

# The tools never resolve the scope themselves — resolution stays in the HTTP layer
! grep -q 'get_http_request' src/semantic_search/server.py                  # must be silent (absent)

# The scope transport is a context variable, not module state
grep -qE '(contextvars\.)?ContextVar' src/semantic_search/scopes.py         # must be silent (present)
! grep -nE '(indexer|app)\.(scope|roots)\s*=' src/semantic_search/http_server.py src/semantic_search/server.py

# The marker-reset seam exists and is wired into the shared fixture file
grep -q 'reset_http_transport' tests/conftest.py                            # must be silent (present)

# The tools pass the resolved roots into the read calls
grep -n '_request_roots()' src/semantic_search/server.py                    # helper definition + all three tools (4 lines)

# The MCP probes pass
uv run pytest tests/test_mcp_scoping.py -q
uv run pytest tests/test_http_server.py tests/test_server.py tests/test_scoping.py -q
uv run pytest -q
```

Then prove the interleave probe is load-bearing — a store-only edit does **not** prove it. `VaultIndexer` has no `__slots__`, so `indexer.scope = roots` is silently accepted, but the tools read `current_request_roots()`: a stored attribute nothing reads leaves the probe green and makes the required "confirm it fails" observation unmakeable. Temporarily swap the transport instead: store the resolved roots on the shared indexer inside the `/mcp` guard **and** make `_request_roots()` return that attribute when it is set (falling back to `current_request_roots()` otherwise), re-run `uv run pytest tests/test_mcp_scoping.py -q`, confirm the interleave probe **fails** (the second request overwrites the attribute before the first request reads its scope, so the first response carries the second scope's paths), then remove both edits — restoring the context-variable transport — and confirm the file is green again. Report that observation in the completion summary.
</verification>

<!-- SETTLED — measured against the locked `fastmcp 3.2.4` with real uvicorn, real ASGI middleware, and a
     hand-rolled MCP handshake over a socket, not inferred from the source tree:
       * A context variable bound by an outer ASGI middleware IS visible inside the sync tool body.
       * It carries the value bound on the request that CREATED the MCP session (the initialize POST),
         not the value bound on the request being answered — the session's receive-loop task snapshots
         the context at session creation. A `tools/call` carrying `?scope=B` on a session initialized
         with `?scope=A` is answered from A, silently.
       * `fastmcp.server.dependencies.get_http_request()` IS genuinely per-request in that version,
         which is why the design forbids it inside a tool rather than relying on it being absent.
     DECISION (deliberate, not an accident): accept session-scoped semantics. The frozen transport is one
     MCP URL per client, so session scope and request scope are the same thing in the deployed shape.
     Requirement 3 implements exactly this, and requirement 4 pins it with a probe that sends a
     `tools/call` carrying a different-but-valid scope on an established session and asserts the
     session's scope answers. The rejected alternative — resolving the scope inside the tool from the
     live HTTP request — is the resolution-in-the-tool the frozen boundary forbids. -->
