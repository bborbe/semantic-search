.PHONY: install sync format lint typecheck check test precommit

# Install dependencies (alias for sync)
install: sync

# Sync dependencies
sync:
	@uv sync --all-extras

format:
	uv run ruff format .
	uv run ruff check --fix . || true

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

check: lint typecheck

test: sync
	uv run pytest -v || test $$? -eq 5

precommit: sync format test check
	@echo "✓ All precommit checks passed"
