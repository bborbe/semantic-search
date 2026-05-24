"""Tests for MCP server tools."""

from pathlib import Path
from unittest.mock import patch

import pytest


class TestMcpGetContentTool:
    """Tests for get_content MCP tool."""

    def test_get_content_full_mode(self, temp_vault: Path) -> None:
        """get_content MCP tool returns full content in full mode."""
        test_file = temp_vault / "test-note.md"
        test_file.write_text("Full content here.")

        # Patch CONTENT_PATHS so server uses our temp vault
        import semantic_search.server as server_module

        original_paths = server_module.CONTENT_PATHS
        server_module.CONTENT_PATHS = [str(temp_vault)]

        # Reset the factory singleton so each test gets a fresh indexer
        import semantic_search.factory as factory

        factory._indexer = None
        factory._watcher = None

        try:
            # Patch SentenceTransformer so indexing is fast
            with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
                mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
                mock_st.return_value.encode.return_value = [[0.1] * 384]

                from semantic_search.server import get_content

                result = get_content(path=str(test_file))

            assert result["mode"] == "full"
            assert result["path"] == str(test_file.resolve())
            assert result["content"] == "Full content here."
        finally:
            server_module.CONTENT_PATHS = original_paths

    def test_get_content_snippet_mode(self, temp_vault: Path) -> None:
        """get_content MCP tool returns snippet when snippet=True."""
        test_file = temp_vault / "snippet-note.md"
        test_file.write_text("Line zero.\nUNIQUE_TOKEN_XYZ in line one.\nLine two.\n")

        import semantic_search.server as server_module

        original_paths = server_module.CONTENT_PATHS
        server_module.CONTENT_PATHS = [str(temp_vault)]

        import semantic_search.factory as factory

        factory._indexer = None
        factory._watcher = None

        try:
            with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
                mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
                mock_st.return_value.encode.return_value = [[0.1] * 384]

                from semantic_search.server import get_content

                result = get_content(
                    path=str(test_file),
                    snippet=True,
                    query="UNIQUE_TOKEN_XYZ",
                    context_lines=2,
                )

            assert result["mode"] == "snippet"
            assert "UNIQUE_TOKEN_XYZ" in result["content"]
        finally:
            server_module.CONTENT_PATHS = original_paths

    def test_get_content_path_outside_roots_raises(self, temp_vault: Path) -> None:
        """get_content with path outside vault roots raises ValueError."""
        import semantic_search.server as server_module

        original_paths = server_module.CONTENT_PATHS
        server_module.CONTENT_PATHS = [str(temp_vault)]

        import semantic_search.factory as factory

        factory._indexer = None
        factory._watcher = None

        try:
            with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
                mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
                mock_st.return_value.encode.return_value = [[0.1] * 384]

                from semantic_search.server import get_content

                with pytest.raises(ValueError, match="not in indexed roots"):
                    get_content(path="/etc/passwd")
        finally:
            server_module.CONTENT_PATHS = original_paths

    def test_get_content_missing_file_raises(self, temp_vault: Path) -> None:
        """get_content with non-existent file raises FileNotFoundError."""
        import semantic_search.server as server_module

        original_paths = server_module.CONTENT_PATHS
        server_module.CONTENT_PATHS = [str(temp_vault)]

        import semantic_search.factory as factory

        factory._indexer = None
        factory._watcher = None

        try:
            with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
                mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
                mock_st.return_value.encode.return_value = [[0.1] * 384]

                from semantic_search.server import get_content

                with pytest.raises(FileNotFoundError):
                    get_content(path=str(temp_vault / "does-not-exist.md"))
        finally:
            server_module.CONTENT_PATHS = original_paths
