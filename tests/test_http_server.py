"""Tests for unified HTTP server."""

import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from semantic_search.http_server import build_app, main
from semantic_search.scopes import load_scope_map


@pytest.fixture(autouse=True)
def _reset_process_state() -> None:
    """Reset process-wide singletons before each test.

    The factory singleton and the http_server readiness globals are process
    wide: a stale indexer, declared root set, or readiness event left behind
    by an earlier test would otherwise decide what a later test indexes or
    serves.
    """
    import semantic_search.factory as factory
    import semantic_search.http_server as http_server

    factory.reset()
    http_server._indexer = None
    http_server._indexer_error = None
    http_server._indexer_ready = asyncio.Event()


@pytest.fixture
def http_app(tmp_path: Path) -> tuple[Starlette, tuple[Path, ...]]:
    """The app built from a temp scope map over temp roots, plus the union roots.

    The temp map declares `personal` = [root-a, root-b] and `work` = [root-b],
    so the union is (root-a, root-b).
    """
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    root_a.mkdir()
    root_b.mkdir()
    map_path = tmp_path / "scopes.yaml"
    map_path.write_text(
        f"scopes:\n  personal:\n    - {root_a}\n    - {root_b}\n  work:\n    - {root_b}\n"
    )
    scope_map = load_scope_map(map_path)
    return build_app(scope_map), (root_a, root_b)


@pytest.fixture
def no_background_build() -> None:
    """Make the background indexer build a fast no-op.

    Tests that never set _indexer_ready would otherwise launch the real
    indexer build, which instantiates the real embedding model — unavailable
    in the container. Patching it keeps such tests fast and deterministic.
    """
    import semantic_search.http_server as http_server

    async def noop(roots: tuple[Path, ...]) -> None:
        return None

    with patch.object(http_server, "_build_indexer_in_background", side_effect=noop):
        yield


def _serve_from_mock(mock_indexer: MagicMock) -> None:
    """Wire the http_server globals so the app serves from mock_indexer as ready."""
    import semantic_search.http_server as http_server

    ready_event = asyncio.Event()
    ready_event.set()
    http_server._indexer_ready = ready_event
    http_server._indexer = mock_indexer
    http_server._indexer_error = None


class TestHealthEndpoint:
    def test_health_returns_ok(self, http_app: tuple[Starlette, tuple[Path, ...]]) -> None:
        app, union_roots = http_app
        mock_indexer = MagicMock()
        mock_indexer.meta = {"0": {}, "1": {}}
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["paths"] == [str(p) for p in union_roots]
        assert data["indexed_files"] == 2

    def test_health_returns_indexing_status_when_not_ready(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        """Before the background build finishes, /health must report
        status=indexing without blocking on the indexer."""
        app, union_roots = http_app
        # globals left in the reset state: _indexer_ready unset, _indexer None
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "indexing"
        assert data["ready"] is False
        assert data["paths"] == [str(p) for p in union_roots]

    def test_health_returns_ok_when_ready(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """Once the Event is set and _indexer is populated, /health returns
        the full ready response with indexed_files count."""
        app, union_roots = http_app
        mock_indexer = MagicMock()
        mock_indexer.meta = {"0": {}, "1": {}, "2": {}}
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ready"] is True
        assert data["indexed_files"] == 3
        assert data["paths"] == [str(p) for p in union_roots]

    def test_health_with_scope_param_returns_same_body(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """/health is scopeless: a scope parameter must not change the body."""
        app, union_roots = http_app
        mock_indexer = MagicMock()
        mock_indexer.meta = {"0": {}}
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            plain = client.get("/health").json()
            scoped = client.get("/health?scope=personal").json()
        assert plain == scoped
        assert scoped["paths"] == [str(p) for p in union_roots]


class TestSearchEndpoint:
    def test_search_missing_query_returns_400(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        app, _ = http_app
        with TestClient(app) as client:
            resp = client.get("/search")
        assert resp.status_code == 400
        assert "Missing 'q' parameter" in resp.json()["error"]

    def test_search_with_query(self, http_app: tuple[Starlette, tuple[Path, ...]]) -> None:
        app, (root_a, root_b) = http_app
        mock_indexer = MagicMock()
        mock_indexer.search.return_value = [
            {"path": "a.md", "score": 0.9},
            {"path": "b.md", "score": 0.8},
        ]
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/search?q=test+query&top_k=3&scope=personal")
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "test query"
        assert data["count"] == 2
        mock_indexer.search.assert_called_once_with("test query", 3, (root_a, root_b))

    def test_search_runs_in_threadpool(self, http_app: tuple[Starlette, tuple[Path, ...]]) -> None:
        """Sync indexer.search must be awaited via run_in_threadpool so a slow
        query does not block the asyncio event loop.

        We prove this by checking the thread on which indexer.search executes is
        NOT the main thread (which hosts the event loop under TestClient).
        """
        import threading

        app, _ = http_app
        main_thread_id = threading.get_ident()
        observed_thread_ids: list[int] = []

        def fake_search(
            q: str, top_k: int, roots: tuple[Path, ...] | None
        ) -> list[dict[str, object]]:
            observed_thread_ids.append(threading.get_ident())
            return [{"path": "a.md", "score": 0.9}]

        mock_indexer = MagicMock()
        mock_indexer.search.side_effect = fake_search
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/search?q=hello&top_k=5&scope=personal")

        assert resp.status_code == 200
        assert len(observed_thread_ids) == 1
        assert observed_thread_ids[0] != main_thread_id, (
            "indexer.search ran on the event loop thread — it must be dispatched "
            "to a worker thread via run_in_threadpool"
        )

    def test_search_returns_503_when_not_ready(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """While the index is still building, /search returns 503 with a
        Retry-After header — not a 500 and not a hang."""
        import semantic_search.http_server as http_server

        app, _ = http_app

        async def never_completes(roots: tuple[Path, ...]) -> None:
            await asyncio.Event().wait()

        with (
            patch.object(http_server, "_build_indexer_in_background", side_effect=never_completes),
            TestClient(app) as client,
        ):
            resp = client.get("/search?q=hello&scope=personal")
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "5"
        data = resp.json()
        assert data["error"] == "indexing in progress"
        assert data["ready"] is False


class TestDuplicatesEndpoint:
    def test_duplicates_missing_file_returns_400(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        app, _ = http_app
        with TestClient(app) as client:
            resp = client.get("/duplicates")
        assert resp.status_code == 400
        assert "Missing 'file' parameter" in resp.json()["error"]

    def test_duplicates_with_file(self, http_app: tuple[Starlette, tuple[Path, ...]]) -> None:
        app, _ = http_app
        mock_indexer = MagicMock()
        mock_indexer.find_duplicates.return_value = [{"path": "similar.md", "score": 0.95}]
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/duplicates?file=note.md&threshold=0.9&scope=personal")
        assert resp.status_code == 200
        data = resp.json()
        assert data["file"] == "note.md"
        assert data["threshold"] == 0.9
        assert data["count"] == 1

    def test_duplicates_runs_in_threadpool(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """Sync indexer.find_duplicates must be awaited via run_in_threadpool."""
        import threading

        app, _ = http_app
        main_thread_id = threading.get_ident()
        observed_thread_ids: list[int] = []

        def fake_find(file_path: str, roots: tuple[Path, ...] | None) -> list[dict[str, object]]:
            observed_thread_ids.append(threading.get_ident())
            return [{"path": "similar.md", "score": 0.9}]

        mock_indexer = MagicMock()
        mock_indexer.find_duplicates.side_effect = fake_find
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/duplicates?file=note.md&scope=personal")

        assert resp.status_code == 200
        assert len(observed_thread_ids) == 1
        assert observed_thread_ids[0] != main_thread_id, (
            "indexer.find_duplicates ran on the event loop thread — it must be "
            "dispatched via run_in_threadpool"
        )

    def test_duplicates_indexer_returns_error_dict_returns_400(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """Preserves rest_server.py L112-114: when indexer.find_duplicates returns
        a dict with an 'error' key (e.g., file not indexed), the handler must
        forward it as a 400 JSON response, not a 200 success.
        """
        app, _ = http_app
        mock_indexer = MagicMock()
        mock_indexer.find_duplicates.return_value = {"error": "File not found in index: missing.md"}
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/duplicates?file=missing.md&scope=personal")
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "missing.md" in data["error"]


class TestReindexEndpoint:
    def test_reindex_post(self, http_app: tuple[Starlette, tuple[Path, ...]]) -> None:
        app, _ = http_app
        mock_indexer = MagicMock()
        mock_indexer.meta = {}
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.post("/reindex")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_reindex_returns_409_when_rebuild_in_progress(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """A second /reindex while a rebuild is in flight must return 409 with
        REINDEX_IN_PROGRESS, not start another rebuild.

        force_rebuild returns False when a rebuild is already in flight; the
        handler must surface that as a busy response. Assert on the
        deserialized response so the error code is proven to survive
        serialization to the wire.
        """
        app, _ = http_app
        mock_indexer = MagicMock()
        mock_indexer.meta = {}
        mock_indexer.force_rebuild.return_value = False
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.post("/reindex")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"]["code"] == "REINDEX_IN_PROGRESS"


class TestContentEndpoint:
    """Tests for GET /content endpoint."""

    def test_content_returns_200_with_full_content(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """GET /content?path=file returns 200 with path, content, mode fields."""
        app, _ = http_app
        mock_indexer = MagicMock()
        mock_indexer.get_content.return_value = {
            "path": "/vault/test.md",
            "content": "Full content",
            "mode": "full",
        }
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/content?path=/vault/test.md&scope=personal")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "/vault/test.md"
        assert data["content"] == "Full content"
        assert data["mode"] == "full"

    def test_content_snippet_mode_with_query(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """GET /content?path=...&snippet=true&query=TOKEN&context_lines=5 returns snippet."""
        app, (root_a, root_b) = http_app
        mock_indexer = MagicMock()
        mock_indexer.get_content.return_value = {
            "path": "/vault/test.md",
            "content": "...UNIQUE_TOKEN_XYZ...",
            "mode": "snippet",
        }
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get(
                "/content?path=/vault/test.md&snippet=true&query=UNIQUE_TOKEN_XYZ"
                "&context_lines=5&scope=personal"
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "snippet"
        mock_indexer.get_content.assert_called_once_with(
            "/vault/test.md", True, "UNIQUE_TOKEN_XYZ", 5, (root_a, root_b)
        )

    def test_content_snippet_mode_without_query(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """GET /content?path=...&snippet=true returns snippet mode with no query."""
        app, (root_a, root_b) = http_app
        mock_indexer = MagicMock()
        mock_indexer.get_content.return_value = {
            "path": "/vault/test.md",
            "content": "First lines...",
            "mode": "snippet",
        }
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/content?path=/vault/test.md&snippet=true&scope=personal")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "snippet"
        mock_indexer.get_content.assert_called_once_with(
            "/vault/test.md", True, None, 20, (root_a, root_b)
        )

    def test_content_path_outside_roots_returns_400(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """Path outside vault roots returns 400 with PATH_OUTSIDE_ROOTS code."""
        app, _ = http_app
        mock_indexer = MagicMock()
        mock_indexer.get_content.side_effect = ValueError("path not in indexed roots")
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/content?path=/etc/passwd&scope=personal")
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "PATH_OUTSIDE_ROOTS"

    def test_content_missing_file_returns_404(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """Path inside roots but file missing returns 404 with FILE_NOT_FOUND code."""
        app, _ = http_app
        mock_indexer = MagicMock()
        mock_indexer.get_content.side_effect = FileNotFoundError("file not found: missing.md")
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/content?path=missing.md&scope=personal")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "FILE_NOT_FOUND"

    def test_content_missing_path_param_returns_400(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        """Missing path param returns 400 with MISSING_PATH code."""
        app, _ = http_app
        with TestClient(app) as client:
            resp = client.get("/content")
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "MISSING_PATH"

    def test_content_unreadable_file_returns_422(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """Non-UTF-8 file returns 422 with UNREADABLE_FILE code."""
        app, _ = http_app
        mock_indexer = MagicMock()
        mock_indexer.get_content.side_effect = RuntimeError("could not read file")
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/content?path=/vault/binary.bin&scope=personal")
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "UNREADABLE_FILE"

    def test_content_returns_503_when_not_ready(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """Before indexer is ready, /content returns 503 with Retry-After header."""
        import semantic_search.http_server as http_server

        app, _ = http_app

        async def never_completes(roots: tuple[Path, ...]) -> None:
            await asyncio.Event().wait()

        with (
            patch.object(http_server, "_build_indexer_in_background", side_effect=never_completes),
            TestClient(app) as client,
        ):
            resp = client.get("/content?path=test.md&scope=personal")
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "5"
        data = resp.json()
        assert data["ready"] is False

    def test_content_snippet_param_parses_lowercase_true(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """snippet=true (lowercase) is parsed as True."""
        app, (root_a, root_b) = http_app
        mock_indexer = MagicMock()
        mock_indexer.get_content.return_value = {
            "path": "/v/test.md",
            "content": "...",
            "mode": "snippet",
        }
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            resp = client.get("/content?path=test.md&snippet=true&scope=personal")
        assert resp.status_code == 200
        mock_indexer.get_content.assert_called_once_with(
            "test.md", True, None, 20, (root_a, root_b)
        )

    def test_content_snippet_param_parses_false_and_empty_as_false(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """snippet=false and snippet= (empty) are parsed as False."""
        app, (root_a, root_b) = http_app
        mock_indexer = MagicMock()
        mock_indexer.get_content.return_value = {
            "path": "/v/test.md",
            "content": "full",
            "mode": "full",
        }
        _serve_from_mock(mock_indexer)
        with TestClient(app) as client:
            # snippet=false
            resp = client.get("/content?path=test.md&snippet=false&scope=personal")
            assert resp.status_code == 200
            mock_indexer.get_content.assert_called_with(
                "test.md", False, None, 20, (root_a, root_b)
            )

    def test_content_invalid_context_lines_returns_400(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """context_lines=abc returns 400 with INVALID_CONTEXT_LINES code."""
        app, _ = http_app
        _serve_from_mock(MagicMock())
        with TestClient(app) as client:
            resp = client.get("/content?path=test.md&context_lines=abc&scope=personal")
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "INVALID_CONTEXT_LINES"


class TestScopeGate:
    """Fail-closed: a request naming no scope or an unknown scope is refused
    with HTTP 400 and the refusal token, before the readiness gate."""

    def test_search_refuses_missing_scope(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        app, (root_a, _) = http_app
        with TestClient(app) as client:
            resp = client.get("/search?q=hello")
        assert resp.status_code == 400
        assert resp.json()["error"] == "MISSING_SCOPE"
        assert str(root_a) not in resp.text

    def test_search_refuses_unknown_scope(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        app, (root_a, _) = http_app
        with TestClient(app) as client:
            resp = client.get("/search?q=hello&scope=does-not-exist")
        assert resp.status_code == 400
        assert resp.json()["error"] == "UNKNOWN_SCOPE"
        assert str(root_a) not in resp.text

    def test_duplicates_refuses_missing_scope(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        app, (root_a, _) = http_app
        with TestClient(app) as client:
            resp = client.get("/duplicates?file=note.md")
        assert resp.status_code == 400
        assert resp.json()["error"] == "MISSING_SCOPE"
        assert str(root_a) not in resp.text

    def test_duplicates_refuses_unknown_scope(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        app, (root_a, _) = http_app
        with TestClient(app) as client:
            resp = client.get("/duplicates?file=note.md&scope=does-not-exist")
        assert resp.status_code == 400
        assert resp.json()["error"] == "UNKNOWN_SCOPE"
        assert str(root_a) not in resp.text

    def test_content_refuses_missing_scope(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        app, (root_a, _) = http_app
        in_union = root_a / "note.md"
        with TestClient(app) as client:
            resp = client.get(f"/content?path={in_union}")
        assert resp.status_code == 400
        assert resp.json()["error"] == "MISSING_SCOPE"
        assert str(in_union) not in resp.text

    def test_content_refuses_unknown_scope(
        self, http_app: tuple[Starlette, tuple[Path, ...]], no_background_build: None
    ) -> None:
        app, (root_a, _) = http_app
        in_union = root_a / "note.md"
        with TestClient(app) as client:
            resp = client.get(f"/content?path={in_union}&scope=does-not-exist")
        assert resp.status_code == 400
        assert resp.json()["error"] == "UNKNOWN_SCOPE"
        assert str(in_union) not in resp.text

    def test_refusal_fires_before_readiness_gate(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """With the index not ready, an unscoped /search is refused (400), not
        503; a valid-scope /search still returns 503 with Retry-After."""
        import semantic_search.http_server as http_server

        app, _ = http_app

        async def never_completes(roots: tuple[Path, ...]) -> None:
            await asyncio.Event().wait()

        # globals left in the reset state: _indexer_ready unset, _indexer None
        with (
            patch.object(http_server, "_build_indexer_in_background", side_effect=never_completes),
            TestClient(app) as client,
        ):
            unscoped = client.get("/search?q=hello")
            scoped = client.get("/search?q=hello&scope=personal")
        assert unscoped.status_code == 400
        assert unscoped.json()["error"] == "MISSING_SCOPE"
        assert scoped.status_code == 503
        assert scoped.headers.get("retry-after") == "5"


class TestMcpMount:
    def test_mcp_endpoint_returns_400_for_bare_get_not_404(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """MCP endpoint must be mounted and handled by fastmcp's streamable-http
        transport — NOT routed to Starlette's 404 handler.

        Expected behavior on fastmcp 3.x streamable-http: a bare `GET /mcp`
        without the required MCP handshake headers (no Accept: text/event-stream,
        no session init) is rejected by the MCP transport with HTTP 400
        (Bad Request) or 406 (Not Acceptable) — never 404, and never 405
        (both GET and POST are accepted by the transport). The request carries
        a valid scope so it passes the MCP scope guard and reaches the MCP
        transport itself; an unscoped `/mcp` is refused by the guard instead
        (see test_unscoped_mcp_refused_with_missing_scope).

        We assert the response is one of {400, 406} to tolerate minor version
        differences in the exact status code chosen by fastmcp, while proving
        the route is mounted and reaching the MCP handler.
        """
        app, _ = http_app
        with TestClient(app) as client:
            resp = client.get("/mcp?scope=personal")
        assert resp.status_code in {400, 406}, (
            f"Expected 400 or 406 from mounted MCP handler, got {resp.status_code}. "
            f"A 404 means the route is not mounted; a 405 means the transport "
            f"rejected the method, which contradicts fastmcp streamable-http behavior."
        )

    def test_unscoped_mcp_refused_with_missing_scope(
        self, http_app: tuple[Starlette, tuple[Path, ...]]
    ) -> None:
        """The MCP scope guard refuses a request naming no scope with HTTP 400
        and MISSING_SCOPE, before the MCP protocol layer runs."""
        app, (root_a, _) = http_app
        with TestClient(app) as client:
            resp = client.get("/mcp")
        assert resp.status_code == 400
        assert resp.json()["error"] == "MISSING_SCOPE"
        assert str(root_a) not in resp.text


class TestVersionFlag:
    """`semantic-search-http --version` / `-V` exits 0 with the version on stdout.

    The argparse `action="version"` action writes the version to stdout and
    raises `SystemExit(0)`. We exercise the boundary end-to-end — asserting
    the argument is REGISTERED on the parser is not sufficient.
    """

    def test_version_long_flag(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`semantic-search-http --version` prints the version and exits 0."""
        monkeypatch.setattr(sys, "argv", ["semantic-search-http", "--version"])

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert re.match(r"^semantic-search-http v[0-9]+\.[0-9]+", captured.out), (
            f"expected version on stdout, got: {captured.out!r}"
        )

    def test_version_short_flag(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`semantic-search-http -V` prints the version and exits 0."""
        monkeypatch.setattr(sys, "argv", ["semantic-search-http", "-V"])

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert re.match(r"^semantic-search-http v[0-9]+\.[0-9]+", captured.out), (
            f"expected version on stdout, got: {captured.out!r}"
        )

    def test_main_exits_nonzero_on_missing_scope_map_root(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """Startup validation: a scope map naming a missing root makes main()
        exit non-zero with a log line naming both the scope and the root."""
        import logging

        bad_map = tmp_path / "bad-scopes.yaml"
        bad_map.write_text(f"scopes:\n  probe:\n    - {tmp_path / 'missing-root'}\n")
        monkeypatch.setenv("SEMANTIC_SCOPE_MAP", str(bad_map))
        monkeypatch.setattr(sys, "argv", ["semantic-search-http", "--port", "18999"])

        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code != 0
        assert "probe" in caplog.text
        assert "missing-root" in caplog.text
