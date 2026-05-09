# Releasing Semantic Search

How to ship a new version of `semantic-search`. Mandatory reading before tagging or bumping plugin JSONs.

## Two surfaces, two version streams

semantic-search ships two artifacts that version independently but stay aligned at release time:

| Surface | Versioned by | Consumed by | Bumped how |
|---------|--------------|-------------|------------|
| **Python package** | git tag `vX.Y.Z` (via `hatch-vcs`) | end users via `uv tool install git+https://github.com/bborbe/semantic-search`; `semantic-search-http` launchd/systemd services | Operator tags `vX.Y.Z` after CHANGELOG entry written |
| **Plugin** | `.claude-plugin/plugin.json` `version` + `.claude-plugin/marketplace.json` (`metadata.version` AND `plugins[0].version`) | Claude Code via the marketplace | Manual — operator bumps the three JSON fields |

A single change can touch one surface or both. Both share **one CHANGELOG**, one version sequence.

## 🚨 Version alignment — locked at release time only

All four version strings MUST equal each other **at release time**:

1. `CHANGELOG.md` — top `## vX.Y.Z` entry
2. `.claude-plugin/plugin.json` — `"version"`
3. `.claude-plugin/marketplace.json` — `metadata.version`
4. `.claude-plugin/marketplace.json` — `plugins[0].version`

The git tag itself is the **fifth** binding — `hatch-vcs` derives `__version__` from it, so `uv tool list` and `semantic-search --version` always reflect whatever tag points at HEAD.

The check is **release-time only** — `make precommit` does NOT run it. Run the manual check (below) before tagging or before pushing plugin JSON bumps.

**Why not in `precommit`**: every refactor commit would otherwise have to bump plugin JSONs in lockstep, burning release numbers on internal work. (Vault-cli learned this the hard way; we apply the same lesson here.)

## The release gate (run BEFORE every tag or plugin bump)

`make precommit` is the available gate. It covers format + test + lint + typecheck on the Python package — but does **not** cover:

- Real MCP server behavior (fastmcp wire format)
- Real REST server behavior (HTTP contract)
- Real launchd/systemd integration
- The slash-command surfaces (`/semantic-search:search`, `:research`, `:configure`)

Until a `scenarios/` regression suite exists, the operator must manually exercise:

1. **MCP path:** restart Claude Code, run `/semantic-search:search "obsidian"` against a configured instance — must return results
2. **REST path:** `curl http://127.0.0.1:8321/search?q=test&top_k=3` — must return JSON results
3. **Configure path:** if `commands/configure.md` changed, walk a fresh install on a clean machine (or `~/.claude/mcp-*.json` snapshot)

If any path fails, do **not** tag or bump plugin. Fix the regression first.

## Version alignment check (release-time)

`scripts/check-versions.sh` enforces the locked model: top CHANGELOG entry == `plugin.json` `version` == `marketplace.json` `metadata.version` == `marketplace.json` `plugins[0].version`. Run via `make check-versions`, or via `make release-check` (which adds `make precommit` first).

```bash
make release-check          # full gate: precommit + check-versions
# or, just the version check:
make check-versions
# or directly:
bash scripts/check-versions.sh
```

The git tag is **not** checked here — `hatch-vcs` derives the Python package version from it at install time, so the tag is bound to whatever it points at by definition.

**NOT wired into `make precommit`** — see the "Version alignment" section above for why.

## Python package release (manual)

There is **no** `autoRelease` daemon for semantic-search. Every Python release is a deliberate operator action.

1. **Land all changes** for the release on `master` via the dark-factory pipeline (per `CLAUDE.md` — never code directly).
2. **Run `make precommit`** — must pass.
3. **Run the release gate** above (manual MCP/REST exercise).
4. **Edit `CHANGELOG.md`**: rename `## Unreleased` → `## vX.Y.Z` at the top, summarising every change since the previous tag (binary AND plugin in one section — there is one CHANGELOG).
5. **Run `make check-versions`** — must report `✅ all four versions equal` (this implies plugin JSONs already bumped to match — see plugin release below).
6. **Commit:** `git commit -am "release vX.Y.Z: <summary>"`.
7. **Tag and push:**

   ```bash
   git tag vX.Y.Z
   git push && git push --tags
   ```

8. **Verify:**

   ```bash
   git fetch --tags
   git describe --tags --abbrev=0
   uv tool upgrade semantic-search
   semantic-search --version    # must equal vX.Y.Z
   ```

`hatch-vcs` reads the tag at install time and writes it to `src/semantic_search/_version.py`. No `pyproject.toml` bump is ever needed.

## Plugin release (manual)

Whenever any of `commands/`, `agents/`, `docs/`, or `skills/` change, the plugin version must be bumped. The Python package release does not bump plugin JSONs.

### When to bump

```bash
LAST_PLUGIN_TAG=$(git log --oneline -- .claude-plugin/ | head -1 | awk '{print $1}')
git diff "$LAST_PLUGIN_TAG"..HEAD --name-only -- commands/ agents/ docs/ skills/
# any output → plugin needs a bump
```

### Procedure

1. **Pick the next version.** Increment minor from the latest `CHANGELOG.md` entry. Plugin and Python package share the same CHANGELOG and the same monotonic version sequence.
2. **Update all three plugin fields** to the new version (no `v` prefix in JSON):
   - `.claude-plugin/plugin.json` `"version"`
   - `.claude-plugin/marketplace.json` `metadata.version`
   - `.claude-plugin/marketplace.json` `plugins[0].version`
3. **Add a `## vX.Y.Z` section** to `CHANGELOG.md` at the top, covering all changes since the previous entry.
4. **Run `make release-check`** — must pass `precommit` AND `check-versions`.
5. **Commit:** `git commit -am "release plugin vX.Y.Z: <summary>"`.
6. **Tag** (same tag covers both surfaces — they share the version sequence): `git tag vX.Y.Z && git push && git push --tags`.

### Common plugin-release mistakes

- Forgetting `.claude-plugin/` files — CHANGELOG advances but plugin stays at old version.
- Creating a separate "Plugin vX" CHANGELOG section. Wrong — one CHANGELOG, one version sequence.
- Different version strings across the three JSON fields. The marketplace rejects mismatches silently and refuses to load the plugin.
- Bumping the plugin version BEFORE running the release gate. Surface changes that ship in the same release escape the manual check.

## Install (the moment a new version reaches consumers)

```bash
uv tool upgrade semantic-search
semantic-search --version     # must equal vX.Y.Z
```

The plugin's install is automatic via the marketplace once the bumped JSONs reach `master` — Claude Code re-checks the marketplace periodically.

## Backwards compatibility

- **MCP server names** — slash commands (`search.md`, `research.md`) hard-list `mcp__semantic-search__`, `mcp__semantic-search-personal__`, `mcp__semantic-search-work__` in `allowed-tools`. Custom labels reach via REST fallback only. Adding a fourth conventional label requires editing `allowed-tools` AND the "Known servers" tables in both commands.
- **Port discovery** — REST fallback enumerates running `semantic-search-http` services via launchd/systemd. Don't break the `com.github.bborbe.semantic-search-http[-<label>]` plist label convention without updating `commands/search.md` and `commands/research.md`.
- **`/configure`** — the source of truth for plist/unit naming and MCP config layout. Keep `search.md` and `research.md` in sync with whatever `configure.md` produces.

## See also

- `CLAUDE.md` § "Development Standards" — toolchain, test conventions, architecture
- `CLAUDE.md` § "Dark Factory Workflow" — never code directly; all changes go through dark-factory
- `docs/launchd-service.md` — macOS multi-instance setup
- `docs/systemd-user-service.md` — Linux multi-instance setup
- `docs/dod.md` — Definition of Done
- `commands/configure.md` — the slash-command counterpart of the launchd/systemd guides
