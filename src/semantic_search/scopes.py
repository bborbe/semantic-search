"""Load, validate, and resolve the scope map.

The scope map is a YAML file mapping a scope name to an ordered list of
absolute root directories that scope is allowed to see. This module owns
loading and shape-checking the map, checking that its roots are usable on
this host, and resolving a request's scope name to its root list.

It also owns the request-scope transport: a context variable carrying the
resolved roots the HTTP layer bound for the MCP session being served, plus a
process-global marker for whether this process serves the HTTP mount. A
context variable is the correct primitive because it is per-task: two MCP
sessions in flight at once each see their own value, and the value a tool
body observes is the one bound on the request that established its session.
"""

import contextvars
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import yaml

SCOPE_MAP_ENV = "SEMANTIC_SCOPE_MAP"

# Where the scope map is read from when SCOPE_MAP_ENV is unset or empty. Built
# from Path.home() at resolution time so it follows $HOME, and deliberately not
# platformdirs.user_config_dir: on macOS that resolves to
# ~/Library/Application Support/, not the ~/.config/<tool>/ convention the
# sibling tools on this machine use.
DEFAULT_SCOPE_MAP_RELPATH = (".config", "semantic-search", "config.yaml")

# The resolved roots bound for the MCP session currently being served, or
# None when nothing is bound (the stdio transport). Bound by the HTTP layer's
# MCP guard at session establishment; read by the tools in `server.py`.
_request_roots_var: contextvars.ContextVar[tuple[Path, ...] | None] = contextvars.ContextVar(
    "request_roots", default=None
)

# True once this process has constructed the HTTP app (see `mark_http_transport`).
_http_transport = False


class ScopeConfigError(Exception):
    """The scope map is missing, empty, malformed, or names an unusable root."""


class ScopeRequestError(Exception):
    """Base class for a request whose scope cannot be resolved."""


class MissingScopeError(ScopeRequestError):
    """A request named no scope."""


class UnknownScopeError(ScopeRequestError):
    """A request named a scope that is not declared."""


@dataclass(frozen=True)
class ScopeMap:
    """The declared scopes: scope name -> ordered tuple of absolute roots."""

    scopes: Mapping[str, tuple[Path, ...]]

    @property
    def union_roots(self) -> tuple[Path, ...]:
        """Deduplicated union of every scope's roots, in first-appearance order."""
        seen: set[Path] = set()
        union: list[Path] = []
        for roots in self.scopes.values():
            for root in roots:
                if root not in seen:
                    seen.add(root)
                    union.append(root)
        return tuple(union)


class ScopeMapResolution(NamedTuple):
    """Where the scope map was resolved from: the path, and which rule chose it."""

    path: Path
    source: str  # "env" when SCOPE_MAP_ENV named it, "default" otherwise


def scope_map_path_from_env() -> ScopeMapResolution:
    """Resolve the scope map path and report which rule chose it.

    Precedence: a non-empty `SCOPE_MAP_ENV` wins and its value is the path
    (`source == "env"`). When the variable is unset *or* empty, the path falls
    back to `~/.config/semantic-search/config.yaml` under `Path.home()`
    (`source == "default"`).

    This function never raises: a missing, unreadable, or malformed map is
    still a startup failure, but it is raised by `load_scope_map` and
    `validate_scope_map`, which name the path they tried.
    """
    raw = os.environ.get(SCOPE_MAP_ENV)
    if raw:
        return ScopeMapResolution(path=Path(raw), source="env")
    return ScopeMapResolution(
        path=Path.home().joinpath(*DEFAULT_SCOPE_MAP_RELPATH), source="default"
    )


def load_scope_map(path: Path) -> ScopeMap:
    """Parse and shape-check the map file; raise ScopeConfigError on any problem."""
    try:
        raw = path.read_text()
    except OSError as e:
        raise ScopeConfigError(f"cannot read scope map {path}: {e}") from e

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ScopeConfigError(f"scope map {path} is not valid YAML: {e}") from e

    if not isinstance(data, dict) or "scopes" not in data:
        raise ScopeConfigError(f"scope map {path} must be a mapping with a 'scopes' key")
    scopes_raw = data["scopes"]
    if not isinstance(scopes_raw, dict):
        raise ScopeConfigError(f"scope map {path}: 'scopes' must be a mapping of scope names")
    if not scopes_raw:
        raise ScopeConfigError(f"scope map {path}: 'scopes' must declare at least one scope")

    scopes: dict[str, tuple[Path, ...]] = {}
    for name, value in scopes_raw.items():
        if not isinstance(name, str):
            raise ScopeConfigError(f"scope map {path}: scope name must be a string, got {name!r}")
        if not isinstance(value, list):
            raise ScopeConfigError(f"scope map {path}: scope {name!r} must be a list of roots")
        if not value:
            raise ScopeConfigError(f"scope map {path}: scope {name!r} has no roots")
        roots: list[Path] = []
        for root in value:
            if not isinstance(root, str):
                raise ScopeConfigError(
                    f"scope map {path}: scope {name!r} has a non-string root {root!r}"
                )
            root_path = Path(root)
            if not root_path.is_absolute():
                raise ScopeConfigError(
                    f"scope map {path}: scope {name!r} root {root!r} is not absolute"
                )
            roots.append(root_path)
        scopes[name] = tuple(roots)
    return ScopeMap(scopes=scopes)


def validate_scope_map(scope_map: ScopeMap) -> None:
    """Raise ScopeConfigError when a declared root is unusable."""
    for name, roots in scope_map.scopes.items():
        for root in roots:
            if not root.exists():
                raise ScopeConfigError(f"scope {name!r} root {root} does not exist")
            if not root.is_dir():
                raise ScopeConfigError(f"scope {name!r} root {root} is not a directory")
            if not os.access(root, os.R_OK | os.X_OK):
                raise ScopeConfigError(f"scope {name!r} root {root} is not readable")


def resolve_scope(scope_map: ScopeMap, raw_values: Sequence[str] | str | None) -> tuple[Path, ...]:
    """Return the roots for every scope name a request carried, or raise.

    `raw_values` is the primary contract: every scope value the request carried,
    in request order. A bare string is one value and `None` is no value; both
    legacy forms are retained so single-scope callers keep working unchanged.

    Blank values are ignored, so a request whose only scope values are blank is
    the same as one that named none. Values are never split or interpreted: a
    value containing a comma is one literal scope name. Every non-blank value is
    looked up in request order, and the result is the deduplicated union of the
    named scopes' roots in first-appearance order — the rule
    `ScopeMap.union_roots` documents. Naming the same scope more than once is
    de-duplication, not an error.

    Raises:
        MissingScopeError: When no non-blank scope value was given.
        UnknownScopeError: When a value is not a declared scope. The whole
            request is rejected; the roots of the declared values are never
            returned.
    """
    if raw_values is None:
        values: Sequence[str] = ()
    elif isinstance(raw_values, str):
        values = (raw_values,)
    else:
        values = raw_values

    seen: set[Path] = set()
    union: list[Path] = []
    found = False
    for value in values:
        if value.strip() == "":
            continue
        found = True
        roots = scope_map.scopes.get(value)
        if roots is None:
            raise UnknownScopeError(f"unknown scope {value!r}")
        for root in roots:
            if root not in seen:
                seen.add(root)
                union.append(root)
    if not found:
        raise MissingScopeError("request named no scope")
    return tuple(union)


def set_request_roots(roots: tuple[Path, ...]) -> contextvars.Token[tuple[Path, ...] | None]:
    """Bind the roots resolved for the request being served; return the reset token.

    The binding lives in the current asyncio task's context, so a session's
    receive-loop task (spawned from the request that created it) sees the
    value bound here, while a concurrent request in another task sees its own.
    """
    return _request_roots_var.set(roots)


def reset_request_roots(token: contextvars.Token[tuple[Path, ...] | None]) -> None:
    """Undo a previous `set_request_roots`."""
    _request_roots_var.reset(token)


def current_request_roots() -> tuple[Path, ...] | None:
    """Return the roots bound for the MCP session being served, or None when nothing is bound."""
    return _request_roots_var.get()


def mark_http_transport() -> None:
    """Mark this process as serving the HTTP mount (called once, at app construction)."""
    global _http_transport
    _http_transport = True


def http_transport() -> bool:
    """Return True when this process serves the HTTP mount."""
    return _http_transport


def reset_http_transport() -> None:
    """Clear the HTTP-transport marker. Test seam: pytest shares one process across modules."""
    global _http_transport
    _http_transport = False
