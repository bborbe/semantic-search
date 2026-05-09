---
allowed-tools: mcp__semantic-search__search_related, mcp__semantic-search-personal__search_related, mcp__semantic-search-work__search_related, Bash, Read, Grep, Glob
argument-hint: <topic> [--server=<label>]
description: Multi-step research over indexed markdown — semantic search across all available MCP servers (or scoped via --server), read top files, synthesize findings
---

## Usage

```
/semantic-search:research kafka backup strategy
/semantic-search:research "obsidian git workflow"
/semantic-search:research --server=work sentry alerting flow
```

## Process

### Step 1: Validate input

- First non-flag argument → `topic`
- `--server=<label>` (optional) → scope to a single MCP server; omit to query every available server
- If no topic: show usage and STOP.

### Step 2: Initial semantic search

**Discover available MCP servers** (must match this command's `allowed-tools`):

| Tool name | Conventional label |
|---|---|
| `mcp__semantic-search__search_related` | `(default)` |
| `mcp__semantic-search-personal__search_related` | `personal` |
| `mcp__semantic-search-work__search_related` | `work` |

If `--server=<label>` was passed: keep only the matching tool. Else: attempt all three in parallel and treat any tool that errors with "not available" / "unknown tool" as absent.

**Query all available MCP servers** with `top_k=10` each:

```
<tool-name>(query=<topic>, top_k=10)
```

Run in parallel. Each result list is tagged with its server label. Merge by `score` descending; keep top 10 overall.

**Fall back to REST** if no MCP server is wired up:

```bash
# macOS
launchctl list 2>/dev/null | awk '/com\.github\.bborbe\.semantic-search-http/ {print $3}'
# Linux
systemctl --user list-units 'semantic-search-http*' --no-legend 2>/dev/null | awk '{print $1}'
```

For each running service, infer port from plist/unit and query:

```bash
curl -fsS --max-time 10 "http://127.0.0.1:<PORT>/search?q=$(printf %s "<topic>" | jq -sRr @uri)&top_k=10"
```

Override default with `SEMANTIC_SEARCH_URL` if needed.

**Last resort** if both MCP and REST fail:

```
Grep pattern="<topic keywords>" path=<content roots> -i --files-with-matches
```

Note in the final report which transport(s) were used and which servers were queried.

### Step 3: Categorize results

Sort merged results into buckets based on path:

- **Guides / Reference** — paths containing `guide`, `hub`, `framework`, or located in known knowledge folders
- **Operational / Runbooks** — paths containing `runbook`, `alert`, or in `*Runbooks*` folders
- **Notes / Tasks** — daily notes, task files, miscellaneous

When results come from multiple servers, also note the cross-vault distribution (e.g. *"6 hits from `personal`, 4 from `work`"*) — clusters across vaults often signal a real cross-cutting concept.

### Step 4: Read top sources

Read the top 3–5 most relevant files (skip duplicates of the same concept). Use `Read` directly; do not stream the entire file if it's long — read first 200 lines and grep for the topic if needed.

### Step 5: Optional follow-up searches

If the initial query has obvious related terms surfaced in step 4 (synonyms, related concepts), run 1–2 targeted follow-up `search_related` (or REST `/search`) calls to fill gaps. Cap total searches at 3. Reuse the same multi-server fan-out unless `--server=<label>` was set.

### Step 6: Synthesize findings

Output a single concise report:

```
🔬 Research: <topic>   (servers queried: personal, work)

## Key Findings
- <finding 1> — source: [personal] <path>
- <finding 2> — source: [work] <path>

## Reference Docs (read first)
1. [personal] <path> — <one-line why>
2. [work] <path> — <one-line why>

## Related Concepts
- <concept> — see <path>

## Confidence
High / Medium / Low — <one-line reason>

Source: MCP × 2   |   Source: REST × 1   |   Source: Grep fallback
```

Always tag each cited path with its source server label.

## Notes

- Multi-server query is the default — useful when a concept (e.g. *"sentry flow"*) lives in one vault and supporting docs live in another.
- Cap total file reads at ~5 to avoid context bloat.
- If the topic spans multiple distinct concepts, surface that and ask the user to narrow.
- Prefer guides over daily notes when both surface for the same concept.
- For tasks that are mostly "find one specific file", use `/semantic-search:search` instead — `research` is for synthesis across multiple sources.
- Adding a new instance label requires extending this command's `allowed-tools` list and the "Known servers" table above.
