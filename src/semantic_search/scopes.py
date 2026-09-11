"""Load, validate, and resolve the scope map.

The scope map is a YAML file mapping a scope name to an ordered list of
absolute root directories that scope is allowed to see. This module owns
loading and shape-checking the map, checking that its roots are usable on
this host, and resolving a request's scope name to its root list. It carries
no per-request state: request-scoped transport belongs to a later prompt.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

SCOPE_MAP_ENV = "SEMANTIC_SCOPE_MAP"


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


def scope_map_path_from_env() -> Path:
    """Return the map file named by SCOPE_MAP_ENV; raise ScopeConfigError when unset or empty."""
    raw = os.environ.get(SCOPE_MAP_ENV)
    if not raw:
        raise ScopeConfigError(
            f"{SCOPE_MAP_ENV} environment variable is not set; "
            "refusing to start without a scope map"
        )
    return Path(raw)


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


def resolve_scope(scope_map: ScopeMap, raw: str | None) -> tuple[Path, ...]:
    """Return the roots for a request's scope name, or raise.

    Raises:
        MissingScopeError: When no scope name was given (None or blank).
        UnknownScopeError: When the scope name is not a declared scope.
    """
    if raw is None or raw.strip() == "":
        raise MissingScopeError("request named no scope")
    roots = scope_map.scopes.get(raw)
    if roots is None:
        raise UnknownScopeError(f"unknown scope {raw!r}")
    return roots
