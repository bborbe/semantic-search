---
status: completed
spec: [004-per-vault-scoping]
summary: Added the committed scope map (scopes.yaml), the scopes loader/validator/resolver module, the union index via factory root-set declaration, and the fail-closed MISSING_SCOPE/UNKNOWN_SCOPE HTTP gate with full test coverage
execution_id: semantic-search-exec-026-spec-004-scope-map-and-union-index
dark-factory-version: dev
created: "2026-09-11T11:27:52Z"
queued: "2026-09-11T12:32:41Z"
started: "2026-09-11T12:50:31Z"
completed: "2026-09-11T12:58:40Z"
---

# Add the scope map, the union index, and the fail-closed scope gate

<summary>
- The HTTP server gets a committed scope map: five named scopes, each an ordered list of the directories that scope is allowed to see
- The five scopes reproduce exactly the five path sets the five running search daemons index today — no scope gains a directory and none loses one
- The server builds exactly one index, over the union of every declared scope's roots, instead of indexing whichever path list happened to arrive first
- A request that names no scope is refused with HTTP 400 and the token `MISSING_SCOPE`, never answered from the full index
- A request that names a scope the server does not know is refused with HTTP 400 and the token `UNKNOWN_SCOPE`
- The refusal happens before the readiness check, so an unscoped request is refused whether or not the index has finished building
- The health endpoint reports the union root set, and keeps answering identically when a scope parameter is present
- A scope map that names a missing, unusable, or non-declared root stops the process at startup with a non-zero exit and a log line naming both the scope and the root
- The stdio MCP transport and the one-shot CLI keep their existing content-path configuration and gain no scope handling at all
- The scope map's file name and the environment variable that points at it are fixed here, because later prompts and the operator's service definition both depend on them
</summary>

<objective>
Give the merged HTTP server a single, committed declaration of what each client is allowed to see, and make the server refuse — rather than answer broadly — any request that does not name a known scope. This is the foundation the rest of spec 004 builds on: the union root set, the one-index-per-process invariant, and the fail-closed gate. The read paths are deliberately not scoped in this prompt (prompt 2 does that) and the MCP surface is not touched (prompt 3 does that).
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.13+, `uv`, strict mypy, src/ layout, dark-factory workflow — never code directly), and `docs/dod.md` for the Definition of Done.

Read these files before making changes:

- `src/semantic_search/http_server.py` — the whole file. `CONTENT_PATHS` (module level, from the `CONTENT_PATH` env var) is currently the HTTP server's index root source; that is what changes. `_build_indexer_in_background` builds the indexer in a worker thread so the port binds immediately; `health` reports `paths`; `search` / `duplicates` / `content` / `reindex` are the routes; `build_app` wires the routes and mounts the FastMCP app; `main` parses `--host` / `--port` / `--version` and calls `uvicorn.run`.
- `src/semantic_search/factory.py` — the module-level singleton behind a `Lock`. `create_indexer(content_paths)` creates the indexer on first call and returns the existing one afterwards, so **whichever caller arrives first decides what is indexed**. That behaviour is the obstacle this prompt removes.
- `src/semantic_search/server.py` — defines the FastMCP instance and the three tools, each calling `create_indexer(CONTENT_PATHS)`. This module is imported by both the stdio entry point and the HTTP server. Do not add scope handling here.
- `src/semantic_search/indexer.py` — `VaultIndexer.__init__(vault_paths, embedding_model="all-MiniLM-L6-v2", duplicate_threshold=0.85)` accepts a single path string or a list; `self.vault_paths` is a `list[Path]`; `meta` maps index position to `{"path": ...}`; `_embed_text` calls `self.model.encode([text], normalize_embeddings=True, show_progress_bar=False)` and the index is built with `faiss.IndexFlatIP(self.model.get_sentence_embedding_dimension())`.
- `tests/test_http_server.py` — the existing HTTP tests. They call `build_app()` with no arguments and monkeypatch the module globals `_indexer`, `_indexer_ready`, `_indexer_error`. Several of them assert 200 on unscoped requests and will need the scope parameter; two of them patch `_build_indexer_in_background` with a zero-argument `side_effect`.
- `tests/test_server.py` — the stdio tool tests; they patch `semantic_search.server.CONTENT_PATHS` and reset `semantic_search.factory._indexer` / `_watcher`. Their shape is the template for the new stdio test in requirement 8.
- `tests/conftest.py` — `_isolated_indexer_cache` (autouse) redirects `user_cache_dir` into a per-test tmp dir; `temp_vault` and `multi_vaults` fixtures.

Pattern guides (in-container paths — read the relevant ones before writing code):

- `/home/node/.claude/plugins/marketplaces/coding/docs/python-architecture-patterns.md` — constructor injection, the composition root, and "DON'T: Use global singletons".
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-ioc-guide.md` — `Protocol` for dependency surfaces, "Inject Configuration Objects, Not Primitives".
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-factory-pattern.md` — the existing factory's shape.
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-logging-guide.md` — log-line conventions.

**Three facts you must not get wrong:**

1. The committed scope map names host directories that do **not** exist inside the container. Never run the server or its validation against the committed map in a test or a verification step — tests always build their own temp map over temp roots.
2. `scopes.yaml` is the one file in this repo where absolute host paths are correct. Every other path you write in code, tests, and prompts stays repo-relative.
3. **The container cannot load the real embedding model.** `all-MiniLM-L6-v2` is not in the container image, is not mounted, and `huggingface.co` is not on the container's proxy allowlist — so any test that instantiates the real `SentenceTransformer` fails inside the container and takes `make precommit` down with it. In-process tests patch `semantic_search.indexer.SentenceTransformer`; the subprocess probe in requirement 8 must inject a stub package on `PYTHONPATH` instead (requirement 8 spells out the stub's contract).
</context>

<requirements>

1. **Create `scopes.yaml` at the repository root** — the committed scope map. Exactly this content:

   ```yaml
   scopes:
     personal:
       - /Users/bborbe/Documents/Obsidian/Personal
       - /Users/bborbe/Documents/Obsidian/Trading
       - /Users/bborbe/Documents/Obsidian/Family
       - /Users/bborbe/Documents/Obsidian/OpenClaw
       - /Users/bborbe/Documents/Obsidian/Gaming
       - /Users/bborbe/Documents/workspaces/trading/docs
       - /Users/bborbe/Documents/workspaces/dark-factory/docs
       - /Users/bborbe/Documents/workspaces/cqrs/docs
       - /Users/bborbe/Documents/workspaces/coding/docs
     brogrammers:
       - /Users/bborbe/Documents/Obsidian/Brogrammers
       - /Users/bborbe/Documents/workspaces/dark-factory/docs
       - /Users/bborbe/Documents/workspaces/sm-octopus/docs
       - /Users/bborbe/Documents/workspaces/coding/docs
     boss:
       - /Users/bborbe/Documents/Obsidian/Personal
       - /Users/bborbe/Documents/Obsidian/Trading
       - /Users/bborbe/Documents/Obsidian/Family
       - /Users/bborbe/Documents/Obsidian/OpenClaw
       - /Users/bborbe/Documents/Obsidian/Boss
       - /Users/bborbe/Documents/workspaces/trading/docs
       - /Users/bborbe/Documents/workspaces/dark-factory/docs
       - /Users/bborbe/Documents/workspaces/cqrs/docs
       - /Users/bborbe/Documents/workspaces/coding/docs
     openbrain:
       - /Users/bborbe/Documents/Obsidian/OpenBrain
       - /Users/bborbe/Documents/Obsidian/Brogrammers
       - /Users/bborbe/Documents/Obsidian/Personal
       - /Users/bborbe/Documents/workspaces/openbrain/docs
       - /Users/bborbe/Documents/workspaces/dark-factory/docs
       - /Users/bborbe/Documents/workspaces/coding/docs
     starcitzen:
       - /Users/bborbe/Documents/Obsidian/StarCitizen
   ```

   These five lists are frozen: they are the five path sets the five running daemons index today, captured before this work started. Do not add, remove, reorder, or "tidy" a root. The 15 distinct roots across the five lists are the union the merged index is built over. The file name `scopes.yaml` and the five scope names are frozen — later prompts, the service definition, and the operator's documentation all reference them.

2. **Create `src/semantic_search/scopes.py`** — the scope map's loader, validator, and resolver. Public surface (signatures are the contract; bodies are yours):

   ```python
   SCOPE_MAP_ENV = "SEMANTIC_SCOPE_MAP"

   class ScopeConfigError(Exception):
       """The scope map is missing, empty, malformed, or names an unusable root."""

   class ScopeRequestError(Exception):
       """Base class for a request whose scope cannot be resolved."""

   class MissingScopeError(ScopeRequestError):
       """A request named no scope."""

   class UnknownScopeError(ScopeRequestError):
       """A request named a scope that is not declared."""

   @dataclass(frozen=True)
   class ScopeMap:
       """The declared scopes: scope name -> ordered tuple of absolute roots."""
       scopes: Mapping[str, tuple[Path, ...]]

       @property
       def union_roots(self) -> tuple[Path, ...]:
           """Deduplicated union of every scope's roots, in first-appearance order."""

   def scope_map_path_from_env() -> Path:
       """Return the map file named by SCOPE_MAP_ENV; raise ScopeConfigError when unset or empty."""

   def load_scope_map(path: Path) -> ScopeMap:
       """Parse and shape-check the map file; raise ScopeConfigError on any problem."""

   def validate_scope_map(scope_map: ScopeMap) -> None:
       """Raise ScopeConfigError when a declared root is unusable."""

   def resolve_scope(scope_map: ScopeMap, raw: str | None) -> tuple[Path, ...]:
       """Return the roots for a request's scope name, or raise."""
   ```

   Contracts:
   - `load_scope_map` parses YAML with `yaml.safe_load` (PyYAML is already a dependency) and raises `ScopeConfigError` for: an unreadable or missing file, invalid YAML, a document that is not a mapping with a `scopes` key, a `scopes` value that is not a mapping, an empty `scopes` mapping, a scope whose value is not a list, an empty root list, a non-string root, and a root that is not absolute. The error message names the file and what is wrong with it.
   - `validate_scope_map` raises `ScopeConfigError` when a declared root does not exist, is not a directory, or is not readable (`os.access(root, os.R_OK | os.X_OK)`). **The message must contain both the scope name and the offending root path**, because that message is the startup log line (requirement 4) and the acceptance criterion asserts on both. Validation runs before the index build — it must not need an index, and it must not walk the trees.
   - `resolve_scope` raises `MissingScopeError` when `raw` is `None` or blank, `UnknownScopeError` when `raw` is not a declared scope name, and otherwise returns that scope's roots. A scope name is only ever a dictionary key — it is never turned into a filesystem path, never interpolated into a path, and never passed to a shell.
   - **This module must not contain the strings `MISSING_SCOPE` or `UNKNOWN_SCOPE`.** The refusal tokens belong to the HTTP layer (requirement 3); this module only says *which kind* of failure occurred.
   - No per-request state of any kind in this module — prompt 3 adds the request-scoped transport. Do not pre-empt it with a module global.

3. **Rewire `src/semantic_search/http_server.py`** onto the scope map:

   - `CONTENT_PATHS` and the `CONTENT_PATH` env var stop being this module's index root source. Delete the module-level `CONTENT_PATHS`; the union root set comes from the injected scope map. (`CONTENT_PATH` keeps feeding the stdio transport and the CLI — see requirement 5.)
   - `build_app(scope_map: ScopeMap) -> Starlette` — the scope map is injected here, at construction, and the routes close over it. `main()` is the composition root: it loads and validates the map, declares the union root set (requirement 4), then builds the app. Do not stash the map in a module global and do not mutate the app after construction.
   - **`main()` declares the index roots; `build_app` must not.** In `main()`, after the map has validated and before `build_app(...)` is called, call `declare_index_roots([str(p) for p in scope_map.union_roots])`. The `[str(p) for p in ...]` form is required: `union_roots` is typed `tuple[Path, ...]` and `create_indexer` takes `list[str]` under mypy strict. `build_app` declares nothing, for two reasons: every HTTP test calls it, and the declaration is process-global state that outlives the test that set it — a declaration inside `build_app` would leak one test's temp roots into whatever module runs next.
   - Keep `_indexer`, `_indexer_ready`, `_indexer_error`, and `get_indexer()` as they are — the existing tests monkeypatch those names, and the readiness contract is unchanged.
   - `_build_indexer_in_background` takes the union roots as an argument (the lifespan passes `scope_map.union_roots`), logs them, and builds through `create_indexer([str(p) for p in roots])` — `create_indexer` takes `list[str]`, so the `Path` → `str` conversion happens here, at the call site, not inside the factory. The index is still built in the background: the port binds immediately and `/health` answers while the build is in flight.
   - **Add the fail-closed gate to `/search`, `/duplicates`, and `/content`.** Each handler resolves the request's scope and, on failure, returns HTTP 400 with a JSON body whose `error` value is the token `MISSING_SCOPE` (no scope named) or `UNKNOWN_SCOPE` (unknown scope name). Define both tokens as module-level constants in `http_server.py` and map `MissingScopeError` / `UnknownScopeError` to them in one small helper so the mapping exists in exactly one place. Enumerate the exception types — no broad `except Exception` in new code.
   - **Handler ordering, in this exact order:** (1) the existing parameter check — `/search` still answers `Missing 'q' parameter`, `/content` still answers `MISSING_PATH`, `/duplicates` still answers `Missing 'file' parameter`; (2) the scope gate; (3) the existing readiness gate (`503` + `Retry-After: 5`); (4) the work. An unscoped request therefore gets 400 whether or not the index is ready, and the existing parameter-error tests keep passing.
   - `/health` stays scopeless: it reports `paths` as the **union** root set (as strings, in `union_roots` order) and `indexed_files` as today, and it must return the same body when a `scope` parameter is present rather than erroring. `/reindex` stays scopeless and otherwise untouched.
   - Keep `--host`, `--port`, and `--version` behaviour unchanged. `main()` additionally: reads the map path from `SCOPE_MAP_ENV`, loads it, validates it, and on `ScopeConfigError` logs an ERROR line carrying the exception message (which names the scope and the root) and exits non-zero via `sys.exit(1)` — before `uvicorn.run`. `--version` must still exit 0 without touching the scope map.
   - This prompt only **validates and refuses**. A request that names a *valid* scope is still answered from the union index in this prompt — narrowing the read paths is prompt 2's deliverable, and inventing a half-filter here would duplicate it.

4. **Make the union root set — not the first caller — decide what is indexed.** In `src/semantic_search/factory.py`, keep `create_indexer(content_paths)` and its singleton semantics, and add (import `Sequence` from `collections.abc`):

   ```python
   def declare_index_roots(roots: Sequence[str]) -> None:
       """Declare this process's index root set; the HTTP entry point calls this once, before serving."""

   def reset() -> None:
       """Drop the singleton and the declared root set, so the next create_indexer starts clean."""
   ```

   Contracts:
   - Once declared, the process index is built over the declared roots whichever caller creates it first; before any declaration, the roots passed to `create_indexer` are used, exactly as today. **`main()` — and only `main()` — declares `[str(p) for p in scope_map.union_roots]`** (requirement 3). That is what stops the FastMCP tools, which call `create_indexer(CONTENT_PATHS)` in the same process, from creating a second index over the stdio default. `build_app` declares nothing. The stdio process never declares anything, so it keeps building from `CONTENT_PATH`.
   - `reset()` is test support: under `_indexer_lock`, stop the existing watcher if there is one (`VaultWatcher.stop()`), then clear `_indexer`, `_watcher`, and the declared root set. Every place this prompt resets the singleton calls `factory.reset()` — a stale indexer *or a stale declaration* left behind by an earlier test would otherwise decide what a later test indexes. This includes the four pre-existing direct resets in `tests/test_server.py` (four sites pairing `factory._indexer = None` with `factory._watcher = None`): migrate all four to `factory.reset()`. The direct assignment clears only the singleton and leaves a stale declaration behind, and `reset()` is the only supported way to drop both.
   - **Keep the singleton deliberately.** `server.py`'s tools call `create_indexer(CONTENT_PATHS)` with no injection seam, and the spec requires exactly one index per process. `python-architecture-patterns.md` shows this shape under "DON'T: Use global singletons" — that section does not apply here. Do not refactor the factory into injected instances, and do not add a construction seam in this prompt; prompt 3 depends on this module's shape as it stands.
   - **The precedence is a contract with its own test.** Add to `tests/test_factory.py` (a new file, matching the repo's per-module test naming): after `declare_index_roots([A])`, `create_indexer([B])` returns an indexer whose `vault_paths` equals `[Path(A)]` — the declared set wins over the caller's list; with no declaration, `create_indexer([B])` returns an indexer whose `vault_paths` equals `[Path(B)]`. Call `factory.reset()` between the two cases so the first case's declaration and singleton cannot decide the second. Patch `semantic_search.indexer.SentenceTransformer` in both cases so neither builds a real embedding index.
   - Document the precedence in the docstrings of both `create_indexer` and `declare_index_roots`. This is a process-scoped root set — it is **not** the per-request scope, which must never be stored here or anywhere else.

5. **Leave the stdio transport and the CLI alone.** `src/semantic_search/server.py` keeps `CONTENT_PATHS` and its three `create_indexer(CONTENT_PATHS)` calls; `src/semantic_search/cli.py` and `src/semantic_search/__main__.py` are not touched. No scope parameter, no refusal, no scope lookup reaches them.

6. **Create `tests/test_scopes.py`** covering the loader, the validator, and the resolver, over temp files and temp directories:

   - a valid temp map loads into the expected `scopes` mapping, and `union_roots` is deduplicated in first-appearance order (build a temp map where one root appears in two scopes and assert the union has it once);
   - `scope_map_path_from_env` returns the env var's path, and raises `ScopeConfigError` when the env var is unset and when it is empty;
   - `load_scope_map` raises `ScopeConfigError` for a missing file, for an unreadable file (`chmod 000` — the container runs as a non-root user), for invalid YAML, for a document without `scopes`, for a `scopes` value that is not a mapping, for an empty `scopes` mapping, for a scope whose value is not a list, for an empty root list, for a non-string root, and for a relative root;
   - `validate_scope_map` accepts a map whose roots are readable temp directories, and raises `ScopeConfigError` naming **both** the scope and the root for a root that does not exist, for a root that is a regular file rather than a directory, and for a root that exists but is not readable (`chmod 000`);
   - `resolve_scope` returns the declared roots for a known name and raises `MissingScopeError` for `None` and for `""`, `UnknownScopeError` for an undeclared name.

7. **Update `tests/test_http_server.py`** so it drives the new contract (this file is extended, not replaced):

   - Every request that expects a 2xx now carries `scope=<name>`, and each test builds its app from a temp scope map over temp roots (a small helper fixture keeps this readable). Call `semantic_search.factory.reset()` per test — the singleton is process-wide, so a stale indexer from an earlier test would otherwise decide what is indexed. (`build_app` deliberately does not declare roots, so an HTTP test never has to undo a declaration; it still has to drop the singleton.)
   - Add the fail-closed probes: `/search?q=...` with no `scope` → 400 and the body contains `MISSING_SCOPE`; `/search?q=...&scope=does-not-exist` → 400 and the body contains `UNKNOWN_SCOPE`; the same two for `/duplicates` and `/content`. For each refusal also assert that a path that *is* in the union does not appear in the response body.
   - Add the "refusal fires before the readiness gate" probe: with `_indexer_ready` unset, an unscoped `/search` returns 400 (not 503), while a valid-scope `/search` still returns 503 with `Retry-After: 5`.
   - Add the `/health` probes: `paths` equals the union of the temp map's roots, and `/health?scope=<name>` returns the same body as `/health`.
   - Add the startup-validation probe: with `SCOPE_MAP_ENV` pointing at a temp map whose root does not exist, `main()` raises `SystemExit` with a non-zero code and the captured log/stderr contains both the scope name and the offending root path. Follow the shape of `TestVersionFlag`, which already drives `main()` in-process and asserts `SystemExit.code`.
   - Update the two tests that patch `_build_indexer_in_background` with a zero-argument `side_effect` so they tolerate the roots argument the lifespan now passes.

8. **Prove the stdio transport gained nothing.** Add to `tests/test_server.py`:

   - a test that calls the `search_related` tool directly (the existing style: patch `server_module.CONTENT_PATHS` to a temp root, call `factory.reset()`, patch `semantic_search.indexer.SentenceTransformer`) with **no** scope anywhere, and asserts it returns at least one result whose path resolves under that temp root and raises no scope error — i.e. it answered from `CONTENT_PATH` instead of refusing. The patched `encode` must return a 2-D `numpy.ndarray` — `np.array([[0.1] * 384])`, the form `tests/test_indexer.py` uses — **not** the plain list-of-lists (`[[0.1] * 384]`) the rest of `tests/test_server.py` uses: `_embed_text` calls `.astype("float32")` on the result, so a list raises, `rebuild_index`'s per-file `except` swallows it, the index comes out empty (`len(meta) == 0`), `search()` returns `[]`, and the required "at least one result" assertion can never pass;
   - a subprocess probe that spawns the stdio server and speaks JSON-RPC over stdin, with the real embedding model replaced **across the process boundary**. The container cannot load `all-MiniLM-L6-v2` (context fact 3), so an in-process patch is not available here — the probe must shadow the installed package instead:
     1. Write a stub `sentence_transformers` package into a temp directory: `sentence_transformers/__init__.py` defining a `SentenceTransformer` class whose `__init__(self, model_name: str)` accepts the model name, whose `get_sentence_embedding_dimension(self) -> int` returns 384, and whose `encode(self, sentences, normalize_embeddings=False, show_progress_bar=False)` returns a `numpy.ndarray` of shape `(len(sentences), 384)` and dtype `float32`. Those two members are the whole contract: `VaultIndexer._embed_text` calls `encode([text], normalize_embeddings=True, show_progress_bar=False)` and then `.astype("float32")`, and the index dimension comes from `faiss.IndexFlatIP(model.get_sentence_embedding_dimension())`. Make the vector deterministic — seed it from `hashlib.sha256(text.encode()).digest()`, never from Python's `hash()`, whose string hashing is salted per process — so repeated runs build the same index and return the same ordering.
     2. Spawn `[sys.executable, "-m", "semantic_search", "serve"]` with `subprocess.Popen(..., stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)` — **not** `subprocess.run`, which cannot drive an interactive conversation. `env=` is a copy of `os.environ` carrying `CONTENT_PATH` = the temp root and `PYTHONPATH` = the stub directory prepended to any existing `PYTHONPATH`. `PYTHONPATH` entries precede `site-packages` on `sys.path`, so the stub shadows the installed `sentence_transformers` — that shadowing is what makes the probe runnable in the container at all.
     3. **Drive the conversation interactively, one message at a time.** The stdio transport tears the session down the moment stdin reaches EOF, so a `tools/call` response produced after teardown is lost: writing all three messages and then closing stdin — the `subprocess.run(input=...)` form — yields **only** the `initialize` response and exit 1, with `anyio.ClosedResourceError` raised inside `mcp/shared/session.py::_send_response` on stderr. A batch probe can therefore never see the `tools/call` response, and the only way it "passes" is by burning the entire 180 s deadline. Never batch the three messages, and never fall back to asserting against a timeout exception's captured stdout.
        - Read stdout on a background `threading.Thread` that appends each received line to a list, so a read never blocks the deadline check; write each request to `proc.stdin` followed by `flush()`.
        - Write the `initialize` request, then wait under the deadline for the line whose parsed JSON carries that request's `id`; only then write `notifications/initialized`, then the `tools/call` request for `search_related` — give that request a **different** `id` from the `initialize` request, so the second wait can never match the first response.
        - Keep waiting under a 180 s deadline for the line whose parsed JSON carries the `tools/call`'s `id`. Parse each stdout line as JSON, skipping lines that are not JSON objects (collect them for the failure message), and assert on that response's `result` — the returned paths include a path under the temp root, and neither `MISSING_SCOPE` nor `UNKNOWN_SCOPE` appears anywhere in stdout. Assert on the response's `result`, never on framing details.
        - Only after that response has arrived, close stdin and terminate the process (`proc.terminate()`, then `proc.wait(timeout=...)`, killing on `TimeoutExpired`).
     4. If the deadline expires, **terminate the process first** (`proc.terminate()`, then `proc.wait(timeout=...)`, killing on `TimeoutExpired`) and only then read the tail of `proc.stderr` — reading the stderr pipe while the child is still alive blocks until EOF and would hang the failure path itself. Fail with an assertion message carrying every stdout line received so far and that stderr tail — a hang must report what actually arrived, never an opaque timeout, and never an assertion weakened so that a slow probe still passes.

   The deterministic fake `SentenceTransformer` that the later scoping prompts share will live in `tests/conftest.py` as a fixture that prompt 2 adds (prompt 2 names it `deterministic_sentence_transformer`) — do not create it here, and do not define one inside `tests/test_scopes.py` or any other test module.

9. **Add a `## Unreleased` section to `CHANGELOG.md`** (it is absent — `v0.19.0` consumed it) with one bullet describing the user-facing change: the HTTP server now requires a `scope` query parameter on `/search`, `/duplicates`, and `/content`, and refuses a request that names no scope or an unknown scope with HTTP 400 (`MISSING_SCOPE` / `UNKNOWN_SCOPE`) instead of answering it from the full index; the scope map lives in `scopes.yaml` and is named by the `SEMANTIC_SCOPE_MAP` environment variable. Write it as a user-facing entry, not as a commit log. **Claim only what this prompt ships:** a *valid* scope is still answered from the union index here, so the entry must not say the server "answers only from that scope". Narrowing the read paths is prompt 2's deliverable, and prompt 2 extends this same entry when it lands.

10. **Strict typing and style.** Full annotations on every new function and on `ScopeMap`, docstrings on everything (`docs/dod.md`), no `print()` in `src/`, no broad `except Exception` in new code, no new runtime dependency. `make precommit` must be clean.

**Self-check before finishing:** re-run `<verification>` and confirm every command behaves as stated; then walk each requirement above against the change and confirm each acceptance probe in this prompt's `<verification>` block passes. If any command's output is not what the block says it must be, fix the code — do not weaken the command.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Do NOT scope the read paths in this prompt. `/search`, `/duplicates`, and `/content` validate the scope and refuse an unusable one; narrowing the result set is prompt 2.
- Do NOT touch the `/mcp` mount, the FastMCP tools, or the request-scoped transport in this prompt — prompt 3 owns them.
- Do NOT change what the stdio MCP transport or the `semantic-search` CLI read. Both keep `CONTENT_PATH` as their root source and gain no scope handling — this is a deliberate non-goal with its own acceptance criterion.
- Do NOT declare index roots anywhere except `main()` — not in `build_app`, not at module import. The declaration is process-global and outlives the test that set it.
- Do NOT refactor `factory.py`'s singleton into injected instances or add a construction seam. `server.py`'s tools call `create_indexer(CONTENT_PATHS)` with no injection seam and the spec requires exactly one index per process; prompt 3 depends on this module's shape as it stands.
- Do NOT add a scope parameter to `/health` or `/reindex`, and do NOT make `/health` or `/reindex` refuse anything. `/health` is the readiness probe; `/reindex` rebuilds the whole union index.
- Do NOT add a configuration knob that disables scoping or restores union-wide answers. Fail-closed is invariant: a deployment that needs unscoped answers is a different product.
- Do NOT add a per-scope result-count or memory limit, and do NOT change chunking, the embedding model, or quantization.
- Do NOT index anything outside the 15 declared roots — no OctopusAgent, no DataAssistant, no other vault.
- Do NOT change the frozen contract: the parameter name is `scope`, the five scope names are `personal`, `brogrammers`, `boss`, `openbrain`, `starcitzen`, and the refusal is HTTP 400 carrying `MISSING_SCOPE` / `UNKNOWN_SCOPE`.
- Do NOT add a scope fallback to the union index, and do NOT return an empty result set in place of a refusal — an empty set is indistinguishable from a legitimate no-match.
- Do NOT create or edit `README.md`, `docs/design/per-vault-scoping.md`, `docs/launchd-service.md`, `docs/releasing-semantic-search.md`, `docs/systemd-user-service.md`, the `commands/*.md` files, or anything under `scenarios/` — those are direct edits handled outside the prompt pipeline. `README.md` belongs on that list even though `docs/dod.md` requires a README update on a usage change: this prompt invalidates README's `CONTENT_PATH` example for `semantic-search-http`, and that `docs/dod.md` rule is satisfied by the spec's direct-edit list, not by this prompt.
- Do NOT rename `scopes.yaml` or `SEMANTIC_SCOPE_MAP` later in the spec; the operator's service definition and the scenario helper both follow this decision.
- Keep `make precommit` green: format + test + lint + typecheck, mypy strict, ruff clean. Existing tests that this prompt does not explicitly update must keep passing.
- Repo-relative paths everywhere except the roots inside `scopes.yaml`, which are host paths by definition.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Then confirm each of these:

```bash
# The gate's tokens live in the HTTP layer, not in the shared scope module
grep -c 'MISSING_SCOPE\|UNKNOWN_SCOPE' src/semantic_search/http_server.py    # must print >= 2
! grep -q 'MISSING_SCOPE\|UNKNOWN_SCOPE' src/semantic_search/scopes.py       # must be silent (absent)

# The HTTP server no longer derives its index roots from CONTENT_PATH
! grep -q 'CONTENT_PATHS' src/semantic_search/http_server.py                 # must be silent (absent)
grep -q 'SEMANTIC_SCOPE_MAP' src/semantic_search/scopes.py                   # must be silent (present)

# The stdio tools are still driven through the patched content path
grep -c 'CONTENT_PATHS' tests/test_server.py                                 # must print >= 4
uv run pytest tests/test_server.py -q                                        # must pass

# The new scope module and the factory root-declaration precedence
uv run pytest tests/test_scopes.py tests/test_factory.py -q                  # must pass

# Startup validation: a map naming a missing root exits non-zero and names both
TMPD=$(mktemp -d)
printf 'scopes:\n  probe:\n    - %s/missing-root\n' "$TMPD" > "$TMPD/scopes.yaml"
SEMANTIC_SCOPE_MAP="$TMPD/scopes.yaml" timeout 60 uv run semantic-search-http --port 18999 > "$TMPD/startup.log" 2>&1
echo "exit=$?"                                    # must print exit=1; 124 would mean it started and hung instead of refusing
grep -q 'probe' "$TMPD/startup.log" && grep -q 'missing-root' "$TMPD/startup.log"   # must be silent (both present)
! grep -q 'Serving REST + MCP' "$TMPD/startup.log"   # must be silent (absent — it never bound a port)

# The CLI keeps CONTENT_PATH and keeps failing loudly without it
env -u CONTENT_PATH uv run semantic-search search kubernetes > /tmp/cli-nopath.out 2>&1
echo "exit=$?"                                                               # must print exit=1
grep -q 'CONTENT_PATH environment variable not set' /tmp/cli-nopath.out      # must be silent (present)

# The committed map is the frozen five-scope mapping — full equality, order included.
# The expected lists below are transcribed from the frozen acceptance fixture's
# ports[*].health.paths; a root moved between two scopes must fail this check.
uv run python - <<'PY'
import pathlib

import yaml

expected = {
    "personal": [
        "/Users/bborbe/Documents/Obsidian/Personal",
        "/Users/bborbe/Documents/Obsidian/Trading",
        "/Users/bborbe/Documents/Obsidian/Family",
        "/Users/bborbe/Documents/Obsidian/OpenClaw",
        "/Users/bborbe/Documents/Obsidian/Gaming",
        "/Users/bborbe/Documents/workspaces/trading/docs",
        "/Users/bborbe/Documents/workspaces/dark-factory/docs",
        "/Users/bborbe/Documents/workspaces/cqrs/docs",
        "/Users/bborbe/Documents/workspaces/coding/docs",
    ],
    "brogrammers": [
        "/Users/bborbe/Documents/Obsidian/Brogrammers",
        "/Users/bborbe/Documents/workspaces/dark-factory/docs",
        "/Users/bborbe/Documents/workspaces/sm-octopus/docs",
        "/Users/bborbe/Documents/workspaces/coding/docs",
    ],
    "boss": [
        "/Users/bborbe/Documents/Obsidian/Personal",
        "/Users/bborbe/Documents/Obsidian/Trading",
        "/Users/bborbe/Documents/Obsidian/Family",
        "/Users/bborbe/Documents/Obsidian/OpenClaw",
        "/Users/bborbe/Documents/Obsidian/Boss",
        "/Users/bborbe/Documents/workspaces/trading/docs",
        "/Users/bborbe/Documents/workspaces/dark-factory/docs",
        "/Users/bborbe/Documents/workspaces/cqrs/docs",
        "/Users/bborbe/Documents/workspaces/coding/docs",
    ],
    "openbrain": [
        "/Users/bborbe/Documents/Obsidian/OpenBrain",
        "/Users/bborbe/Documents/Obsidian/Brogrammers",
        "/Users/bborbe/Documents/Obsidian/Personal",
        "/Users/bborbe/Documents/workspaces/openbrain/docs",
        "/Users/bborbe/Documents/workspaces/dark-factory/docs",
        "/Users/bborbe/Documents/workspaces/coding/docs",
    ],
    "starcitzen": [
        "/Users/bborbe/Documents/Obsidian/StarCitizen",
    ],
}

actual = yaml.safe_load(pathlib.Path("scopes.yaml").read_text())["scopes"]
assert set(actual) == set(expected), f"scope names differ: {sorted(actual)}"
for name, roots in expected.items():
    assert list(actual[name]) == roots, f"{name}: {list(actual[name])!r} != {roots!r}"
assert len({r for roots in actual.values() for r in roots}) == 15, "distinct-root count changed"
print("scopes.yaml matches the frozen five-scope mapping (15 distinct roots)")
PY
# must print: scopes.yaml matches the frozen five-scope mapping (15 distinct roots)

# The full suite
uv run pytest -q
```

Note the two `grep -c` traps: `grep -c` exits 1 when the count is 0, which is why the absence checks are written as `! grep -q` and the presence checks are stated as "must be silent (present)".
</verification>
