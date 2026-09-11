#!/usr/bin/env python3
"""Replay the frozen pre-consolidation acceptance baseline against a live server.

The fixture is the oracle captured from the five separate daemons before the
consolidation merge. This tool reads it as-is — it never rewrites it — and asks
the merged server, scope by scope, whether its `/search` result paths match the
baseline in order.

Exit codes (the operator's contract):
    0  every query of every scope matched.
    1  every attempted comparison completed, and at least one differed.
    2  comparison was impossible: a usage error, an unreadable or malformed
       fixture, or an HTTP/parse failure (unreachable server, timeout,
       non-2xx response, or a body that is not the {"query", "results",
       "count"} envelope). 2 wins over 1 whenever both occur in one run.
"""

import argparse
import http.client
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REQUEST_TIMEOUT_SECONDS = 30.0


class FixtureError(Exception):
    """The fixture could not be read or does not match the frozen schema."""


class RequestError(Exception):
    """A comparison request could not be completed against the server."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line into an argparse Namespace."""
    parser = argparse.ArgumentParser(
        prog="replay-scope-fixture",
        description=(
            "Replay the frozen pre-consolidation acceptance baseline against a live "
            "scoped server, one scope at a time, comparing each scope's /search result "
            "paths in order. Exit 0 = all matched, 1 = a difference, 2 = could not compare."
        ),
    )
    parser.add_argument("--fixture", required=True, help="Path to the frozen baseline JSON fixture")
    parser.add_argument(
        "--base-url",
        required=True,
        help="The merged server's root, e.g. http://127.0.0.1:8321",
    )
    return parser.parse_args(argv)


def load_fixture(path: Path) -> dict[str, Any]:
    """Load the frozen baseline fixture and validate its full shape.

    The whole fixture is validated before any request is issued, so a corrupt
    oracle is refused with a reason naming the fixture path rather than being
    discovered mid-replay after the server was already contacted.

    Raises:
        FixtureError: When the file cannot be read, is not valid UTF-8 JSON,
            or does not match the frozen schema — a missing top-level key, a
            port without a label, a query with no block, or a result entry
            without a path.
    """
    try:
        raw = path.read_text()
    except OSError as e:
        raise FixtureError(f"cannot read fixture {path}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise FixtureError(f"fixture {path} is not valid JSON: {e}") from e
    except UnicodeDecodeError as e:
        raise FixtureError(f"fixture {path} is not valid UTF-8: {e}") from e

    if not isinstance(data, dict):
        raise FixtureError(f"fixture {path} must be a JSON object")
    for key in ("top_k", "ports", "queries", "search"):
        if key not in data:
            raise FixtureError(f"fixture {path} is missing the {key!r} key")
    if not isinstance(data["top_k"], int):
        raise FixtureError(f"fixture {path}: 'top_k' must be an integer")
    if not isinstance(data["ports"], dict) or not data["ports"]:
        raise FixtureError(f"fixture {path}: 'ports' must be a non-empty mapping")
    if not isinstance(data["queries"], list) or not data["queries"]:
        raise FixtureError(f"fixture {path}: 'queries' must be a non-empty list")
    if not all(isinstance(query, str) for query in data["queries"]):
        raise FixtureError(f"fixture {path}: 'queries' must be a list of strings")
    if not isinstance(data["search"], dict):
        raise FixtureError(f"fixture {path}: 'search' must be a mapping")

    for port, port_data in data["ports"].items():
        if not isinstance(port_data, dict) or not isinstance(port_data.get("label"), str):
            raise FixtureError(f"fixture {path}: port {port!r} must carry a 'label' string")
    for query in data["queries"]:
        query_block = data["search"].get(query)
        if not isinstance(query_block, dict):
            raise FixtureError(f"fixture {path}: query {query!r} has no search block")
        for port in data["ports"]:
            port_block = query_block.get(port)
            if not isinstance(port_block, dict):
                raise FixtureError(f"fixture {path}: query {query!r}, port {port!r} has no block")
            results = port_block.get("results")
            if not isinstance(results, list):
                raise FixtureError(
                    f"fixture {path}: query {query!r}, port {port!r} has no 'results' list"
                )
            for index, entry in enumerate(results):
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise FixtureError(
                        f"fixture {path}: query {query!r}, port {port!r}: "
                        f"result {index} has no 'path' string"
                    )
    return data


def expected_paths(fixture: dict[str, Any], query: str, port: str) -> list[str]:
    """Return the fixture's ordered expected paths for one query/port block.

    Safe after `load_fixture` has validated the whole fixture shape.
    """
    return [str(entry["path"]) for entry in fixture["search"][query][port]["results"]]


def build_url(base_url: str, scope: str, query: str, top_k: int) -> str:
    """Build the scoped /search request URL for one query.

    A trailing slash on the base URL is stripped so it never produces a double
    slash or a mismatched comparison.
    """
    base = base_url.rstrip("/")
    params = urllib.parse.urlencode({"q": query, "top_k": top_k, "scope": scope})
    return f"{base}/search?{params}"


def fetch_paths(base_url: str, scope: str, query: str, top_k: int) -> list[str]:
    """Issue one scoped /search request and return the ordered result paths.

    Raises:
        RequestError: When the request cannot complete — an unreachable
            server, a timeout, a non-2xx response, or a response body that
            does not parse into the {"query", "results", "count"} envelope.
    """
    url = build_url(base_url, scope, query, top_k)
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except (OSError, http.client.HTTPException) as e:
        raise RequestError(f"request to {url} failed: {e}") from e
    except UnicodeDecodeError as e:
        raise RequestError(f"response from {url} is not valid UTF-8: {e}") from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise RequestError(f"response from {url} is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise RequestError(f"response from {url} is not a JSON object")
    results = data.get("results")
    if not isinstance(results, list):
        raise RequestError(f"response from {url} has no 'results' list")
    paths: list[str] = []
    for index, entry in enumerate(results):
        if not isinstance(entry, dict) or "path" not in entry:
            raise RequestError(f"response from {url}: result {index} has no 'path'")
        paths.append(str(entry["path"]))
    return paths


def difference_message(scope: str, query: str, actual: list[str], expected: list[str]) -> str:
    """Describe one result-list difference for stderr."""
    lines = [f"difference: scope {scope!r}, query {query!r}: result paths differ"]
    if len(actual) != len(expected):
        lines.append(f"  length differs: expected {len(expected)}, got {len(actual)}")
    for index, (actual_path, expected_path) in enumerate(zip(actual, expected, strict=False)):
        if actual_path != expected_path:
            lines.append(f"  first difference at position {index}:")
            lines.append(f"    expected: {expected_path}")
            lines.append(f"    actual:   {actual_path}")
            break
    return "\n".join(lines)


def replay(fixture: dict[str, Any], base_url: str) -> int:
    """Replay every scope's queries against the server; return the exit code.

    Prints one line per scope to stdout when that scope's comparisons all
    completed, and writes every difference and every comparison failure to
    stderr. A scope whose request fails stops issuing that scope's remaining
    queries (reported once, never hammered per query). A comparison that could
    not complete (exit 2) takes precedence over a difference (exit 1).
    """
    top_k = fixture["top_k"]
    queries = fixture["queries"]
    ports = fixture["ports"]

    diff_found = False
    compare_failed = False

    for port, port_data in ports.items():
        scope = port_data["label"]
        scope_failed = False
        compared = 0
        for query in queries:
            if scope_failed:
                break
            try:
                actual = fetch_paths(base_url, scope, query, top_k)
            except RequestError as e:
                print(f"error: scope {scope!r}, query {query!r}: {e}", file=sys.stderr)
                scope_failed = True
                compare_failed = True
                continue
            expected = expected_paths(fixture, query, port)
            compared += 1
            if actual != expected:
                print(difference_message(scope, query, actual, expected), file=sys.stderr)
                diff_found = True
        if not scope_failed:
            print(f"{scope}: {compared} queries compared")

    if compare_failed:
        return 2
    if diff_found:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Load the fixture, replay it against the server, and return the exit code."""
    args = parse_args(argv)
    try:
        fixture = load_fixture(Path(args.fixture))
    except FixtureError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return replay(fixture, args.base_url)


if __name__ == "__main__":
    sys.exit(main())
