"""CLI commands for semantic search."""

import argparse
import os
import sys

from .indexer import VaultIndexer


def _get_content_path() -> str:
    """Get content path from environment or error."""
    content_path = os.environ.get("CONTENT_PATH")
    if not content_path:
        print("Error: CONTENT_PATH environment variable not set", file=sys.stderr)
        print("Usage: CONTENT_PATH=/path/to/content semantic-search <query>", file=sys.stderr)
        sys.exit(1)
    return content_path


def search():
    """Search for related notes."""
    parser = argparse.ArgumentParser(description="Search for semantically related notes")
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument("-n", "--top-k", type=int, default=5, help="Number of results (default: 5)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show more details")
    args = parser.parse_args()

    content_path = _get_content_path()
    query = " ".join(args.query)

    if args.verbose:
        print(f"Content path: {content_path}", file=sys.stderr)
        print(f"Query: {query}", file=sys.stderr)
        print(file=sys.stderr)

    indexer = VaultIndexer(content_path)

    results = indexer.search(query, top_k=args.top_k)

    if not results:
        print("No results found.")
        return

    for r in results:
        print(f"{r['score']:.3f}  {r['path']}")


def duplicates():
    """Find duplicate notes."""
    parser = argparse.ArgumentParser(description="Find potential duplicate notes")
    parser.add_argument("file", help="File to check for duplicates (absolute or relative to vault)")
    parser.add_argument("-t", "--threshold", type=float, default=0.85,
                        help="Similarity threshold (default: 0.85)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show more details")
    args = parser.parse_args()

    content_path = _get_content_path()

    if args.verbose:
        print(f"Content path: {content_path}", file=sys.stderr)
        print(f"File: {args.file}", file=sys.stderr)
        print(file=sys.stderr)

    indexer = VaultIndexer(content_path, duplicate_threshold=args.threshold)

    results = indexer.find_duplicates(args.file)

    if isinstance(results, dict) and "error" in results:
        print(f"Error: {results['error']}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print("No duplicates found.")
        return

    for r in results:
        print(f"{r['score']:.3f}  {r['path']}")
