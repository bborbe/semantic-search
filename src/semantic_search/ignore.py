"""Vault ignore module for filtering paths via .semanticignore patterns."""

import logging
import re
from pathlib import Path

import pathspec

logger = logging.getLogger(__name__)

_MAX_IGNORE_FILE_SIZE = 1 * 1024 * 1024  # 1 MiB
_IGNORE_FILENAME = ".semanticignore"


def _empty_spec() -> pathspec.PathSpec:
    """Return a PathSpec that matches nothing (accept-all / ignore-nothing)."""
    return pathspec.PathSpec.from_lines("gitignore", [])


class VaultIgnore:
    """Loads a vault root's .semanticignore file and decides which paths to exclude.

    Patterns use gitignore (gitwildmatch) semantics and are matched relative to
    the vault root using POSIX-style forward slashes. A missing or empty
    .semanticignore means "ignore nothing". The .semanticignore file itself is
    always ignored.
    """

    def __init__(self, vault_root: str | Path) -> None:
        """Initialize VaultIgnore for the given vault root.

        Reads and compiles .semanticignore immediately. A missing file is
        treated as "ignore nothing" and does not produce an error.

        Args:
            vault_root: Absolute or relative path to the vault root directory.
        """
        self._vault_root = Path(vault_root).resolve()
        self._spec = self._load()

    def is_ignored(self, path: str | Path) -> bool:
        """Return True iff the given path should be excluded from indexing.

        The path may be absolute or relative; it is interpreted relative to the
        vault root. Paths outside the vault root are treated as NOT ignored.

        Args:
            path: The path to test. May be absolute or relative.

        Returns:
            True if the path matches an ignore pattern, False otherwise.
        """
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = (self._vault_root / resolved).resolve()

        try:
            rel = resolved.relative_to(self._vault_root)
        except ValueError:
            return False

        posix_rel = rel.as_posix()

        if posix_rel == _IGNORE_FILENAME:
            return True

        return bool(self._spec.match_file(posix_rel))

    def reload(self) -> None:
        """Re-read .semanticignore from disk and rebuild the compiled matcher.

        Used when the file changes at runtime. Replaces the internal matcher
        atomically (build the new one fully, then assign).
        """
        self._spec = self._load()

    def _load(self) -> pathspec.PathSpec:
        """Read .semanticignore and compile a PathSpec matcher.

        Returns an accept-all (ignore-nothing) matcher on any I/O or size error.
        Malformed pattern lines are logged at ERROR and skipped; valid lines
        are still compiled into the returned matcher.

        Returns:
            A compiled PathSpec, or an empty (accept-all) PathSpec on error.
        """
        ignore_path = self._vault_root / _IGNORE_FILENAME
        if not ignore_path.exists():
            return _empty_spec()

        try:
            size = ignore_path.stat().st_size
        except OSError as exc:
            logger.error("Cannot stat %s: %s", ignore_path, exc)
            return _empty_spec()

        if size > _MAX_IGNORE_FILE_SIZE:
            logger.error(
                "%s exceeds 1 MiB (%d bytes); treating vault filter as accept-all",
                ignore_path,
                size,
            )
            return _empty_spec()

        try:
            content = ignore_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.error("Cannot read %s: %s", ignore_path, exc)
            return _empty_spec()

        good_lines: list[str] = []
        for line_num, raw_line in enumerate(content.splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            try:
                pathspec.PathSpec.from_lines("gitignore", [stripped])
            except re.error:
                logger.error(
                    "Skipping malformed pattern on line %d of %s: %r",
                    line_num,
                    ignore_path,
                    stripped,
                )
                continue

            good_lines.append(stripped)

        return pathspec.PathSpec.from_lines("gitignore", good_lines)
