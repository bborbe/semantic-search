# Definition of Done

## Code Quality

- [ ] All functions have docstrings
- [ ] No `print()` in library code (use `logging`)
- [ ] Type hints on all function signatures
- [ ] No broad exception catches (`except Exception`)

## Testing

- [ ] `make test` passes (all tests green)
- [ ] New/changed code has test coverage

## Type Safety

- [ ] `make typecheck` passes (mypy strict, 0 errors)
- [ ] No new `type: ignore` without justification comment

## Linting

- [ ] `make lint` passes (ruff, 0 errors)
- [ ] `make format` produces no changes

## Build

- [ ] `make precommit` passes end-to-end
- [ ] No new dependencies without justification

## Documentation

- [ ] CHANGELOG.md updated if user-facing change
- [ ] README.md updated if API/usage changed
