"""Tests for the scope map loader, validator, and resolver."""

from pathlib import Path

import pytest

from semantic_search.scopes import (
    MissingScopeError,
    ScopeConfigError,
    ScopeMap,
    UnknownScopeError,
    load_scope_map,
    resolve_scope,
    scope_map_path_from_env,
    validate_scope_map,
)


def _write_map(tmp_path: Path, content: str) -> Path:
    """Write a scope map file into tmp_path and return its path."""
    path = tmp_path / "scopes.yaml"
    path.write_text(content)
    return path


def test_load_valid_map_and_union_dedup(tmp_path: Path) -> None:
    """A valid map loads into the expected scopes mapping, and union_roots is
    deduplicated in first-appearance order (a root shared by two scopes
    appears once)."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    path = _write_map(
        tmp_path,
        f"scopes:\n  one:\n    - {root_a}\n    - {root_b}\n  two:\n    - {root_b}\n",
    )

    scope_map = load_scope_map(path)

    assert scope_map.scopes["one"] == (root_a, root_b)
    assert scope_map.scopes["two"] == (root_b,)
    assert scope_map.union_roots == (root_a, root_b)


class TestScopeMapPathFromEnv:
    def test_returns_env_var_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        map_path = tmp_path / "scopes.yaml"
        monkeypatch.setenv("SEMANTIC_SCOPE_MAP", str(map_path))
        assert scope_map_path_from_env() == map_path

    def test_raises_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SEMANTIC_SCOPE_MAP", raising=False)
        with pytest.raises(ScopeConfigError):
            scope_map_path_from_env()

    def test_raises_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEMANTIC_SCOPE_MAP", "")
        with pytest.raises(ScopeConfigError):
            scope_map_path_from_env()


class TestLoadScopeMap:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ScopeConfigError, match="cannot read scope map"):
            load_scope_map(tmp_path / "missing.yaml")

    def test_unreadable_file(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "scopes:\n  one:\n    - /tmp\n")
        path.chmod(0)
        try:
            with pytest.raises(ScopeConfigError, match="cannot read scope map"):
                load_scope_map(path)
        finally:
            path.chmod(0o644)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "scopes: [unclosed\n  - bad")
        with pytest.raises(ScopeConfigError, match="not valid YAML"):
            load_scope_map(path)

    def test_empty_document(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "")
        with pytest.raises(ScopeConfigError, match="'scopes' key"):
            load_scope_map(path)

    def test_document_without_scopes_key(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "other: true\n")
        with pytest.raises(ScopeConfigError, match="'scopes' key"):
            load_scope_map(path)

    def test_scopes_value_not_a_mapping(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "scopes:\n  - personal\n")
        with pytest.raises(ScopeConfigError, match="'scopes' must be a mapping"):
            load_scope_map(path)

    def test_empty_scopes_mapping(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "scopes: {}\n")
        with pytest.raises(ScopeConfigError, match="at least one scope"):
            load_scope_map(path)

    def test_scope_value_not_a_list(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "scopes:\n  one: /some/root\n")
        with pytest.raises(ScopeConfigError, match="must be a list of roots"):
            load_scope_map(path)

    def test_empty_root_list(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "scopes:\n  one: []\n")
        with pytest.raises(ScopeConfigError, match="has no roots"):
            load_scope_map(path)

    def test_non_string_root(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "scopes:\n  one:\n    - 12345\n")
        with pytest.raises(ScopeConfigError, match="non-string root"):
            load_scope_map(path)

    def test_relative_root(self, tmp_path: Path) -> None:
        path = _write_map(tmp_path, "scopes:\n  one:\n    - relative/root\n")
        with pytest.raises(ScopeConfigError, match="not absolute"):
            load_scope_map(path)


class TestValidateScopeMap:
    def test_accepts_readable_directories(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        validate_scope_map(ScopeMap(scopes={"one": (root,)}))

    def test_missing_root_names_scope_and_root(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing-root"
        with pytest.raises(ScopeConfigError) as exc_info:
            validate_scope_map(ScopeMap(scopes={"probe": (missing,)}))
        message = str(exc_info.value)
        assert "probe" in message
        assert "missing-root" in message

    def test_regular_file_root_names_scope_and_root(self, tmp_path: Path) -> None:
        regular = tmp_path / "not-a-dir"
        regular.write_text("x")
        with pytest.raises(ScopeConfigError) as exc_info:
            validate_scope_map(ScopeMap(scopes={"probe": (regular,)}))
        message = str(exc_info.value)
        assert "probe" in message
        assert "not-a-dir" in message

    def test_unreadable_root_names_scope_and_root(self, tmp_path: Path) -> None:
        unreadable = tmp_path / "unreadable"
        unreadable.mkdir()
        unreadable.chmod(0)
        try:
            with pytest.raises(ScopeConfigError) as exc_info:
                validate_scope_map(ScopeMap(scopes={"probe": (unreadable,)}))
            message = str(exc_info.value)
            assert "probe" in message
            assert "unreadable" in message
        finally:
            unreadable.chmod(0o755)


class TestResolveScope:
    def test_known_name_returns_roots(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        scope_map = ScopeMap(scopes={"one": (root,)})
        assert resolve_scope(scope_map, "one") == (root,)

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_missing_scope(self, raw: str | None, tmp_path: Path) -> None:
        scope_map = ScopeMap(scopes={"one": (tmp_path,)})
        with pytest.raises(MissingScopeError):
            resolve_scope(scope_map, raw)

    def test_unknown_scope(self, tmp_path: Path) -> None:
        scope_map = ScopeMap(scopes={"one": (tmp_path,)})
        with pytest.raises(UnknownScopeError):
            resolve_scope(scope_map, "does-not-exist")
