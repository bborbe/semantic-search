---
allowed-tools: mcp__semantic-search__search_related, Read, Grep, Glob
argument-hint: <topic>
description: Multi-step research over indexed markdown — semantic search, read top files, synthesize findings
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

```
mcp__semantic-search__search_related(query=<topic>, top_k=10)
```

If the MCP server is unavailable, fall back to:

```
Grep pattern="<topic keywords>" path=<content roots> -i --files-with-matches
```

### Step 3: Categorize results

Sort results into buckets based on path:

- **Guides / Reference** — paths containing `guide`, `hub`, `framework`, or located in known knowledge folders
- **Operational / Runbooks** — paths containing `runbook`, `alert`, or in `*Runbooks*` folders
- **Notes / Tasks** — daily notes, task files, miscellaneous

### Step 4: Read top sources

Read the top 3–5 most relevant files (skip duplicates of the same concept). Use `Read` directly; do not stream the entire file if it's long — read first 200 lines and grep for the topic if needed.

### Step 5: Optional follow-up searches

If the initial query has obvious related terms surfaced in step 4 (synonyms, related concepts), run 1–2 targeted follow-up `search_related` calls to fill gaps. Cap total searches at 3.

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
```

## Notes

- Cap total file reads at ~5 to avoid context bloat.
- If the topic spans multiple distinct concepts, surface that and ask the user to narrow.
- Prefer guides over daily notes when both surface for the same concept.
- For tasks that are mostly "find one specific file", use `/semantic-search:search` instead — `research` is for synthesis across multiple sources.
