# Official anthropic-claude-code image — installs the Claude Code CLI [refactor 03 §3].
#
# Extends verdi-base and installs the version-PINNED @anthropic-ai/claude-code CLI
# (Node-based). The stack version is part of the arm's identity — pin it here (and
# echo it in the arm's payload + image tag), never `latest`. Build-time network is
# REQUIRED (apt + npm); the RESULT is still digest-pinned and harbor runs it
# offline with --pull=never — the runtime hermeticity the base guarantees.
#
# glibc base (see images/base/Dockerfile) is why Node's prebuilt binaries install
# cleanly here rather than needing a musl from-source build.
FROM verdi-base

# Pinned CLI version. Override at build with --build-arg to match your target
# stack; the default is a starting point and is UNVERIFIED in an offline build
# (README.md "honesty notes").
ARG CLAUDE_CODE_VERSION=1.0.44

USER root
# Node.js + npm for the CLI, then the pinned CLI itself. Clean the apt lists so
# the layer stays lean and digest-stable.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force
USER 65532:65532

COPY agent.py /agent.py
ENTRYPOINT ["python", "/agent.py"]
