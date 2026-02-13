# Semantic Search

Semantic search over markdown files. Find related notes by meaning, not just keywords. Detect duplicates before creating new notes.

Supports two server modes:
- **MCP mode** — For Claude Code integration
- **REST mode** — For OpenClaw, scripts, and HTTP clients

## Features

- Semantic search using sentence-transformers
- Duplicate/similar note detection
- Auto-updating index with file watcher
- Multi-directory support
- Inline tag extraction (`#tag-name`)

## Installation

```bash
# Run directly with uvx (no install needed)
uvx --from git+https://github.com/bborbe/semantic-search semantic-search-mcp serve

# Or install locally
pip install git+https://github.com/bborbe/semantic-search
```

## Server Modes

### MCP Mode (for Claude Code)

```bash
claude mcp add -s project semantic-search \
  --env CONTENT_PATH=/path/to/vault \
  -- \
  uvx --from git+https://github.com/bborbe/semantic-search semantic-search-mcp serve
```

**Tools available:**
- `search_related(query, top_k=5)` — Find semantically related notes
- `check_duplicates(file_path)` — Detect duplicate/similar notes

### REST Mode (for OpenClaw/HTTP)

```bash
# Start server
CONTENT_PATH=/path/to/vault semantic-search-mcp serve --mode rest --port 8321

# Or with uvx
CONTENT_PATH=/path/to/vault uvx --from git+https://github.com/bborbe/semantic-search \
  semantic-search-mcp serve --mode rest --port 8321
```

**Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search?q=...&top_k=5` | GET | Semantic search |
| `/duplicates?file=...&threshold=0.85` | GET | Find duplicate notes |
| `/health` | GET | Health check with index stats |
| `/reindex` | GET/POST | Force index rebuild |

**Example queries:**
```bash
# Search
curl 'http://localhost:8321/search?q=kubernetes+deployment'

# Find duplicates
curl 'http://localhost:8321/duplicates?file=notes/my-note.md'

# Health check
curl 'http://localhost:8321/health'
```

## CLI Commands

One-shot commands without running a server:

```bash
# Search
CONTENT_PATH=/path/to/vault semantic-search-mcp search "kubernetes deployment"

# Find duplicates
CONTENT_PATH=/path/to/vault semantic-search-mcp duplicates path/to/note.md
```

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
