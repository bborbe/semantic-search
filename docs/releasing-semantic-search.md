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

### Alignment applies to EVERY release, including binary-only ones

A binary-only change (only `src/`, `tests/`, `scenarios/` touched — nothing under `commands/`, `agents/`, `docs/`, `skills/`) still advances the shared version sequence, so it still bumps all three plugin JSON fields.

This is easy to get wrong, because §[When plugin JSONs need follow-up](#when-plugin-jsons-need-follow-up) reads as though the JSONs only ever move when a plugin-surface directory changes. That section answers "when does the **plugin surface** need re-publishing", not "when may the four version strings diverge". The answer to the second question is **never at release time**.

Concretely, for a binary-only release:

1. Rename `## Unreleased` → `## vX.Y.Z` in `CHANGELOG.md`.
2. Bump all three `.claude-plugin/` fields to the same `X.Y.Z`.
3. `make release-check` — must print `✅ all four versions equal`.
4. Commit `release vX.Y.Z`, tag, push both.

Skipping step 2 leaves `make check-versions` failing until the next plugin release — the exact trap listed under [Common plugin-release mistakes](#common-plugin-release-mistakes) ("CHANGELOG advances but plugin stays at old version").

**Why not in `precommit`**: every refactor commit would otherwise have to bump plugin JSONs in lockstep, burning release numbers on internal work. (Vault-cli learned this the hard way; we apply the same lesson here.)

## The release gate (run BEFORE `dark-factory prompt approve`)

**Two different `autoRelease` flags exist in this repo. Do not confuse them** — conflating them is what made this document wrong until 2026-08-17:

| File | Value | What it controls |
|---|---|---|
| `.dark-factory.yaml` | **absent → `false`** | Whether *completing a prompt* tags and pushes. It does not: the prompt commits locally and is **not even pushed**. |
| `.maintainer.yaml` | `release.autoRelease: true` | Whether the **maintainer-watcher bot** cuts a release from `## Unreleased` bullets once they reach `master`. It does. |

So the checkpoint is not at approve — it is at **push**. Approving a prompt ships nothing; pushing the resulting commit to `master` hands it to the bot, which tags and releases within ~5 minutes (or immediately via `/github-release-repo-trigger`). Everything after that push is automatic and has no second checkpoint.

Walk the gate before approving anyway. Once the prompt is approved the code exists and the next push carries it, so approve → push is the path of least resistance and the gate is easy to skip in between.

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

## Python package release (MANUAL — `autoRelease` is off)

> **Corrected 2026-08-17.** This section previously claimed `autoRelease: true` and "no manual binary release step". Both were false. `.dark-factory.yaml` sets no `autoRelease` key and `dark-factory config` resolves it to `false`. Verify before trusting any claim here:
>
> ```bash
> dark-factory config | grep autoRelease
> ```

### What actually happens when a prompt completes

Traced through the dark-factory source (`v0.194.0`):

1. `pkg/processor/workflow_helpers.go` — `autoRelease` false → `CommitOnly`. The change is committed **locally**, logged as `committed changes (autoRelease disabled, skipping tag)`.
2. `pkg/processor/workflow_executor_direct.go` — the branch push is gated on `AutoRelease`, so **nothing is pushed**.
3. No tag is created. `## Unreleased` is **not** renamed.

Net: a completed prompt leaves one unpushed local commit. If you approve a prompt and walk away expecting a release, you get silence — **until you push**.

### What DOES release: the maintainer bot

`.maintainer.yaml` sets `release.autoRelease: true`. Once a commit carrying `## Unreleased` bullets reaches `master`, the maintainer-watcher bot cuts the tag and release on its own — ~5 min on the periodic scan, or immediately via `/github-release-repo-trigger`.

The bot does **not** touch `.claude-plugin/*.json`, so a bot-cut release leaves `make check-versions` failing until an operator bumps the three fields. Cutting the release by hand (below) in the same commit as the JSON bump avoids that window entirely, which is why the manual path is still the recommended one.

### Releasing by hand (preferred — keeps versions aligned)

1. Confirm the working tree is clean and tests pass — `make release-check`.
2. Rename `## Unreleased` → `## vX.Y.Z` in `CHANGELOG.md` (bump per the `fix:`/`feat:` mix).
3. Bump all three `.claude-plugin/` version fields to the same `X.Y.Z` — see [Alignment applies to every release](#alignment-applies-to-every-release-including-binary-only-ones).
4. `make release-check` again — must print `✅ all four versions equal`.
5. `git commit -m "release vX.Y.Z: <summary>"`, `git tag vX.Y.Z`, `git push && git push origin vX.Y.Z`.
6. `uv tool upgrade semantic-search` and restart the launchd instances.

### If you ever enable autoRelease

Setting `autoRelease: true` in `.dark-factory.yaml` switches the completion path to `CommitAndRelease` (`pkg/git/helpers.go`), which stages all changes, computes the next version, rewrites `## Unreleased` → `## vX.Y.Z`, commits `release vX.Y.Z`, tags, and pushes both branch and tag. At that point approval genuinely ships with no second checkpoint, the scenario gate below becomes load-bearing, and this section must be rewritten again. Note it still would **not** bump the `.claude-plugin/` JSONs, so `make check-versions` would fail after every auto-tag.

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

`autoRelease` bumps the **binary**. It never edits `.claude-plugin/*.json` — regardless of what the prompt touched. So **every** auto-tag leaves the repo transiently misaligned: `CHANGELOG.md` shows the new `vX.Y.Z` while the three JSON fields still show the previous one, and `make check-versions` fails until an operator bumps them.

Two cases, same remedy:

| Prompt touched | Why the JSONs must catch up |
|----------------|-----------------------------|
| `commands/`, `agents/`, `docs/`, `skills/` | The plugin surface actually changed — consumers need the new version to pick it up |
| Only `src/`, `tests/`, `scenarios/` | The plugin surface is unchanged, but the shared version sequence advanced — see [Alignment applies to every release](#alignment-applies-to-every-release-including-binary-only-ones) |

In both cases, follow the [Plugin release](#plugin-release-manual) procedure to bring the three JSON fields up to the latest tag. Do the bump **before** tagging where possible (one `release vX.Y.Z` commit covering CHANGELOG + JSONs), so the repo is never pushed in a state where `make check-versions` fails.

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
