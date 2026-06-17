"""Tests for VaultIgnore."""

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pathspec.gitignore import GitIgnoreSpecPattern

from semantic_search.ignore import VaultIgnore


class TestVaultIgnoreMissingFile:
    """Tests for behavior when .semanticignore does not exist."""

    def test_missing_file_ignores_nothing(self, tmp_path: Path) -> None:
        """A missing .semanticignore causes is_ignored to return False for all paths."""
        vault = tmp_path / "vault"
        vault.mkdir()
        vi = VaultIgnore(vault)

        assert not vi.is_ignored(vault / "note.md")
        assert not vi.is_ignored(vault / "sub" / "note.md")
        assert not vi.is_ignored(vault / "secret.txt")


class TestVaultIgnoreEmptyFile:
    """Tests for behavior when .semanticignore exists but is empty."""

    def test_empty_file_ignores_nothing(self, tmp_path: Path) -> None:
        """An empty .semanticignore means ignore nothing."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("")
        vi = VaultIgnore(vault)

        assert not vi.is_ignored(vault / "note.md")
        assert not vi.is_ignored(vault / "archive" / "old.md")


class TestVaultIgnoreSimplePattern:
    """Tests for simple filename patterns."""

    def test_simple_pattern_matches_file(self, tmp_path: Path) -> None:
        """A simple pattern like secret.md ignores that file but not others."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("secret.md\n")
        vi = VaultIgnore(vault)

        assert vi.is_ignored(vault / "secret.md")
        assert not vi.is_ignored(vault / "public.md")
        assert not vi.is_ignored(vault / "notes.md")


class TestVaultIgnoreDirectoryPattern:
    """Tests for directory patterns (trailing slash)."""

    def test_directory_pattern_matches_contents(self, tmp_path: Path) -> None:
        """A pattern like archive/ ignores files inside archive/ but not elsewhere."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("archive/\n")
        vi = VaultIgnore(vault)

        assert vi.is_ignored(vault / "archive" / "old.md")
        assert not vi.is_ignored(vault / "a.md")
        assert not vi.is_ignored(vault / "notes" / "note.md")


class TestVaultIgnoreDoubleStarPattern:
    """Tests for double-star glob patterns."""

    def test_double_star_matches_nested_files(self, tmp_path: Path) -> None:
        """A pattern like **/draft.md ignores draft.md at any nesting depth."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("**/draft.md\n")
        vi = VaultIgnore(vault)

        assert vi.is_ignored(vault / "draft.md")
        assert vi.is_ignored(vault / "notes" / "draft.md")
        assert vi.is_ignored(vault / "a" / "b" / "draft.md")
        assert not vi.is_ignored(vault / "notes" / "other.md")


class TestVaultIgnoreNegationPattern:
    """Tests for negation patterns."""

    def test_negation_un_ignores_file(self, tmp_path: Path) -> None:
        """A negation pattern like !archive/keep.md exempts that file from the rule."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("archive/\n!archive/keep.md\n")
        vi = VaultIgnore(vault)

        assert vi.is_ignored(vault / "archive" / "old.md")
        assert not vi.is_ignored(vault / "archive" / "keep.md")
        assert not vi.is_ignored(vault / "a.md")


class TestVaultIgnoreAnchoredPattern:
    """Tests for root-anchored patterns (leading slash)."""

    def test_anchored_pattern_matches_root_only(self, tmp_path: Path) -> None:
        """A leading slash anchors the pattern to the vault root."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("/root-only.md\n")
        vi = VaultIgnore(vault)

        assert vi.is_ignored(vault / "root-only.md")
        assert not vi.is_ignored(vault / "sub" / "root-only.md")


class TestVaultIgnoreSelfAlwaysIgnored:
    """Tests that .semanticignore itself is always reported as ignored."""

    def test_semanticignore_always_ignored_with_empty_patterns(self, tmp_path: Path) -> None:
        """The .semanticignore file itself is ignored even if the file is empty."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("")
        vi = VaultIgnore(vault)

        assert vi.is_ignored(vault / ".semanticignore")

    def test_semanticignore_always_ignored_when_missing(self, tmp_path: Path) -> None:
        """The .semanticignore file itself is ignored even when .semanticignore is absent."""
        vault = tmp_path / "vault"
        vault.mkdir()
        vi = VaultIgnore(vault)

        assert vi.is_ignored(vault / ".semanticignore")


class TestVaultIgnoreMalformedPattern:
    """Tests for malformed pattern line handling."""

    def test_malformed_pattern_logs_error_with_line_number(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A malformed pattern logs an ERROR naming the line number; valid patterns still work."""
        vault = tmp_path / "vault"
        vault.mkdir()
        # [z-a] is an invalid character range that pathspec rejects
        (vault / ".semanticignore").write_text("[z-a]\n*.py\n")

        with caplog.at_level(logging.ERROR, logger="semantic_search.ignore"):
            vi = VaultIgnore(vault)

        # An ERROR record must reference line 1 (the malformed line)
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "Expected at least one ERROR log for malformed pattern"
        assert any("1" in r.getMessage() for r in error_records), (
            "ERROR message should mention the line number (1)"
        )

        # The valid *.py pattern on line 2 must still be compiled and applied
        assert vi.is_ignored(vault / "script.py")
        assert not vi.is_ignored(vault / "note.md")


class TestPathspecBoundaryContract:
    """Pins the upstream pathspec behavior that VaultIgnore's malformed-line detection relies on."""

    def test_bad_range_pattern_raises(self) -> None:
        """GitIgnoreSpecPattern('[z-a]') must raise an exception.

        This test pins the behavior that the exception-based malformed detection
        path relies on. If pathspec changes so that [z-a] no longer raises,
        this test will fail and you must pick a new sentinel.
        """
        with pytest.raises(re.error):
            GitIgnoreSpecPattern("[z-a]")

    def test_valid_pattern_has_non_none_include(self) -> None:
        """A valid pattern like *.md must produce include=True (not None).

        Ensures the include=None detection path only fires for genuinely
        rejected patterns, not for valid ones.
        """
        pat = GitIgnoreSpecPattern("*.md")
        assert pat.include is not None


class TestVaultIgnoreOversizedFile:
    """Tests for .semanticignore files exceeding the 1 MiB size limit."""

    def test_oversized_file_logs_error_and_ignores_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An oversized .semanticignore logs an ERROR and treats vault as accept-all."""
        vault = tmp_path / "vault"
        vault.mkdir()
        # Write a file larger than 1 MiB
        ignore_path = vault / ".semanticignore"
        ignore_path.write_text("# " + "x" * (1024 * 1024 + 1))

        with caplog.at_level(logging.ERROR, logger="semantic_search.ignore"):
            vi = VaultIgnore(vault)

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "Expected ERROR log for oversized file"

        # Matcher should accept everything (ignore nothing)
        assert not vi.is_ignored(vault / "note.md")
        assert not vi.is_ignored(vault / "archive" / "old.md")


class TestVaultIgnoreUnreadableFile:
    """Tests for an unreadable .semanticignore file."""

    def test_unreadable_file_logs_error_and_ignores_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An unreadable .semanticignore logs an ERROR and treats vault as accept-all."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("*.md\n")

        with (
            patch("pathlib.Path.read_text", side_effect=OSError("permission denied")),
            caplog.at_level(logging.ERROR, logger="semantic_search.ignore"),
        ):
            vi = VaultIgnore(vault)

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "Expected ERROR log for unreadable file"

        # Matcher should accept everything (ignore nothing)
        assert not vi.is_ignored(vault / "note.md")


class TestVaultIgnoreReload:
    """Tests for the reload() method."""

    def test_reload_picks_up_new_patterns(self, tmp_path: Path) -> None:
        """After reload(), newly added patterns take effect."""
        vault = tmp_path / "vault"
        vault.mkdir()
        ignore_path = vault / ".semanticignore"
        ignore_path.write_text("")
        vi = VaultIgnore(vault)

        # Before reload: path is not ignored
        assert not vi.is_ignored(vault / "secret.md")

        # Write a new pattern
        ignore_path.write_text("secret.md\n")
        vi.reload()

        # After reload: path is now ignored
        assert vi.is_ignored(vault / "secret.md")
        assert not vi.is_ignored(vault / "public.md")


class TestVaultIgnorePathHandling:
    """Tests for path handling edge cases."""

    def test_path_outside_vault_not_ignored(self, tmp_path: Path) -> None:
        """A path outside the vault root is never considered ignored."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("*.md\n")
        vi = VaultIgnore(vault)

        outside = tmp_path / "other" / "note.md"
        assert not vi.is_ignored(outside)

    def test_relative_path_resolved_against_vault(self, tmp_path: Path) -> None:
        """A relative path is resolved relative to the vault root."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".semanticignore").write_text("secret.md\n")
        vi = VaultIgnore(vault)

        assert vi.is_ignored("secret.md")
        assert not vi.is_ignored("public.md")
