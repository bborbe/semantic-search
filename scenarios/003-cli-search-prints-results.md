---
status: active
---

# Scenario 003: CLI one-shot search prints results

Validates that the installed `semantic-search` entry point (the one-shot CLI from `pyproject.toml` `[project.scripts]`) runs against a content directory and prints results to stdout — covering the third user-facing binary and catching packaging/entry-point regressions that unit tests cannot.

## Setup
- [ ] Working tree is the repo under change (`cd ~/Documents/workspaces/semantic-search`)
- [ ] `uv sync` has been run; `uv run semantic-search --help` works
- [ ] Prepare a content directory: `CONTENT_DIR=$(scenarios/helper/setup-content-dir.sh)` (corpus includes a `kubernetes.md`)

## Action
- [ ] Run the CLI search and capture both streams:
  ```bash
  CONTENT_PATH="$CONTENT_DIR" uv run semantic-search search kubernetes \
    > /tmp/scenario-003-out.txt 2> /tmp/scenario-003-err.txt
  echo "exit=$?" > /tmp/scenario-003-exit.txt
  ```

## Expected
- [ ] Exit code is 0: `grep -q '^exit=0$' /tmp/scenario-003-exit.txt`
- [ ] Stdout is non-empty: `[ -s /tmp/scenario-003-out.txt ]`
- [ ] Stdout contains at least one result row referencing a `.md` file: `grep -E '\.md(\s|:|$)' /tmp/scenario-003-out.txt` matches
- [ ] Stdout contains no log-level keywords (logging must not bleed into the result channel): `grep -E '\b(INFO|DEBUG|WARNING|ERROR|CRITICAL)\b\s+\[' /tmp/scenario-003-out.txt` returns no matches

## Cleanup
- `rm -f /tmp/scenario-003-out.txt /tmp/scenario-003-err.txt /tmp/scenario-003-exit.txt`
- `rm -rf "$CONTENT_DIR"` if the helper created it
