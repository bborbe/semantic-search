"""Tests for the scope map loader, validator, and resolver."""

import logging
from pathlib import Path

import pytest

from semantic_search.http_server import resolve_startup_scope_map
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


def _default_map_path(home: Path) -> Path:
    """Return the default scope map path under a redirected HOME."""
    return home / ".config" / "semantic-search" / "config.yaml"


def _redirect_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Point the default scope map resolution at `home` by redirecting $HOME."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SEMANTIC_SCOPE_MAP", raising=False)


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
        """A set variable wins: its value is the path and the source is `env`."""
        map_path = tmp_path / "scopes.yaml"
        monkeypatch.setenv("SEMANTIC_SCOPE_MAP", str(map_path))
        resolution = scope_map_path_from_env()
        assert resolution.path == map_path
        assert resolution.source == "env"

    def test_defaults_when_unset(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """An unset variable resolves to the user config path under $HOME."""
        _redirect_home(monkeypatch, tmp_path)
        resolution = scope_map_path_from_env()
        assert resolution.path == _default_map_path(tmp_path)
        assert resolution.source == "default"

    def test_defaults_when_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """An empty variable is the same branch as unset: it falls to the default."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("SEMANTIC_SCOPE_MAP", "")
        resolution = scope_map_path_from_env()
        assert resolution.path == _default_map_path(tmp_path)
        assert resolution.source == "default"


class TestDefaultScopeMapFailureModes:
    """The default relaxes where the map comes from, never whether it must load.

    With $HOME redirected, the default points into tmp_path; each bad shape
    must still refuse to start, and the message must name the path tried so an
    operator can tell which of the two candidate files was read.
    """

    def test_absent_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _redirect_home(monkeypatch, tmp_path)
        default = _default_map_path(tmp_path)
        with pytest.raises(ScopeConfigError) as exc_info:
            load_scope_map(scope_map_path_from_env().path)
        assert str(default) in str(exc_info.value)

    def test_unreadable_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _redirect_home(monkeypatch, tmp_path)
        default = _default_map_path(tmp_path)
        default.parent.mkdir(parents=True)
        default.write_text("scopes:\n  one:\n    - /tmp\n")
        default.chmod(0)
        try:
            with pytest.raises(ScopeConfigError) as exc_info:
                load_scope_map(scope_map_path_from_env().path)
            assert str(default) in str(exc_info.value)
        finally:
            default.chmod(0o644)

    def test_invalid_yaml(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _redirect_home(monkeypatch, tmp_path)
        default = _default_map_path(tmp_path)
        default.parent.mkdir(parents=True)
        default.write_text("scopes: [unclosed\n")
        with pytest.raises(ScopeConfigError) as exc_info:
            load_scope_map(scope_map_path_from_env().path)
        assert str(default) in str(exc_info.value)

    def test_no_scopes_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _redirect_home(monkeypatch, tmp_path)
        default = _default_map_path(tmp_path)
        default.parent.mkdir(parents=True)
        default.write_text("other: 1\n")
        with pytest.raises(ScopeConfigError) as exc_info:
            load_scope_map(scope_map_path_from_env().path)
        assert str(default) in str(exc_info.value)


class TestStartupScopeMapLog:
    """The startup log names the resolved file AND which rule chose it.

    Both directions are asserted: a hardcoded source label, or a hardcoded
    path, fails one of the two cases.
    """

    def test_logs_default_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        _redirect_home(monkeypatch, tmp_path)
        default = _default_map_path(tmp_path)
        with caplog.at_level(logging.INFO):
            resolution = resolve_startup_scope_map()
        assert resolution.path == default
        assert f"Scope map: {default} (source: default)" in caplog.text

    def test_logs_env_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        map_path = tmp_path / "scopes.yaml"
        monkeypatch.setenv("SEMANTIC_SCOPE_MAP", str(map_path))
        with caplog.at_level(logging.INFO):
            resolution = resolve_startup_scope_map()
        assert resolution.path == map_path
        assert f"Scope map: {map_path} (source: env)" in caplog.text


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


def _multi_map(tmp_path: Path) -> ScopeMap:
    """A map with two disjoint scopes and a third that shares a root with one."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_c = tmp_path / "c"
    return ScopeMap(scopes={"a": (root_a,), "b": (root_b,), "shared": (root_a, root_c)})


class TestResolveScopeUnion:
    """The multi-value contract: union of every named scope, de-duplicated."""

    def test_union_of_two_disjoint_scopes(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        scope_map = ScopeMap(scopes={"a": (root_a,), "b": (root_b,)})
        assert resolve_scope(scope_map, ["a", "b"]) == (root_a, root_b)

    def test_union_order_is_first_appearance(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_c = tmp_path / "c"
        scope_map = ScopeMap(scopes={"a": (root_a, root_c), "b": (root_b,)})
        assert resolve_scope(scope_map, ["a", "b"]) == (root_a, root_c, root_b)

    def test_order_independent_same_root_set(self, tmp_path: Path) -> None:
        scope_map = _multi_map(tmp_path)
        assert set(resolve_scope(scope_map, ["a", "b"])) == set(
            resolve_scope(scope_map, ["b", "a"])
        )

    def test_order_independent_resolves_both_scopes(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        scope_map = _multi_map(tmp_path)
        resolved = resolve_scope(scope_map, ["b", "a"])
        assert root_a in resolved
        assert root_b in resolved

    def test_shared_root_appears_once(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_c = tmp_path / "c"
        scope_map = _multi_map(tmp_path)
        resolved = resolve_scope(scope_map, ["a", "shared"])
        assert resolved == (root_a, root_c)
        assert resolved.count(root_a) == 1

    def test_repeated_name_equals_single_name(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        scope_map = ScopeMap(scopes={"a": (root_a,)})
        assert resolve_scope(scope_map, ["a", "a", "a"]) == resolve_scope(scope_map, ["a"])

    def test_bare_string_is_one_value(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        scope_map = ScopeMap(scopes={"a": (root_a,)})
        assert resolve_scope(scope_map, "a") == (root_a,)


class TestResolveScopeRejections:
    """Every rejection path: the whole request fails, and the value is named."""

    @pytest.mark.parametrize("bad", ["does-not-exist", "bogus", "a,b"])
    def test_unknown_name_is_named(self, bad: str, tmp_path: Path) -> None:
        scope_map = ScopeMap(scopes={"a": (tmp_path / "a",)})
        with pytest.raises(UnknownScopeError) as exc_info:
            resolve_scope(scope_map, [bad])
        assert bad in str(exc_info.value)

    def test_prefix_of_declared_name_is_unknown(self, tmp_path: Path) -> None:
        scope_map = ScopeMap(scopes={"personal": (tmp_path / "p",)})
        with pytest.raises(UnknownScopeError) as exc_info:
            resolve_scope(scope_map, ["perso"])
        assert "perso" in str(exc_info.value)

    def test_first_undeclared_value_is_named(self, tmp_path: Path) -> None:
        scope_map = ScopeMap(scopes={"a": (tmp_path / "a",)})
        with pytest.raises(UnknownScopeError) as exc_info:
            resolve_scope(scope_map, ["bogus", "worse"])
        assert "bogus" in str(exc_info.value)
        assert "worse" not in str(exc_info.value)

    def test_whole_request_rejected_when_any_name_unknown(self, tmp_path: Path) -> None:
        scope_map = ScopeMap(scopes={"a": (tmp_path / "a",), "b": (tmp_path / "b",)})
        with pytest.raises(UnknownScopeError):
            resolve_scope(scope_map, ["a", "bogus"])

    def test_comma_value_is_not_split(self, tmp_path: Path) -> None:
        scope_map = ScopeMap(scopes={"a": (tmp_path / "a",), "b": (tmp_path / "b",)})
        with pytest.raises(UnknownScopeError) as exc_info:
            resolve_scope(scope_map, ["a,b"])
        assert "a,b" in str(exc_info.value)

    @pytest.mark.parametrize("raw", [[], None, "", "   ", [""], ["", "   "]])
    def test_missing_scope(self, raw: object, tmp_path: Path) -> None:
        scope_map = ScopeMap(scopes={"a": (tmp_path / "a",)})
        with pytest.raises(MissingScopeError) as exc_info:
            resolve_scope(scope_map, raw)  # type: ignore[arg-type]
        assert str(exc_info.value) == "request named no scope"

    def test_blank_value_ignored_alongside_real_name(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        scope_map = ScopeMap(scopes={"a": (root_a,)})
        assert resolve_scope(scope_map, ["", "a"]) == (root_a,)
