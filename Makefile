.PHONY: verify test lint sync

# Full verification gate: ALL unit and integration tests plus structural
# import contracts. This is the mandatory post-feature check (see CLAUDE.md).
verify: test lint

test:
	uv run pytest -q

lint:
	uv run lint-imports

sync:
	uv sync
