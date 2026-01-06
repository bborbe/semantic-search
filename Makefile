.PHONY: all install test precommit

all: precommit

install:
	uv sync --all-extras

test:
	uv run pytest

precommit: test
	@echo "All checks passed"
