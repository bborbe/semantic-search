"""Fourteen-query sweep: a repeated-scope request draws from every scope it
names and from nothing outside them.

The single-query probes in `tests/test_scoping.py` assert membership and the
fail-closed paths one request at a time. This module is the broad-coverage
assertion on top of them: across a whole query set it proves that a resolution
bug which widens beyond the named scopes — or which silently drops one of them —
is caught rather than shipped. Both clauses are load-bearing and are proven so
inside the sweep: every query must return at least one path under EACH named
scope (an all-empty sweep fails here, it does not pass with a zero leakage
count), and no query may return a single path outside the named scopes, not even
from a third declared scope the request never names.

The query set is built locally rather than replayed from the acceptance
baseline. The baseline's fourteen queries exist only in the host vault fixture
(`80 Attachments/semantic-search-consolidation-baseline-2026-09-11.json` in the
Personal vault) — there is no copy in the repository and none in the git
history, so it is not readable from inside the container. This sweep therefore
mirrors the baseline's cardinality (`BASELINE_QUERY_COUNT`) and result cap
(`TOP_K`) over a corpus it builds itself, and the operator's real-fixture replay
stays on the spec's verification ladder (spec 004's replay tool).

Like the scoping probes, this drives a real Starlette app (via TestClient) over
real temporary content roots, and resets the process-wide state before and after
so a stale indexer from an earlier test never decides what is indexed.
"""

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import semantic_search.factory as factory
import semantic_search.http_server as http_server
from semantic_search.http_server import build_app
from semantic_search.scopes import ScopeMap

# The acceptance baseline's query count and `top_k`. The sweep mirrors both so
# its shape is comparable to the operator's real-fixture replay.
BASELINE_QUERY_COUNT = 14
TOP_K = 20

# The deterministic encoder's fixed vocabulary (see `tests/conftest.py`): a
# document or query's similarity is the count of these tokens it contains, so
# every query below and every document in the corpus is built from them.
_PROBE_TOKENS = (
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
)

# Six single tokens plus eight two-token combinations is fourteen distinct
# queries, every one drawn from the encoder's vocabulary.
QUERIES: list[str] = [
    *_PROBE_TOKENS,
    "alpha beta",
    "alpha gamma",
    "beta gamma",
    "beta delta",
    "gamma delta",
    "delta epsilon",
    "epsilon zeta",
    "zeta alpha",
]

# A query added or dropped later changes the sweep's coverage silently; this
# makes that a loud failure at import time instead.
assert len(QUERIES) == BASELINE_QUERY_COUNT

# Every corpus document contains all six tokens, so every query matches a
# document in every corpus root.
_PROBE_DOCUMENT = "alpha beta gamma delta epsilon zeta\n" * 5


class _QueryCounts(NamedTuple):
    """One query's result counts, split by which named scope the path sits under."""

    query: str
    scope_a: int
    scope_b: int
    outside: int


class _Corpus(NamedTuple):
    """The temp roots and the scope map the sweep drives."""

    root_a: Path
    root_b: Path
    root_c: Path
    root_empty_a: Path
    root_empty_b: Path
    scope_map: ScopeMap


def _is_under(path: str, root: Path) -> bool:
    """Return True when `path` resolves inside `root`."""
    return Path(path).resolve().is_relative_to(root.resolve())


def _wait_for_ready(client: TestClient, attempts: int = 200) -> None:
    """Poll /health until the background index build reports ready."""
    for _ in range(attempts):
        resp = client.get("/health")
        if resp.status_code == 200 and resp.json().get("ready") is True:
            return
        time.sleep(0.05)
    raise AssertionError("indexer never became ready within the polling budget")


@pytest.fixture(autouse=True)
def _reset_process_state() -> Iterator[None]:
    """Reset process-wide singletons before and after each sweep test.

    The factory singleton and the http_server readiness globals are process
    wide: a stale indexer or readiness event left behind by an earlier test
    would otherwise decide what a later test indexes or serves.
    """
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
def sweep_corpus(tmp_path: Path) -> _Corpus:
    """Build the five temp roots and the five-scope map the sweep drives.

    The three sibling corpus roots each hold two documents containing all six
    probe tokens, so every query matches a document in every root. `scope_c` is
    declared so its root is part of the index's build input even though the
    sweep request never names it — that is what makes the leakage clause
    load-bearing. The two empty scopes exist only for the empty-union probe.
    """
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    root_c = tmp_path / "root-c"
    root_empty_a = tmp_path / "root-empty-a"
    root_empty_b = tmp_path / "root-empty-b"

    for root in (root_a, root_b, root_c):
        root.mkdir()
        for index in range(2):
            (root / f"doc-{index}.md").write_text(_PROBE_DOCUMENT)
    for empty_root in (root_empty_a, root_empty_b):
        empty_root.mkdir()

    scope_map = ScopeMap(
        scopes={
            "scope_a": (root_a,),
            "scope_b": (root_b,),
            "scope_c": (root_c,),
            "scope_empty_a": (root_empty_a,),
            "scope_empty_b": (root_empty_b,),
        }
    )
    return _Corpus(
        root_a=root_a,
        root_b=root_b,
        root_c=root_c,
        root_empty_a=root_empty_a,
        root_empty_b=root_empty_b,
        scope_map=scope_map,
    )


@pytest.fixture
def sweep_app(
    sweep_corpus: _Corpus, deterministic_sentence_transformer: type
) -> Iterator[tuple[_Corpus, TestClient]]:
    """Serve the corpus's app over real temp roots and wait for it to be ready.

    The app's background task builds the index over the union of every declared
    scope's roots; the deterministic encoder is patched in for the app's
    lifetime so results are comparable between runs.
    """
    app = build_app(sweep_corpus.scope_map)
    with (
        patch(
            "semantic_search.indexer.SentenceTransformer",
            deterministic_sentence_transformer,
        ),
        TestClient(app) as client,
    ):
        _wait_for_ready(client)
        yield sweep_corpus, client


def _sweep(corpus: _Corpus, client: TestClient) -> list[_QueryCounts]:
    """Run every query once and report what came back from each named scope.

    For each query the leakage clause is first shown to be load-bearing: an
    unscoped search on the app's own index reaches `root_c`, so an out-of-scope
    count of zero cannot merely mean the index never held the unnamed scope's
    documents. The union request is then issued with the parameter genuinely
    repeated, and its paths are counted by the root they resolve under.
    """
    report: list[_QueryCounts] = []
    for query in QUERIES:
        unscoped = http_server.get_indexer().search(query, top_k=TOP_K)
        assert any(_is_under(r["path"], corpus.root_c) for r in unscoped), (
            f"unscoped search for {query!r} returned no path under the unnamed "
            f"scope's root, so the leakage clause below would prove nothing: "
            f"{[r['path'] for r in unscoped]}"
        )

        response = client.get(
            "/search",
            params=[
                ("q", query),
                ("top_k", str(TOP_K)),
                ("scope", "scope_a"),
                ("scope", "scope_b"),
            ],
        )
        assert response.status_code == 200, f"query {query!r} was refused: {response.text}"

        paths = [r["path"] for r in response.json()["results"]]
        counts = _QueryCounts(
            query=query,
            scope_a=sum(1 for path in paths if _is_under(path, corpus.root_a)),
            scope_b=sum(1 for path in paths if _is_under(path, corpus.root_b)),
            outside=sum(
                1
                for path in paths
                if not _is_under(path, corpus.root_a) and not _is_under(path, corpus.root_b)
            ),
        )
        print(
            f"sweep {counts.query}: scope_a={counts.scope_a} "
            f"scope_b={counts.scope_b} outside={counts.outside}"
        )
        report.append(counts)
    return report


def _format_report(report: list[_QueryCounts]) -> str:
    """Render the whole report as one line per query, for assertion messages."""
    return "; ".join(
        f"{c.query}: scope_a={c.scope_a} scope_b={c.scope_b} outside={c.outside}" for c in report
    )


class TestUnionScopeSweep:
    """Across fourteen queries a repeated-scope request draws from every scope
    it names and from nothing outside them."""

    def test_every_query_covers_both_named_scopes_and_leaks_nothing(
        self, sweep_app: tuple[_Corpus, TestClient]
    ) -> None:
        corpus, client = sweep_app

        report = _sweep(corpus, client)

        assert len(report) == BASELINE_QUERY_COUNT, (
            f"the sweep ran {len(report)} queries, expected {BASELINE_QUERY_COUNT} — "
            f"a shrunken sweep cannot prove the same coverage"
        )

        # The positive clause is load-bearing: an all-empty sweep also reports
        # zero out-of-scope paths, so it must fail here instead of passing.
        missed_a = [c for c in report if c.scope_a == 0]
        missed_b = [c for c in report if c.scope_b == 0]
        assert not missed_a and not missed_b, (
            f"the union request returned no path under a named scope for "
            f"{len(missed_a)} queries (scope_a) and {len(missed_b)} queries (scope_b); "
            f"a sweep that returns nothing proves nothing: {_format_report(report)}"
        )

        total_outside = sum(c.outside for c in report)
        assert total_outside == 0, (
            f"the union request leaked {total_outside} out-of-scope paths across "
            f"{len(report)} queries: {_format_report(report)}"
        )

    def test_empty_union_is_an_empty_result_not_an_error(
        self, sweep_app: tuple[_Corpus, TestClient]
    ) -> None:
        """A valid repeated scope whose union matches nothing is an empty
        result, not an error — the spec's failure-modes table.

        An out-of-vocabulary query does not produce this case (the deterministic
        encoder returns a zero vector and search() then returns arbitrary
        zero-score documents), so the two empty roots are what make the result
        set empty. No `sweep ` line is printed here: the shell check counts
        exactly BASELINE_QUERY_COUNT of them.
        """
        _corpus, client = sweep_app

        response = client.get(
            "/search",
            params=[
                ("q", "alpha"),
                ("top_k", str(TOP_K)),
                ("scope", "scope_empty_a"),
                ("scope", "scope_empty_b"),
            ],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 0
        assert body["results"] == []
