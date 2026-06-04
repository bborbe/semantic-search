"""Entry point for the semantic search server."""

import logging
import os
import sys

from ._version import __version__
from .logging_setup import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Main entry point with subcommands."""
    # Configure logging from environment
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    configure_logging(log_level)

    try:
        if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "-V"):
            print(f"semantic-search-mcp v{__version__}")
            sys.exit(0)

        if len(sys.argv) < 2:
            print(
                "Error: no subcommand given. Did you mean 'serve'?",
                file=sys.stderr,
            )
            print(
                "  When wired into Claude Code via 'claude mcp add', the command "
                "must end with 'serve'.",
                file=sys.stderr,
            )
            print(file=sys.stderr)
            _print_usage()
            sys.exit(1)

        cmd = sys.argv[1]
        sys.argv = [sys.argv[0], *sys.argv[2:]]  # Remove subcommand from args

        if cmd == "serve":
            _serve()
        elif cmd == "search":
            from .cli import search

            search()
        elif cmd == "duplicates":
            from .cli import duplicates

            duplicates()
        else:
            print(f"Error: unknown subcommand '{cmd}'.", file=sys.stderr)
            print(file=sys.stderr)
            _print_usage()
            sys.exit(1)
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except OSError as e:
        logger.error(f"I/O error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception:
        logger.exception("Unexpected error occurred")
        sys.exit(1)


def _serve() -> None:
    """Start the stdio MCP server."""
    from .server import run as run_mcp

    run_mcp()


def _print_usage() -> None:
    print("Usage: semantic-search-mcp <command>", file=sys.stderr)
    print(file=sys.stderr)
    print("Commands:", file=sys.stderr)
    print("  serve        Start stdio MCP server (for Claude Code)", file=sys.stderr)
    print("  search       Search for related notes (one-shot)", file=sys.stderr)
    print("  duplicates   Find duplicate notes (one-shot)", file=sys.stderr)
    print(file=sys.stderr)
    print("Examples:", file=sys.stderr)
    print("  semantic-search-mcp serve", file=sys.stderr)
    print("  semantic-search-mcp search kubernetes deployment", file=sys.stderr)
    print("  semantic-search-mcp duplicates path/to/note.md", file=sys.stderr)
    print(file=sys.stderr)
    print(
        "Note: this binary runs stdio MCP only. For HTTP (REST + MCP-over-HTTP),", file=sys.stderr
    )
    print("use `semantic-search-http`. For one-shot CLI, use `semantic-search`.", file=sys.stderr)


def main_cli() -> None:
    """CLI entry point for one-shot search and duplicate commands only."""
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    configure_logging(log_level)

    try:
        if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "-V"):
            print(f"semantic-search v{__version__}")
            sys.exit(0)

        if len(sys.argv) < 2:
            print("Error: no subcommand given.", file=sys.stderr)
            print(file=sys.stderr)
            _print_cli_usage()
            sys.exit(1)

        cmd = sys.argv[1]
        sys.argv = [sys.argv[0], *sys.argv[2:]]  # Remove subcommand from args

        if cmd == "search":
            from .cli import search

            search()
        elif cmd == "duplicates":
            from .cli import duplicates

            duplicates()
        else:
            print(f"Error: unknown subcommand '{cmd}'.", file=sys.stderr)
            print(file=sys.stderr)
            _print_cli_usage()
            sys.exit(1)
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except OSError as e:
        logger.error(f"I/O error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception:
        logger.exception("Unexpected error occurred")
        sys.exit(1)


def _print_cli_usage() -> None:
    print("Usage: semantic-search <command> [options]", file=sys.stderr)
    print(file=sys.stderr)
    print("Commands:", file=sys.stderr)
    print("  search       Search for related notes (one-shot)", file=sys.stderr)
    print("  duplicates   Find duplicate notes (one-shot)", file=sys.stderr)
    print(file=sys.stderr)
    print("Examples:", file=sys.stderr)
    print("  semantic-search search kubernetes deployment", file=sys.stderr)
    print("  semantic-search duplicates path/to/note.md", file=sys.stderr)


if __name__ == "__main__":
    main()
