---
status: verifying
tags:
    - dark-factory
    - spec
approved: "2026-05-24T20:41:13Z"
generating: "2026-05-24T20:55:58Z"
prompted: "2026-05-24T21:00:05Z"
verifying: "2026-05-24T21:18:02Z"
branch: dark-factory/content-fetch-endpoint
---

## Summary

- Add a separate content-fetch capability so remote callers can retrieve file content after a search, without needing local filesystem access.
- Today `search_related` returns paths and scores only. Clients are forced to read the file themselves, blocking remote deployment of semantic-search.
- New MCP tool `get_content` and new HTTP route `/content` accept a path and return either the full file or a focused snippet around the best match for an optional query.
- Path access is restricted to files inside the indexed roots; traversal outside is rejected.
- `search_related` response shape is unchanged — preview and content stay in the dedicated fetch call to keep search responses small.

## Problem

`search_related` returns only paths and scores. To actually consume a result, the caller must read the file from disk. That works for local clients (Claude Code on the same host) but blocks every remote-deployment scenario: agents on another host, hosted MCP gateways, or any client behind a network boundary cannot read paths returned by the server. There is no first-class way to ask the server "give me the content of result #2" today.

## Goal

After this work, a caller that can reach semantic-search over HTTP or MCP can complete the full search-and-consume loop using only the server's API — no shared filesystem required. The caller issues `search_related`, picks a result, and calls `get_content` (MCP) or `GET /content` (HTTP) to retrieve either the whole file or a query-focused snippet. The server refuses to serve any path outside its indexed roots.

## Non-goals

- Do NOT change the `search_related` response shape. No inline preview, no inline content. If a future consumer demands embedded previews, that is a separate spec.
- Do NOT add authentication, rate limiting, or per-user access control. Trust model is unchanged from today's REST server.
- Do NOT add a streaming or chunked response mode. One request returns one JSON body.
- Do NOT add a write or edit endpoint. Read-only.
- Do NOT introduce a new chunking or embedding strategy. Snippet location operates on raw file text.
- Do NOT cache fetched content beyond the lifetime of the request.
- Do NOT add a configuration knob to disable the endpoint — the endpoint is invariant; if a future deployment demands selective exposure, that's a separate spec.
- Do NOT impose a server-side file-size cap on full mode in this spec; memory is bounded by the host and by the file sizes already present in the indexed roots. A size cap is a separate spec if remote operators need one.

## Desired Behavior

1. **MCP tool `get_content` exists** with parameters: `path` (required string), `snippet` (boolean, default `false`), `query` (optional string, default `null`), `context_lines` (integer, default `20`). It returns a JSON object with at least `path`, `content`, and `mode` (`"full"` or `"snippet"`).
2. **HTTP route `GET /content` exists** with the same parameters as query-string arguments. Returns `200` with a JSON body matching the MCP tool's shape on success.
3. **Full mode** (`snippet=false`): the response `content` is the entire file as UTF-8 text and `mode` is `"full"`.
4. **Snippet mode with query** (`snippet=true`, `query` non-empty): the response `content` is the best-matching line in the file plus `context_lines` lines before and after (clamped to file bounds), `mode` is `"snippet"`. "Best-matching" is the line whose case-insensitive substring overlap with the query tokens is highest; ties broken by first occurrence.
5. **Snippet mode without query** (`snippet=true`, `query` null or empty): the response `content` is the first `2 * context_lines + 1` lines of the file (the head), `mode` is `"snippet"`.
6. **Path validation**: the server resolves the supplied `path` and rejects it unless the resolved path is inside at least one configured indexed root (the `vault_paths` the indexer was constructed with). Symlinks are resolved before the check. Rejected paths return an error without reading any file content.
7. **Missing file**: when the resolved path passes the root check but the file does not exist on disk, the server returns an error distinct from path-rejection.
8. **`search_related` is unchanged**: its request and response schemas, field names, and field types are identical to the prior release.
9. **Readiness gating**: the HTTP route returns the same "indexer not ready" response shape as other gated routes when `_indexer_ready` is not set. The MCP tool does not need a separate gate because MCP tool registration already waits on indexer init in the existing pattern.
10. **README documents the two-step flow** with a concrete `search_related → get_content` example and a "Remote Deployment" note explaining that callers no longer need filesystem access.
11. **CHANGELOG has an `## Unreleased` entry** describing the new endpoint.

## Constraints

- Python 3.14+, `uv` package manager, strict mypy must pass (`make typecheck`, 0 errors).
- `make precommit` must pass end-to-end (format + test + lint + typecheck).
- No new runtime dependencies. Snippet location uses the standard library only.
- `search_related` MCP tool signature and HTTP `/search` response body are frozen.
- `VaultIndexer.vault_paths` is the authoritative list of indexed roots — path validation must use this attribute, not an alternate source of truth.
- `_read_file()` on the indexer is the canonical reader and must be reused (do not open files directly in the new endpoint code).
- Project DoD (`docs/dod.md`) applies: docstrings on new functions, type hints, no `print()` in library code, no broad `except Exception`.

## Failure Modes

| Trigger | Expected behavior | Recovery | Detection |
|---------|-------------------|----------|-----------|
| Path outside indexed roots (including via `..`, absolute path elsewhere, or symlink that resolves outside) | Reject with a path-validation error; no file read attempted | Caller corrects path; no server state change | Error response body identifies "path not in indexed roots"; structured log line at WARNING |
| Path inside roots but file does not exist | Return a not-found error distinct from the path-validation error | Caller picks a different path; no server state change | Error response body identifies "file not found"; INFO log |
| File exists but is not UTF-8 decodable | Return an error indicating the file could not be read as text | Caller retries with another path | `_read_file` already handles this; error surfaces with a "could not read file" message |
| `snippet=true`, `query` provided, but no line matches any query token | Fall back to the file-head behavior (same as the "no query" branch) and return `mode="snippet"` | None needed; deterministic | Response still has `mode="snippet"` and head content; no error |
| `context_lines` negative or absurdly large | Clamp to a sensible range (0 minimum; cap at the file length) and proceed | None needed | Response succeeds with clamped window |
| `/content` called before indexer is ready | Same gated response shape as other readiness-gated routes | Client retries after indexer init | Response matches existing "not ready" pattern; existing log line covers it |
| Very large file requested in full mode | Server still returns the full content; no special handling | None — out of scope for this spec | n/a (full mode is documented as returning the whole file) |

## Security / Abuse Cases

- **Attacker controls**: the `path`, `query`, `snippet`, and `context_lines` parameters on a public HTTP endpoint (if the deployment exposes one).
- **Trust boundary crossed**: the HTTP/MCP boundary into the server's filesystem.
- **Path traversal**: `../`, absolute paths outside indexed roots, and symlinks that resolve outside roots must all be rejected by the resolved-path-vs-roots check. The check must use `Path.resolve()` (which follows symlinks) before comparison.
- **Resource exhaustion via `context_lines`**: must be clamped so a malicious large value cannot allocate unbounded memory beyond file size.
- **Query injection**: `query` is used only for in-memory line matching, never as a shell argument, regex compiled from user input without bounds, or database parameter. Treat as plain text.
- **Information disclosure**: only files inside `vault_paths` are reachable; the same files are already indexed and discoverable via `search_related`, so `get_content` does not widen the disclosure surface.

## Acceptance Criteria

- [ ] `make precommit` exits 0 (includes format + test + lint + typecheck) — evidence: exit code.
- [ ] MCP tool `get_content` is registered and callable. Evidence: an integration test invokes the MCP tool with a real indexed temp vault and asserts a non-empty `content` field and `mode == "full"`; test passes.
- [ ] HTTP route `GET /content?path=<file>` returns 200 with a JSON body containing `path`, `content`, and `mode` for a file inside the indexed roots. Evidence: pytest using `httpx.AsyncClient` against the FastAPI app asserts status 200 and the three fields.
- [ ] Full mode returns the entire file. Evidence: test reads a fixture file via the endpoint and asserts `content == fixture_text` and `mode == "full"`.
- [ ] Snippet mode with query returns a window containing the matching line. Evidence: test fixture has a known unique line, request uses a query token from that line, response `content` contains the unique line and `mode == "snippet"`; assertion on line count `<= 2 * context_lines + 1`.
- [ ] Snippet mode without query returns the file head. Evidence: test fixture file of N lines with `context_lines=5`; response `content` equals first 11 lines and `mode == "snippet"`.
- [ ] Path-traversal attempt is rejected. Evidence: test requests `path="../../etc/passwd"` (or an absolute path outside the indexed roots); response indicates path-not-in-roots; no file content is returned; no traversal-target read occurs (assert via mock or via the fact that the response error code is the validation one).
- [ ] Symlink escape is rejected. Evidence: test creates a symlink inside the indexed root that points outside; request for that symlink path is rejected by the resolved-path check.
- [ ] Missing-file response differs from path-validation rejection. Evidence: test requests a path inside the indexed root that does not exist; response JSON `body["error"]["code"] == "FILE_NOT_FOUND"` (string equality, nested shape); traversal-rejection test asserts `body["error"]["code"] == "PATH_OUTSIDE_ROOTS"` (string equality, nested shape); the two strings differ. Error response shape is `{"error": {"code": "<CODE>", "message": "..."}}` — nested, not flat.
- [ ] `search_related` response shape is unchanged. Evidence: existing `search_related` tests pass without modification (no diff in their assertions); evidence is the unchanged file in `git diff`.
- [ ] README contains a two-step example using `search_related` then `get_content` and a "Remote Deployment" section noting filesystem access is no longer required. Evidence: `grep -n 'get_content' README.md` returns at least one line; `grep -n -i 'remote deployment' README.md` returns at least one line.
- [ ] `CHANGELOG.md` has an `## Unreleased` entry mentioning the new endpoint. Evidence: `grep -n 'get_content\|/content' CHANGELOG.md` returns at least one line under an `## Unreleased` header.
- [ ] `context_lines` clamping works for negative and oversized values. Evidence: parametrized test with `context_lines=-5` and `context_lines=10_000` on a small file; response succeeds with clamped behavior (no exception, no error).
- [ ] Readiness gate on `/content`. Evidence: test starts the HTTP app with `_indexer_ready` un-set and asserts the same response shape that other gated routes produce.

**Scenario coverage**: NO new scenario. All criteria above are reachable by unit and integration tests against the FastAPI app and the MCP tool registration; no real cluster, real `gh`, or real Docker is required. Remote-deployment "smoke" is a manual operator check, not a regression-blocking scenario.

## Verification

```
cd ~/Documents/workspaces/semantic-search
make precommit
```

Expected: exit code 0, all tests pass, mypy reports 0 errors, ruff reports 0 errors, formatter produces no diff.

Manual smoke (operator, after merge — not part of the automated gate):

```
# host A: start server bound to a network interface
# host B: another machine
curl "http://hostA:PORT/search?query=foo"
# pick a path from the response
curl "http://hostA:PORT/content?path=<path>&snippet=true&query=foo&context_lines=10"
```

Expected: host B receives the file snippet without needing the file to exist locally.

## Do-Nothing Option

If we do nothing, semantic-search remains usable only when the client shares a filesystem with the server. Remote deployment is blocked; every consuming agent must run on the same host or mount the indexed vault. The current local-only workflow is acceptable for Claude Code on the developer's machine but is the explicit blocker for remote and multi-tenant deployments. Doing nothing means rejecting the remote-deployment goal entirely.

## Verification Result

**Verified:** 2026-05-24T21:50:39Z (HEAD 4314255)
**Binary:** /Users/bborbe/Documents/workspaces/go/bin/dark-factory (v0.171.1-3-gd94f1fa)
**Scenario:** Real-HTTP replay of scenarios 004 (happy paths) + 005 (error responses) against live `semantic-search-http` on loopback, plus `make precommit` covering test-based ACs and structural grep checks for README/CHANGELOG.
**Evidence:**
- Scenario 004 A2 (full mode): `curl /content?path=/tmp/scenario-content/kubernetes.md` → 200, body `{"path":"/private/tmp/scenario-content/kubernetes.md","content":"# Kubernetes deployment notes\n\n...","mode":"full"}`
- Scenario 004 A3 (snippet mode): `curl ...?snippet=true&query=autoscaling&context_lines=0` → 200, body `{"...","content":"container scheduling, rolling updates, and horizontal pod autoscaling.","mode":"snippet"}` (70 bytes vs 174 bytes full — narrower confirmed)
- Scenario 005 A5 (traversal): `curl /content?path=/etc/passwd` → 400, body `{"error":{"code":"PATH_OUTSIDE_ROOTS","message":"path not in indexed roots"}}`
- Scenario 005 A6 (missing): `curl ...?path=/tmp/scenario-content/does-not-exist.md` → 404, body `{"error":{"code":"FILE_NOT_FOUND","message":"file not found: /tmp/scenario-content/does-not-exist.md"}}`
- `make precommit` → exit 0 in 10.5s, 98 tests passed, mypy 0 issues, ruff clean (covers AC1, AC6, AC8, AC13, AC14)
- AC10 README: `grep -n get_content README.md` → L53, L126; `grep -ni "remote deployment" README.md` → L124
- AC11 CHANGELOG L13: `feat: Add get_content MCP tool and GET /content REST endpoint...`
- AC12 search_related unchanged: `git diff origin/master..HEAD -- tests/test_http_server.py` shows zero changes to `search_related` assertions
- Bug found and fixed during scenario replay: `indexer.get_content` compared `resolved_path` against unresolved `vault_paths`, falsely rejecting `/tmp/...` on macOS (resolves to `/private/tmp/...`). Fixed via `vp.resolve()` in `is_relative_to` check; regression test `test_unresolved_vault_path_with_symlink_root_accepted` added. Test bug in `test_content_returns_503_when_not_ready` also fixed (background indexer not properly patched).
**Verdict:** PASS
