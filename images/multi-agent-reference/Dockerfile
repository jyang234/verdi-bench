# Multi-agent reference trial-agent image for the verdi harbor path [EVAL-21 + EVAL-24].
# Digest-pinnable, stdlib-only (no pip install, no build-time network), and it runs
# as an arbitrary uid:gid under `--cap-drop ALL --security-opt no-new-privileges`.
# The whole workflow (orchestrator → planner → workers) executes INSIDE this one
# container — one image, one task, arm-parameterized via /verdi/request.json.
# See README.md for the full harbor compliance contract.
FROM python:3.12-alpine
COPY agent.py /agent.py
ENTRYPOINT ["python", "/agent.py"]
