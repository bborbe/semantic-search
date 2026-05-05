---
allowed-tools: mcp__semantic-search__search_related, Bash, Read, Grep, Glob
argument-hint: <topic>
description: Multi-step research over indexed markdown — semantic search (MCP or REST), read top files, synthesize findings
---

## Usage

```
/semantic-search:research kafka backup strategy
/semantic-search:research "obsidian git workflow"
```

## Process

### Step 1: Validate input

If no topic provided, show usage and STOP.

### Step 2: Initial semantic search

**Try MCP first:**

```
mcp__semantic-search__search_related(query=<topic>, top_k=10)
```

**Fall back to REST** if MCP is unavailable or errors:

```bash
SEMANTIC_SEARCH_URL="${SEMANTIC_SEARCH_URL:-http://127.0.0.1:8321}"
curl -fsS --max-time 10 "${SEMANTIC_SEARCH_URL}/search?q=$(printf %s "<topic>" | jq -sRr @uri)&top_k=10"
```

**Last resort** if both fail:

```
Grep pattern="<topic keywords>" path=<content roots> -i --files-with-matches
```

Note in the final report which transport was used (MCP / REST / Grep).

### Step 3: Categorize results

Sort results into buckets based on path:

- **Guides / Reference** — paths containing `guide`, `hub`, `framework`, or located in known knowledge folders
- **Operational / Runbooks** — paths containing `runbook`, `alert`, or in `*Runbooks*` folders
- **Notes / Tasks** — daily notes, task files, miscellaneous

### Step 4: Read top sources

Read the top 3–5 most relevant files (skip duplicates of the same concept). Use `Read` directly; do not stream the entire file if it's long — read first 200 lines and grep for the topic if needed.

### Step 5: Optional follow-up searches

If the initial query has obvious related terms surfaced in step 4 (synonyms, related concepts), run 1–2 targeted follow-up `search_related` (or REST `/search`) calls to fill gaps. Cap total searches at 3.

### Step 6: Synthesize findings

Output a single concise report:

```
🔬 Research: <topic>

## Key Findings
- <finding 1> — source: <path>
- <finding 2> — source: <path>

## Reference Docs (read first)
1. <path> — <one-line why>
2. <path> — <one-line why>

## Related Concepts
- <concept> — see <path>

## Confidence
High / Medium / Low — <one-line reason>

Source: MCP   |   Source: REST (http://127.0.0.1:8321)   |   Source: Grep fallback
```

## Notes

- Cap total file reads at ~5 to avoid context bloat.
- If the topic spans multiple distinct concepts, surface that and ask the user to narrow.
- Prefer guides over daily notes when both surface for the same concept.
- For tasks that are mostly "find one specific file", use `/semantic-search:search` instead — `research` is for synthesis across multiple sources.
- Override the REST URL with `SEMANTIC_SEARCH_URL` if your service runs on a non-default host/port.
- Both MCP and REST hit the same warm indexer when the HTTP service is running.
