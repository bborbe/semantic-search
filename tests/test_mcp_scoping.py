"""Scoping probes for the MCP-over-HTTP mount.

Each probe needs a real socket and the real MCP layer, so it starts a uvicorn
server in a background thread over a real temp content set and drives it with
a real FastMCP client — and, where the client helper cannot express the probe
(a single session header reused across two query strings), with a hand-rolled
JSON-RPC handshake over httpx. The index is built by the app's background
task, so every probe waits for `/health` to report ready before issuing MCP
calls, and resets the process-wide state before and after so a stale indexer
from an earlier test never decides what is served. Every wait carries an
explicit timeout so a regression fails red rather than stalling the suite.
"""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

import semantic_search.factory as factory
import semantic_search.http_server as http_server
from semantic_search.http_server import build_app
from semantic_search.scopes import ScopeMap

# The headers a real MCP streamable-HTTP client sends on every request.
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "semantic-search-probe", "version": "1.0"},
    },
}


def _reset_http_state() -> None:
    """Reset the process-wide singletons the app's lifespan and handlers use.

    The factory singleton and the http_server readiness globals are process
    wide: a stale indexer, readiness event, or error left behind by an earlier
    module in the same pytest process would otherwise decide what a later test
    indexes or serves. `factory.reset()` alone is insufficient because the
    app's `_build_indexer_in_background` early-returns while the module-level
    `_indexer_ready` event is set.
    """
    factory.reset()
    http_server._indexer = None
    http_server._indexer_error = None
    http_server._indexer_ready = asyncio.Event()


def _wait_for_server_started(server: uvicorn.Server) -> None:
    """Block until uvicorn has bound its port, or fail after the deadline."""
    deadline = time.monotonic() + 15.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise AssertionError("uvicorn server never started within the deadline")


def _wait_for_health_ready(base_url: str, attempts: int = 300) -> None:
    """Poll /health until the background index build reports ready, or fail."""
    with httpx.Client(timeout=2.0, trust_env=False) as client:
        for _ in range(attempts):
            resp = client.get(f"{base_url}/health")
            if resp.status_code == 200 and resp.json().get("ready") is True:
                return
            time.sleep(0.05)
    raise AssertionError("indexer never became ready within the polling budget")


def _sse_data(body: str) -> dict[str, Any]:
    """Extract the first JSON payload from a streamable-HTTP SSE response body."""
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError(f"no data line in SSE response: {body[:300]!r}")


def _result_paths(result: Any) -> list[str]:
    """Extract the returned path list from a tools/call result.

    The tools return a list of {"path", ...} dicts or a single {"path", ...}
    dict, both serialised by the MCP layer into a JSON text content block.
    """
    if not result.content:
        return []
    data = json.loads(result.content[0].text)
    if isinstance(data, dict):
        return [str(data["path"])]
    return [str(item["path"]) for item in data]


async def _initialize_session(client: httpx.AsyncClient, url: str) -> str:
    """Perform the initialize handshake and return the `mcp-session-id` header."""
    resp = await client.post(url, json=INITIALIZE_BODY, headers=MCP_HEADERS)
    resp.raise_for_status()
    session_id = resp.headers.get("mcp-session-id")
    assert session_id is not None, f"no mcp-session-id header in response: {resp.headers}"
    _sse_data(resp.text)  # validates the initialize result arrived
    return session_id


async def _send_initialized(client: httpx.AsyncClient, url: str, session_id: str) -> None:
    """Send the notifications/initialized notification (the server replies 202)."""
    resp = await client.post(
        url,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**MCP_HEADERS, "mcp-session-id": session_id},
    )
    assert resp.status_code == 202, f"expected 202, got {resp.status_code}: {resp.text[:200]}"


async def _call_tool(
    client: httpx.AsyncClient,
    url: str,
    session_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Send a tools/call and return the parsed JSON-RPC result payload."""
    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    resp = await client.post(url, json=body, headers={**MCP_HEADERS, "mcp-session-id": session_id})
    resp.raise_for_status()
    return _sse_data(resp.text)["result"]


@pytest.fixture(autouse=True)
def _bypass_container_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the container's outbound HTTP proxy for loopback connections.

    The container exports HTTP_PROXY/HTTPS_PROXY with no NO_PROXY, so httpx
    (and the FastMCP client's httpx transport) would route the test's loopback
    MCP requests through the proxy, which answers 403 Filtered. Pinning
    127.0.0.1 and localhost into NO_PROXY keeps the probes on the loopback.
    """
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture
def mcp_server(tmp_path: Path, deterministic_sentence_transformer: type):
    """A running uvicorn server over a temp scope map and temp content roots.

    Yields (base_url, root_a, root_b) with `scope_a` -> root_a and
    `scope_b` -> root_b. Root A holds two alpha documents and a gamma
    document; root B holds one alpha document — so an unscoped query for
    "alpha" matches in both roots and the probes can prove the scope filter is
    what excludes the other root. The shared deterministic encoder fixture is
    patched into the indexer for the server's whole lifetime, and the server
    is shut down via `should_exit` before the fixture returns.
    """
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "a-alpha.md").write_text("alpha\n" * 10)
    (root_a / "a-alpha-2.md").write_text("alpha\n" * 10)
    (root_a / "a-gamma.md").write_text("gamma\n" * 10)
    (root_a / "a-file.md").write_text("alpha content")
    (root_b / "b-alpha.md").write_text("alpha\n" * 10)
    (root_b / "b-file.md").write_text("beta content")

    scope_map = ScopeMap(scopes={"scope_a": (root_a,), "scope_b": (root_b,)})

    with patch("semantic_search.indexer.SentenceTransformer", deterministic_sentence_transformer):
        _reset_http_state()
        app = build_app(scope_map)
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            _wait_for_server_started(server)
            port = server.servers[0].sockets[0].getsockname()[1]
            base_url = f"http://127.0.0.1:{port}"
            _wait_for_health_ready(base_url)
            yield base_url, root_a, root_b
        finally:
            server.should_exit = True
            thread.join(timeout=10.0)
            _reset_http_state()


class TestMcpSearchToolScoped:
    """The MCP search tool answers only from its session's scope."""

    async def test_search_related_returns_only_in_scope_paths(self, mcp_server) -> None:
        base_url, root_a, root_b = mcp_server
        url = f"{base_url}/mcp?scope=scope_a"

        # The union index does hold an in-scope-for-B match, so a fall-back to
        # the union would change the result instead of returning the same
        # A-only list — the scope filter is load-bearing.
        unscoped = http_server.get_indexer().search("alpha", top_k=10)
        unscoped_paths = [r["path"] for r in unscoped]
        assert any(Path(p).resolve().is_relative_to(root_b.resolve()) for p in unscoped_paths)

        async with Client(StreamableHttpTransport(url), timeout=10.0) as client:
            result = await client.call_tool_mcp("search_related", {"query": "alpha", "top_k": 5})

        paths = _result_paths(result)
        assert len(paths) >= 1
        for path in paths:
            assert Path(path).resolve().is_relative_to(root_a.resolve()), (
                f"{path} not under scope_a"
            )
        assert not any(Path(p).resolve().is_relative_to(root_b.resolve()) for p in paths), (
            "a scope_b path leaked into the scope_a result"
        )


class TestMcpScopeRefusals:
    """The MCP guard refuses an unscoped or unknown-scope request with HTTP 400."""

    async def test_unscoped_call_refused_with_missing_scope(self, mcp_server) -> None:
        base_url, root_a, root_b = mcp_server
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.post(f"{base_url}/mcp", json=INITIALIZE_BODY, headers=MCP_HEADERS)
        assert resp.status_code == 400
        assert "MISSING_SCOPE" in resp.text
        assert str(root_a) not in resp.text
        assert str(root_b) not in resp.text

    async def test_unknown_scope_call_refused_with_unknown_scope(self, mcp_server) -> None:
        base_url, root_a, root_b = mcp_server
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.post(
                f"{base_url}/mcp?scope=does-not-exist",
                json=INITIALIZE_BODY,
                headers=MCP_HEADERS,
            )
        assert resp.status_code == 400
        assert "UNKNOWN_SCOPE" in resp.text
        assert str(root_a) not in resp.text
        assert str(root_b) not in resp.text


class TestMcpScopeFixedAtSessionCreation:
    """A tools/call carrying a different valid scope on an established session
    is still answered from the session's scope — session scope is fixed at
    session creation, which is deliberate on the frozen one-URL-per-client
    transport."""

    async def test_call_with_different_scope_answers_from_session_scope(self, mcp_server) -> None:
        base_url, root_a, root_b = mcp_server
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            session_id = await _initialize_session(client, f"{base_url}/mcp?scope=scope_a")
            await _send_initialized(client, f"{base_url}/mcp?scope=scope_a", session_id)
            # Same session header, but scope_b on the request: the session was
            # created with scope_a, so the call must be answered from scope_a.
            result = await _call_tool(
                client,
                f"{base_url}/mcp?scope=scope_b",
                session_id,
                "search_related",
                {"query": "alpha", "top_k": 5},
            )

        text = result["content"][0]["text"]
        paths = [item["path"] for item in json.loads(text)]
        assert len(paths) >= 1
        for path in paths:
            assert Path(path).resolve().is_relative_to(root_a.resolve()), (
                f"{path} not under scope_a"
            )
        assert not any(Path(p).resolve().is_relative_to(root_b.resolve()) for p in paths), (
            "a scope_b path answered a session created with scope_a"
        )


class TestMcpInterleavedRequests:
    """Two sessions in flight at once never observe each other's scope."""

    async def test_concurrent_sessions_answer_from_own_scope(self, mcp_server) -> None:
        base_url, root_a, root_b = mcp_server
        url_a = f"{base_url}/mcp?scope=scope_a"
        url_b = f"{base_url}/mcp?scope=scope_b"

        real_create_indexer = factory.create_indexer
        entered = threading.Event()
        hold = threading.Event()
        first = True

        def blocking_create_indexer(paths: list[str]):
            """Hold the first tool call between the guard and the scope read.

            The tool runs `create_indexer(CONTENT_PATHS)` before it evaluates
            `_request_roots()`, so a wrapper that blocks the first call sits
            exactly between the guard's scope resolution and the tool's scope
            read. With a scope stored on the shared indexer, the second
            request overwrites the store before the first request reads it and
            the first response would come back with the second scope's paths.
            """
            nonlocal first
            if first:
                first = False
                entered.set()
                if not hold.wait(timeout=5.0):
                    raise TimeoutError("interleave hold was not released")
            return real_create_indexer(paths)

        async with (
            Client(StreamableHttpTransport(url_a), timeout=10.0) as client_a,
            Client(StreamableHttpTransport(url_b), timeout=10.0) as client_b,
        ):
            with patch(
                "semantic_search.server.create_indexer", side_effect=blocking_create_indexer
            ):
                task_a = asyncio.create_task(
                    client_a.call_tool_mcp("search_related", {"query": "alpha", "top_k": 5})
                )
                assert await asyncio.to_thread(entered.wait, 10.0), (
                    "first request never reached create_indexer"
                )
                result_b = await client_b.call_tool_mcp(
                    "search_related", {"query": "alpha", "top_k": 5}
                )
                paths_b = _result_paths(result_b)
                hold.set()
                result_a = await task_a
                paths_a = _result_paths(result_a)

        assert len(paths_a) >= 1
        assert len(paths_b) >= 1
        for path in paths_a:
            assert Path(path).resolve().is_relative_to(root_a.resolve()), (
                f"{path} not under scope_a"
            )
        for path in paths_b:
            assert Path(path).resolve().is_relative_to(root_b.resolve()), (
                f"{path} not under scope_b"
            )
        assert set(paths_a).isdisjoint(paths_b), "the two sessions saw each other's paths"


class TestMcpAllToolsScoped:
    """check_duplicates and get_content are scoped over the mount too."""

    async def test_check_duplicates_returns_only_in_scope_paths(self, mcp_server) -> None:
        base_url, root_a, root_b = mcp_server
        url = f"{base_url}/mcp?scope=scope_a"

        # An unscoped duplicate check would also return the near-identical
        # scope_b document — the scope filter is what excludes it.
        unscoped = http_server.get_indexer().find_duplicates(str(root_a / "a-alpha.md"))
        unscoped_paths = [d["path"] for d in unscoped]
        assert any(Path(p).resolve().is_relative_to(root_b.resolve()) for p in unscoped_paths)

        async with Client(StreamableHttpTransport(url), timeout=10.0) as client:
            result = await client.call_tool_mcp(
                "check_duplicates", {"file_path": str(root_a / "a-alpha.md")}
            )

        paths = _result_paths(result)
        assert len(paths) >= 1
        for path in paths:
            assert Path(path).resolve().is_relative_to(root_a.resolve()), (
                f"{path} not under scope_a"
            )

    async def test_get_content_serves_in_scope_and_refuses_out_of_scope(self, mcp_server) -> None:
        base_url, root_a, root_b = mcp_server
        url = f"{base_url}/mcp?scope=scope_a"

        async with Client(StreamableHttpTransport(url), timeout=10.0) as client:
            served = await client.call_tool_mcp("get_content", {"path": str(root_a / "a-file.md")})
            refused = await client.call_tool_mcp("get_content", {"path": str(root_b / "b-file.md")})

        served_paths = _result_paths(served)
        assert len(served_paths) == 1
        assert Path(served_paths[0]).resolve().is_relative_to(root_a.resolve())
        assert refused.isError, "an out-of-scope get_content must be refused, not served"


class TestFailClosedWithoutScopeBinding:
    """On the HTTP transport with no request roots bound, the tool helper
    raises RuntimeError instead of answering from the union index."""

    def test_request_roots_raises_runtime_error_on_http_transport(self) -> None:
        from semantic_search.scopes import mark_http_transport, reset_http_transport
        from semantic_search.server import _request_roots

        mark_http_transport()
        try:
            with pytest.raises(RuntimeError):
                _request_roots()
        finally:
            reset_http_transport()
