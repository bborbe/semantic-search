---
status: completed
summary: 'Renamed package from semantic-search-mcp to semantic-search: moved src/semantic_search_mcp to src/semantic_search, updated pyproject.toml, server.py, __main__.py, all test files, README.md, CLAUDE.md, and added CHANGELOG entry'
container: semantic-search-001-rename-package-to-semantic-search
dark-factory-version: v0.89.1-dirty
created: "2026-04-03T11:24:01Z"
queued: "2026-04-03T11:24:01Z"
started: "2026-04-03T11:24:13Z"
completed: "2026-04-03T11:38:28Z"
---

<summary>
- Rename the Python package from semantic_search_mcp to semantic_search
- Rename the CLI binary from semantic-search-mcp to semantic-search
- Move the source directory to match the new package name
- Update all imports, test references, and documentation to use the new name
- The MCP server name changes from "semantic-search-mcp" to "semantic-search"
</summary>

<objective>
Rename the entire package from `semantic_search_mcp` to `semantic_search`. The binary entry point changes from `semantic-search-mcp` to `semantic-search`. The project name in pyproject.toml changes from `semantic-search-mcp` to `semantic-search`. All internal imports, test patches, CLI usage strings, and documentation must reflect the new name.
</objective>

<context>
Read CLAUDE.md for project conventions and architecture.

The package currently lives at `src/semantic_search_mcp/`. Every Python file imports from `semantic_search_mcp`. Tests use `patch("semantic_search_mcp....")`. The CLI binary is registered as `semantic-search-mcp` in pyproject.toml `[project.scripts]`.

Files that need changes:
- `src/semantic_search_mcp/` directory → rename to `src/semantic_search/`
- `pyproject.toml` — project name, scripts entry, hatch build packages
- `src/semantic_search_mcp/server.py` — FastMCP name string
- `src/semantic_search_mcp/__main__.py` — usage/help strings
- `tests/test_imports.py` — import paths
- `tests/test_indexer.py` — all `patch("semantic_search_mcp.indexer...")` strings
- `tests/test_watcher.py` — all `patch("semantic_search_mcp.indexer...")` strings
- `tests/test_rest_server.py` — all imports and patch strings
- `tests/__init__.py` — docstring
- `tests/conftest.py` — check for imports
- `README.md` — CLI usage examples
- `CHANGELOG.md` — historical references can stay, but add new entry
- `CLAUDE.md` — architecture section
</context>

<requirements>
1. Rename directory: `mv src/semantic_search_mcp src/semantic_search`
2. In `pyproject.toml`:
   - Change `name = "semantic-search-mcp"` to `name = "semantic-search"`
   - Change `semantic-search-mcp = "semantic_search_mcp.__main__:main"` to `semantic-search = "semantic_search.__main__:main"`
   - Change `packages = ["src/semantic_search_mcp"]` to `packages = ["src/semantic_search"]`
3. In `src/semantic_search/server.py`:
   - Change `FastMCP("semantic-search-mcp")` to `FastMCP("semantic-search")`
4. In `src/semantic_search/__main__.py`:
   - Replace all `semantic-search-mcp` in usage/help strings with `semantic-search`
5. In all test files under `tests/`:
   - Replace all `semantic_search_mcp` imports with `semantic_search`
   - Replace all `patch("semantic_search_mcp.` with `patch("semantic_search.`
   - Update `tests/__init__.py` docstring
6. In `README.md`:
   - Replace all `semantic-search-mcp` CLI references with `semantic-search`
7. In `CLAUDE.md`:
   - Replace `src/semantic_search_mcp/` with `src/semantic_search/` in architecture section
8. Add CHANGELOG.md entry under a new `## [Unreleased]` section:
   - `- Rename package from semantic-search-mcp to semantic-search (binary, imports, package name)`
9. Do NOT change historical entries in CHANGELOG.md
</requirements>

<constraints>
- Do NOT commit — dark-factory handles git
- Existing tests must still pass after rename
- Do not change any logic, only names/paths/strings
- Keep the same directory structure (src/ layout)
- The `egg-info` directory at root (`semantic_search_mcp.egg-info/`) can be ignored (build artifact)
</constraints>

<verification>
Run `make precommit` -- must pass.
</verification>
