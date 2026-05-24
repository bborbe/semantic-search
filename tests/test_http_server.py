"""Tests for unified HTTP server."""

import asyncio
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from semantic_search.http_server import build_app


class TestHealthEndpoint:
    def test_health_returns_ok(self) -> None:
        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        original_error = http_server._indexer_error
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.meta = {"0": {}, "1": {}}
            http_server._indexer = mock_indexer
            http_server._indexer_error = None
            with TestClient(build_app()) as client:
                resp = client.get("/health")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
            http_server._indexer_error = original_error
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "paths" in data
        assert data["indexed_files"] == 2

    def test_health_returns_indexing_status_when_not_ready(self) -> None:
        """Before the background build finishes, /health must report
        status=indexing without blocking on the indexer."""
        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        original_error = http_server._indexer_error
        try:
            http_server._indexer_ready = asyncio.Event()  # unset
            http_server._indexer = None
            http_server._indexer_error = None
            with TestClient(build_app()) as client:
                resp = client.get("/health")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
            http_server._indexer_error = original_error
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "indexing"
        assert data["ready"] is False
        assert "paths" in data

    def test_health_returns_ok_when_ready(self) -> None:
        """Once the Event is set and _indexer is populated, /health returns
        the full ready response with indexed_files count."""
        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        original_error = http_server._indexer_error
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.meta = {"0": {}, "1": {}, "2": {}}
            http_server._indexer = mock_indexer
            http_server._indexer_error = None
            with TestClient(build_app()) as client:
                resp = client.get("/health")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
            http_server._indexer_error = original_error
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ready"] is True
        assert data["indexed_files"] == 3
        assert "paths" in data


class TestSearchEndpoint:
    def test_search_missing_query_returns_400(self) -> None:
        with TestClient(build_app()) as client:
            resp = client.get("/search")
        assert resp.status_code == 400
        assert "Missing 'q' parameter" in resp.json()["error"]

    def test_search_with_query(self) -> None:
        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.search.return_value = [
                {"path": "a.md", "score": 0.9},
                {"path": "b.md", "score": 0.8},
            ]
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/search?q=test+query&top_k=3")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
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

        import semantic_search.http_server as http_server

        main_thread_id = threading.get_ident()
        observed_thread_ids: list[int] = []

        def fake_search(q: str, top_k: int) -> list[dict[str, object]]:
            observed_thread_ids.append(threading.get_ident())
            return [{"path": "a.md", "score": 0.9}]

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.search.side_effect = fake_search
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/search?q=hello&top_k=5")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer

        assert resp.status_code == 200
        assert len(observed_thread_ids) == 1
        assert observed_thread_ids[0] != main_thread_id, (
            "indexer.search ran on the event loop thread — it must be dispatched "
            "to a worker thread via run_in_threadpool"
        )

    def test_search_returns_503_when_not_ready(self) -> None:
        """While the index is still building, /search returns 503 with a
        Retry-After header — not a 500 and not a hang."""
        from unittest.mock import patch

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer

        async def never_completes() -> None:
            await asyncio.Event().wait()

        try:
            http_server._indexer_ready = asyncio.Event()  # unset
            http_server._indexer = None
            with (
                patch.object(
                    http_server, "_build_indexer_in_background", side_effect=never_completes
                ),
                TestClient(build_app()) as client,
            ):
                resp = client.get("/search?q=hello")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "5"
        data = resp.json()
        assert data["error"] == "indexing in progress"
        assert data["ready"] is False


class TestDuplicatesEndpoint:
    def test_duplicates_missing_file_returns_400(self) -> None:
        with TestClient(build_app()) as client:
            resp = client.get("/duplicates")
        assert resp.status_code == 400
        assert "Missing 'file' parameter" in resp.json()["error"]

    def test_duplicates_with_file(self) -> None:
        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.find_duplicates.return_value = [{"path": "similar.md", "score": 0.95}]
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/duplicates?file=note.md&threshold=0.9")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 200
        data = resp.json()
        assert data["file"] == "note.md"
        assert data["threshold"] == 0.9
        assert data["count"] == 1

    def test_duplicates_runs_in_threadpool(self) -> None:
        """Sync indexer.find_duplicates must be awaited via run_in_threadpool."""
        import threading

        import semantic_search.http_server as http_server

        main_thread_id = threading.get_ident()
        observed_thread_ids: list[int] = []

        def fake_find(file_path: str) -> list[dict[str, object]]:
            observed_thread_ids.append(threading.get_ident())
            return [{"path": "similar.md", "score": 0.9}]

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.find_duplicates.side_effect = fake_find
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/duplicates?file=note.md")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer

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
        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.find_duplicates.return_value = {
                "error": "File not found in index: missing.md"
            }
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/duplicates?file=missing.md")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "missing.md" in data["error"]


class TestReindexEndpoint:
    def test_reindex_post(self) -> None:
        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.meta = {}
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.post("/reindex")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestContentEndpoint:
    """Tests for GET /content endpoint."""

    def test_content_returns_200_with_full_content(self) -> None:
        """GET /content?path=file returns 200 with path, content, mode fields."""
        import asyncio
        from unittest.mock import MagicMock

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.get_content.return_value = {
                "path": "/vault/test.md",
                "content": "Full content",
                "mode": "full",
            }
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/content?path=/vault/test.md")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "/vault/test.md"
        assert data["content"] == "Full content"
        assert data["mode"] == "full"

    def test_content_snippet_mode_with_query(self) -> None:
        """GET /content?path=...&snippet=true&query=TOKEN&context_lines=5 returns snippet."""
        import asyncio
        from unittest.mock import MagicMock

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.get_content.return_value = {
                "path": "/vault/test.md",
                "content": "...UNIQUE_TOKEN_XYZ...",
                "mode": "snippet",
            }
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get(
                    "/content?path=/vault/test.md&snippet=true&query=UNIQUE_TOKEN_XYZ&context_lines=5"
                )
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "snippet"
        mock_indexer.get_content.assert_called_once_with(
            "/vault/test.md", True, "UNIQUE_TOKEN_XYZ", 5
        )

    def test_content_snippet_mode_without_query(self) -> None:
        """GET /content?path=...&snippet=true returns snippet mode with no query."""
        import asyncio
        from unittest.mock import MagicMock

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.get_content.return_value = {
                "path": "/vault/test.md",
                "content": "First lines...",
                "mode": "snippet",
            }
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/content?path=/vault/test.md&snippet=true")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "snippet"
        mock_indexer.get_content.assert_called_once_with("/vault/test.md", True, None, 20)

    def test_content_path_outside_roots_returns_400(self) -> None:
        """Path outside vault roots returns 400 with PATH_OUTSIDE_ROOTS code."""
        import asyncio
        from unittest.mock import MagicMock

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.get_content.side_effect = ValueError("path not in indexed roots")
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/content?path=/etc/passwd")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "PATH_OUTSIDE_ROOTS"

    def test_content_missing_file_returns_404(self) -> None:
        """Path inside roots but file missing returns 404 with FILE_NOT_FOUND code."""
        import asyncio
        from unittest.mock import MagicMock

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.get_content.side_effect = FileNotFoundError("file not found: missing.md")
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/content?path=missing.md")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"]["code"] == "FILE_NOT_FOUND"

    def test_content_missing_path_param_returns_400(self) -> None:
        """Missing path param returns 400 with MISSING_PATH code."""
        import asyncio

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/content")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "MISSING_PATH"

    def test_content_unreadable_file_returns_422(self) -> None:
        """Non-UTF-8 file returns 422 with UNREADABLE_FILE code."""
        import asyncio
        from unittest.mock import MagicMock

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.get_content.side_effect = RuntimeError("could not read file")
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/content?path=/vault/binary.bin")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "UNREADABLE_FILE"

    def test_content_returns_503_when_not_ready(self) -> None:
        """Before indexer is ready, /content returns 503 with Retry-After header."""
        import asyncio
        from unittest.mock import patch

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer

        async def never_completes() -> None:
            await asyncio.Event().wait()

        try:
            http_server._indexer_ready = asyncio.Event()  # unset
            http_server._indexer = None
            with (
                patch.object(
                    http_server, "_build_indexer_in_background", side_effect=never_completes
                ),
                TestClient(build_app()) as client,
            ):
                resp = client.get("/content?path=test.md")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") == "5"
        data = resp.json()
        assert data["ready"] is False

    def test_content_snippet_param_parses_lowercase_true(self) -> None:
        """snippet=true (lowercase) is parsed as True."""
        import asyncio
        from unittest.mock import MagicMock

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.get_content.return_value = {
                "path": "/v/test.md",
                "content": "...",
                "mode": "snippet",
            }
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/content?path=test.md&snippet=true")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 200
        mock_indexer.get_content.assert_called_once_with("test.md", True, None, 20)

    def test_content_snippet_param_parses_false_and_empty_as_false(self) -> None:
        """snippet=false and snippet= (empty) are parsed as False."""
        import asyncio
        from unittest.mock import MagicMock

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            mock_indexer.get_content.return_value = {
                "path": "/v/test.md",
                "content": "full",
                "mode": "full",
            }
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                # snippet=false
                resp = client.get("/content?path=test.md&snippet=false")
                assert resp.status_code == 200
                mock_indexer.get_content.assert_called_with("test.md", False, None, 20)
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer

    def test_content_invalid_context_lines_returns_400(self) -> None:
        """context_lines=abc returns 400 with INVALID_CONTEXT_LINES code."""
        import asyncio

        import semantic_search.http_server as http_server

        original_event = http_server._indexer_ready
        original_indexer = http_server._indexer
        try:
            ready_event = asyncio.Event()
            ready_event.set()
            http_server._indexer_ready = ready_event
            mock_indexer = MagicMock()
            http_server._indexer = mock_indexer
            with TestClient(build_app()) as client:
                resp = client.get("/content?path=test.md&context_lines=abc")
        finally:
            http_server._indexer_ready = original_event
            http_server._indexer = original_indexer
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["code"] == "INVALID_CONTEXT_LINES"


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
