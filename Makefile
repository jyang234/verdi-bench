.PHONY: verify test lint sync shakedown corpus-groundwork-v0

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

# Emit the groundwork-v0 experiment (tasks.yaml + holdouts/) and reference-solution
# trees to a GITIGNORED scratch dir, then strict-lint the emitted tasks.yaml.
# Generated output is NEVER committed — only the task SOURCES under
# corpora/groundwork-v0/tasks/ are. Needs the pinned verdi-go toolchain: set
# VERDI_FLOWMAP_BIN / VERDI_GROUNDWORK_BIN (or put flowmap+groundwork on PATH) to
# the SAME build the committed graphs were frozen at (corpora/groundwork-v0/README
# "Provenance"). The k=5 flake baselines run per-task on top of this — see that
# README's "Admission status".
GWV0_OUT ?= scratch/groundwork-v0
corpus-groundwork-v0:
	python3 corpora/groundwork-v0/build_tasks.py --out $(GWV0_OUT)/expt
	python3 corpora/groundwork-v0/build_tasks.py --solutions $(GWV0_OUT)/solutions
	uv run bench corpus validate-tasks $(GWV0_OUT)/expt
