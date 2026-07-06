.PHONY: verify test lint sync shakedown

# Full verification gate: ALL unit and integration tests plus structural
# import contracts. This is the mandatory post-feature check (see CLAUDE.md).
verify: test lint

test:
	uv run pytest -q

lint:
	uv run lint-imports

sync:
	uv sync

# Behavioral acceptance shakedown: the hermetic golden path + the 18-vector
# tripwire matrix, driven end-to-end through the bench CLI (no keys, no Docker).
# Every advertised fence is made to fire. The real-judge / Docker / browser /
# harbor layers are opt-in — see scripts/shakedown/README.md.
shakedown:
	uv run python scripts/shakedown/golden.py
	uv run python scripts/shakedown/tripwires.py
