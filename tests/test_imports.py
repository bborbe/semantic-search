"""Test that critical imports work without errors.

Catches dependency incompatibilities like fastmcp + pydantic version conflicts
(e.g., 'cannot specify both default and default_factory').
"""


def test_fastmcp_import() -> None:
    """FastMCP must import without pydantic compatibility errors."""
    from fastmcp import FastMCP  # noqa: F401


def test_server_module_import() -> None:
    """Server module must import successfully."""
    from semantic_search_mcp import server  # noqa: F401
