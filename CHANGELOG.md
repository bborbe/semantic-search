# Changelog

All notable changes to this project will be documented in this file.

Please choose versions by [Semantic Versioning](http://semver.org/).

* MAJOR version when you make incompatible API changes,
* MINOR version when you add functionality in a backwards-compatible manner, and
* PATCH version when you make backwards-compatible bug fixes.

## Unreleased

- Add REST server mode for OpenClaw and HTTP clients
- New `--mode rest` option for serve command
- REST endpoints: /search, /duplicates, /health, /reindex
- Configurable port with `--port` flag (default: 8321)
- Index stays in memory with file watching (same as MCP mode)
- Improve README with uv tool install instructions and CPU-only note
- Add CI workflow for lint, typecheck, and tests

## v0.6.0

- Upgrade to Python 3.14 from 3.12
- Update major dependencies: anyio 4.12.1, cryptography 46.0.4, fastmcp 2.14.5, huggingface-hub 1.3.7, numpy 2.4.2
- Update development tools: mypy 1.19.1, ruff with enhanced rule set
- Add croniter dependency for scheduling support
- Improve type safety with stricter mypy configuration

## v0.5.1

- Fix tilde expansion in vault paths (CONTENT_PATH with ~/ now works correctly)
- Add test coverage for tilde path expansion

## v0.5.0

- Add inline tag extraction from markdown content (#tag syntax)
- Merge inline tags with frontmatter tags (lowercase, deduplicated)
- Add comprehensive test coverage for tag extraction edge cases
- Support special characters in tags (hyphens, underscores, slashes)
- Update documentation with indexed content details

## v0.4.1

- Extract factory function to dedicated factory.py module (composition root pattern)
- Add logging_setup.py for centralized logging configuration
- Add LOG_LEVEL environment variable support for runtime log control
- Add comprehensive CLI exception handling (FileNotFoundError, OSError, KeyboardInterrupt)
- Add proper exit codes for CLI errors (130 for Ctrl+C interruption)

## v0.4.0

- Migrate package to modern src/ layout (semantic_search_mcp/ → src/semantic_search_mcp/)
- Migrate build system from setuptools to hatchling
- Enable strict mypy mode with targeted type ignore overrides (global ignore → 2 packages)
- Add enhanced type annotations for watchdog (replace 4 Any types with proper types)
- Add thread safety to lazy-initialized globals in server.py
- Add pytest async configuration for fastmcp compatibility
- Add __version__ string to package __init__.py
- Improve type guard pattern in cli.py (replace assert with explicit check)
- Simplify Makefile (37 → 22 lines, remove verbose echo statements)
- Add comprehensive ruff rules (SIM, RUF)
- Add readme field to pyproject.toml
- Update mypy source path to src/ directory
- Add verbose output to pytest in Makefile

## v0.3.0

- Add comprehensive type hints throughout codebase
- Replace print() with proper logging (info/warning/error levels)
- Enable strict mypy type checking (disallow_untyped_defs)
- Add types-PyYAML dependency for YAML type stubs
- Add C4 (flake8-comprehensions) to ruff lint rules
- Add BSD-2-Clause license to pyproject.toml
- Add install target to Makefile
- Include typecheck in Makefile check target
- Rename index_file() to add_file_to_index() to avoid naming collision

## v0.2.1

- Add ruff linting and formatting to Makefile
- Fix lint errors (ambiguous variable names, strict zip)
- Improve pyproject.toml with ruff and mypy config

## v0.2.0

- Add multi-directory support via comma-separated CONTENT_PATH
- Add pytest test suite with 11 tests
- Add Makefile with test target

## v0.1.1

- Add authorization restrictions to GitHub Actions workflows
- Restrict @claude trigger to bborbe and trusted collaborators only

## v0.1.0

- Add semantic search over markdown files using sentence-transformers
- Add duplicate/similar note detection
- Add MCP server with `search_related` and `check_duplicates` tools
- Add auto-updating index with file watcher
- Store indexes in temp directory for multi-session isolation
