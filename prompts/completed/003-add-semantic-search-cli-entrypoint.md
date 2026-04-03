---
status: completed
summary: Added semantic-search CLI binary exposing only search and duplicates commands, with tests and README/CHANGELOG updates.
container: semantic-search-003-add-semantic-search-cli-entrypoint
dark-factory-version: v0.89.1-dirty
created: "2026-04-03T12:18:45Z"
queued: "2026-04-03T12:18:45Z"
started: "2026-04-03T12:18:48Z"
completed: "2026-04-03T12:22:36Z"
---

<summary>
- A new CLI binary "semantic-search" is available for one-shot search and duplicate detection
- The existing "semantic-search-mcp" binary continues to work unchanged for MCP/REST server mode
- The CLI binary does not expose the serve command, preventing accidental server starts
- Users get a clean separation between server operation and command-line queries
- Tests verify the CLI binary rejects the serve command
</summary>

<objective>
Add a second entry point `semantic-search` that exposes only the CLI commands (`search`, `duplicates`) without the `serve` command. The existing `semantic-search-mcp` entry point remains unchanged with full functionality.
</objective>

<context>
Read CLAUDE.md for project conventions.
Read `src/semantic_search/__main__.py` — find `main()` and `_print_usage()` functions.
Read `pyproject.toml` — find `[project.scripts]` section, currently has one entry: `semantic-search-mcp`.

Currently `semantic-search-mcp` handles all subcommands: `serve`, `search`, `duplicates`. The goal is to split this so that:
- `semantic-search-mcp serve` — starts MCP or REST server (unchanged)
- `semantic-search search <query>` — one-shot search
- `semantic-search duplicates <file>` — one-shot duplicate check
- `semantic-search serve` — should NOT work (not a valid command)
</context>

<requirements>
1. In `src/semantic_search/__main__.py`, add a `main_cli()` function that:
   - Configures logging (same as `main()`)
   - Only accepts `search` and `duplicates` subcommands
   - Has its own `_print_cli_usage()` showing only CLI commands, with binary name `semantic-search`
   - Prints usage and exits with code 1 if `serve` or unknown command is given

2. In `pyproject.toml` `[project.scripts]`, add a second entry:
   ```
   semantic-search = "semantic_search.__main__:main_cli"
   ```
   Keep the existing `semantic-search-mcp` entry unchanged.

3. Update `README.md`:
   - In the "MCP Mode" and "REST Mode" sections, use `semantic-search-mcp` for server examples
   - In the "CLI Commands" section, use `semantic-search` for one-shot examples
   - Add a brief note explaining the two binaries

4. In `tests/test_main.py` (new file), add a test that verifies `main_cli()` exits with `SystemExit(1)` when given `serve` as argument. Do not modify existing `_print_usage()` — it belongs to `main()`.
</requirements>

<constraints>
- Do NOT commit — dark-factory handles git
- Existing tests must still pass
- Do not change the behavior of `main()` or the `semantic-search-mcp` binary
- Do not duplicate logic — `main_cli()` should reuse the same `search()` and `duplicates()` imports from `cli.py`
</constraints>

<verification>
Run `make precommit` -- must pass.
</verification>
