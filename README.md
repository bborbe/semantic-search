# Semantic Search MCP

MCP server that adds semantic search over markdown files to Claude Code. Find related notes by meaning, not just keywords. Detect duplicates before creating new notes.

## Installation

From GitHub:
```bash
claude mcp remove -s project semantic-search
claude mcp add -s project semantic-search \
--env CONTENT_PATH=/path/to/your/content \
-- \
uvx --from git+https://github.com/bborbe/semantic-search-mcp semantic-search-mcp serve
```

Local development:
```bash
claude mcp remove -s project semantic-search
claude mcp add -s project semantic-search \
--env CONTENT_PATH=/path/to/your/content \
-- \
uvx --reinstall --from /path/to/semantic-search-mcp semantic-search-mcp serve
```

Replace `/path/to/your/content` with your markdown directory (e.g., Obsidian vault path).

## Usage

Ask Claude:
- "Do I have notes about X?" → searches related notes
- "Find similar notes to Y" → finds semantically related content
- Before creating new notes → Claude checks for duplicates automatically

## Tools

- `search_related(query, top_k=5)` - Find semantically related notes
- `check_duplicates(file_path)` - Detect duplicate/similar notes

## How It Works

First run downloads a small embedding model (~90MB) and indexes your markdown files (<1s for typical vaults). Each Claude Code session gets its own index in `/tmp/` that auto-updates when files change. Multiple sessions work independently without conflicts.

## License

This project is licensed under the BSD 2-Clause License - see the [LICENSE](LICENSE) file for details.
