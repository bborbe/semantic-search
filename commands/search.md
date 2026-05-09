---
allowed-tools: mcp__semantic-search__search_related, mcp__semantic-search-personal__search_related, mcp__semantic-search-work__search_related, Bash
argument-hint: <query> [top_k] [--server=<label>]
description: Semantic search over indexed markdown files via the semantic-search MCP server (multi-instance, with REST fallback)
---

## Usage

```
/semantic-search:search kubernetes deployment
/semantic-search:search "go testing patterns" 10
/semantic-search:search --server=work sentry runbook
```

## Process

### 1. Parse arguments

- First non-flag argument (or full string if unquoted) → `query`
- Trailing integer → `top_k` (default 5)
- `--server=<label>` (optional) → scope to a single MCP server (e.g. `personal`, `work`); omit to query every available server
- If no query: show usage and STOP.

### 2. Discover available MCP servers

Known servers (must match this command's `allowed-tools`):

| Tool name | Conventional label |
|---|---|
| `mcp__semantic-search__search_related` | `(default)` |
| `mcp__semantic-search-personal__search_related` | `personal` |
| `mcp__semantic-search-work__search_related` | `work` |

If `--server=<label>` was passed: keep only the matching tool. Else: attempt all three in parallel and treat any tool that errors with "not available" / "unknown tool" as absent.

If none of the listed MCP tools is wired up, jump to step 4 (REST fallback).

### 3. Query all available MCP servers

For each available tool, call:

```
<tool-name>(query=<query>, top_k=<top_k>)
```

Run them in parallel. Each result list is tagged with its server label.

**Merge strategy:** combine all results, sort by `score` descending, keep top `top_k` overall. Preserve the source label per result.

Jump to step 5 (render).

### 4. Fall back to REST

Enumerate running `semantic-search-http` services and their ports, then query each.

```bash
# macOS — list running services
launchctl list 2>/dev/null | awk '/com\.github\.bborbe\.semantic-search-http/ {print $3}'

# Linux — list running units
systemctl --user list-units 'semantic-search-http*' --no-legend 2>/dev/null | awk '{print $1}'
```

For each running service, infer its port from the plist/unit file (`--port <N>` argument) and query:

```bash
curl -fsS --max-time 10 "http://127.0.0.1:<PORT>/search?q=$(printf %s "<query>" | jq -sRr @uri)&top_k=<top_k>"
```

Merge results by score (same as step 3), label each with the service instance name (e.g. `personal@8321`, `work@8322`).

Override default REST endpoint with `SEMANTIC_SEARCH_URL` (single-server mode):

```bash
SEMANTIC_SEARCH_URL="${SEMANTIC_SEARCH_URL:-http://127.0.0.1:8321}"
```

If no service is running and `SEMANTIC_SEARCH_URL` does not respond, STOP and instruct the user:

> No semantic-search MCP server is wired up and no `semantic-search-http` service is running. Run `/semantic-search:configure` to set one up.

### 5. Render results

```
🔍 Top <N> results for "<query>"   (servers queried: personal, work)

1. [personal]    <path> (score: 0.87)
2. [work] <path> (score: 0.81)
3. [personal]    <path> (score: 0.79)
...

Source: MCP × 2   |   Source: REST × 1   |   Source: REST + MCP
```

Always indicate which servers were queried and which transport was used per server.

### 6. Suggest follow-up

- `Read <top result>` to inspect the most relevant file
- `/semantic-search:research <query>` for a deeper synthesis across multiple results
- `/semantic-search:search --server=<label> <query>` to scope to one vault if cross-vault noise is high

## Notes

- Querying multiple servers is the default — you don't need to know in advance which vault has the answer. Use `--server=<label>` to scope when the answer is clearly local.
- Semantic search finds related concepts even when terminology differs (e.g., English ↔ German).
- Scores above ~0.6 typically indicate strong relevance; below ~0.4 may be noise.
- Both MCP and REST transports hit the same warm indexer per service. MCP is preferred because it surfaces in Claude's tool list; REST is the fallback for unwired or broken-MCP-config states.
- Adding a new instance label requires extending this command's `allowed-tools` list and the "Known servers" table above.
