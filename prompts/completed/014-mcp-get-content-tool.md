---
status: completed
spec: [001-content-fetch-endpoint]
summary: Added get_content MCP tool to server.py using @mcp.tool decorator, reusing create_indexer() pattern and delegating to indexer.get_content()
container: semantic-search-exec-014-mcp-get-content-tool
dark-factory-version: v0.171.1-3-gd94f1fa
created: "2026-05-24T21:00:00Z"
queued: "2026-05-24T21:07:05Z"
started: "2026-05-24T21:10:20Z"
completed: "2026-05-24T21:11:19Z"
branch: dark-factory/content-fetch-endpoint
---

## Summary

- Add `get_content` MCP tool to `server.py` using the `@mcp.tool` decorator
- Parameters mirror the HTTP endpoint: `path`, `snippet`, `query`, `context_lines`
- Returns the same response shape as `VaultIndexer.get_content()`
- Reuses the same core logic by calling `create_indexer()` and delegating to `indexer.get_content()`

## Objective

Add the MCP tool that enables content-fetch from the MCP protocol layer. This is the companion to the HTTP `/content` endpoint from prompt 2.

## Context

Read these files before making changes:

- `/workspace/src/semantic_search/server.py` — existing `@mcp.tool` definitions (`search_related`, `check_duplicates`), the `create_indexer()` pattern, and how the module-level `CONTENT_PATHS` is used
- `/workspace/src/semantic_search/factory.py` — the singleton pattern for the indexer
- `/workspace/src/semantic_search/indexer.py` — the `get_content()` method signature from prompt 1
- `/workspace/docs/dod.md` — DoD checklist

## Requirements

1. In `src/semantic_search/server.py`, add a new `@mcp.tool` function:

   ```python
   @mcp.tool
   def get_content(
       path: str,
       snippet: bool = False,
       query: str | None = None,
       context_lines: int = 20,
   ) -> dict[str, str]:
       """Fetch the content of a file from the indexed vault.

       Args:
           path: File path (absolute or relative to an indexed root)
           snippet: If True, return a snippet around the best-matching line instead of the full file
           query: Search string to find the best-matching line (only used when snippet=True)
           context_lines: Number of lines before and after the match to include (default 20)

       Returns:
           Dict with keys: "path" (resolved absolute path), "content" (string), "mode" ("full" | "snippet")

       Raises:
           ValueError: If path resolves outside the indexed vault roots
           FileNotFoundError: If path is inside roots but file does not exist
       """
   ```

2. Inside the function, call `create_indexer(CONTENT_PATHS)` to get the indexer, then call `indexer.get_content(path, snippet, query, context_lines)` and return the result directly.

3. The MCP tool does NOT need its own readiness gate — the spec says: "The MCP tool does not need a separate gate because MCP tool registration already waits on indexer init in the existing pattern."

4. Follow `docs/dod.md` — docstring, type hints on all params and return.

## Constraints

- Must use the same `create_indexer()` pattern as existing tools — not a separate instance
- Do NOT add a readiness gate to the MCP tool (it already waits on indexer init)
- Return type is `dict[str, str]` to match prompt 1's `VaultIndexer.get_content()` signature (FastMCP accepts any JSON-serializable return — this is for signature consistency, not an MCP protocol requirement)
- Tests for this tool are written in prompt 4 (`TestMcpGetContentTool`); do NOT add ad-hoc tests in this prompt
- Exceptions raised by `indexer.get_content()` propagate out of the MCP tool unchanged — do not catch and re-shape them

## Verification

Run `make test` after each change. When complete, run `make precommit`.

```bash
cd /workspace && make test
cd /workspace && make precommit
```