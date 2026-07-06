# Multi-agent reference trial-agent image — multi-turn workflow, platform: generic
# [EVAL-21 + EVAL-24; refactor 03 §3].
#
# Extends verdi-base and drops in agent.py — nothing else. The agent is stdlib-only
# (it imports verdi_agent, already on the base's PYTHONPATH), so there is no pip
# layer and no build-time network: the image is digest-pinnable and harbor runs it
# offline with --pull=never, unprivileged, as an arbitrary uid:gid. The whole
# workflow (orchestrator → planner → workers → critic) executes INSIDE this one
# container — one image, one task, arm-parameterized via /verdi/request.json. See
# README.md for the full harbor compliance contract.
#
# `bench images build images/reference/multi-agent` builds verdi-base first (this
# FROM depends on it), then this context.
FROM verdi-base
COPY agent.py /agent.py
ENTRYPOINT ["python", "/agent.py"]
