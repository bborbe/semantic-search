"""Entry point for the semantic search server."""

import argparse
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
    """Handle serve command with mode selection."""
    parser = argparse.ArgumentParser(description="Start semantic search server")
    parser.add_argument(
        "--mode",
        choices=["mcp", "rest"],
        default="mcp",
        help="Server mode: mcp (default) or rest"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8321,
        help="Port for REST server (default: 8321)"
    )
    args = parser.parse_args()

    if args.mode == "mcp":
        from .server import run as run_mcp

        run_mcp()
    else:
        from .rest_server import run as run_rest

        run_rest(port=args.port)


def _print_usage() -> None:
    print("Usage: semantic-search-mcp <command> [options]")
    print()
    print("Commands:")
    print("  serve        Start server (MCP or REST mode)")
    print("  search       Search for related notes (one-shot)")
    print("  duplicates   Find duplicate notes (one-shot)")
    print()
    print("Serve options:")
    print("  --mode mcp|rest   Server mode (default: mcp)")
    print("  --port PORT       REST server port (default: 8321)")
    print()
    print("Examples:")
    print("  semantic-search-mcp serve                    # MCP mode (for Claude Code)")
    print("  semantic-search-mcp serve --mode rest        # REST mode (for OpenClaw)")
    print("  semantic-search-mcp serve --mode rest --port 9000")
    print("  semantic-search-mcp search trading strategy")
    print("  semantic-search-mcp duplicates path/to/note.md")


if __name__ == "__main__":
    main()
