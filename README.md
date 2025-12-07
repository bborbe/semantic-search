# semantic-search-mcp

MCP server for semantic search over Obsidian vaults (or any markdown collection).

Uses sentence-transformers for embeddings and FAISS for vector search.

## Features

- Semantic search across markdown files
- Duplicate/similar note detection
- Auto-updates index on file changes
- Works as MCP server or standalone CLI

## Installation

Requires Python 3.10+.

```bash
# Via uvx (recommended)
uvx --from git+https://github.com/bborbe/semantic-search-mcp semantic-search-mcp serve

# Or clone and run locally
git clone https://github.com/bborbe/semantic-search-mcp
cd semantic-search-mcp
uvx --from . semantic-search-mcp serve
```

## Usage

Set `VAULT_PATH` environment variable to your vault location.

### CLI

```bash
# Search for related notes
VAULT_PATH=/path/to/vault semantic-search-mcp search trading strategy

# Find duplicates of a file
VAULT_PATH=/path/to/vault semantic-search-mcp duplicates "path/to/note.md"

# Start MCP server
VAULT_PATH=/path/to/vault semantic-search-mcp serve
```

### MCP Configuration

Add to your `.claude/mcp.json`:

```json
{
  "mcpServers": {
    "semantic-search": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/bborbe/semantic-search-mcp",
        "semantic-search-mcp",
        "serve"
      ],
      "env": {
        "VAULT_PATH": "/path/to/your/vault"
      }
    }
  }
}
```

For local development, use `--reinstall` to pick up changes:

```json
{
  "args": [
    "--reinstall",
    "--from",
    "/path/to/semantic-search-mcp",
    "semantic-search-mcp",
    "serve"
  ]
}
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_related(query, top_k=5)` | Search for semantically related notes |
| `check_duplicates(file_path)` | Find potential duplicate notes |

## Index Storage

The vector index is stored in `.semantic-search/` inside your vault:

```
vault/
├── .semantic-search/
│   ├── vector_index.faiss
│   └── index_meta.json
└── ... your notes
```

First run downloads the embedding model (~90MB) and indexes all markdown files.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `VAULT_PATH` | `./vault` | Path to markdown vault |

## Dependencies

- [sentence-transformers](https://www.sbert.net/) - Text embeddings (model: all-MiniLM-L6-v2)
- [faiss-cpu](https://github.com/facebookresearch/faiss) - Vector similarity search
- [fastmcp](https://github.com/jlowin/fastmcp) - MCP server framework
- [watchdog](https://github.com/gorakhargosh/watchdog) - File system monitoring

## License

This project is licensed under the BSD 2-Clause License - see the [LICENSE](LICENSE) file for details.
