.PHONY: sync
sync:
	uv sync --all-extras

.PHONY: precommit
precommit: format test check
	@echo "ready to commit"

.PHONY: format
format:
	@echo "Formatting Python files..."
	@uv run ruff format .
	@uv run ruff check --fix . || true
	@echo "✅ Format complete"

.PHONY: test
test:
	@echo "Running tests..."
	@uv run python -m pytest tests/ -v

.PHONY: check
check: lint
	@echo "✅ All checks passed"

.PHONY: lint
lint:
	@echo "Running ruff..."
	@uv run ruff check .

.PHONY: typecheck
typecheck:
	@echo "Running mypy..."
	@uv run mypy semantic_search_mcp/
