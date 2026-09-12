---
status: completed
summary: Defaulted the scope map to ~/.config/semantic-search/config.yaml when SEMANTIC_SCOPE_MAP is unset or empty, returning a ScopeMapResolution(path, source) that main() logs at startup, with the variable still winning and every bad map still exiting non-zero.
execution_id: semantic-search-default-scope-map-exec-032-default-scope-map-path
dark-factory-version: v0.193.0
created: "2026-09-12T07:00:08Z"
queued: "2026-09-12T07:33:43Z"
started: "2026-09-12T07:35:20Z"
completed: "2026-09-12T07:39:21Z"
---

# Default the scope map to the user config path

<summary>
- The server starts without `SEMANTIC_SCOPE_MAP` being set
- When the variable is unset, the scope map is read from the user config directory
- Setting the variable still wins — precedence is unchanged
- A missing, unreadable, or malformed map still refuses to start
- The startup log names the map file and whether it came from the variable or the default
- A tool installed without any environment setup can now run
- The launchd service no longer has to point its scope map into a git working tree
- Existing deployments that set the variable behave exactly as before
- The per-request scope rules are untouched — a missing `?scope=` is still an error
- Unit tests cover the resolution order, every failure mode, and the startup log in both directions, and a changelog entry records the change
</summary>

<objective>
Give the scope map a default path so the server can start with no environment variable set, while keeping the variable as an override and keeping every startup failure fail-closed. Today the process exits when `SEMANTIC_SCOPE_MAP` is unset, so a package-installed tool cannot run at all, and the live launchd service is forced to name its scope map by a path inside a git working tree — which makes a `git checkout` a production change.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.13+, `uv`, `src/` layout, strict mypy, never code directly) and `docs/dod.md` for the Definition of Done.

Read these files before making changes:

- `src/semantic_search/scopes.py` — the change lands in `scope_map_path_from_env()`. It reads `SCOPE_MAP_ENV` and currently raises `ScopeConfigError` when the value is missing or empty. `load_scope_map(path)` and `validate_scope_map(scope_map)` sit below it and already raise `ScopeConfigError` naming what they could not use — `load_scope_map` names the map path, `validate_scope_map` names the scope and the root. Neither needs a change.
- `src/semantic_search/http_server.py` — the only call site, in `main()`: `scope_map_path_from_env()` then `load_scope_map(...)` then `validate_scope_map(...)`, wrapped in `try/except ScopeConfigError` that logs `f"Invalid scope map: {e}"` and calls `sys.exit(1)`. The startup log line belongs here.
- `tests/test_scopes.py` — class `TestScopeMapPathFromEnv` is the style anchor (class-based, plain `assert`, `monkeypatch`, `tmp_path`). `test_raises_when_unset` and `test_raises_when_empty` assert today's behaviour and must be rewritten. `test_returns_env_var_path` keeps its behaviour (the variable still wins), but its assertion must be rewritten to the new return type — see `<constraints>`.
- `docs/launchd-service.md` § "Adding a scope", and the scope-map sentence above it — the precedence rule belongs here.
- `docs/design/per-vault-scoping.md` § "The scope map artifact" — states the map is named by the environment variable.
- `docs/releasing-semantic-search.md` § "Backwards compatibility" and its "Migrating from a single `CONTENT_PATH`" recipe.
- `CHANGELOG.md` — the top of the file. The most recent release, `## v0.21.0`, is already cut, so there is no `## Unreleased` section right now; this change adds one.

**Verified facts you can rely on (re-measured on this tree):**

- `SCOPE_MAP_ENV = "SEMANTIC_SCOPE_MAP"` is a module constant in `scopes.py`. The unset and empty cases are a single branch (`if not raw`), so both fall through to the default.
- `load_scope_map` requires only that the file parse as a mapping containing a `scopes` key; it tolerates every other top-level key. A `config.yaml` holding `scopes:` is therefore already the accepted shape — the parser needs no change, and future settings can join the same file.
- The default path is a runtime constant, NOT a file to read. The operator's machine has it at `~/.config/semantic-search/config.yaml` (byte-identical to the repo's `scopes.yaml`: 5 scopes, 15 distinct roots). It is NOT present in the container, so every check below builds its own file under a redirected `HOME`.
- `indexer.py` uses `platformdirs.user_cache_dir` for the index cache. `platformdirs.user_config_dir` is NOT the right call here: on macOS it resolves to `~/Library/Application Support/`, and the convention this change follows is the literal `~/.config/<tool>/` path already used by the sibling tools on this machine.
- `Path.home()` honours `$HOME`, so a test redirects the default by monkeypatching `HOME` to a temporary directory.

Pattern guides (in-container paths — read the one you need before writing):

- `/home/node/.claude/plugins/marketplaces/coding/docs/changelog-guide.md` — the `- feat: ...` bullet convention and the frozen-preamble rule.
- `/home/node/.claude/plugins/marketplaces/coding/docs/python-logging-guide.md` — the project's logging conventions.
- `/home/node/.claude/plugins/marketplaces/coding/docs/test-pyramid-triggers.md` — the unit-test triggers that apply to this change.
</context>

<requirements>

1. **Add the default and report its source** in `scope_map_path_from_env()` (`src/semantic_search/scopes.py`). The contract:

   - It returns a `ScopeMapResolution` — a `NamedTuple` with `path: Path` and `source: str`.
   - `source` is exactly `"env"` when `SCOPE_MAP_ENV` holds a non-empty value, and exactly `"default"` otherwise (unset OR empty — one branch, as today).
   - When `source` is `"env"`, `path` is `Path(raw)` — identical to today's behaviour.
   - When `source` is `"default"`, `path` is built as `Path.home() / ".config" / "semantic-search" / "config.yaml"` so it follows `$HOME`.
   - It **no longer raises** on unset or empty. Every other failure stays exactly where it is: `load_scope_map` and `validate_scope_map` keep raising `ScopeConfigError` naming the path they tried.
   - Update the docstring to state the precedence rule and the fallback.
   - Do **not** call `platformdirs.user_config_dir` — on macOS it resolves to `~/Library/Application Support/`, which is not the convention this change follows.

2. **Log the resolved map and its source at startup** in `src/semantic_search/http_server.py`. Extract the resolution and its log line into a module-level function so a unit test can exercise it without loading the embedding model:

   ```python
   def resolve_startup_scope_map() -> ScopeMapResolution:
       """Resolve the scope map path and log which file and source will be used."""
   ```

   It calls `scope_map_path_from_env()` and logs exactly one line in this form:

   ```
   Scope map: <path> (source: <env|default>)
   ```

   `main()` calls `resolve_startup_scope_map()` in place of the bare `scope_map_path_from_env()` call, and keeps the existing `try/except ScopeConfigError` then `logger.error` then `sys.exit(1)` around the load and validate steps. Update `main()`'s docstring too — it currently says the composition root "reads and validates the scope map named by `SEMANTIC_SCOPE_MAP`", which is no longer the whole rule; state the precedence and the default.

3. **Add tests** to `tests/test_scopes.py`, extending class `TestScopeMapPathFromEnv` or adding siblings in the same file and style. Use `monkeypatch.setenv("HOME", str(tmp_path))` so the default resolves inside `tmp_path`, and assert exact values rather than substrings:
   - variable set: `source == "env"` and `path` is that path;
   - variable unset: `source == "default"` and `path == tmp_path / ".config" / "semantic-search" / "config.yaml"`;
   - variable set to the empty string: same as unset (`source == "default"`).
   - Each of the four bad-default failure modes, with `HOME` redirected so the default points into `tmp_path`, must raise `ScopeConfigError` whose message names the path it tried:
     - file absent;
     - file present but unreadable (`chmod(0)` — mirror the existing `test_unreadable_file` in this file);
     - file present but not valid YAML;
     - file present, valid YAML, but no `scopes` key.
   - The startup log line: assert via `caplog.at_level(logging.INFO)` that `resolve_startup_scope_map()` emits `Scope map: <path> (source: default)` with `HOME` redirected and the variable unset, **and** `Scope map: <path> (source: env)` with the variable set. Both directions are required — a hardcoded `source` label must fail one of them.
   - **The happy path through the real entry point**, added to `tests/test_http_server.py`: with `HOME` redirected to a `tmp_path` holding a valid `.config/semantic-search/config.yaml` and `SEMANTIC_SCOPE_MAP` deleted, `main()` must reach app construction rather than exiting. Follow the two exemplars already in that file — `test_main_exits_nonzero_on_missing_scope_map_root` (the failure direction) and `TestVersionFlag`, which drives `main()` via `monkeypatch.setattr(sys, "argv", [...])`. Patch `uvicorn.run` so no port is bound. This is the only test that exercises the outcome the objective states — "a tool installed without any environment setup can now run" — through the code path production actually takes.

4. **Correct every doc and durable record that states the variable is the only way to name the map:**
   - `docs/launchd-service.md` — state the precedence rule where the scope map is described: `SEMANTIC_SCOPE_MAP` when set, otherwise `~/.config/semantic-search/config.yaml`. Name the default path explicitly.
   - `docs/systemd-user-service.md` — carries the byte-identical sentence to the launchd one at ~line 187 ("Scopes are declared in `scopes.yaml` (named by the `SEMANTIC_SCOPE_MAP` env var)"), plus "each instance still needs its own unit file, `Port`, and `SEMANTIC_SCOPE_MAP`" at ~line 219. State the same precedence rule and name the default path, exactly as for the launchd doc — leaving this one unedited ships a contradictory doc pair.
   - `docs/design/per-vault-scoping.md` § "The scope map artifact" — the map is named by the variable, or defaults to the user config path.
   - `docs/releasing-semantic-search.md` — three spots reference the startup rule or the no-shim guarantee: the § "Backwards compatibility" lead bullet ("the server **refuses to start** unless `SEMANTIC_SCOPE_MAP` names a readable scope map", ~line 245), the migration recipe's step 2 ("Without it the process exits with `refusing to start without a scope map`", ~line 260), and the sentence beginning "There is no compat shim and none is planned" (~line 266). Correct the first two so they describe the new precedence, and keep the v0.20.0 history intact — the lead bullet should still record that the variable was required as of v0.20.0, with the current rule stated beside it (variable overrides default; the default applies when the variable is unset; a missing or unreadable map still refuses). The third does **not** flip: it is about the per-request `?scope=` parameter, which this change deliberately leaves alone, so its claim remains true and must not be weakened. It needs only a disambiguating clause, because "default" becomes overloaded once a config-path default exists — state that the no-shim guarantee is about a missing `?scope=`, and that the config-path default is a different mechanism selecting *which scope map to read*, never *which scope to serve*.
   - `README.md` — the HTTP usage block (~line 64) shows `SEMANTIC_SCOPE_MAP=scopes.yaml semantic-search-http --host 127.0.0.1 --port 8321` as the only form. Add one sentence: the variable may be omitted, in which case the scope map is read from `~/.config/semantic-search/config.yaml`.
   - `specs/in-progress/004-per-vault-scoping.md` (~line 165; if it has moved, edit it wherever it now lives under `specs/`) — a durable record that this change falsifies. Its failure-mode row currently reads "Scope definition source missing or empty | Process refuses to start; never indexes nothing and never serves the union unscoped | Operator restores the artifact, reloads | Non-zero exit code plus log line | ...". That is correct **today** — `if not raw` covers the empty string, so an empty variable does refuse — and becomes false with this change. Widen the trigger to "missing or empty **and no usable default**" and the recovery to "restores the artifact **or the default**", and **add** "naming the path tried" to the evidence column (it reads only "Non-zero exit code plus log line" today): with two candidate sources, which file the server is actually reading is otherwise unanswerable at runtime.

     The same file states the same falsified rule a second time in **Desired Behavior 7** (~line 136): "A scope naming a root that is not present in the built index — nonexistent, unreadable, or not among the declared roots — or a missing or empty scope definition source, stops the process with a non-zero exit and a log line naming the scope and the offending root." Correcting only the table leaves the spec contradicting itself. Widen that clause identically, to "a scope definition source that is missing or empty **and has no usable default**, or a scope naming a root that is not present in the built index"; leave the rest of Desired Behavior 7 unchanged.

   Two of those spots carry a rule this change does **not** relax, and the edit must keep them saying so:
   - The "no compat shim" sentence is about the **per-request** `?scope=` parameter, not the startup path. Phrase it so a reader cannot conflate the two.
   - `docs/design/per-vault-scoping.md` § "Fail-closed" is likewise request-level. **Do NOT change it.** This change relaxes the startup path only; the distinction must survive the edit.

5. **Add a CHANGELOG entry** — add a `## Unreleased` section directly above the highest released section (`## v0.21.0`), containing one `- feat: ...` bullet naming the user-visible effect: the server now starts without `SEMANTIC_SCOPE_MAP`, reading `~/.config/semantic-search/config.yaml` when the variable is unset, with the variable still winning when set.

   Every released section is frozen — do NOT edit `## v0.21.0` or anything below it, and do not move or edit the changelog header (everything above the first `##` heading). In particular, the `- docs: ...` bullet that now sits under `## v0.21.0` describes the v0.20.0 breaking change and is historically accurate; leave it alone.

6. **Self-check before finishing:** re-run `<verification>` and confirm every step reports PASS; then walk each acceptance criterion (default resolves, variable still wins, still fail-closed in all four modes, resolved path observable, no behaviour change when the variable is set) against the change and state the outcome in your report.

</requirements>

<constraints>
- Python only; no new dependencies.
- **Do NOT change the per-request scope behaviour.** `?scope=` stays required on `/search`, `/duplicates`, and `/content` (REST) and on the MCP config URL; `MISSING_SCOPE` and `UNKNOWN_SCOPE` stay HTTP 400; there is still no union-wide fallback for a request that omits the scope.
- **Do NOT add a fallback that starts degraded.** When the map cannot be loaded, the process must exit non-zero. Never start with zero scopes, and never fall back to the union index.
- Do NOT use `platformdirs.user_config_dir`.
- Do NOT change `load_scope_map`, `validate_scope_map`, `resolve_scope`, or `ScopeMap` — the parser and validator already behave correctly.
- Existing tests must still pass. All three tests in `TestScopeMapPathFromEnv` are rewritten to the new return type: `test_returns_env_var_path` keeps its behaviour (the variable still wins) but must now assert `res.path == map_path` and `res.source == "env"` — comparing the resolution directly against a `Path` is False by construction, so leaving it as-is would fail. The two tests asserting a raise on unset/empty become the default-resolution tests. No other test in the file changes.
- Docstrings on all functions, type hints on all signatures, no `print()` in library code, no new broad `except Exception` — per `docs/dod.md`.
- Do NOT commit — dark-factory handles git.
- Keep `make precommit` green: `ruff format` and `ruff check` clean at the repo's 100-column limit, `mypy src` strict, full suite green.
</constraints>

<verification>
Run `make precommit` — must pass (sync + format + test + lint + typecheck).

Then confirm each of these. Every step shells out to `uv`; a `127` from any step means the command never ran, which is a verification failure — never read it as a pass.

```bash
# Guard: if `uv` is missing, every step below reports 127 and nothing was actually verified
command -v uv >/dev/null || echo "FAIL: uv is not in PATH - every step below would report 127"

# Keep uv's cache on its mounted path: the HOME redirects below would otherwise move it
# off /home/node/.cache/uv and could send uv to the network for a re-sync.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/home/node/.cache/uv}"

# 1. Resolution order: the default applies when unset, the variable wins when set
tmp="$(mktemp -d)"
mkdir -p "$tmp/.config/semantic-search"
printf 'scopes:\n  one:\n    - /tmp\n' > "$tmp/.config/semantic-search/config.yaml"

HOME="$tmp" uv run python -c "
from semantic_search.scopes import scope_map_path_from_env
r = scope_map_path_from_env()
print(r.source, r.path)
" > "$tmp/default.txt" 2>&1
cat "$tmp/default.txt"
if grep -q "^default " "$tmp/default.txt" && grep -qF "$tmp/.config/semantic-search/config.yaml" "$tmp/default.txt"; then
  echo "PASS: unset variable resolves to the user config path"
else
  echo "FAIL: unset variable did not resolve to the user config path"
fi

HOME="$tmp" SEMANTIC_SCOPE_MAP=/tmp/other.yaml uv run python -c "
from semantic_search.scopes import scope_map_path_from_env
r = scope_map_path_from_env()
print(r.source, r.path)
" > "$tmp/env.txt" 2>&1
cat "$tmp/env.txt"
if grep -q "^env /tmp/other.yaml$" "$tmp/env.txt"; then
  echo "PASS: the variable still wins"
else
  echo "FAIL: the variable did not win over the default"
fi

# 2. The empty-string case behaves as unset
HOME="$tmp" SEMANTIC_SCOPE_MAP= uv run python -c "
from semantic_search.scopes import scope_map_path_from_env
r = scope_map_path_from_env()
print(r.source, r.path)
" > "$tmp/empty.txt" 2>&1
if grep -q "^default " "$tmp/empty.txt"; then
  echo "PASS: an empty variable falls through to the default"
else
  echo "FAIL: an empty variable did not fall through to the default (got: $(cat "$tmp/empty.txt"))"
fi

# 3. Each bad-default mode refuses to start and names the path it tried
for mode in absent unreadable invalid no_scopes_key; do
  t="$(mktemp -d)"
  mkdir -p "$t/.config/semantic-search"
  f="$t/.config/semantic-search/config.yaml"
  case "$mode" in
    absent)        : ;;
    unreadable)    printf 'scopes:\n  one:\n    - /tmp\n' > "$f"; chmod 000 "$f" ;;
    invalid)       printf 'scopes: [unclosed\n' > "$f" ;;
    no_scopes_key) printf 'other: 1\n' > "$f" ;;
  esac
  out="$(HOME="$t" uv run python -c "
from semantic_search.scopes import scope_map_path_from_env, load_scope_map, validate_scope_map
m = load_scope_map(scope_map_path_from_env().path)
validate_scope_map(m)
print('STARTED - this is the failure')
" 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qF "config.yaml"; then
    echo "PASS: $mode refuses to start and names the path"
  else
    echo "FAIL: $mode rc=$rc - output: $out"
  fi
  chmod -R u+rwX "$t" 2>/dev/null
done

# 4. The startup log names the file AND its source, in BOTH directions.
#    Checking the path as well as the label matters: a label-only check would
#    still pass if the logged path were hardcoded.
HOME="$tmp" uv run python -c "
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
from semantic_search.http_server import resolve_startup_scope_map
resolve_startup_scope_map()
" > "$tmp/log-default.txt" 2>&1
cat "$tmp/log-default.txt"
if grep -q "(source: default)" "$tmp/log-default.txt" && grep -qF "$tmp/.config/semantic-search/config.yaml" "$tmp/log-default.txt"; then
  echo "PASS: startup log reports source=default with the resolved path"
else
  echo "FAIL: startup log did not report source=default with the resolved path"
fi

HOME="$tmp" SEMANTIC_SCOPE_MAP=/tmp/other.yaml uv run python -c "
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
from semantic_search.http_server import resolve_startup_scope_map
resolve_startup_scope_map()
" > "$tmp/log-env.txt" 2>&1
cat "$tmp/log-env.txt"
if grep -q "(source: env)" "$tmp/log-env.txt" && grep -qF "/tmp/other.yaml" "$tmp/log-env.txt"; then
  echo "PASS: startup log reports source=env with the resolved path"
else
  echo "FAIL: startup log did not report source=env with the resolved path - a hardcoded label or path would pass the default case and fail this one"
fi

# 5. The new tests, then the whole suite
uv run pytest tests/test_scopes.py tests/test_http_server.py -q
make test

# 6. CHANGELOG: a fresh Unreleased sits first, directly above the frozen v0.21.0
first="$(grep -m1 '^## ' CHANGELOG.md)"
if [ "$first" = "## Unreleased" ]; then
  echo "PASS: Unreleased is the first section"
else
  echo "FAIL: Unreleased missing or not first (first heading is: $first)"
fi
frozen="$(grep -c '^- docs: state that the scope change is breaking as of v0.20.0' CHANGELOG.md)"
if [ "$frozen" = "1" ]; then
  echo "PASS: the released v0.21.0 bullet is untouched"
else
  echo "FAIL: the released v0.21.0 bullet changed or vanished (count: $frozen)"
fi
```
</verification>
