---
status: completed
summary: 'Changed stream=sys.stdout to stream=sys.stderr in logging_setup.py, added tests/test_logging_setup.py pinning the stderr routing, and inserted ## Unreleased entry in CHANGELOG.md.'
container: semantic-search-011-fix-logging-stream-to-stderr
dark-factory-version: v0.156.1-1-g04f3863-dirty
created: "2026-05-16T11:19:00Z"
queued: "2026-05-16T11:19:00Z"
started: "2026-05-16T11:19:16Z"
completed: "2026-05-16T11:21:38Z"
---
<summary>
- Logger output from the `semantic-search` binaries is routed to stderr instead of stdout
- Fixes a stdio MCP protocol corruption: when running `semantic-search-mcp serve`, stdout is the JSON-RPC channel and every log line was being injected into it
- HTTP / CLI invocations gain the standard Unix behavior (logs on stderr, structured output on stdout) at no cost
- A new pytest pins the behavior so a future refactor of `configure_logging` cannot silently re-route logs back to stdout
- No public signature or log format changes — single-keyword fix
- CHANGELOG `## Unreleased` records the protocol-channel rationale for future reviewers
</summary>

<objective>
Fix a one-line bug in `src/semantic_search/logging_setup.py`: `logging.basicConfig(..., stream=sys.stdout)` corrupts the stdio MCP JSON-RPC channel when the binary runs as `semantic-search-mcp serve`. Change `stream=sys.stdout` to `stream=sys.stderr` and pin the behavior with a test using `capsys`/`capfd`. Add a CHANGELOG entry under `## Unreleased`.
</objective>

<context>
Read `CLAUDE.md` for project conventions (Python 3.14+, `uv`, strict mypy, src/ layout, dark-factory workflow, tests in `tests/test_*.py`, pytest, `unittest.mock`).

Read these files before making changes:

- `src/semantic_search/logging_setup.py` — the entire file is 21 lines. `configure_logging(level: str = "INFO") -> None` calls `logging.basicConfig(format=..., level=..., datefmt=..., stream=sys.stdout)`. Only the `stream=` argument changes; everything else stays exactly as is.
- `tests/conftest.py` — defines shared pytest fixtures (`temp_vault`, `multi_vaults`). The new test file does NOT need any of these fixtures; it relies only on built-in `capsys` (or `capfd`).
- `tests/test_main.py`, `tests/test_imports.py` — existing test style: PEP-8, `class Test<Feature>:` grouping, type-annotated test methods returning `-> None`, no f-string assertion messages required.
- `CHANGELOG.md` — current top entry is `## v0.11.0`. There is NO `## Unreleased` section yet. The agent must insert a new `## Unreleased` section immediately above `## v0.11.0`.

**Why stderr is correct here:**

- The `semantic-search-mcp serve` entrypoint speaks JSON-RPC over **stdout** (stdio transport per the Model Context Protocol spec). Any non-protocol bytes on stdout corrupt the framing and break the client connection.
- Standard Unix convention: logs and diagnostics go to **stderr**; structured/program output goes to **stdout**. `logging.basicConfig` defaults to `sys.stderr` for exactly this reason — the current `stream=sys.stdout` is an explicit (incorrect) override.
- HTTP and CLI binaries are unaffected by the channel choice — both stdout and stderr are visible in their terminals/log collectors — so no behavior regresses there.

**Why `capsys` (not `caplog`):**

- `caplog` intercepts the logging records before they hit any handler, so it cannot prove which stream the handler writes to.
- `capsys` captures the actual bytes written to `sys.stdout` and `sys.stderr` after `logging.basicConfig` has installed its `StreamHandler`. This is the only way to assert the stream routing is correct end-to-end.
- `capfd` works too (captures at the file-descriptor level) and is also acceptable. Either is fine; `capsys` is simpler and sufficient here.

**Pytest-and-basicConfig gotcha:**

- `logging.basicConfig` is idempotent — if the root logger already has handlers, it's a no-op. Other tests in the suite may have already called `configure_logging` (or pytest's own log capture may have installed handlers). The new test must therefore clear the root logger's handlers before calling `configure_logging`, otherwise the basicConfig call silently does nothing and the test would pass for the wrong reason. Use:

  ```python
  root = logging.getLogger()
  for h in list(root.handlers):
      root.removeHandler(h)
  ```

  before invoking `configure_logging("INFO")` in the test.

- Pytest by default disables propagation/captures logs via its own mechanism. The test must explicitly disable pytest's log capture interference for this one test by setting the root level low and reading captured streams directly. The handler-clear above plus `capsys` is sufficient.

**Current verbatim code that must change** in `src/semantic_search/logging_setup.py`:

```python
"""Logging configuration."""

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s",
        level=log_level,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
```
</context>

<requirements>

1. **Edit `src/semantic_search/logging_setup.py`.** Replace `stream=sys.stdout` with `stream=sys.stderr` inside the `logging.basicConfig(...)` call. Do not touch:
   - the module docstring,
   - the `import logging` / `import sys` lines,
   - the `configure_logging` signature `(level: str = "INFO") -> None`,
   - the docstring of `configure_logging`,
   - the `log_level = getattr(...)` line,
   - the `format=` string,
   - the `level=` argument,
   - the `datefmt=` argument.

   The diff must be exactly one character change in one line. After the edit, the function body looks like:

   ```python
   log_level = getattr(logging, level.upper(), logging.INFO)

   logging.basicConfig(
       format="%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s",
       level=log_level,
       datefmt="%Y-%m-%d %H:%M:%S",
       stream=sys.stderr,
   )
   ```

2. **Create `tests/test_logging_setup.py`** with the following contents (and only the following contents):

   ```python
   """Tests for semantic_search.logging_setup."""

   import logging

   import pytest

   from semantic_search.logging_setup import configure_logging


   class TestConfigureLoggingStream:
       """configure_logging must route log records to stderr, not stdout.

       The stdio MCP transport (`semantic-search-mcp serve`) uses stdout for
       JSON-RPC framing. Any log line on stdout corrupts the protocol channel
       and breaks the client connection. This test pins the stream choice.
       """

       def test_logger_writes_to_stderr_not_stdout(
           self, capsys: pytest.CaptureFixture[str]
       ) -> None:
           """logger.error must appear on stderr and must NOT appear on stdout."""
           # basicConfig is idempotent: clear any handlers a prior test installed,
           # otherwise our configure_logging call is a no-op and the assertion
           # below would pass for the wrong reason.
           root = logging.getLogger()
           for handler in list(root.handlers):
               root.removeHandler(handler)

           configure_logging("INFO")

           logger = logging.getLogger("semantic_search.test_logging_setup")
           logger.error("test-msg-12345")

           # Flush all handlers so capsys sees the bytes.
           for handler in logging.getLogger().handlers:
               handler.flush()

           captured = capsys.readouterr()
           assert "test-msg-12345" in captured.err, (
               "logger output must be on stderr to avoid corrupting the "
               "stdio MCP JSON-RPC channel on stdout"
           )
           assert "test-msg-12345" not in captured.out, (
               "logger output leaked to stdout — would corrupt the stdio MCP "
               "protocol channel during `semantic-search-mcp serve`"
           )
   ```

   Notes:
   - File path is exactly `tests/test_logging_setup.py` at the repo root.
   - Do not add any fixtures beyond the built-in `capsys`. Do not import `conftest` fixtures.
   - Type-annotate the `capsys` parameter as `pytest.CaptureFixture[str]` to satisfy strict mypy.
   - End the file with one trailing newline (standard PEP 8).
   - Do not add a `__main__` block or `pytest.main()` invocation.

3. **Update `CHANGELOG.md`.** The current top entry is `## v0.11.0`. Insert a new `## Unreleased` section immediately above `## v0.11.0` with exactly one bullet:

   ```
   ## Unreleased

   - fix: route logger output to stderr (was stdout, corrupted stdio MCP protocol channel during serve mode)
   ```

   Keep one blank line between the new bullet and `## v0.11.0`. Do not modify any existing entry. Do not add any other bullets under `## Unreleased`.

4. **Do NOT modify** any other file. Specifically: do not touch `src/semantic_search/__main__.py`, do not touch any MCP/HTTP entrypoint, do not touch `pyproject.toml`, do not touch `Makefile`, do not touch existing tests.

</requirements>

<constraints>
- Do NOT commit — dark-factory handles git.
- `configure_logging(level: str = "INFO") -> None` signature is unchanged.
- The log format string `"%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s"` is unchanged.
- The `datefmt="%Y-%m-%d %H:%M:%S"` is unchanged.
- The `import logging` and `import sys` lines stay as they are.
- Do NOT remove the `stream=` argument entirely (even though `sys.stderr` is the basicConfig default) — keeping it explicit documents the choice and prevents a future "clean up redundant default" PR from silently regressing the fix.
- Do NOT switch to `caplog` for the test — it does not prove stream routing.
- Do NOT add a `pytest.ini` / `pyproject.toml` log_cli config knob — this fix is in code, not config.
- Do NOT change any other logger configuration (no new `Logger.addHandler`, no `propagate` toggles, no `dictConfig`).
- Do NOT introduce new top-level dependencies. `pytest` is already a dev dep; `capsys` is built in.
- Existing tests must still pass.
- Strict mypy must still pass — `pytest.CaptureFixture[str]` is the correct annotation for `capsys`.
- Repo-relative paths only.
- Out of scope: print()/usage routing (handled by PR #5), subcommand structure changes, logging format/datefmt changes, HTTP server logging, refactoring `configure_logging` signature.
</constraints>

<verification>
Run `make test` — must pass (full pytest suite green).

Run `make precommit` if it exists in the Makefile — must pass (format + test + lint + typecheck). If `make precommit` is not defined, run `make lint` and `make typecheck` individually and confirm both pass.

Specifically confirm each of the following commands at the repo root:

- `grep -F 'stream=sys.stderr' src/semantic_search/logging_setup.py` → exactly one match.
- `grep -F 'stream=sys.stdout' src/semantic_search/logging_setup.py` → zero matches (exit 1 from grep is expected).
- `uv run pytest tests/test_logging_setup.py -v` → exit 0; the single test method `test_logger_writes_to_stderr_not_stdout` reported as PASSED.
- `grep -E '^## Unreleased' CHANGELOG.md` → exactly one match.
- The bullet `- fix: route logger output to stderr (was stdout, corrupted stdio MCP protocol channel during serve mode)` appears under `## Unreleased` and above `## v0.11.0`.
- `make test` → exit 0.

Manual smoke (not required to pass in CI): after install, run `semantic-search-mcp serve </dev/null >/tmp/mcp.stdout 2>/tmp/mcp.stderr` for a moment, then kill it. `cat /tmp/mcp.stdout` should contain only JSON-RPC framing (or be empty); `cat /tmp/mcp.stderr` should contain the startup log lines.
</verification>
