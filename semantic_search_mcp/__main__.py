"""Entry point for the semantic search MCP server."""

import sys


def main():
    """Main entry point with subcommands."""
    if len(sys.argv) < 2:
        _print_usage()
        sys.exit(1)

    cmd = sys.argv[1]
    sys.argv = [sys.argv[0]] + sys.argv[2:]  # Remove subcommand from args

    if cmd == "serve":
        from .server import run
        run()
    elif cmd == "search":
        from .cli import search
        search()
    elif cmd == "duplicates":
        from .cli import duplicates
        duplicates()
    else:
        _print_usage()
        sys.exit(1)


def _print_usage():
    print("Usage: semantic-search-mcp <command> [options]")
    print()
    print("Commands:")
    print("  serve        Start MCP server")
    print("  search       Search for related notes")
    print("  duplicates   Find duplicate notes")
    print()
    print("Examples:")
    print("  semantic-search-mcp serve")
    print("  semantic-search-mcp search trading strategy")
    print("  semantic-search-mcp duplicates path/to/note.md")


if __name__ == "__main__":
    main()
