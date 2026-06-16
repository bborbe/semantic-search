# Changelog

All notable changes to this project will be documented in this file.

Please choose versions by [Semantic Versioning](http://semver.org/).

* MAJOR version when you make incompatible API changes,
* MINOR version when you add functionality in a backwards-compatible manner, and
* PATCH version when you make backwards-compatible bug fixes.

## Unreleased

- docs: add CI and DeepWiki badges to README

## v0.16.0

- feat: add `--version` / `-V` to all three CLIs (`semantic-search`, `semantic-search-mcp`, `semantic-search-http`)

## v0.15.2

- chore: Lower `requires-python` floor from `>=3.14` to `>=3.13` and move ruff `target-version` / mypy `python_version` in lockstep. Unblocks installation in containers that ship only Python 3.13 (Debian 13 trixie default, Ubuntu 24.04 LTS after the 3.12 window). No source code change; `uv.lock` regenerated against the new floor.

## v0.15.1

- fix(indexer): `get_content` rejected valid paths whose vault root crossed a symlink (e.g. macOS `/tmp` → `/private/tmp`). The validator now resolves vault roots before the `is_relative_to` comparison.
- test: add regression `test_unresolved_vault_path_with_symlink_root_accepted`; align `/content` 503 test with `/search` pattern (patch `_build_indexer_in_background`).
- chore(scenarios): add `004-http-content-fetch-happy-path.md` and `005-http-content-fetch-error-responses.md` covering real-socket end-to-end coverage that `TestClient` cannot exercise.
- chore: bump plugin JSONs (`.claude-plugin/plugin.json`, `marketplace.json`) from 0.11.0 to 0.15.1 to align with the binary version (catches up on docs changes shipped since v0.11.0).

## v0.15.0

- feat: Add `get_content` MCP tool and `GET /content` REST endpoint for retrieving file content from indexed vaults. Supports full-file and query-focused snippet modes. Enables remote deployment of semantic-search clients without filesystem access to the vault directory.

## v0.14.0

- feat: add `get_content` MCP tool exposing `VaultIndexer.get_content()` to the MCP protocol layer

## v0.13.0

- feat: add `GET /content` REST endpoint exposing `VaultIndexer.get_content()` over HTTP

## v0.12.0

## v0.11.1

- fix: route logger output to stderr (was stdout, corrupted stdio MCP protocol channel during serve mode)

## v0.11.0

- feat: `/semantic-search:search` and `/semantic-search:research` now query every available semantic-search MCP instance and merge results by score. Add `--server=<label>` flag to scope to a single instance. Conventional labels (`personal`, `work`) listed in each command's `allowed-tools`; custom labels reach via REST fallback (port-scanning of running `semantic-search-http` services).
- chore: extract `check-versions` to `scripts/check-versions.sh` (4-field locked check: CHANGELOG top + `plugin.json` + `marketplace.json` `metadata.version` + `plugins[0].version`); add `make check-versions` and `make release-check` (`precommit + check-versions`). Aligns with `dark-factory` / `vault-cli` / `coding` release-gate shape.
- docs: add `docs/releasing-semantic-search.md` covering two-surface release model (Python pkg via `hatch-vcs` + plugin), version alignment, install gate, plugin release procedure.

## v0.10.5

- feat: Adopt `hatch-vcs` so the Python package version is derived from git tags. `pyproject.toml` no longer carries a manual `version` field — `git tag vX.Y.Z` is now the single source of truth. `__version__` reads from a generated `src/semantic_search/_version.py` (gitignored). No more drift between tag, package version, and what `uv tool list` reports.

## v0.10.4

- fix: Bump `pyproject.toml` version from `0.6.2` (stale since 2025-09) to `0.10.4`. The Python package version was never bumped despite v0.7–v0.10 releases, so `uv tool list` reported `v0.6.2` regardless of which commit was installed — breaking the v0.10.3 outdated-binary detection in `/semantic-search:configure`. Going forward, plugin and Python package share a single version train.

## v0.10.3

- feat: `/semantic-search:configure` now compares installed vs. latest version (`uv tool list` vs. GitHub releases API) and offers `uv tool upgrade semantic-search` when out of date. Includes restart hints for any running launchd/systemd instances so they pick up the new binary. Skips silently if the GitHub API is unreachable.

## v0.10.2

- feat: `/semantic-search:configure` auto-suggests `max(existing port) + 1` when adding a new instance, instead of starting at 8321 and prompting on conflict. Reconfigure path reuses the existing port by default.
- docs: Explicit "each instance MUST bind a unique port" note in the multi-instance sections of `docs/launchd-service.md` and `docs/systemd-user-service.md` — TCP can't be shared, so two plists/units on the same port cause one to restart in a loop.

## v0.10.1

- chore: Replace domain-specific examples with generic placeholders (`work`, `kubernetes deployment`, `project`) across docs, CLI help text, indexer comments, and tests. Repo is public — examples should be domain-neutral.

## v0.10.0

- feat: `/semantic-search:configure` now does pre-flight detection (probes `/health`, lists existing launchd/systemd units, reads existing MCP registration) and offers Skip / Add-another-instance / Reconfigure paths instead of blindly writing a new plist.
- feat: Multi-instance support — `/configure` accepts an instance label (e.g. `personal`, `work`) that becomes the plist/unit suffix and the MCP server name suffix, so multiple `semantic-search-http` services can run side-by-side on different ports for different content domains.
- docs: Added "Multi-instance setup" sections to `docs/launchd-service.md` and `docs/systemd-user-service.md` documenting the label/port/MCP-name pattern.

## v0.9.1

- feat: `/semantic-search:search` and `/semantic-search:research` now try the MCP tool first and fall back to the REST endpoint (`http://127.0.0.1:8321/search`) when MCP is unavailable. Useful before `/semantic-search:configure` registers MCP, or when MCP config is broken but the HTTP service is up. Override the URL with `SEMANTIC_SEARCH_URL`.

## v0.9.0

- feat: Package as Claude Code marketplace plugin. Adds `.claude-plugin/{plugin,marketplace}.json` and three commands: `/semantic-search:configure` (interactive launchd/systemd-user setup + MCP registration), `/semantic-search:search` (wraps `search_related`), `/semantic-search:research` (multi-step synthesis across results). Install via `claude plugin marketplace add bborbe/semantic-search && claude plugin install semantic-search`.

## v0.8.4

- fix: Implement `on_moved` in `_VaultEventHandler` so atomic-replace writes (Obsidian, obsidian-git) keep files in the index. Without this, every Obsidian save silently dropped the file from the index because the rename phase was unhandled.
- fix: Extract `_is_path_indexable` helper so the `.md` + dotfile-segment filter applies symmetrically to both source and destination paths in move events.

## v0.8.3

- fix: HTTP server test suite — prevent background indexer build from overwriting mocked state. `_build_indexer_in_background` now skips when `_indexer_ready` is already set, and the 503-not-ready test patches the build coroutine so the race that caused 7 test failures in CI is eliminated.
- chore: ignore `/.dark-factory.log`

## v0.8.2

- fix: Disable `tqdm` progress bar in `sentence-transformers` encode calls — eliminates `'tqdm' object has no attribute 'sp'` race condition that occurred when the watcher thread and HTTP search handler called `model.encode()` concurrently. 153 production errors pre-v0.8.1; residual risk during cold-start rebuild / tombstone compaction now eliminated.

## v0.8.1

- fix: HTTP server now binds its port immediately on startup and builds the initial vault index in a background task. `/health` returns `{"status":"indexing","ready":false,...}` during the build and `{"status":"ok","ready":true,...}` once done. `/search`, `/duplicates`, and `/reindex` return HTTP 503 with a `Retry-After: 5` header while the indexer is not yet ready. Fixes connection-refused / hung requests during cold start on large vaults.

## v0.8.0

- feat: Move persistent index cache from OS temp directory to platformdirs user cache dir (macOS: ~/Library/Caches/semantic-search/, Linux: ~/.cache/semantic-search/, Windows: %LOCALAPPDATA%/semantic-search/Cache/). macOS no longer auto-cleans the cache.
- feat: One-time best-effort migration of existing tempdir cache to the new user cache location on first startup — no forced re-embed for existing users.

## v0.7.1

- fix: Run blocking VaultIndexer.search and VaultIndexer.find_duplicates calls via starlette.concurrency.run_in_threadpool so HTTP endpoints no longer block the asyncio event loop. /health and /mcp now stay responsive while a search or duplicate query is in flight.

## v0.7.0

- feat: Rename package from semantic-search-mcp to semantic-search (binary, imports, package name)
- fix: Prevent unbounded memory growth in VaultIndexer — deduplicate index entries on file modify, remove content from metadata, debounce watcher events, and replace per-delete rebuild with batched rebuild
- feat: Add `semantic-search` CLI binary exposing only `search` and `duplicates` commands (no `serve`)
- fix: Convert file watcher from full index rebuild per event to true incremental add/update/remove, ending the runaway rebuild loop that re-embedded all ~4000 vault files on every save.
- fix: Filter watcher events to ignore non-markdown files and any path with a dotfile segment (.git, .obsidian, .semantic-search, .DS_Store). Eliminates self-triggered rebuilds caused by save_index writes.
- fix: Remove process-PID from FAISS index cache path so the embedded index survives process restart (startup now loads from disk instead of re-embedding).
- feat: Tombstone-based logical delete for indexed entries; search and find_duplicates filter tombstones; index self-compacts when tombstone ratio exceeds 20%.

## v0.6.2

- Fix fastmcp + pydantic >=2.12 compatibility by pinning fastmcp>=2.12.4
- Add import smoke tests to catch dependency incompatibilities
- Improve README install/upgrade documentation

## v0.6.1

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
