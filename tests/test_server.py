"""Tests for MCP server tools."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# A deterministic stand-in for the real embedding model, injected into the
# subprocess probe via PYTHONPATH (the container cannot load all-MiniLM-L6-v2).
# Vectors are seeded from sha256 so repeated runs build the same index.
_STUB_SENTENCE_TRANSFORMERS = '''\
"""Deterministic stand-in for the real embedding model."""

import hashlib

import numpy as np

DIMENSION = 384


class SentenceTransformer:
    """Minimal contract: __init__(model_name), get_sentence_embedding_dimension(), encode()."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def get_sentence_embedding_dimension(self) -> int:
        return DIMENSION

    def encode(
        self,
        sentences: list[str],
        normalize_embeddings: bool = False,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        vectors = []
        for sentence in sentences:
            digest = hashlib.sha256(sentence.encode()).digest()
            base = np.frombuffer(digest, dtype=np.uint8).astype(np.float32)
            vectors.append(np.tile(base, DIMENSION // len(base) + 1)[:DIMENSION])
        result = np.stack(vectors).astype(np.float32)
        if normalize_embeddings:
            result = result / np.linalg.norm(result, axis=1, keepdims=True)
        return result
'''


def _wait_for_json_id(lines: list[str], target_id: int, deadline: float) -> dict[str, Any] | None:
    """Return the first parsed JSON line carrying target_id, or None on deadline.

    Lines that are not JSON objects are skipped (and can be surfaced in a
    failure message); the match is on the request's `id`, never on framing.
    """
    while time.monotonic() < deadline:
        for line in list(lines):
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(message, dict) and message.get("id") == target_id:
                return message
        time.sleep(0.05)
    return None


def _terminate_and_read_stderr(proc: subprocess.Popen[str]) -> str:
    """Terminate the child and return the tail of its stderr.

    Must only be called after the child has been terminated — reading the
    stderr pipe while it is still alive blocks until EOF.
    """
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    if proc.stderr is None:
        return ""
    return proc.stderr.read()[-2000:]


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

        factory.reset()

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

        factory.reset()

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

        factory.reset()

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

        factory.reset()

        try:
            with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
                mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
                mock_st.return_value.encode.return_value = [[0.1] * 384]

                from semantic_search.server import get_content

                with pytest.raises(FileNotFoundError):
                    get_content(path=str(temp_vault / "does-not-exist.md"))
        finally:
            server_module.CONTENT_PATHS = original_paths


class TestStdioHasNoScope:
    """The stdio MCP transport gains no scope handling (requirement 8)."""

    def test_search_related_answers_from_content_path_without_scope(self, temp_vault: Path) -> None:
        """search_related answers from CONTENT_PATH with no scope anywhere.

        The patched encode must return a 2-D numpy.ndarray (not a list), since
        _embed_text calls .astype("float32") on it — a list would raise, the
        index would come out empty, and search would return [].
        """
        import numpy as np

        import semantic_search.factory as factory
        import semantic_search.server as server_module

        original_paths = server_module.CONTENT_PATHS
        server_module.CONTENT_PATHS = [str(temp_vault)]
        factory.reset()

        try:
            with patch("semantic_search.indexer.SentenceTransformer") as mock_st:
                mock_st.return_value.get_sentence_embedding_dimension.return_value = 384
                mock_st.return_value.encode.return_value = np.array([[0.1] * 384])

                from semantic_search.server import search_related

                results = search_related("test")
        finally:
            server_module.CONTENT_PATHS = original_paths

        assert len(results) >= 1
        vault_resolved = temp_vault.resolve()
        for result in results:
            assert Path(result["path"]).is_relative_to(vault_resolved)

    def test_stdio_subprocess_answers_search_without_scope(self, tmp_path: Path) -> None:
        """A real stdio server process answers a tools/call for search_related
        from CONTENT_PATH, with no scope anywhere and no scope refusal.

        The container cannot load the real embedding model, so the probe
        shadows the installed sentence_transformers package with a deterministic
        stub on PYTHONPATH and drives the JSON-RPC conversation interactively,
        one message at a time.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text(
            "# Test Note\n\nContent for the deterministic stdio probe.\n"
        )

        stub_root = tmp_path / "stub"
        pkg_dir = stub_root / "sentence_transformers"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text(_STUB_SENTENCE_TRANSFORMERS)

        env = dict(os.environ)
        env["CONTENT_PATH"] = str(vault)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(stub_root) + os.pathsep + existing_pythonpath
            if existing_pythonpath
            else str(stub_root)
        )

        proc = subprocess.Popen(
            [sys.executable, "-m", "semantic_search", "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None

        stdout_lines: list[str] = []

        def _read_stdout() -> None:
            for line in proc.stdout:
                stdout_lines.append(line)

        reader = threading.Thread(target=_read_stdout, daemon=True)
        reader.start()

        deadline = time.monotonic() + 180.0
        init_id = 1
        call_id = 2
        stderr_tail = ""

        try:
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": init_id,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "semantic-search-test", "version": "1.0"},
                        },
                    }
                )
                + "\n"
            )
            proc.stdin.flush()

            init_response = _wait_for_json_id(stdout_lines, init_id, deadline)
            if init_response is None:
                stderr_tail = _terminate_and_read_stderr(proc)
                raise AssertionError(
                    "initialize response never arrived (deadline expired); stdout so far:\n"
                    + "".join(stdout_lines)
                    + "\nstderr tail:\n"
                    + stderr_tail
                )

            proc.stdin.write(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            )
            proc.stdin.flush()

            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": call_id,
                        "method": "tools/call",
                        "params": {
                            "name": "search_related",
                            "arguments": {"query": "test note", "top_k": 5},
                        },
                    }
                )
                + "\n"
            )
            proc.stdin.flush()

            call_response = _wait_for_json_id(stdout_lines, call_id, deadline)
            if call_response is None:
                stderr_tail = _terminate_and_read_stderr(proc)
                raise AssertionError(
                    "tools/call response never arrived (deadline expired); stdout so far:\n"
                    + "".join(stdout_lines)
                    + "\nstderr tail:\n"
                    + stderr_tail
                )

            result_text = json.dumps(call_response["result"])
            assert str(vault.resolve()) in result_text, (
                f"search_related result did not include a path under the temp root; "
                f"result={result_text}"
            )
            stdout_text = "".join(stdout_lines)
            assert "MISSING_SCOPE" not in stdout_text, "stdio probe saw a MISSING_SCOPE refusal"
            assert "UNKNOWN_SCOPE" not in stdout_text, "stdio probe saw an UNKNOWN_SCOPE refusal"
        finally:
            proc.stdin.close()
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
