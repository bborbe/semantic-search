"""Probes for the frozen-fixture replay tool (`scripts/replay-scope-fixture.py`).

The stub-server probes drive the tool over a real socket against a canned
`http.server` handler, exercising the tool's exit-code contract (0 = match,
1 = difference, 2 = could not compare) without touching a real index. The
end-to-end probe starts the real scoped server over temp content roots and a
temp scope map, captures a fixture from it, and proves the tool and the server
agree on the response envelope — not merely on a stub's.
"""

import asyncio
import hashlib
import http.server
import json
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import httpx
import pytest
import uvicorn

import semantic_search.factory as factory
import semantic_search.http_server as http_server
from semantic_search.http_server import build_app
from semantic_search.scopes import ScopeMap

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_SCRIPT = REPO_ROOT / "scripts" / "replay-scope-fixture.py"

PORT_LABELS = [("8321", "personal"), ("8322", "brogrammers")]
QUERIES = ["star citizen ship loadout", "obsidian vault plugin"]
TOP_K = 3


def _run_replay(fixture_path: Path, base_url: str) -> subprocess.CompletedProcess[str]:
    """Run the replay tool as a subprocess and capture its output."""
    return subprocess.run(
        [
            sys.executable,
            str(REPLAY_SCRIPT),
            "--fixture",
            str(fixture_path),
            "--base-url",
            base_url,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _path_table() -> dict[tuple[str, str], list[str]]:
    """A deterministic path table keyed by (query, port)."""
    table: dict[tuple[str, str], list[str]] = {}
    for query in QUERIES:
        for port, label in PORT_LABELS:
            slug = query.replace(" ", "-")
            table[(query, port)] = [f"/vault/{label}/{slug}-{i}.md" for i in range(TOP_K)]
    return table


def _build_fixture(
    *,
    ports: list[tuple[str, str]] | None = None,
    queries: list[str] | None = None,
    top_k: int = TOP_K,
    paths: dict[tuple[str, str], list[str]] | None = None,
) -> dict[str, Any]:
    """Build a fixture dict in the frozen baseline schema."""
    ports = ports or PORT_LABELS
    queries = queries or QUERIES
    paths = paths if paths is not None else _path_table()

    search: dict[str, Any] = {}
    for query in queries:
        search[query] = {}
        for port, _label in ports:
            query_paths = paths.get((query, port), [])
            search[query][port] = {
                "query": query,
                "results": [{"path": p, "score": 1.0} for p in query_paths],
                "count": len(query_paths),
            }
    return {
        "captured_at": "2026-09-11T10:38:23.957397+00:00",
        "purpose": "temp test fixture for replay-scope-fixture",
        "top_k": top_k,
        "ports": {
            port: {
                "label": label,
                "health": {"status": "ok", "ready": True, "paths": [], "indexed_files": 0},
            }
            for port, label in ports
        },
        "queries": queries,
        "search": search,
    }


def _write_fixture(path: Path, fixture: dict[str, Any]) -> Path:
    """Write a fixture dict as JSON and return the path."""
    path.write_text(json.dumps(fixture, indent=2))
    return path


def _perturbed_copy(path: Path, query: str, port: str) -> Path:
    """Return a new fixture path with one path deleted from one query/port block."""
    data = json.loads(path.read_text())
    block = data["search"][query][port]
    block["results"].pop(0)
    block["count"] = len(block["results"])
    perturbed = path.with_name(path.stem + "-perturbed.json")
    perturbed.write_text(json.dumps(data, indent=2))
    return perturbed


class StubHandler(http.server.BaseHTTPRequestHandler):
    """Canned /search stub: records each request and answers from a response table.

    Class attributes carry the shared state — `ThreadingHTTPServer` builds a
    fresh handler per request, so per-instance state would be lost. The test
    fixture resets these before each probe.
    """

    responses: ClassVar[dict[tuple[str, str], list[str]]] = {}
    received: ClassVar[list[tuple[str, str, str]]] = []
    mode: ClassVar[str] = "paths"

    def do_GET(self) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        scope = params.get("scope", [""])[0]
        top_k = params.get("top_k", [""])[0]
        query = params.get("q", [""])[0]
        type(self).received.append((scope, top_k, query))

        if type(self).mode == "500":
            body = json.dumps({"error": "boom"}).encode()
            self.send_response(500)
        elif type(self).mode == "not-envelope":
            body = json.dumps({"detail": "nope"}).encode()
            self.send_response(200)
        elif type(self).mode == "missing-path":
            body = json.dumps({"query": query, "results": [{"score": 1.0}], "count": 1}).encode()
            self.send_response(200)
        else:
            paths = type(self).responses.get((scope, query), [])
            body = json.dumps(
                {
                    "query": query,
                    "results": [{"path": p, "score": 1.0} for p in paths],
                    "count": len(paths),
                }
            ).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message: str, *args: Any) -> None:
        """Silence the default per-request logging."""


@pytest.fixture
def stub_server() -> Iterator[tuple[http.server.ThreadingHTTPServer, str]]:
    """A ThreadingHTTPServer on an ephemeral port with fresh canned state."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    server.daemon_threads = True
    StubHandler.responses = {}
    StubHandler.received = []
    StubHandler.mode = "paths"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield server, f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


def _load_stub_from_fixture(
    server: http.server.ThreadingHTTPServer, fixture: dict[str, Any]
) -> None:
    """Point the stub's response table at the fixture's own path blocks."""
    responses: dict[tuple[str, str], list[str]] = {}
    for query, query_block in fixture["search"].items():
        for port, port_block in query_block.items():
            label = fixture["ports"][port]["label"]
            responses[(label, query)] = [r["path"] for r in port_block["results"]]
    server.RequestHandlerClass.responses = responses
    server.RequestHandlerClass.received = []


@pytest.fixture(autouse=True)
def _bypass_container_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the container's outbound HTTP proxy for loopback connections.

    The container exports HTTP_PROXY/HTTPS_PROXY with no NO_PROXY, so httpx and
    the replay tool's urllib (which inherits the parent env) would route the
    probes' loopback requests through the proxy, which answers 403 Filtered.
    """
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def _reset_process_state() -> None:
    """Reset the process-wide singletons the app's lifespan and handlers use.

    `factory.reset()` alone is insufficient because `_build_indexer_in_background`
    early-returns while the module-level `_indexer_ready` event is set, so a
    leftover from an earlier test module would decide what a later probe
    indexes and serves.
    """
    factory.reset()
    http_server._indexer = None
    http_server._indexer_error = None
    http_server._indexer_ready = asyncio.Event()


@pytest.fixture(autouse=True)
def _reset_between_probes() -> None:
    """Reset process-wide index state before and after each probe."""
    _reset_process_state()
    yield
    _reset_process_state()


class TestStubServerReplay:
    """The replay tool's exit-code contract against a canned stub server."""

    def _arm_match(self, tmp_path: Path, server: http.server.ThreadingHTTPServer) -> Path:
        """Write a matching fixture and arm the stub to serve its path blocks."""
        fixture = _build_fixture()
        path = _write_fixture(tmp_path / "fixture.json", fixture)
        _load_stub_from_fixture(server, fixture)
        return path

    def test_matching_fixture_exits_zero_with_one_line_per_scope(
        self, tmp_path: Path, stub_server: tuple[http.server.ThreadingHTTPServer, str]
    ) -> None:
        server, base_url = stub_server
        fixture_path = self._arm_match(tmp_path, server)

        result = _run_replay(fixture_path, base_url)

        assert result.returncode == 0, result.stderr
        stdout_lines = result.stdout.splitlines()
        assert len(stdout_lines) == 2
        for label in ("personal", "brogrammers"):
            assert any(label in line for line in stdout_lines)
        assert "Traceback" not in result.stderr

    def test_trailing_slash_base_url_is_normalised(
        self, tmp_path: Path, stub_server: tuple[http.server.ThreadingHTTPServer, str]
    ) -> None:
        server, base_url = stub_server
        fixture_path = self._arm_match(tmp_path, server)

        result = _run_replay(fixture_path, base_url + "/")

        assert result.returncode == 0, result.stderr

    def test_fixture_is_never_rewritten(
        self, tmp_path: Path, stub_server: tuple[http.server.ThreadingHTTPServer, str]
    ) -> None:
        server, base_url = stub_server
        fixture_path = self._arm_match(tmp_path, server)
        before = hashlib.sha256(fixture_path.read_bytes()).hexdigest()

        result = _run_replay(fixture_path, base_url)

        assert result.returncode == 0, result.stderr
        after = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        assert before == after

    def test_perturbed_fixture_exits_one_naming_scope_and_query(
        self, tmp_path: Path, stub_server: tuple[http.server.ThreadingHTTPServer, str]
    ) -> None:
        server, base_url = stub_server
        fixture_path = self._arm_match(tmp_path, server)
        perturbed = _perturbed_copy(fixture_path, QUERIES[0], PORT_LABELS[0][0])

        result = _run_replay(perturbed, base_url)

        assert result.returncode == 1
        assert PORT_LABELS[0][1] in result.stderr
        assert QUERIES[0] in result.stderr

    def test_stub_received_scope_top_k_and_decoded_query(
        self, tmp_path: Path, stub_server: tuple[http.server.ThreadingHTTPServer, str]
    ) -> None:
        server, base_url = stub_server
        fixture_path = self._arm_match(tmp_path, server)

        result = _run_replay(fixture_path, base_url)

        assert result.returncode == 0, result.stderr
        expected = sorted(
            (label, str(TOP_K), query) for _port, label in PORT_LABELS for query in QUERIES
        )
        assert sorted(server.RequestHandlerClass.received) == expected

    def test_missing_fixture_exits_two_naming_fixture_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.json"

        result = _run_replay(missing, "http://127.0.0.1:1")

        assert result.returncode == 2
        assert "does-not-exist" in result.stderr
        assert "Traceback" not in result.stderr

    def test_unreachable_server_exits_two_naming_url(self, tmp_path: Path) -> None:
        fixture_path = _write_fixture(tmp_path / "fixture.json", _build_fixture())

        result = _run_replay(fixture_path, "http://127.0.0.1:1")

        assert result.returncode == 2
        assert "http://127.0.0.1:1" in result.stderr
        assert "Traceback" not in result.stderr

    def test_stub_answering_500_exits_two_not_one(
        self, tmp_path: Path, stub_server: tuple[http.server.ThreadingHTTPServer, str]
    ) -> None:
        server, base_url = stub_server
        fixture_path = self._arm_match(tmp_path, server)
        server.RequestHandlerClass.mode = "500"

        result = _run_replay(fixture_path, base_url)

        assert result.returncode == 2
        assert "500" in result.stderr
        assert "Traceback" not in result.stderr

    def test_stub_answering_non_envelope_exits_two_without_traceback(
        self, tmp_path: Path, stub_server: tuple[http.server.ThreadingHTTPServer, str]
    ) -> None:
        server, base_url = stub_server
        fixture_path = self._arm_match(tmp_path, server)
        server.RequestHandlerClass.mode = "not-envelope"

        result = _run_replay(fixture_path, base_url)

        assert result.returncode == 2
        assert result.stderr != ""
        assert "Traceback" not in result.stderr

    def test_stub_answering_result_without_path_exits_two(
        self, tmp_path: Path, stub_server: tuple[http.server.ThreadingHTTPServer, str]
    ) -> None:
        server, base_url = stub_server
        fixture_path = self._arm_match(tmp_path, server)
        server.RequestHandlerClass.mode = "missing-path"

        result = _run_replay(fixture_path, base_url)

        assert result.returncode == 2
        assert "Traceback" not in result.stderr


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


@pytest.fixture
def scoped_server(tmp_path: Path, deterministic_sentence_transformer: type):
    """A running uvicorn server over two temp scopes, in a background thread.

    Yields (base_url, root_a, root_b) with scope names `scope_a` and `scope_b`.
    Root A holds an alpha and a beta document; root B holds an alpha and a beta
    document — so both probe queries return results in both scopes and the
    captured fixture is never empty. Process-wide index state is reset before
    the server starts and after it stops, so a leftover indexer from an earlier
    module never decides what this probe replays against.
    """
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "a-alpha.md").write_text("alpha gamma\n" * 10)
    (root_a / "a-beta.md").write_text("beta gamma\n" * 10)
    (root_b / "b-alpha.md").write_text("alpha delta\n" * 10)
    (root_b / "b-beta.md").write_text("beta delta\n" * 10)

    scope_map = ScopeMap(scopes={"scope_a": (root_a,), "scope_b": (root_b,)})

    with patch("semantic_search.indexer.SentenceTransformer", deterministic_sentence_transformer):
        _reset_process_state()
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
            _reset_process_state()


def _capture_paths(base_url: str, scope: str, query: str, top_k: int) -> list[str]:
    """Query the live scoped server and return its ordered result paths."""
    with httpx.Client(timeout=5.0, trust_env=False) as client:
        resp = client.get(base_url + "/search", params={"q": query, "top_k": top_k, "scope": scope})
        resp.raise_for_status()
        data = resp.json()
        return [str(r["path"]) for r in data["results"]]


def _capture_fixture(
    base_url: str, scope_names: list[str], queries: list[str], top_k: int
) -> dict[str, Any]:
    """Assemble a fixture in the frozen schema by querying the live server."""
    ports = {
        str(8321 + index): {
            "label": scope,
            "health": {"status": "ok", "ready": True, "paths": [], "indexed_files": 0},
        }
        for index, scope in enumerate(scope_names)
    }
    search: dict[str, Any] = {}
    for query in queries:
        search[query] = {}
        for port, port_data in ports.items():
            scope = port_data["label"]
            paths = _capture_paths(base_url, scope, query, top_k)
            search[query][port] = {
                "query": query,
                "results": [{"path": p, "score": 1.0} for p in paths],
                "count": len(paths),
            }
    return {
        "captured_at": "2026-09-11T10:38:23.957397+00:00",
        "purpose": "e2e capture from the live scoped server",
        "top_k": top_k,
        "ports": ports,
        "queries": queries,
        "search": search,
    }


class TestReplayAgainstRealScopedServer:
    """The tool agrees with the real scoped server on the response envelope."""

    def test_capture_and_replay_matches_then_perturbation_differs(
        self, tmp_path: Path, scoped_server: tuple[str, Path, Path]
    ) -> None:
        base_url, _root_a, _root_b = scoped_server
        scope_names = ["scope_a", "scope_b"]
        queries = ["alpha", "beta"]
        top_k = 5

        fixture = _capture_fixture(base_url, scope_names, queries, top_k)
        for query in queries:
            for port in fixture["ports"]:
                assert fixture["search"][query][port]["count"] >= 1, (
                    f"capture for query {query!r}, scope "
                    f"{fixture['ports'][port]['label']!r} is empty — "
                    "an empty capture makes the match and perturbation vacuous"
                )

        fixture_path = _write_fixture(tmp_path / "captured.json", fixture)

        matched = _run_replay(fixture_path, base_url)
        assert matched.returncode == 0, matched.stderr
        assert len(matched.stdout.splitlines()) == len(scope_names)

        perturbed = _perturbed_copy(fixture_path, queries[0], next(iter(fixture["ports"])))
        differed = _run_replay(perturbed, base_url)
        assert differed.returncode == 1, differed.stderr
        assert scope_names[0] in differed.stderr
        assert queries[0] in differed.stderr
