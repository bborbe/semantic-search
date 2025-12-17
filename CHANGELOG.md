# Changelog

All notable changes to this project will be documented in this file.

Please choose versions by [Semantic Versioning](http://semver.org/).

* MAJOR version when you make incompatible API changes,
* MINOR version when you add functionality in a backwards-compatible manner, and
* PATCH version when you make backwards-compatible bug fixes.

## v0.1.1

- Add authorization restrictions to GitHub Actions workflows
- Restrict @claude trigger to bborbe and trusted collaborators only

## v0.1.0

- Add semantic search over markdown files using sentence-transformers
- Add duplicate/similar note detection
- Add MCP server with `search_related` and `check_duplicates` tools
- Add auto-updating index with file watcher
- Store indexes in temp directory for multi-session isolation
