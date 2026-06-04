---
status: verifying
approved: "2026-06-04T10:21:38Z"
generating: "2026-06-04T10:26:38Z"
prompted: "2026-06-04T10:35:41Z"
verifying: "2026-06-04T13:10:49Z"
branch: dark-factory/relax-python-floor
---

## Summary

`semantic-search`'s `requires-python` floor is pinned to `>=3.14`, but the source uses no 3.14-only features. The pin blocks installation in containers shipping Python 3.13 (e.g. the Hermes Agent gateway on Debian 13 trixie) without any technical reason. Lower the floor to `>=3.13` so the package installs cleanly anywhere `uv` can fetch a 3.13 interpreter — without giving up linter / type-checker rigor (ruff `target-version` and mypy `python_version` move in lockstep with the package floor).

## Problem

`pyproject.toml` declares three matched pins for Python 3.14:

| Line | Field | Value |
|------|-------|-------|
| 5 | `[project] requires-python` | `">=3.14"` |
| 52 | `[tool.ruff] target-version` | `"py314"` |
| 69 | `[tool.mypy] python_version` | `"3.14"` |

`uv.lock:3` mirrors `requires-python = ">=3.14"`.

The codebase actually only uses `from typing import Any` + `from __future__ import annotations`. No PEP 695 generics, no `match`/`case`, no 3.14-only stdlib (e.g. `t-strings`, deferred evaluation of annotations as runtime default), no `Self` from `typing` beyond what 3.11 already shipped. Verified by:

```bash
cd ~/Documents/workspaces/semantic-search
grep -rE "^from __future__|^from typing|^import typing" src/   # only `from typing import Any` + `__future__ annotations`
grep -rE "PEP[ -]?6(95|92|49)|sys\.version_info" src/ tests/    # zero matches
```

Git history shows commit `e0596c4 upgrade to Python 3.14 and update dependencies` — a deliberate version bump, but no 3.14-specific code landed in the change.

Concrete blocker: installing semantic-search via `uv tool install git+https://github.com/bborbe/semantic-search` inside the Hermes Agent gateway container (`hermes-agent-dev:latest`, Debian 13 trixie, system Python 3.13) fails because `uv` will not resolve a project that declares `requires-python = ">=3.14"` against a 3.13 interpreter. Workaround today: install Python 3.14 via `uv python install 3.14` inside the container (~50 MB extra, ~adds layer cost on every image rebuild). Fixing the floor eliminates that workaround for every consumer.

## Goal

`semantic-search` installs and operates correctly under any Python `>=3.13`. Ruff lint + mypy type-check continue to enforce the same rules at the new floor (no regression in static analysis strictness). `uv.lock` is regenerated against the lowered floor so transitively-resolved dependency versions reflect what 3.13 can actually consume.

## Non-goals

- Lowering to `>=3.12` or `>=3.11` — only `>=3.13` is in scope. Each lower floor is a separate decision (dependency-set behavior on 3.12 deserves its own verification round).
- Changing any source code in `src/` or `tests/` — no behavioral change. Only `pyproject.toml`, `uv.lock`, and CHANGELOG.
- Touching plugin JSON manifests (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`) — they version the plugin surface, not the Python package surface; unchanged.
- Bumping `fastmcp`, `sentence-transformers`, or any other direct dependency. Lockfile regen MAY pull updated transitive versions if the constraint solver chooses to; direct dependency constraints (the version pins inside `[project] dependencies`) are unchanged.
- CI matrix expansion — running CI on both 3.13 and 3.14 is a follow-up if useful, not blocking this change.
- Changing `[tool.ruff] target-version` or `[tool.mypy] python_version` to `"py312"` / `"3.12"` or below — they stay in lockstep with the package floor at `py313` / `3.13`.

## Do-Nothing Option

Leaving the floor at 3.14 keeps the Hermes Agent gateway image carrying an extra Python interpreter via `uv python install 3.14` (≈ 50 MB layer, on every image rebuild). Same friction recurs in every future container that ships Python 3.13 (Debian 13 trixie default, Ubuntu 24.04 LTS default once it bumps from 3.12, RHEL 10). Trivial today (one image), compounds over time. The cost of the fix is bounded (3 pin sites + lockfile regen + scenario walk), so the asymmetry is firmly in favor of fixing.

Letting the floor stay also means contributors with 3.13 (the current Debian/Ubuntu stable default) cannot `uv tool install` from source without first installing 3.14 themselves — a real onboarding tax for what is otherwise a one-command install.

## Reproduction

```bash
docker exec hermes uv tool install \
  git+https://github.com/bborbe/semantic-search.git
# Expected (today, against requires-python=">=3.14"):
#   error: Because semantic-search requires Python >=3.14
#   and there is no version of Python available that satisfies that constraint,
#   we cannot proceed.
```

After the fix, the same command should succeed against the existing 3.13 interpreter without any prior `uv python install` step.

## Expected vs Actual

|   | Expected (after fix) | Actual (today) |
|---|----------------------|----------------|
| `requires-python` | `">=3.13"` | `">=3.14"` |
| `ruff target-version` | `"py313"` | `"py314"` |
| `mypy python_version` | `"3.13"` | `"3.14"` |
| `uv.lock requires-python` | `">=3.13"` | `">=3.14"` |
| `uv tool install` on Python 3.13 | Succeeds | Errors with floor-violation |
| `uv tool install` on Python 3.14 | Succeeds (still allowed) | Succeeds |
| `make precommit` | Passes | Passes |
| Lint rules applied | py313 (no regression in strictness vs py314 — both modern) | py314 |

## Constraints

- `make precommit` must continue to pass — format, test, lint (ruff py313), typecheck (mypy 3.13), all green.
- No source code change in `src/` or `tests/` — strictly metadata + lockfile.
- The new `uv.lock` MUST resolve cleanly against Python 3.13 (verify with `uv lock --python 3.13` if `uv` exposes that override; otherwise verify the lockfile's `requires-python` field reads `>=3.13`).
- All 5 active `scenarios/` must still pass on Python 3.14 (existing CI / dev box) — no regression on the higher boundary. The release gate per `docs/releasing-semantic-search.md` is mandatory because `pyproject.toml` is in the path filter.
- `[project.dependencies]` versions unchanged. Lockfile may shift transitives; direct deps stay pinned where they already were.
- CHANGELOG.md gets a `## Unreleased` entry under "Changed" describing the floor relax — the maintainer github-releaser bot picks it up after merge to master and tags + pushes per `.maintainer.yaml: release.autoRelease: true`.

## Failure Modes

| Trigger | Detection | Expected behavior | Recovery |
|---|---|---|---|
| Direct dep fails to resolve under `>=3.13` (e.g. `fastmcp` requires `>=3.14`) | `uv lock` exits non-zero | Spec is rejected — re-evaluate which floor is achievable | Re-target to `>=3.14` (no change) and document why the floor cannot move; OR bump the offending dep |
| mypy raises new errors at `python_version = "3.13"` | `make typecheck` exits non-zero | Fix the genuine type errors (they were latent under py314) | If errors are spurious / py313-only stdlib regression, scope-creep is rejected; revert to py314 and document |
| ruff raises new findings at `target-version = "py313"` | `make lint` exits non-zero | Apply mechanical fixes (likely `UP*` pyupgrade rules) | Auto-fixable via `ruff check --fix` |
| Scenario regresses on Python 3.14 after the lockfile regen | Scenario walk fails | Revert lockfile change | Pin the transitive that regressed; re-lock |

## Acceptance Criteria

- [ ] `pyproject.toml` line 5 reads `requires-python = ">=3.13"` — evidence: `grep -n '^requires-python' pyproject.toml` returns exactly `5:requires-python = ">=3.13"`.
- [ ] `pyproject.toml` ruff section reads `target-version = "py313"` — evidence: `grep -n 'target-version' pyproject.toml` returns exactly one line: `52:target-version = "py313"`.
- [ ] `pyproject.toml` mypy section reads `python_version = "3.13"` — evidence: `grep -nE '^python_version' pyproject.toml` returns exactly `69:python_version = "3.13"`.
- [ ] `uv.lock` declares `requires-python = ">=3.13"` and has been regenerated against the new floor — evidence: `grep -n '^requires-python' uv.lock` returns `3:requires-python = ">=3.13"`.
- [ ] `make precommit` exits 0 — evidence: terminal capture of the full `make precommit` run with `echo "EXIT=$?"` showing 0.
- [ ] `CHANGELOG.md` has a `## Unreleased` entry with a flat `chore:` bullet mentioning the floor relax (no `### Changed` subsection — project changelog convention forbids subsections, see `coding/docs/changelog-guide.md:44`) — evidence: `grep -nE '^## Unreleased' CHANGELOG.md` returns ≥1 line AND `grep -nE '^- chore:.*requires-python' CHANGELOG.md` returns ≥1 line.
- [ ] **Scenario walk (release gate)** — all 5 active scenarios pass against the working tree on Python 3.14 (the current dev-box Python). Evidence: each scenario's Setup → Action → Expected manually walked, with terminal output pasted into the verification report for the verify-spec stage. Specifically:
  - `scenarios/001-mcp-stdio-no-stdout-pollution.md`
  - `scenarios/002-http-rest-search-returns-json.md`
  - `scenarios/003-cli-search-prints-results.md`
  - `scenarios/004-http-content-fetch-happy-path.md`
  - `scenarios/005-http-content-fetch-error-responses.md`
- [ ] **Runtime install on Python 3.13** — inside the Hermes Agent gateway container (`docker exec hermes bash`), `uv tool install git+https://github.com/bborbe/semantic-search.git@feature/relax-python-floor` succeeds AND `semantic-search --version` prints a version (any tag derived from the branch via hatch-vcs). Evidence: paste both commands' stdout/exit codes into the verify-spec report. This MUST be executed against the feature branch (not master), since the change ships in this branch first.
- [ ] No source files under `src/` or `tests/` modified — evidence: `git diff master...HEAD --name-only -- 'src/**' 'tests/**'` is empty.

## Verification

```bash
cd ~/Documents/workspaces/semantic-search-py313
make precommit
git diff master...HEAD -- pyproject.toml uv.lock CHANGELOG.md   # only these three files should differ
git diff master...HEAD --name-only -- 'src/**' 'tests/**'       # must be empty

# Scenario gate
ls scenarios/*.md
# Walk each scenario's Setup → Action → Expected against the working tree.

# Runtime install verification (Python 3.13 path):
docker exec hermes uv tool install \
  "git+https://github.com/bborbe/semantic-search.git@feature/relax-python-floor"
docker exec hermes semantic-search --version
```

## Open Questions

- None blocking. `>=3.12` vs `>=3.13` was considered; the floor is set at `>=3.13` because (a) it matches Hermes Agent's Debian 13 trixie default, (b) it matches Ubuntu's near-term default after the 3.12-LTS window, and (c) lowering further requires verifying that direct dependencies (`fastmcp`, `sentence-transformers`, `faiss-cpu`) actually resolve cleanly on 3.12, which is its own evaluation.
