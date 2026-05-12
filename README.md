# Semantic Search

Semantic search over markdown files. Find related notes by meaning, not just keywords. Detect duplicates before creating new notes.

Supports two server transports:
- **stdio MCP** — For Claude Code integration (one process per session)
- **HTTP** — Combined MCP-over-HTTP + REST on one port; one warm process shared by all clients

## Features

- Semantic search using sentence-transformers
- Duplicate/similar note detection
- Auto-updating index with file watcher
- Multi-directory support
- Inline tag extraction (`#tag-name`)

## Install

**CPU-only install** — recommended for **macOS** (any Mac, Apple Silicon or Intel) and **Linux/Windows without an NVIDIA GPU**. Saves ~5GB of CUDA binaries. On macOS, Apple GPU (MPS) is still auto-detected and used via PyTorch's built-in MPS backend — the "CPU" label refers only to the absence of CUDA, not to the compute device at runtime.

```bash
uv tool install --index https://download.pytorch.org/whl/cpu \
  git+https://github.com/bborbe/semantic-search
```

**CUDA install** — only for **Linux/Windows with a dedicated NVIDIA GPU**. Not applicable to macOS (NVIDIA CUDA is not supported on Mac).

```bash
uv tool install git+https://github.com/bborbe/semantic-search
```

## Upgrade

```bash
uv tool upgrade semantic-search
```

## Server Modes

### stdio MCP (per-session Claude Code)

Spawns one process per Claude Code session. Simple, but each session loads its own ~400 MB–1 GB model copy.

```bash
claude mcp add -s project semantic-search \
  --env CONTENT_PATH=/path/to/vault \
  -- \
  uvx --from git+https://github.com/bborbe/semantic-search semantic-search-mcp serve
```

**Tools available:**
- `search_related(query, top_k=5)` — Find semantically related notes
- `check_duplicates(file_path)` — Detect duplicate/similar notes

### HTTP (shared across all clients)

Single long-running process serves MCP-over-HTTP at `/mcp` plus REST at `/search`, `/duplicates`, `/health`, `/reindex`. All Claude Code sessions and REST clients share one warm indexer.

```bash
CONTENT_PATH=/path/to/vault semantic-search-http --host 127.0.0.1 --port 8321
```

Point Claude Code at it via MCP config:

```json
{
  "mcpServers": {
    "semantic-search": {
      "type": "http",
      "url": "http://127.0.0.1:8321/mcp"
    }
  }
}
```

**REST endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp` | POST | MCP-over-HTTP (Claude Code) |
| `/search?q=...&top_k=5` | GET | Semantic search |
| `/duplicates?file=...&threshold=0.85` | GET | Find duplicate notes |
| `/health` | GET | Health check with index stats |
| `/reindex` | GET/POST | Force index rebuild |

**Example queries:**
```bash
# Search
curl 'http://127.0.0.1:8321/search?q=kubernetes+deployment'

# Find duplicates
curl 'http://127.0.0.1:8321/duplicates?file=notes/my-note.md'

# Health check
curl 'http://127.0.0.1:8321/health'
```

## Claude Code Plugin

This repo also ships as a Claude Code marketplace plugin with commands for setup, search, and research.

### Install

```bash
claude plugin marketplace add bborbe/semantic-search
claude plugin install semantic-search
```

### Update

```bash
claude plugin marketplace update semantic-search
claude plugin update semantic-search@semantic-search
```

### Quick Start

```bash
# One-shot interactive setup: installs the binary, writes the launchd/systemd
# unit, registers the MCP server in your Claude config.
/semantic-search:configure

# Search indexed markdown
/semantic-search:search kubernetes deployment

# Multi-step research across results
/semantic-search:research kafka backup strategy
```

### Commands

| Command | Description |
|---------|-------------|
| `/semantic-search:configure` | Install `semantic-search-http` as a launchd (macOS) or systemd-user (Linux) service and register the MCP server in Claude Code |
| `/semantic-search:search <query> [top_k]` | Semantic search via the running MCP server |
| `/semantic-search:research <topic>` | Multi-step research — search, categorize, read top sources, synthesize |

## Run in Background

For production-style usage, run `semantic-search-http` as a background service so every Claude Code session (and any REST client) shares one warm process.

| Platform | Guide |
|----------|-------|
| macOS (launchd) | [`docs/launchd-service.md`](docs/launchd-service.md) |
| Linux (systemd) | [`docs/systemd-user-service.md`](docs/systemd-user-service.md) |

Quick example (macOS):

```bash
launchctl load ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist
```

Quick example (Linux):

```bash
systemctl --user enable --now semantic-search-http.service
```

## CLI Commands

One-shot commands without running a server:

```bash
# Search
CONTENT_PATH=/path/to/vault semantic-search search "kubernetes deployment"

# Find duplicates
CONTENT_PATH=/path/to/vault semantic-search duplicates path/to/note.md
```

## Binaries

| Binary | Purpose |
|--------|---------|
| `semantic-search-http` | Combined HTTP server — MCP at `/mcp` + REST endpoints. Run once, share across clients. |
| `semantic-search-mcp` | stdio MCP server — one per Claude Code session. Use when HTTP service is not set up. |
| `semantic-search` | CLI only — `search` and `duplicates` one-shot commands. |

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CONTENT_PATH` | Directory to index (comma-separated for multiple) | `./content` |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | `INFO` |

### Multiple Directories

Index multiple directories by separating paths with commas:

```bash
CONTENT_PATH=/path/to/vault1,/path/to/vault2,/path/to/docs
```

All directories are indexed together and searched as one unified index.

## How It Works

First run downloads a small embedding model (~90MB) and indexes your markdown files (<1s for typical vaults). The index auto-updates when files change via filesystem watcher.

### Indexed Content

Each markdown file is indexed with weighted components:

| Component | Weight | Notes |
|-----------|--------|-------|
| Filename | 3x | |
| Frontmatter `title` | 3x | |
| Frontmatter `tags` | 2x | Merged with inline tags |
| Frontmatter `aliases` | 2x | |
| Inline tags (`#tag`) | 2x | Extracted from body |
| First H1 heading | 2x | |
| Body content | 1x | First 500 words |

## Development

```bash
# Clone
git clone https://github.com/bborbe/semantic-search
cd semantic-search

# Install dev dependencies
make install

# Run checks
make check

# Run tests
make test
```

## License

BSD 2-Clause License — see [LICENSE](LICENSE).
