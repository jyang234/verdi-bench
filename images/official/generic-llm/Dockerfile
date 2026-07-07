# Official generic-llm image — single-turn chat agent, platform: generic [refactor 03 §3].
#
# Extends verdi-base and drops in agent.py — nothing else. The agent is stdlib-only
# (it imports verdi_agent, already on the base's PYTHONPATH), so there is no pip
# layer and no build-time network: the image is digest-pinnable and harbor runs it
# offline with --pull=never.
#
# `bench images build generic-llm` builds verdi-base first (this FROM depends on
# it), then this context.
FROM verdi-base
COPY agent.py /agent.py
ENTRYPOINT ["python", "/agent.py"]
