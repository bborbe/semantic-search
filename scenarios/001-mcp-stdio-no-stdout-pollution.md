---
status: active
---

# Scenario 001: MCP stdio server keeps stdout clean

Validates that `semantic-search-mcp serve` writes only JSON-RPC frames to stdout while all log output goes to stderr, so MCP clients (Claude Code, Cursor) receive a clean protocol channel.

## Setup
- [ ] Working tree is the repo under change (`cd ~/Documents/workspaces/semantic-search`)
- [ ] `uv sync` has been run; `uv run semantic-search-mcp --help` works
- [ ] Prepare a content directory: `CONTENT_DIR=$(scenarios/helper/setup-content-dir.sh)`

## Action
- [ ] Run a one-shot `initialize` request via the helper, capturing streams separately:
  ```bash
  CONTENT_PATH="$CONTENT_DIR" scenarios/helper/probe-mcp.sh \
    /tmp/scenario-001-out.txt /tmp/scenario-001-err.txt
  ```

## Expected
- [ ] `/tmp/scenario-001-out.txt` exists and is non-empty
- [ ] First non-empty line of stdout is valid JSON: `head -1 /tmp/scenario-001-out.txt | jq . > /dev/null` exits 0
- [ ] Stdout contains no plain log lines: `grep -Ev '^\s*$|^\{' /tmp/scenario-001-out.txt` returns no matches (every non-empty line begins with `{`)
- [ ] Stdout contains no log-level keywords: `grep -E '\b(INFO|DEBUG|WARNING|ERROR|CRITICAL)\b\s+\[' /tmp/scenario-001-out.txt` returns no matches
- [ ] `/tmp/scenario-001-err.txt` is non-empty and contains at least one log line: `grep -E '\b(INFO|DEBUG|WARNING|ERROR)\b' /tmp/scenario-001-err.txt` matches at least once

## Cleanup
- `rm -f /tmp/scenario-001-out.txt /tmp/scenario-001-err.txt`
- `rm -rf "$CONTENT_DIR"` if the helper created it for this scenario
