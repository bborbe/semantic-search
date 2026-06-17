---
allowed-tools: Task
argument-hint: "<topic> [--server=<label>] [--max-iters=<N>] [--workspace=<dir>]"
description: Goal-directed vault exploration — Planner/Generator/Evaluator loop with filesystem state, stops when topic coverage is satisfied
---

## Usage

```
/semantic-search:explorer obsidian workflow
/semantic-search:explorer "kafka backup strategy" --max-iters=12
/semantic-search:explorer --server=personal raw schema cdb pipeline
/semantic-search:explorer claude code agents --workspace=~/explorer-runs/claude
```

## When to use

- `/semantic-search:search` — one-shot ranked list, no traversal
- `/semantic-search:research` — multi-source synthesis from top-N hits, no link traversal, no external exploration
- **`/semantic-search:explorer`** — goal-directed exploration with self-assessed stopping: **planner first understands what the user is actually asking** and decomposes it into concrete sub-questions, generator explores (vault pages, semantic searches, external repos/URLs), evaluator decides when each sub-question is answered

## Process

### 1. Parse arguments

- First non-flag string → `<topic>` (required; show usage and STOP if missing)
- `--server=<label>` → scope semantic search to one MCP server (`personal`, `work`, `(default)`); omit to query all
- `--max-iters=<N>` → hard cap on generator iterations (default `20`)
- `--workspace=<dir>` → explicit workspace path; default `$HOME/.semantic-search-explorer/<topic-slug>-<unix-ts>/`

### 2. Compute the workspace path

Default: `$HOME/.semantic-search-explorer/<topic-slug>-<unix-ts>/`. The agent creates the directory on first write and owns the filesystem layout. Preserved after the run for inspection.

### 3. Invoke the explorer-assistant agent

```
Task tool with:
  subagent_type: 'semantic-search:explorer-assistant'
  prompt: 'Explore topic <topic>. Workspace: <WORKSPACE>. Server scope: <server or "all">. Max iterations: <N>.'
```

The agent runs the Planner → Generator → Evaluator loop, writes synthesis to `<WORKSPACE>/synthesis.md`, and returns a short report.

### 4. Present the result

Print the agent's report verbatim. Always include the workspace path so the user can inspect `spec.md`, `notes/`, `visited.md`, `findings.md`, `synthesis.md`. The agent owns the exact format.

## Notes

- The agent owns the loop, satisfaction check, move selection, and stop logic. This command is the thin interface.
- **Filesystem state, not context.** Long explorations don't blow context window — each Evaluator pass reads workspace files fresh.
- **External exploration is in scope.** When `spec.md` or `notes/` reference git repos or URLs, the agent may fetch them (`gh`, `git`, `WebFetch`) — bounded by `--max-iters`.
- For "find one specific file" use `/semantic-search:search`; for "synthesize top-N hits without exploration" use `/semantic-search:research`.
- Workspace dirs accumulate under `~/.semantic-search-explorer/`. Clean periodically — they are not auto-deleted.
