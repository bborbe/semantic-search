"""Entry point for the semantic search server."""

import logging
import os
import sys

from .logging_setup import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Main entry point with subcommands."""
    # Configure logging from environment
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    configure_logging(log_level)

    try:
        if len(sys.argv) < 2:
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
    print("Usage: semantic-search-mcp <command>")
    print()
    print("Commands:")
    print("  serve        Start stdio MCP server (for Claude Code)")
    print("  search       Search for related notes (one-shot)")
    print("  duplicates   Find duplicate notes (one-shot)")
    print()
    print("Examples:")
    print("  semantic-search-mcp serve")
    print("  semantic-search-mcp search kubernetes deployment")
    print("  semantic-search-mcp duplicates path/to/note.md")
    print()
    print("Note: this binary runs stdio MCP only. For HTTP (REST + MCP-over-HTTP),")
    print("use `semantic-search-http`. For one-shot CLI, use `semantic-search`.")


def main_cli() -> None:
    """CLI entry point for one-shot search and duplicate commands only."""
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    configure_logging(log_level)

    try:
        if len(sys.argv) < 2:
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
    print("Usage: semantic-search <command> [options]")
    print()
    print("Commands:")
    print("  search       Search for related notes (one-shot)")
    print("  duplicates   Find duplicate notes (one-shot)")
    print()
    print("Examples:")
    print("  semantic-search search kubernetes deployment")
    print("  semantic-search duplicates path/to/note.md")


if __name__ == "__main__":
    main()
