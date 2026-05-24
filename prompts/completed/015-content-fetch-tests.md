---
status: completed
spec: [001-content-fetch-endpoint]
summary: Added comprehensive test coverage for VaultIndexer.get_content(), GET /content HTTP endpoint, and get_content MCP tool - 97 tests passing, make precommit clean
container: semantic-search-exec-015-content-fetch-tests
dark-factory-version: v0.171.1-3-gd94f1fa
created: "2026-05-24T21:00:00Z"
queued: "2026-05-24T21:07:05Z"
started: "2026-05-24T21:11:22Z"
completed: "2026-05-24T21:16:21Z"
branch: dark-factory/content-fetch-endpoint
---

## Summary

- Add integration tests for `VaultIndexer.get_content()` covering all modes and failure paths
- Add integration tests for `GET /content` HTTP endpoint covering all modes, error codes, and the readiness gate
- Add integration tests for `get_content` MCP tool
- All tests follow existing patterns (`unittest.mock.MagicMock` for HTTP, direct fixture for indexer unit tests)
- No scenario tests needed (all criteria reachable by unit/integration tests per spec)

## Objective

Write comprehensive test coverage for the three new components: the core `get_content()` method, the HTTP endpoint, and the MCP tool. Tests cover happy paths and all failure modes from the spec's Failure Modes table.

## Context

Read these files before making changes:

- `/workspace/tests/test_indexer.py` — existing `VaultIndexer` test patterns, especially `_read_file` and path-related tests
- `/workspace/tests/test_http_server.py` — HTTP endpoint test patterns, the module-level global patching (`import semantic_search.http_server as http_server`), and `TestClient` usage
- `/workspace/tests/conftest.py` — `temp_vault` fixture and `_isolated_indexer_cache` pattern
- The new source files from prompts 1-3 to understand the exact signatures and return shapes

## Requirements

### VaultIndexer.get_content() Tests

1. Add a new test class `TestVaultIndexerGetContent` in `tests/test_indexer.py` (project convention is one test file per source module; `tests/test_indexer.py` is the canonical location for `VaultIndexer` tests).

2. **Full mode tests**:
   - Test `get_content(path, snippet=False)` returns full file content and `mode == "full"`
   - Assert the returned `"path"` is the resolved absolute path string
   - Assert `content` equals the raw file text

3. **Snippet mode with query tests**:
   - Create a fixture with known unique lines (e.g., line containing "UNIQUE_TOKEN_XYZ")
   - Call `get_content(path, snippet=True, query="UNIQUE_TOKEN_XYZ", context_lines=2)`
   - Assert `mode == "snippet"`
   - Assert the unique line is in the returned `content`
   - Assert `content` has at most `2 * context_lines + 1` lines

4. **Snippet mode without query tests**:
   - Create a fixture with N lines where N > `2 * context_lines + 1`
   - Call `get_content(path, snippet=True, context_lines=2)`
   - Assert `mode == "snippet"`
   - Assert `content` equals the first 5 lines
   - Assert `content` does NOT include lines beyond the head

5. **Path traversal rejection tests**:
   - Test `path="../../etc/passwd"` → raises `ValueError` with "not in indexed roots" message
   - Test absolute path outside roots → raises `ValueError`
   - Assert no file read occurs (can verify by patching `_read_file` to fail if called)

6. **Symlink escape rejection tests**:
   - Create a symlink inside the vault pointing to a file outside
   - Call `get_content()` with the symlink path
   - Assert raises `ValueError`

7. **Missing file tests**:
   - Create a file path that passes the root check but does not exist
   - Assert raises `FileNotFoundError`
   - Assert the error message differs from the path-validation error

8. **context_lines clamping tests** (parametrized or table-driven):
   - `context_lines=-5` → clamp to `0`, return all lines (or first `2*0+1 = 1` line of head)
   - `context_lines=10000` on a small file → clamp to file length, return all lines

9. **Query with no matches**:
   - Create a fixture
   - Call with `query="NO_MATCHING_TOKEN_THAT_DOES_NOT_APPEAR"`
   - Assert falls back to file-head behavior and `mode == "snippet"` (not an error)

9a. **Non-UTF-8 file** (spec Failure Mode row 3):
   - Write a binary file inside the vault root with bytes that are NOT decodable as utf-8, latin-1, or cp1252 (e.g., `b'\xff\xfe\x00\x00'` is fine for utf-8 but latin-1/cp1252 accept any byte — so the only way to force `_read_file` to return `None` is to mock it). Use `patch.object(indexer, "_read_file", return_value=None)` for this test.
   - Call `get_content(path)` on a file that passes path validation and exists.
   - Assert raises `RuntimeError` with "could not read file" in the message.

### HTTP Endpoint Tests

10. Add a new test class `TestContentEndpoint` in `tests/test_http_server.py`.

11. **Success tests**:
    - `GET /content?path=<file>` returns 200 with `path`, `content`, `mode` fields
    - `GET /content?path=<file>&snippet=true&query=TOKEN&context_lines=5` returns `mode="snippet"`
    - `GET /content?path=<file>&snippet=true` returns `mode="snippet"` (no query)

12. **Error code tests** — error response shape is nested: `{"error": {"code": "<CODE>", "message": "..."}}`. Access via `body["error"]["code"]`.
    - Path outside roots → status 400, `body["error"]["code"] == "PATH_OUTSIDE_ROOTS"`
    - Missing file (path inside roots but file not on disk) → status 404, `body["error"]["code"] == "FILE_NOT_FOUND"`
    - Missing path param → status 400, `body["error"]["code"] == "MISSING_PATH"`
    - Non-UTF-8 file (mock `_read_file` to return None) → status 422, `body["error"]["code"] == "UNREADABLE_FILE"`

13. **Readiness gate test**:
    - Start app with `_indexer_ready` unset
    - `GET /content?path=...` returns 503 with `Retry-After: 5` header and `ready: False` in body
    - Uses the same pattern as `test_search_returns_503_when_not_ready`

14. **Parameter parsing tests**:
    - `snippet=true` (lowercase) is parsed as boolean `True`
    - `snippet=false`, `snippet=` (empty), and absent `snippet` all parse as `False`
    - `context_lines=abc` → status 400, `body["error"]["code"] == "INVALID_CONTEXT_LINES"` (locked contract — do NOT silently parse to 0)

### MCP Tool Tests

15. Create the file `tests/test_server.py` and add a new test class `TestMcpGetContentTool`. (The file does not exist today; do not skip this step.)

16. **Happy-path test** — full mode via the MCP tool boundary:
    - Set up a temp vault with a known file (use the `temp_vault` fixture)
    - Patch `semantic_search.indexer.SentenceTransformer` so embedding is fast
    - Import the MCP tool function from `semantic_search.server` and call `get_content(path=str(test_file))`
    - Assert response has `path`, `content`, `mode` and `mode == "full"`, `content` matches fixture text

17. **Snippet-mode test** through the MCP boundary:
    - Same setup; call `get_content(path=..., snippet=True, query="<token>", context_lines=3)`
    - Assert `mode == "snippet"` and the queried token appears in `content`

18. **Error propagation test** — path outside roots:
    - Set up a temp vault; call `get_content(path="/etc/passwd")` (or any path outside vault roots)
    - Assert raises `ValueError` (the MCP tool does NOT catch and reshape; FastMCP surfaces it to the caller)

19. **Error propagation test** — missing file:
    - Set up a temp vault; call `get_content(path=str(temp_vault / "does-not-exist.md"))`
    - Assert raises `FileNotFoundError`

## Constraints

- Use `unittest.mock.patch` and `unittest.mock.MagicMock` for HTTP tests (existing pattern)
- Use `patch("semantic_search.indexer.SentenceTransformer")` for indexer unit tests (existing pattern)
- All test methods must have `-> None` return type hint
- Follow existing naming: `TestContentEndpoint`, `test_*` methods
- No new test files unless existing `test_*.py` doesn't have a natural place for the tests

## Verification

Run `make test` after each change. When complete, run `make precommit`.

```bash
cd /workspace && make test
cd /workspace && make precommit
```