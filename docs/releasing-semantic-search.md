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

## The release gate (run BEFORE `dark-factory prompt approve`)

`autoRelease: true` is on for this repo (`.dark-factory.yaml`). The daemon ships every approved prompt that adds a `## Unreleased` entry — there is **no second checkpoint** after approval. The operator checkpoint is **before approve**, not after merge.

`make precommit` is one part of the gate (format + test + lint + typecheck) but does **not** cover:

- Real MCP server behavior (stdio framing, fastmcp wire format)
- Real REST server behavior (HTTP contract, real socket bind)
- Real launchd/systemd integration
- The slash-command surfaces (`/semantic-search:search`, `:research`, `:configure`)

The `scenarios/` directory holds the regression suite that exercises these real-binary paths. Walk all `status: active` (and `draft`, if the prompt creates one) scenarios against the current source tree **before** approving the prompt:

```bash
ls scenarios/*.md
# Walk each scenario's Setup → Action → Expected against the working tree.
# Use `/dark-factory:run-scenario` to drive interactively, or execute by hand.
```

Current scenarios:

- `scenarios/001-mcp-stdio-no-stdout-pollution.md` — `semantic-search-mcp serve` keeps stdout clean, logs on stderr
- `scenarios/002-http-rest-search-returns-json.md` — `semantic-search-http` binds a port and returns valid JSON
- `scenarios/003-cli-search-prints-results.md` — `semantic-search search` one-shot returns results, exit 0

If any scenario fails, do **not** approve the prompt. Reject, fix, re-audit. Once approved, the daemon ships whatever the agent produced — there is no rollback short of a follow-up release.

### Empty-diff skip

The one valid skip: nothing on the runtime surface changed since the installed binary.

```bash
INSTALLED=$(semantic-search --version | awk '{print $NF}')
git diff "$INSTALLED"..HEAD --name-only | grep -E '^src/.*\.py$|^pyproject\.toml$|^Makefile$|^tests/.*\.py$'
# empty output → installed binary is byte-equivalent to current source → skip the scenario gate
```

This is the ONLY documented skip. Doc-only / `scenarios/` / `prompts/` / `specs/` changes never reach a runtime artifact and don't need the gate. Do not invent other skips.

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

## Python package release (automatic — operator owns the gate)

Semantic-search runs `dark-factory` against itself with `autoRelease: true` and a `CHANGELOG.md`. Every successful prompt that adds a `## Unreleased` entry produces a new `vX.Y.Z` tag and pushes it. There is **no manual binary release step**. The release is the side-effect of completing a prompt.

The operator's responsibility is to **run the release gate before approving any prompt** that may produce a binary change. Once the prompt is approved, the daemon ships whatever the agent produced — there is no second checkpoint.

### What autoRelease does

After each successful prompt with `## Unreleased` content:

1. Stage all changes (including the agent's `## Unreleased` entry).
2. Determine bump (patch/minor) from the changelog content.
3. Rename `## Unreleased` → `## vX.Y.Z`.
4. Commit `release vX.Y.Z`.
5. Tag `vX.Y.Z`.
6. `git push` + `git push origin vX.Y.Z`.
7. Move the prompt file to `prompts/completed/` and push that commit too.

`hatch-vcs` derives `__version__` from the tag at install time → `uv tool upgrade semantic-search` picks up the new version on the next install. No `pyproject.toml` bump is ever needed.

### Verifying a release shipped

```bash
git fetch --tags
git describe --tags --abbrev=0                                # latest tag
git log "$(git describe --tags --abbrev=0)"..HEAD --oneline   # any unpushed commits beyond it
uv tool upgrade semantic-search
semantic-search --version                                     # must match the tag
```

After a successful autoRelease, both `git status` (clean) and `git rev-list @{u}..HEAD --count` (zero) should hold.

### When plugin JSONs need follow-up

`autoRelease` bumps the **binary**. A prompt that touches `commands/`, `agents/`, `docs/`, or `skills/` is shipped as a binary tag but the **plugin** version in `.claude-plugin/*.json` does NOT auto-bump. After such a prompt completes, follow the [Plugin release](#plugin-release-manual) procedure manually to bring the three JSON fields up to the latest tag.

## GitHub Release (manual — when to surface a milestone)

`autoRelease` creates a `vX.Y.Z` git tag after every approved prompt. Tags are sufficient for `uv tool install git+...@vX.Y.Z`, `git describe`, and any tag-aware consumer.

A **GitHub Release** is a separate, deliberate act — distinct from the tag. It adds release notes, an entry on the repo's Releases tab, an RSS/atom feed for subscribers, and optional binary/wheel assets. Create one **only after**:

1. All `scenarios/` pass against the current source tree.
2. Plugin JSONs are aligned (if `commands/`, `agents/`, `docs/`, or `skills/` changed since the last plugin release).
3. The `CHANGELOG.md` entry summarises what users should care about — not the internal commit log.

Skip the GitHub Release for internal refactors, pre-release/experimental work, or chains of small tags. It is fine to skip several auto-tags and cumulate them into a single milestone Release later.

How:

```bash
TAG=$(git describe --tags --abbrev=0)
gh release create "$TAG" \
  --target master \
  --title "$TAG" \
  --notes "$(awk "/^## $TAG/,/^## v/" CHANGELOG.md | head -n -1)"
```

Verify on github.com → Releases tab. The Release object can be edited (notes, draft state) without retagging.

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
