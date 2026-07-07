# Official openai-codex image — installs the OpenAI Codex CLI [refactor 03 §3].
#
# Extends verdi-base and installs the version-PINNED @openai/codex CLI (Node-based).
# The stack version is part of the arm's identity — pin it here (and echo it in the
# arm's payload + image tag), never `latest`. Build-time network is REQUIRED (apt +
# npm); the RESULT is still digest-pinned and harbor runs it offline with
# --pull=never — the runtime hermeticity the base guarantees.
#
# glibc base (see images/base/Dockerfile) is why the CLI's prebuilt binaries
# install cleanly here rather than needing a musl from-source build.
FROM verdi-base

# Pinned CLI version. Override at build with --build-arg to match your target
# stack; the default is a starting point and is UNVERIFIED in an offline build
# (README.md "honesty notes").
ARG CODEX_VERSION=0.2.0

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g "@openai/codex@${CODEX_VERSION}" \
 && npm cache clean --force
USER 65532:65532

COPY agent.py /agent.py
ENTRYPOINT ["python", "/agent.py"]
