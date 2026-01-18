.PHONY: install format lint typecheck check test precommit

install:
	uv sync --all-extras

format:
	uv run ruff format .
	uv run ruff check --fix . || true

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

check: lint typecheck

test:
	uv run pytest -v || test $$? -eq 5

precommit: format test check
	@echo "✓ All precommit checks passed"
