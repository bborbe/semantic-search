"""Scoping probes for the search, duplicate, and content read paths.

Each probe drives a real Starlette app (via TestClient) over real temporary
content roots — the container has no vault content mounted, so every test
builds its own roots. The index is built by the app's background task, so
each probe waits for `/health` to report ready before issuing requests, and
resets the process-wide state before and after so a stale indexer from an
earlier test never decides what is indexed.
"""

import asyncio
import logging
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import semantic_search.factory as factory
from semantic_search.http_server import build_app
from semantic_search.indexer import VaultIndexer
from semantic_search.scopes import ScopeMap


def _scope_map(root_a: Path, root_b: Path) -> ScopeMap:
    """Scope map with two local scopes over the two temp roots.

    The scope names are local to the test map — the committed `scopes.yaml`
    names host directories that do not exist inside the container.
    """
    return ScopeMap(scopes={"scope_a": (root_a,), "scope_b": (root_b,)})


def _wait_for_ready(client: TestClient, attempts: int = 200) -> None:
    """Poll /health until the background index build reports ready."""
    for _ in range(attempts):
        resp = client.get("/health")
        if resp.status_code == 200 and resp.json().get("ready") is True:
            return
        time.sleep(0.05)
    raise AssertionError("indexer never became ready within the polling budget")


@pytest.fixture(autouse=True)
def _reset_process_state() -> None:
    """Reset process-wide singletons before and after each scoping test.

    The factory singleton and the http_server readiness globals are process
    wide: a stale indexer or readiness event left behind by an earlier test
    would otherwise decide what a later test indexes or serves.
    """
    import semantic_search.factory as factory
    import semantic_search.http_server as http_server

    factory.reset()
    http_server._indexer = None
    http_server._indexer_error = None
    http_server._indexer_ready = asyncio.Event()
    yield
    factory.reset()
    http_server._indexer = None
    http_server._indexer_error = None
    http_server._indexer_ready = asyncio.Event()


@pytest.fixture
def two_roots(tmp_path: Path) -> tuple[Path, Path]:
    """Create two sibling temporary content roots, A and B."""
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    root_a.mkdir()
    root_b.mkdir()
    return root_a, root_b


class TestScopedSearchLeaksNothingAndOrdersIdentically:
    """A scoped search returns exactly what an index over only that scope's
    roots would return — same paths, same order — and leaks nothing."""

    def test_scoped_search_matches_a_only_index_and_leaks_nothing(
        self,
        two_roots: tuple[Path, Path],
        deterministic_sentence_transformer: type,
    ) -> None:
        import semantic_search.http_server as http_server

        root_a, root_b = two_roots
        # A holds two docs at different distances from the probe query; B holds
        # one doc containing the same probe term. The probe tokens come from
        # the deterministic encoder's fixed vocabulary.
        (root_a / "a-near.md").write_text("alpha\n" * 10)
        (root_a / "a-far.md").write_text("gamma\n" * 10)
        (root_b / "b-one.md").write_text("alpha beta beta\n")

        app = build_app(_scope_map(root_a, root_b))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app) as client,
        ):
            _wait_for_ready(client)

            # The leak assertion is load-bearing: an unscoped search on the
            # app's own union index returns the B path, so deleting the scope
            # filter changes the result instead of returning the same A-only
            # list.
            unscoped = http_server.get_indexer().search("alpha", top_k=10)
            unscoped_paths = [r["path"] for r in unscoped]
            assert any(Path(p).resolve().is_relative_to(root_b.resolve()) for p in unscoped_paths)

            scoped = client.get("/search?q=alpha&scope=scope_a&top_k=20")
            assert scoped.status_code == 200
            scoped_data = scoped.json()
            assert scoped_data["count"] >= 1
            for result in scoped_data["results"]:
                assert Path(result["path"]).resolve().is_relative_to(root_a.resolve())
            scoped_paths = [r["path"] for r in scoped_data["results"]]

        # Exit the first app's context and reset the process state so a second
        # app over A alone builds a genuinely A-only index. A second app
        # without the reset would serve the first app's union index filtered
        # with the same roots, and the two path lists would be equal even with
        # the scope filter deleted — the assertion would prove nothing.
        factory.reset()
        http_server._indexer = None
        http_server._indexer_error = None
        http_server._indexer_ready = asyncio.Event()

        app_a = build_app(ScopeMap(scopes={"scope_a": (root_a,)}))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app_a) as client,
        ):
            _wait_for_ready(client)
            oracle = client.get("/search?q=alpha&scope=scope_a&top_k=20")
            assert oracle.status_code == 200
            oracle_paths = [r["path"] for r in oracle.json()["results"]]

        assert scoped_paths == oracle_paths
        assert len(scoped_paths) >= 1


class TestScopedSearchNotTruncatedByOutOfScopeNeighbours:
    """A scope must never return fewer than top_k results merely because
    out-of-scope documents crowd the nearest-neighbour window."""

    def test_scoped_search_widens_beyond_out_of_scope_window(
        self,
        two_roots: tuple[Path, Path],
        deterministic_sentence_transformer: type,
    ) -> None:
        import semantic_search.http_server as http_server

        root_a, root_b = two_roots
        top_k = 3
        # A holds exactly top_k docs, all far from the probe query; B holds
        # more than top_k * 4 docs all nearer the query than every A doc, so
        # the first retrieval window (min(top_k * 4, ntotal)) is entirely out
        # of scope and a filter without widening returns fewer than top_k.
        for i in range(top_k):
            (root_a / f"a{i}.md").write_text("gamma\n" * 10)
        for i in range(top_k * 4 + 4):
            (root_b / f"b{i}.md").write_text("alpha\n" * 10)

        # Assert the geometry rather than assuming it.
        a_count = len(list(root_a.glob("*.md")))
        b_count = len(list(root_b.glob("*.md")))
        assert a_count >= top_k
        assert b_count > top_k * 4

        app = build_app(_scope_map(root_a, root_b))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app) as client,
        ):
            _wait_for_ready(client)

            # All B docs are nearer the probe query than every A doc: an
            # unscoped search for top_k=len(B) returns only B paths.
            unscoped = http_server.get_indexer().search("alpha", top_k=b_count)
            unscoped_paths = [r["path"] for r in unscoped]
            assert len(unscoped_paths) == b_count
            assert all(Path(p).resolve().is_relative_to(root_b.resolve()) for p in unscoped_paths)

            scoped = client.get(f"/search?q=alpha&scope=scope_a&top_k={top_k}")
            assert scoped.status_code == 200
            data = scoped.json()
            assert data["count"] == top_k
            for result in data["results"]:
                assert Path(result["path"]).resolve().is_relative_to(root_a.resolve())


class TestScopedDuplicatesExcludeOutOfScope:
    """Duplicate detection compares a file only against documents inside the
    requested scope, so a near-identical file in another scope is never
    returned."""

    def test_duplicates_never_return_out_of_scope_path(
        self,
        two_roots: tuple[Path, Path],
        deterministic_sentence_transformer: type,
    ) -> None:
        root_a, root_b = two_roots
        # Two near-identical files in A and a near-identical file in B.
        (root_a / "dup-a.md").write_text("alpha\n" * 50)
        (root_a / "dup-a2.md").write_text("alpha\n" * 50)
        (root_b / "dup-b.md").write_text("alpha\n" * 50)

        app = build_app(_scope_map(root_a, root_b))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app) as client,
        ):
            _wait_for_ready(client)

            resp = client.get(f"/duplicates?file={root_a / 'dup-a.md'}&scope=scope_a")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] >= 1
            for dup in data["duplicates"]:
                assert Path(dup["path"]).resolve().is_relative_to(root_a.resolve())


class TestScopedContentRefusesOutOfScopePath:
    """Content fetch serves a file only when it resolves inside the requested
    scope, reusing the route's existing PATH_OUTSIDE_ROOTS refusal."""

    def test_content_refuses_out_of_scope_and_serves_in_scope(
        self,
        two_roots: tuple[Path, Path],
        deterministic_sentence_transformer: type,
    ) -> None:
        root_a, root_b = two_roots
        (root_a / "a-file.md").write_text("alpha content")
        b_file = root_b / "b-file.md"
        b_file.write_text("beta content")

        app = build_app(_scope_map(root_a, root_b))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app) as client,
        ):
            _wait_for_ready(client)

            refused = client.get(f"/content?path={b_file}&scope=scope_a")
            assert refused.status_code == 400
            assert refused.json()["error"]["code"] == "PATH_OUTSIDE_ROOTS"

            served = client.get(f"/content?path={b_file}&scope=scope_b")
            assert served.status_code == 200
            assert served.json()["content"] == "beta content"


class TestRootsNoneContract:
    """search / find_duplicates / get_content called without `roots` behave
    exactly as before — the stdio transport and CLI contract."""

    def test_search_roots_none_covers_all_roots(
        self,
        tmp_path: Path,
        deterministic_sentence_transformer: type,
    ) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / "a1.md").write_text("alpha gamma")
        (root_b / "b1.md").write_text("alpha beta")

        with patch(
            "semantic_search.indexer.SentenceTransformer",
            deterministic_sentence_transformer,
        ):
            indexer = VaultIndexer([str(root_a), str(root_b)])
            results = indexer.search("alpha")

        paths = {r["path"] for r in results}
        assert str(root_a / "a1.md") in paths
        assert str(root_b / "b1.md") in paths

    def test_find_duplicates_roots_none_returns_cross_root_candidates(
        self,
        tmp_path: Path,
        deterministic_sentence_transformer: type,
    ) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / "a1.md").write_text("alpha\n" * 30)
        (root_a / "a2.md").write_text("alpha\n" * 30)
        (root_b / "b1.md").write_text("alpha\n" * 30)

        with patch(
            "semantic_search.indexer.SentenceTransformer",
            deterministic_sentence_transformer,
        ):
            indexer = VaultIndexer([str(root_a), str(root_b)])
            dups = indexer.find_duplicates(str(root_a / "a1.md"))

        paths = {d["path"] for d in dups}
        assert str(root_a / "a2.md") in paths
        # Unscoped: a cross-root duplicate is visible, exactly as before.
        assert str(root_b / "b1.md") in paths

    def test_get_content_roots_none_uses_vault_paths(
        self,
        tmp_path: Path,
        deterministic_sentence_transformer: type,
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "note.md"
        note.write_text("hello alpha")

        with patch(
            "semantic_search.indexer.SentenceTransformer",
            deterministic_sentence_transformer,
        ):
            indexer = VaultIndexer(str(vault))
            result = indexer.get_content(str(note))

        assert result["content"] == "hello alpha"
        assert result["mode"] == "full"
        assert result["path"] == str(note.resolve())


class TestRepeatedScopeSearchesTheUnion:
    """A URL naming two scopes returns results drawn from both of them, and the
    order the scopes are named in does not change the result set."""

    def test_union_contains_paths_under_both_roots(
        self,
        two_roots: tuple[Path, Path],
        deterministic_sentence_transformer: type,
    ) -> None:
        root_a, root_b = two_roots
        # Both scopes hold a document matching the probe query, so a last-wins
        # collapse to either single scope drops the other root's document.
        (root_a / "a-alpha.md").write_text("alpha\n" * 10)
        (root_b / "b-alpha.md").write_text("alpha\n" * 10)

        app = build_app(_scope_map(root_a, root_b))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app) as client,
        ):
            _wait_for_ready(client)
            single_a = client.get("/search?q=alpha&top_k=20&scope=scope_a").json()
            single_b = client.get("/search?q=alpha&top_k=20&scope=scope_b").json()
            union = client.get("/search?q=alpha&top_k=20&scope=scope_a&scope=scope_b").json()

        single_a_paths = {r["path"] for r in single_a["results"]}
        single_b_paths = {r["path"] for r in single_b["results"]}
        union_paths = {r["path"] for r in union["results"]}

        # Count alone is not evidence: search() returns the moment it holds
        # top_k in-scope hits, so a union and a last-wins collapse both return
        # exactly top_k paths. The strict-superset relations and the per-scope
        # membership are what carry the assertion.
        assert any(Path(p).resolve().is_relative_to(root_a.resolve()) for p in union_paths)
        assert any(Path(p).resolve().is_relative_to(root_b.resolve()) for p in union_paths)
        assert union_paths > single_a_paths
        assert union_paths > single_b_paths

    def test_union_membership_is_order_independent(
        self,
        two_roots: tuple[Path, Path],
        deterministic_sentence_transformer: type,
    ) -> None:
        root_a, root_b = two_roots
        (root_a / "a-alpha.md").write_text("alpha\n" * 10)
        (root_b / "b-alpha.md").write_text("alpha\n" * 10)

        app = build_app(_scope_map(root_a, root_b))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app) as client,
        ):
            _wait_for_ready(client)
            ab = client.get("/search?q=alpha&top_k=20&scope=scope_a&scope=scope_b").json()
            ba = client.get("/search?q=alpha&top_k=20&scope=scope_b&scope=scope_a").json()

        ab_paths = [r["path"] for r in ab["results"]]
        ba_paths = [r["path"] for r in ba["results"]]

        # The union is a set of roots and membership is order-independent, so
        # the result order (the index's own nearest-neighbour order restricted
        # to in-scope documents) cannot depend on the parameter order.
        assert ab_paths == ba_paths
        assert set(ab_paths) == set(ba_paths)
        assert ab_paths
        assert any(Path(p).resolve().is_relative_to(root_a.resolve()) for p in ab_paths)
        assert any(Path(p).resolve().is_relative_to(root_b.resolve()) for p in ab_paths)


class TestRepeatedScopeFailsClosedOnUnknownName:
    """Any undeclared name in a repeated scope refuses the whole request, and
    the offending value is named in the log line, never in the body."""

    def test_unknown_name_in_a_repeated_scope_is_refused(
        self,
        two_roots: tuple[Path, Path],
        deterministic_sentence_transformer: type,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        root_a, root_b = two_roots
        (root_a / "a-alpha.md").write_text("alpha\n" * 10)
        (root_b / "b-alpha.md").write_text("alpha\n" * 10)

        app = build_app(_scope_map(root_a, root_b))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app) as client,
        ):
            _wait_for_ready(client)
            with caplog.at_level(logging.WARNING):
                refused = client.get("/search?q=alpha&top_k=20&scope=scope_a&scope=bogus")
            # `bogus` here is an unnamed parameter the handler ignores, not a
            # `scope` value — the boundary the spec calls out.
            ignored = client.get("/search?q=alpha&scope=scope_a&bogus=1")

        assert refused.status_code == 400
        body = refused.json()
        assert body == {"error": "UNKNOWN_SCOPE"}
        assert "results" not in body

        # Count only the records naming the offending value, so an unrelated
        # library warning in the capture cannot make this brittle.
        naming = [r for r in caplog.records if "bogus" in r.getMessage()]
        assert len(naming) >= 1
        assert naming[0].levelno == logging.WARNING

        assert ignored.status_code == 200


class TestRepeatedScopeRejectedFormsStayRejected:
    """The bracket form and the comma form are not scope values and stay
    refused — no parsing code exists for either, and none may be added."""

    def test_bracket_and_comma_forms_stay_refused(
        self,
        two_roots: tuple[Path, Path],
        deterministic_sentence_transformer: type,
    ) -> None:
        root_a, root_b = two_roots
        (root_a / "a-alpha.md").write_text("alpha\n" * 10)

        app = build_app(_scope_map(root_a, root_b))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app) as client,
        ):
            bracket = client.get("/search?q=alpha&scope[]=scope_a&scope[]=scope_b")
            comma = client.get("/search?q=alpha&scope=scope_a,scope_b")

        # `scope[]` is not a `scope` parameter at all, so the request names no
        # scope; `scope=a,b` is one literal unknown name.
        assert bracket.status_code == 400
        assert bracket.json() == {"error": "MISSING_SCOPE"}
        assert comma.status_code == 400
        assert comma.json() == {"error": "UNKNOWN_SCOPE"}


class TestSingleScopeRequestIsByteIdentical:
    """A single-scope request against the union app returns byte-identical
    results — same paths, same order, same scores — to an independently built
    single-scope index over that scope's own roots.

    The frozen pre-change baseline is a retired oracle and is deliberately not
    used: the oracle here is a second app, over root_a alone, with the full
    process-state reset and its own cache directory (the index directory is
    keyed on the joined resolved roots).
    """

    def test_single_scope_request_matches_an_independent_single_scope_index(
        self,
        two_roots: tuple[Path, Path],
        deterministic_sentence_transformer: type,
    ) -> None:
        import semantic_search.http_server as http_server

        root_a, root_b = two_roots
        (root_a / "a-alpha.md").write_text("alpha\n" * 10)
        (root_b / "b-alpha.md").write_text("alpha\n" * 10)

        app = build_app(_scope_map(root_a, root_b))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app) as client,
        ):
            _wait_for_ready(client)

            # The union app's own index genuinely differs from the oracle's:
            # an unscoped search reaches a root_b path, so the two indexes
            # cannot be identical and the comparison proves something.
            unscoped = http_server.get_indexer().search("alpha", top_k=20)
            unscoped_paths = [r["path"] for r in unscoped]
            assert any(Path(p).resolve().is_relative_to(root_b.resolve()) for p in unscoped_paths)

            response = client.get("/search?q=alpha&top_k=20&scope=scope_a")
            assert response.status_code == 200
            union_app = response.json()
            union_app_pairs = [(r["path"], r["score"]) for r in union_app["results"]]

        assert union_app_pairs

        # Full process-state reset plus a second app over root_a alone, so its
        # background build indexes root_a only into its own cache directory.
        factory.reset()
        http_server._indexer = None
        http_server._indexer_error = None
        http_server._indexer_ready = asyncio.Event()

        app_a = build_app(ScopeMap(scopes={"scope_a": (root_a,)}))
        with (
            patch(
                "semantic_search.indexer.SentenceTransformer",
                deterministic_sentence_transformer,
            ),
            TestClient(app_a) as client,
        ):
            _wait_for_ready(client)
            health = client.get("/health").json()
            a_file_count = len(list(root_a.glob("*.md")))
            assert a_file_count > 0
            assert health["paths"] == [str(root_a)]
            assert health["indexed_files"] == a_file_count

            oracle = client.get("/search?q=alpha&top_k=20&scope=scope_a")
            assert oracle.status_code == 200
            oracle_body = oracle.json()

        # Zero tolerance: one extra path, one missing path, a reordering, or a
        # differing score fails.
        assert union_app_pairs == [(r["path"], r["score"]) for r in oracle_body["results"]]
        assert union_app["query"] == oracle_body["query"]
        assert union_app["count"] == oracle_body["count"]
