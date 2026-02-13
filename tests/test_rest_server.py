"""Tests for REST server."""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

from semantic_search_mcp.rest_server import SemanticSearchHandler, get_indexer


class MockRequest:
    """Mock HTTP request for testing handler."""

    def __init__(self, path: str, method: str = "GET"):
        self.path = path
        self.method = method

    def makefile(self, *args, **kwargs):
        return BytesIO(f"{self.method} {self.path} HTTP/1.1\r\n\r\n".encode())


class TestSemanticSearchHandler:
    """Tests for the HTTP request handler."""

    def create_handler(self, path: str, method: str = "GET") -> SemanticSearchHandler:
        """Create a handler instance for testing."""
        # Create mock request/response objects
        handler = object.__new__(SemanticSearchHandler)
        handler.path = path
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.headers = {}
        handler.wfile = BytesIO()
        handler.rfile = BytesIO()
        handler.client_address = ("127.0.0.1", 12345)
        handler.server = MagicMock()
        handler.responses = {
            200: ("OK", "Request fulfilled"),
            400: ("Bad Request", "Bad request syntax"),
            404: ("Not Found", "Nothing matches the given URI"),
            500: ("Internal Server Error", "Server error"),
        }
        return handler

    def parse_response(self, handler: SemanticSearchHandler) -> dict:
        """Parse JSON response from handler's wfile."""
        handler.wfile.seek(0)
        response = handler.wfile.read().decode()
        # Extract JSON body (after headers)
        body = response.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in response else response
        return json.loads(body)

    def test_health_endpoint(self):
        """Test /health endpoint returns status."""
        handler = self.create_handler("/health")

        # Mock the indexer
        with patch("semantic_search_mcp.rest_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.meta = {"0": {}, "1": {}}  # 2 indexed files
            mock_get.return_value = mock_indexer

            handler.do_GET()

        data = self.parse_response(handler)
        assert data["status"] == "ok"
        assert "paths" in data
        assert data["indexed_files"] == 2

    def test_search_missing_query(self):
        """Test /search without query parameter returns error."""
        handler = self.create_handler("/search")

        handler.do_GET()

        data = self.parse_response(handler)
        assert "error" in data
        assert "Missing 'q' parameter" in data["error"]

    def test_search_with_query(self):
        """Test /search with query parameter."""
        handler = self.create_handler("/search?q=test+query&top_k=3")

        with patch("semantic_search_mcp.rest_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.search.return_value = [
                {"path": "note1.md", "score": 0.9},
                {"path": "note2.md", "score": 0.8},
            ]
            mock_get.return_value = mock_indexer

            handler.do_GET()

        data = self.parse_response(handler)
        assert data["query"] == "test query"
        assert data["count"] == 2
        assert len(data["results"]) == 2
        mock_indexer.search.assert_called_once_with("test query", 3)

    def test_duplicates_missing_file(self):
        """Test /duplicates without file parameter returns error."""
        handler = self.create_handler("/duplicates")

        handler.do_GET()

        data = self.parse_response(handler)
        assert "error" in data
        assert "Missing 'file' parameter" in data["error"]

    def test_duplicates_with_file(self):
        """Test /duplicates with file parameter."""
        handler = self.create_handler("/duplicates?file=note.md&threshold=0.9")

        with patch("semantic_search_mcp.rest_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.find_duplicates.return_value = [
                {"path": "similar.md", "score": 0.95},
            ]
            mock_get.return_value = mock_indexer

            handler.do_GET()

        data = self.parse_response(handler)
        assert data["file"] == "note.md"
        assert data["threshold"] == 0.9
        assert data["count"] == 1

    def test_unknown_endpoint(self):
        """Test unknown endpoint returns 404."""
        handler = self.create_handler("/unknown")

        handler.do_GET()

        data = self.parse_response(handler)
        assert "error" in data
        assert "Unknown endpoint" in data["error"]

    def test_reindex_get(self):
        """Test GET /reindex forces reindex."""
        handler = self.create_handler("/reindex")

        with patch("semantic_search_mcp.rest_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.meta = {"0": {}}  # 1 indexed file
            mock_get.return_value = mock_indexer

            # Reset the global indexer
            import semantic_search_mcp.rest_server as rest_module
            rest_module._indexer = mock_indexer

            handler.do_GET()

        data = self.parse_response(handler)
        assert data["status"] == "ok"
        assert "Reindex complete" in data["message"]

    def test_reindex_post(self):
        """Test POST /reindex forces reindex."""
        handler = self.create_handler("/reindex", method="POST")

        with patch("semantic_search_mcp.rest_server.get_indexer") as mock_get:
            mock_indexer = MagicMock()
            mock_indexer.meta = {}  # Empty index
            mock_get.return_value = mock_indexer

            handler.do_POST()

        data = self.parse_response(handler)
        assert data["status"] == "ok"


class TestGetIndexer:
    """Tests for get_indexer singleton."""

    def test_creates_indexer_once(self):
        """Test indexer is created once and reused."""
        import semantic_search_mcp.rest_server as rest_module

        # Reset global state
        rest_module._indexer = None

        with patch("semantic_search_mcp.rest_server.create_indexer") as mock_create:
            mock_indexer = MagicMock()
            mock_create.return_value = mock_indexer

            # First call creates indexer
            result1 = get_indexer()
            assert mock_create.call_count == 1

            # Second call reuses existing
            result2 = get_indexer()
            assert mock_create.call_count == 1

            assert result1 is result2

        # Cleanup
        rest_module._indexer = None
