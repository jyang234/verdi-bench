# Real LLM trial-agent image for the verdi harbor path. Digest-pinnable, no
# registry pull needed at trial time (harbor pins to the local image Id).
FROM python:3.12-alpine
COPY agent.py /agent.py
# stdlib only — no pip install, no network at build time.
ENTRYPOINT ["python", "/agent.py"]
