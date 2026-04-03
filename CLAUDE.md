# CLAUDE.md

Semantic search over markdown files — MCP + REST modes.

## Dark Factory Workflow

**Never code directly.** All code changes go through the dark-factory pipeline.

### Complete Flow

**Spec-based (multi-prompt features):**

1. Create spec -> `/dark-factory:create-spec`
2. Audit spec -> `/dark-factory:audit-spec`
3. User confirms -> `dark-factory spec approve <name>`
4. dark-factory auto-generates prompts from spec
5. Audit prompts -> `/dark-factory:audit-prompt`
6. User confirms -> `dark-factory prompt approve <name>`
7. Start daemon -> `dark-factory daemon` (use Bash `run_in_background: true`)
8. dark-factory executes prompts automatically

**Standalone prompts (simple changes):**

1. Create prompt -> `/dark-factory:create-prompt`
2. Audit prompt -> `/dark-factory:audit-prompt`
3. User confirms -> `dark-factory prompt approve <name>`
4. Start daemon -> `dark-factory daemon` (use Bash `run_in_background: true`)
5. dark-factory executes prompt automatically

### Assess the change size

| Change | Action |
|--------|--------|
| Simple fix, config change, 1-2 files | Write a prompt -> `/dark-factory:create-prompt` |
| Multi-prompt feature, unclear edges, shared interfaces | Write a spec first -> `/dark-factory:create-spec` |

### Read the relevant guide before starting -- every time, not from memory

- Writing a spec -> read [[Dark Factory - Write Spec]] and [[Dark Factory Guide#Specs What Makes a Good Spec]]
- Writing prompts -> read [[Dark Factory - Write Prompts]] and [[Dark Factory Guide#Prompts What Makes a Good Prompt]]
- Running prompts -> read [[Dark Factory - Run Prompt]]

### Claude Code Commands

| Command | Purpose |
|---------|---------|
| `/dark-factory:create-spec` | Create a spec file interactively |
| `/dark-factory:create-prompt` | Create a prompt file from spec or task description |
| `/dark-factory:audit-spec` | Audit spec against preflight checklist |
| `/dark-factory:audit-prompt` | Audit prompt against Definition of Done |

### CLI Commands

| Command | Purpose |
|---------|---------|
| `dark-factory spec approve <name>` | Approve spec (inbox -> queue, triggers prompt generation) |
| `dark-factory prompt approve <name>` | Approve prompt (inbox -> queue) |
| `dark-factory daemon` | Start daemon (watches queue, executes prompts) |
| `dark-factory run` | One-shot mode (process all queued, then exit) |
| `dark-factory status` | Show combined status of prompts and specs |
| `dark-factory prompt list` | List all prompts with status |
| `dark-factory spec list` | List all specs with status |
| `dark-factory prompt retry` | Re-queue failed prompts for retry |
| `dark-factory prompt cancel <name>` | Cancel a running or queued prompt (never use `docker kill`) |

### Key rules

- Prompts go to **`prompts/`** (inbox) -- never to `prompts/in-progress/` or `prompts/completed/`
- Specs go to **`specs/`** (inbox) -- never to `specs/in-progress/` or `specs/completed/`
- Never number filenames -- dark-factory assigns numbers on approve
- Never manually edit frontmatter status -- use CLI commands above
- Always audit before approving (`/dark-factory:audit-prompt`, `/dark-factory:audit-spec`)
- **BLOCKING: Never run `dark-factory prompt approve`, `dark-factory spec approve`, or `dark-factory daemon` without explicit user confirmation.** Write the prompt/spec, then STOP and ask the user to approve.
- **Before starting daemon** -- run `dark-factory status` first to check if one is already running.
- **Start daemon in background** -- use Bash tool with `run_in_background: true` (not foreground, not detached with `&`)

## Development Standards

### Toolchain

- Python 3.14+, `uv` package manager, `hatchling` build backend
- Source at `src/semantic_search/` (src/ layout)
- Strict mypy enabled

### Build and test

- `make precommit` — format + test + lint + typecheck
- `make test` — tests only

### Test conventions

- pytest with pytest-asyncio
- Tests in `tests/test_*.py`
- `unittest.mock.patch` for mocking sentence-transformers and watchdog

## Architecture

```
src/semantic_search/
├── __main__.py      — CLI entry point (serve, search, duplicates)
├── cli.py           — One-shot CLI commands
├── factory.py       — Thread-safe singleton for indexer + watcher
├── indexer.py       — VaultIndexer (FAISS index), VaultWatcher (watchdog)
├── logging_setup.py — Logging configuration
├── rest_server.py   — REST API mode
└── server.py        — MCP server mode (fastmcp)
```

## Key Design Decisions

- **Weighted embeddings** — Filename 3x, title 3x, tags 2x, H1 2x, body 1x (see `docs/design/weighted-embedding-strategy.md`)
- **Thread-safe singleton** — `factory.py` uses Lock for lazy init of indexer + watcher
- **Temp dir for index** — FAISS index stored in `/tmp/semantic-search/{hash}/{pid}/`, not in vault
- **Full content in meta** — `self.meta` stores entire file content per entry (for future features)
- **Two server modes** — MCP (fastmcp) for Claude Code, REST for HTTP clients; share same indexer
- **src/ layout** — Package at `src/semantic_search/`, not root
