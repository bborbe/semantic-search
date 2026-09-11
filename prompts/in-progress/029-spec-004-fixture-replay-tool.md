---
status: approved
spec: [004-per-vault-scoping]
created: "2026-09-11T11:27:52Z"
queued: "2026-09-11T12:32:41Z"
---

# Add the frozen-fixture replay tool

<summary>
- A committed command replays the pre-change acceptance baseline against a live server, scope by scope, and compares the result paths in order
- A perfect match prints one line per scope and exits 0
- Any difference — one extra path, one missing path, one reordering — exits 1 and names the scope and the query that differ
- A run that cannot compare at all — unreadable fixture, unreachable server, request timeout, unparseable body — exits 2 and says why on stderr, distinct from a difference
- The tool speaks plain HTTP with the standard library only, so it can run anywhere the server runs
- The comparison target is the frozen baseline captured before this work started; the tool reads it and never rewrites it
- The tool is exercised in the test suite both against a stub server and end-to-end against the real scoped server
</summary>

<objective>
Deliver the acceptance oracle's instrument: a small, dependency-free command that answers "does this server return, per scope, exactly what the five separate daemons returned before the merge?" The baseline file is frozen and must never be regenerated, so the tool has to read it as-is and fail loudly on any drift — that is what makes the merge safe to ship.
</objective>

<context>
Read `CLAUDE.md` for project conventions and `docs/dod.md` for the Definition of Done.

Prompts 1–3 have already landed: `build_app(scope_map)` serves scoped `/search`, `/duplicates`, and `/content`, `/health` reports the union root set, the scope map lives in `scopes.yaml` (named by the `SEMANTIC_SCOPE_MAP` environment variable), and the HTTP-mounted MCP surface resolves its scope for the session. Only the REST `/search` route matters to this prompt.

Read these files before making changes:

- `src/semantic_search/cli.py` — the repo's Python command-line style, and the primary style anchor for the new script: `argparse.ArgumentParser` built inside the entry function, results written to stdout with `print()`, every diagnostic and usage error written to stderr with `print(..., file=sys.stderr)`, and failure signalled with `sys.exit(1)`.
- `main()` in `src/semantic_search/http_server.py` — the second `argparse` exemplar: `prog=` / `description=` argument construction and a `--version` action. Match this shape.
- `scripts/check-versions.sh` — the only file currently in `scripts/`. Note the toolchain's coverage: `make format` and `make lint` run `ruff format .` / `ruff check .`, so `scripts/` is lint- and format-checked, but `make typecheck` runs `mypy src`, so `scripts/` is **not** type-checked.
- `src/semantic_search/http_server.py` — the `/search` response envelope the tool must parse: `{"query": ..., "results": [{"path": ..., "score": ...}, ...], "count": ...}`.
- `tests/test_scoping.py` and `tests/test_mcp_scoping.py` (from prompts 2 and 3) — the temp-root scope-map fixture and the uvicorn-in-a-background-thread pattern for driving the real app over a real socket. Reuse both. The deterministic fake `SentenceTransformer` is defined in neither of those files: it lives in `tests/conftest.py` as the fixture `deterministic_sentence_transformer` (added by prompt 2), so request it by name rather than redefining it. It returns a **class**, not an instance, so it is applied as `patch("semantic_search.indexer.SentenceTransformer", deterministic_sentence_transformer)`. Do not substitute the older `mock_sentence_transformer` fixture from the same file: it returns a constant vector for every input, so it cannot drive result ordering and would make the replay's ordering comparison vacuous.

**The fixture is not readable from inside the container.** It lives in the Personal vault on the host, at `80 Attachments/semantic-search-consolidation-baseline-2026-09-11.json`, and was captured before any of this work existed. Its schema — verified by reading the file — is:

```jsonc
{
  "captured_at": "2026-09-11T10:38:23.957397+00:00",
  "purpose": "<prose>",
  "top_k": 20,                                  // the top_k every captured query used
  "ports": {                                    // the five pre-consolidation daemons
    "8321": {"label": "personal",    "health": {"status": "ok", "ready": true,
                                                "paths": ["<absolute root>", ...],
                                                "indexed_files": 17101}},
    "8322": {"label": "brogrammers", "health": {...}},
    "8323": {"label": "boss",        "health": {...}},
    "8324": {"label": "openbrain",   "health": {...}},
    "8325": {"label": "starcitzen",  "health": {...}}
  },
  "queries": ["Star Citizen ship loadout", "<13 more>"],   // 14 queries, same order as `search`'s keys
  "search": {                                   // query -> port -> captured response
    "Star Citizen ship loadout": {
      "8321": {"query": "Star Citizen ship loadout",
               "results": [{"path": "<absolute path>", "score": 0.5600966215133667}, ...],  // exactly top_k entries
               "count": 20},
      "8322": {...}, "8323": {...}, "8324": {...}, "8325": {...}
    },
    "<13 more queries>": {...}
  }
}
```

Every one of the 14 queries has a block for all five ports, and every block holds exactly 20 results. The port keys are pre-consolidation ports and exist only to carry each scope's `label` — the label is the scope name. The five labels are `personal`, `brogrammers`, `boss`, `openbrain`, `starcitzen`.

**Do not regenerate, rewrite, move, or "fix" the fixture.** It is the oracle; a baseline captured after the change proves nothing. The tool reads it.

Pattern guides (in-container paths):

- `/home/node/.claude/plugins/marketplaces/coding/docs/python-cli-arguments-guide.md` — Option 2 (argparse) is the shape this script follows: `argparse.ArgumentParser` with required options and `sys.exit` on a usage error. Skip its Pydantic option — that targets applications, and this tool is a standard-library-only script with no Pydantic dependency.
</context>

<requirements>

1. **Create `scripts/replay-scope-fixture.py`** — standard library only (`argparse`, `json`, `sys`, `pathlib`, `urllib.parse`, `urllib.request`, `http.client` if you need its exception types). No third-party import, no `print()`-free restriction here — this is a command-line tool, so stdout is its output channel — but every diagnostic goes to stderr.

   Command line:

   ```
   uv run python scripts/replay-scope-fixture.py --fixture <path-to-baseline.json> --base-url <url>
   ```

   Both arguments are required. `--base-url` is the merged server's root, e.g. `http://127.0.0.1:8321`; a trailing slash must not matter.

2. **Behaviour:**

   - Load the fixture. For each port in `ports`, take its `label` as the scope name; for each query, issue

     `GET {base_url}/search?q=<url-encoded query>&top_k=<fixture top_k>&scope=<label>`

     with a finite per-request timeout of 30 s (`urllib.request.urlopen(request, timeout=30)`) — never hang; an unreachable server must fail fast and loudly, not block. A request that times out is an exit-`2` condition.
   - Parse the response and compare `[r["path"] for r in results]` — the ordered list — against the fixture's `search[query][port]["results"]` path list, in order. Compare the list as a whole; report the first differing position and both paths, plus any length difference, so the failure is diagnosable without a debugger.
   - Print exactly one line per scope on success, each naming the scope and the number of queries compared. Nothing else goes to stdout.
   - **Exit codes — this is the tool's contract with the operator, so pin it exactly:**
     - `0` — every query of every scope matched.
     - `1` — every comparison that was attempted completed, and at least one of them differs. The scope name and the query name must both appear in the output.
     - `2` — comparison was impossible: a usage problem (missing or unknown argument), an unreadable or malformed fixture, or an HTTP/parse failure — an unreachable server, a request timeout, a non-2xx response, or a response body that does not parse into the `{"query", "results", "count"}` envelope.
       A body that *does* parse as JSON but does not match the envelope — `results` missing, `results` not a list, or a result entry without `path` — is also exit `2`. Validate that shape explicitly and route the failure through the same stderr reason as the other exit-`2` paths, so no `KeyError`/`TypeError` escapes as a traceback: a traceback exits `1`, which the operator reads as "difference found" when nothing was compared.
     - **Precedence:** `2` means "could not compare" and wins over `1` whenever both occur in the same run — a run that both failed to compare something and found a difference exits `2`. A run in which every attempted comparison completed and at least one differed exits `1`.
     - Every non-zero path prints a human-readable reason to stderr, and that reason names its cause: the fixture path when the fixture could not be read or parsed, the URL when the request failed, and the scope plus the query when a comparison differed.
   - Catch narrowly. The exit-`2` paths are exactly where a broad catch is tempting, and nothing will catch it for you: `docs/dod.md` forbids `except Exception`, and ruff's `select` list in `pyproject.toml` has no `BLE`, so `make lint` passes a broad catch. Catch `OSError` (it covers file-read failures, `urllib.error.URLError`, `urllib.error.HTTPError` for a non-2xx status, `TimeoutError` for a timeout, and `http.client.RemoteDisconnected`), `json.JSONDecodeError`, `UnicodeDecodeError`, and `http.client.HTTPException` (which is not an `OSError`) explicitly. No bare `except:` and no `except Exception`. Envelope-shape validation is explicit, not exception-driven: check that `results` is present and a list and that each result entry carries `path`, and route a failure through the same exit-`2` stderr path rather than letting a `KeyError` or `TypeError` escape.
   - Report all differences it finds, not just the first one — but stop early enough not to hammer a broken server (a scope whose requests all fail should be reported once, not 14 times).
   - Docstrings on every function and type hints on every signature, per `docs/dod.md`. The script must be `ruff format`/`ruff check` clean at the repo's 100-column limit.

3. **Create `tests/test_replay_scope_fixture.py`** covering both the tool's logic and its use against the real server:

   - **Stub-server coverage** (fast, deterministic): build a temp fixture file with the real schema — two ports/labels, two queries, a small `top_k` — and serve canned responses from a `http.server.ThreadingHTTPServer` bound to port 0 in a background thread. Run the script with `subprocess.run([sys.executable, <repo>/scripts/replay-scope-fixture.py, ...], capture_output=True, text=True)` and assert:
     - a matching fixture exits `0`, and its stdout is exactly one line per scope — assert the exact stdout line count (two lines for the two-scope temp fixture) and that each line contains its scope name;
     - the same run against a `--base-url` carrying a trailing slash (`http://127.0.0.1:<port>/`) also exits `0` — a trailing slash is normalised, not a difference;
     - the temp fixture is byte-identical after the run: hash it before and after and assert the two digests are equal — the tool never rewrites its oracle;
     - a **perturbed** copy — one path deleted from one query in one scope — exits `1` and its output names both the scope and the query;
     - the stub received the scope, the `top_k`, and the URL-decoded query it expects (record the parsed query string in the stub and assert on it — this is the boundary the tool's own encoding crosses);
     - a missing fixture file exits `2` with a reason on stderr and without a traceback, and that reason names the fixture path rather than a connection error — the fixture is loaded and validated before any request is issued;
     - a valid readable fixture pointed at an unreachable base URL (a closed port) exits `2` with a reason on stderr naming the URL, and without a traceback;
     - a stub that answers every request with `500` makes the run exit `2` with a reason on stderr, not `1` — a non-2xx response means "could not compare", never "difference found";
     - a stub that answers `200` with a body that parses as JSON but is not the envelope (e.g. `{"detail": "nope"}`) also exits `2` with a reason on stderr and without a traceback — a missing or malformed `results` key must not raise `KeyError`/`TypeError`, which would exit `1` and read as a difference.
   - **End-to-end coverage** against the real scoped server: start the app from `build_app` over temp content roots and a temp scope map, using prompt 3's uvicorn-in-a-thread pattern and the `deterministic_sentence_transformer` fixture from `tests/conftest.py`; capture a fixture by querying the server for each scope and assembling the fixture schema above; run the replay script against that server and assert it exits `0`; then perturb the captured fixture and assert the same run exits `1` and names the scope and query. Reset the process-wide index state before and after each probe — `factory.reset()` **plus** prompt 2's trio `http_server._indexer = None`, `http_server._indexer_ready = asyncio.Event()` (a fresh unset event), and `http_server._indexer_error = None`. `factory.reset()` alone is not enough: `http_server` holds its own `_indexer` / `_indexer_ready` / `_indexer_error` module globals and `_build_indexer_in_background` early-returns while `_indexer_ready` is already set, so a leftover from an earlier test module would decide what this probe replays against. Before replaying, assert the captured fixture is non-empty — every query/scope block holds at least one result — because an empty capture makes both the exit-`0` assertion and the perturbation vacuous. This is the probe that proves the tool and the server agree on the response envelope, not merely on a stub's.
   - Keep the tests hermetic: no network beyond `127.0.0.1`, no vault content, no dependency on the committed `scopes.yaml`.

4. **Strict typing and style.** `make precommit` must be clean (format + test + lint + typecheck).

**Self-check before finishing:** re-run `<verification>` and confirm every command behaves as stated; then walk each requirement against the change. In particular, prove the perturbation probe is load-bearing: delete the perturbation step's edit from the test (so both runs use the matching fixture) and confirm the `1`-exit assertion fails — i.e. the test really distinguishes a match from a difference. Restore it afterwards.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- Do NOT regenerate, rewrite, or repair the baseline fixture, and do NOT add a `--capture` or `--write` mode that would produce a new one. The fixture is the oracle and it is read-only.
- Do NOT add a third-party dependency. The tool and its tests use the standard library plus what is already declared.
- Do NOT change the server's response envelopes to make the tool's job easier — the tool adapts to the existing `{"query", "results", "count"}` shape.
- Do NOT change the frozen contract: the parameter name is `scope`, the five scope names are `personal`, `brogrammers`, `boss`, `openbrain`, `starcitzen`, and the refusal is HTTP 400 carrying `MISSING_SCOPE` / `UNKNOWN_SCOPE`.
- Do NOT touch `/health`, `/reindex`, the MCP mount, the tools in `src/semantic_search/server.py`, or the stdio/CLI content-path behaviour.
- Do NOT create or edit `docs/design/per-vault-scoping.md`, any other file under `docs/`, the `commands/*.md` files, or anything under `scenarios/` — those are direct edits handled outside the prompt pipeline.
- Do NOT add the fixture file to the repository. It lives in the vault; tests build their own temp fixtures.
- Keep `make precommit` green: format + test + lint + typecheck, mypy strict, ruff clean.
- Repo-relative paths everywhere except the roots inside `scopes.yaml` and the fixture paths a test constructs in `/tmp`.
</constraints>

<verification>
Run `make precommit` — must pass (format + test + lint + typecheck).

Then confirm each of these. Every step shells out to `uv`; a `127` from any step means the command never ran because the image lacks the tool, which is a verification failure — never read it as a pass.

```bash
# Guard: if `uv` is missing, every step below reports 127 and nothing was actually verified
command -v uv >/dev/null || echo "FAIL: uv is not in PATH — every step below would report 127"

# The tool exists and imports nothing third-party.
# `test -f` first: with the file missing, a bare `grep` exits 2 and `!` would turn that into a pass.
test -f scripts/replay-scope-fixture.py && ! grep -qE '^\s*(import|from)\s+(requests|httpx|yaml|numpy|faiss|starlette|fastmcp)' scripts/replay-scope-fixture.py && echo "PASS: tool exists and imports nothing third-party" || echo "FAIL: scripts/replay-scope-fixture.py is missing, or it imports a third-party module"

# Both probes are covered by the suite
uv run pytest tests/test_replay_scope_fixture.py -q

# A missing fixture exits exactly 2, prints a reason to stderr, and does not hang
timeout 30 uv run python scripts/replay-scope-fixture.py --fixture /tmp/does-not-exist.json --base-url http://127.0.0.1:1 >/tmp/replay-out.txt 2>/tmp/replay-err.txt
code=$?
[ "$code" -eq 2 ] && echo "PASS: missing fixture exits 2" || echo "FAIL: expected exit 2, got $code (127 = the command never ran)"
grep -q 'does-not-exist' /tmp/replay-err.txt && echo "PASS: stderr names the fixture path" || echo "FAIL: stderr does not name the fixture path (or is empty)"

# The tool is runnable exactly as the operator's acceptance step runs it
uv run python scripts/replay-scope-fixture.py --help >/dev/null 2>&1
code=$?
[ "$code" -eq 0 ] && echo "PASS: --help exits 0" || echo "FAIL: expected exit 0, got $code (127 = the command never ran)"

# The whole suite
uv run pytest -q
```
</verification>
