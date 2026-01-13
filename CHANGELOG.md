# Changelog

All notable changes to this project will be documented in this file.

Please choose versions by [Semantic Versioning](http://semver.org/).

* MAJOR version when you make incompatible API changes,
* MINOR version when you add functionality in a backwards-compatible manner, and
* PATCH version when you make backwards-compatible bug fixes.

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
