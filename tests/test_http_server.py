"""Tests for unified HTTP server."""

from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from semantic_search.http_server import build_app


class TestHealthEndpoint:
    def test_health_returns_ok(self) -> None:
        with patch("semantic_search.http_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.meta = {"0": {}, "1": {}}
            mock_get.return_value = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "paths" in data
        assert data["indexed_files"] == 2


class TestSearchEndpoint:
    def test_search_missing_query_returns_400(self) -> None:
        with TestClient(build_app()) as client:
            resp = client.get("/search")
        assert resp.status_code == 400
        assert "Missing 'q' parameter" in resp.json()["error"]

    def test_search_with_query(self) -> None:
        with patch("semantic_search.http_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.search.return_value = [
                {"path": "a.md", "score": 0.9},
                {"path": "b.md", "score": 0.8},
            ]
            mock_get.return_value = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/search?q=test+query&top_k=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "test query"
        assert data["count"] == 2
        mock_indexer.search.assert_called_once_with("test query", 3)

    def test_search_runs_in_threadpool(self) -> None:
        """Sync indexer.search must be awaited via run_in_threadpool so a slow
        query does not block the asyncio event loop.

        We prove this by checking the thread on which indexer.search executes is
        NOT the main thread (which hosts the event loop under TestClient).
        """
        import threading

        main_thread_id = threading.get_ident()
        observed_thread_ids: list[int] = []

        def fake_search(q: str, top_k: int) -> list[dict[str, object]]:
            observed_thread_ids.append(threading.get_ident())
            return [{"path": "a.md", "score": 0.9}]

        with patch("semantic_search.http_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.search.side_effect = fake_search
            mock_get.return_value = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/search?q=hello&top_k=5")

        assert resp.status_code == 200
        assert len(observed_thread_ids) == 1
        assert observed_thread_ids[0] != main_thread_id, (
            "indexer.search ran on the event loop thread — it must be dispatched "
            "to a worker thread via run_in_threadpool"
        )


class TestDuplicatesEndpoint:
    def test_duplicates_missing_file_returns_400(self) -> None:
        with TestClient(build_app()) as client:
            resp = client.get("/duplicates")
        assert resp.status_code == 400
        assert "Missing 'file' parameter" in resp.json()["error"]

    def test_duplicates_with_file(self) -> None:
        with patch("semantic_search.http_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.find_duplicates.return_value = [{"path": "similar.md", "score": 0.95}]
            mock_get.return_value = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/duplicates?file=note.md&threshold=0.9")
        assert resp.status_code == 200
        data = resp.json()
        assert data["file"] == "note.md"
        assert data["threshold"] == 0.9
        assert data["count"] == 1

    def test_duplicates_runs_in_threadpool(self) -> None:
        """Sync indexer.find_duplicates must be awaited via run_in_threadpool."""
        import threading

        main_thread_id = threading.get_ident()
        observed_thread_ids: list[int] = []

        def fake_find(file_path: str) -> list[dict[str, object]]:
            observed_thread_ids.append(threading.get_ident())
            return [{"path": "similar.md", "score": 0.9}]

        with patch("semantic_search.http_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.find_duplicates.side_effect = fake_find
            mock_get.return_value = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/duplicates?file=note.md")

        assert resp.status_code == 200
        assert len(observed_thread_ids) == 1
        assert observed_thread_ids[0] != main_thread_id, (
            "indexer.find_duplicates ran on the event loop thread — it must be "
            "dispatched via run_in_threadpool"
        )

    def test_duplicates_indexer_returns_error_dict_returns_400(self) -> None:
        """Preserves rest_server.py L112-114: when indexer.find_duplicates returns
        a dict with an 'error' key (e.g., file not indexed), the handler must
        forward it as a 400 JSON response, not a 200 success.
        """
        with patch("semantic_search.http_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.find_duplicates.return_value = {
                "error": "File not found in index: missing.md"
            }
            mock_get.return_value = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/duplicates?file=missing.md")
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "missing.md" in data["error"]


class TestReindexEndpoint:
    def test_reindex_post(self) -> None:
        with patch("semantic_search.http_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.meta = {}
            mock_get.return_value = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.post("/reindex")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestMcpMount:
    def test_mcp_endpoint_returns_400_for_bare_get_not_404(self) -> None:
        """MCP endpoint must be mounted and handled by fastmcp's streamable-http
        transport — NOT routed to Starlette's 404 handler.

        Expected behavior on fastmcp 3.x streamable-http: a bare `GET /mcp`
        without the required MCP handshake headers (no Accept: text/event-stream,
        no session init) is rejected by the MCP transport with HTTP 400
        (Bad Request) or 406 (Not Acceptable) — never 404, and never 405
        (both GET and POST are accepted by the transport).

        We assert the response is one of {400, 406} to tolerate minor version
        differences in the exact status code chosen by fastmcp, while proving
        the route is mounted and reaching the MCP handler.
        """
        with TestClient(build_app()) as client:
            resp = client.get("/mcp")
        assert resp.status_code in {400, 406}, (
            f"Expected 400 or 406 from mounted MCP handler, got {resp.status_code}. "
            f"A 404 means the route is not mounted; a 405 means the transport "
            f"rejected the method, which contradicts fastmcp streamable-http behavior."
        )
