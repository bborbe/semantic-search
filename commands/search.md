---
allowed-tools: mcp__semantic-search__search_related, Bash
argument-hint: <query> [top_k]
description: Semantic search over indexed markdown files via the semantic-search MCP server (with REST fallback)
---

## Usage

```
/semantic-search:search kubernetes deployment
/semantic-search:search "go testing patterns" 10
```

## Process

### 1. Parse arguments

- First argument (or full string if unquoted) → `query`
- Trailing integer → `top_k` (default 5)
- If no query: show usage and STOP.

### 2. Try MCP first

Call `mcp__semantic-search__search_related(query=<query>, top_k=<top_k>)`.

If it returns results, jump to step 4.

### 3. Fall back to REST

If the MCP tool is unavailable or returns an error, try the REST endpoint on the same `semantic-search-http` service:

```bash
SEMANTIC_SEARCH_URL="${SEMANTIC_SEARCH_URL:-http://127.0.0.1:8321}"
curl -fsS --max-time 10 "${SEMANTIC_SEARCH_URL}/search?q=$(printf %s "<query>" | jq -sRr @uri)&top_k=<top_k>"
```

If the REST call fails too, STOP and instruct the user:

> Neither the MCP server nor the HTTP service is reachable. Run `/semantic-search:configure` to set up the service.

### 4. Render results

```
🔍 Top <N> results for "<query>"

1. <path> (score: 0.87)
2. <path> (score: 0.81)
...

Source: MCP   |   Source: REST (http://127.0.0.1:8321)
```

Always indicate which transport was used so the user knows whether MCP is wired up.

### 5. Suggest follow-up

- `Read <top result>` to inspect the most relevant file
- `/semantic-search:research <query>` for a deeper synthesis across multiple results

## Notes

- Semantic search finds related concepts even when terminology differs (e.g., English ↔ German).
- Scores above ~0.6 typically indicate strong relevance; below ~0.4 may be noise.
- Both transports hit the same warm indexer when the HTTP service is running. MCP is preferred because it surfaces in Claude's tool list; REST is the fallback for pre-configure or broken-MCP-config states.
- Override the REST URL with `SEMANTIC_SEARCH_URL` if your service runs on a non-default host/port.
