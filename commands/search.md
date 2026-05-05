---
allowed-tools: mcp__semantic-search__search_related
argument-hint: <query> [top_k]
description: Semantic search over indexed markdown files via the semantic-search MCP server
---

## Usage

```
/semantic-search:search kubernetes deployment
/semantic-search:search "go testing patterns" 10
```

## Process

1. Parse arguments:
   - First argument (or full string if unquoted) → `query`
   - Trailing integer → `top_k` (default 5)
2. If no query: show usage and STOP.
3. Verify the MCP server is reachable. If `mcp__semantic-search__search_related` is unavailable, instruct the user to run `/semantic-search:configure` and STOP.
4. Call `mcp__semantic-search__search_related(query=<query>, top_k=<top_k>)`.
5. Render results as a ranked list:

```
🔍 Top <N> results for "<query>"

1. <path> (score: 0.87)
2. <path> (score: 0.81)
...
```

6. After the list, suggest:
   - `Read <top result>` to inspect the most relevant file
   - `/semantic-search:research <query>` for a deeper synthesis across multiple results

## Notes

- Semantic search finds related concepts even when terminology differs (e.g., English ↔ German).
- Scores above ~0.6 typically indicate strong relevance; below ~0.4 may be noise.
- The MCP server must be running and indexed (see `/semantic-search:configure`).
