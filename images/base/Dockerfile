# verdi-base — the maintained base image every trial image extends [refactor 03 §3].
#
# It bakes in every harbor compatibility concern (docs/images.md §1) so an author
# writes ONLY agent logic: verdi_agent.py (the stdlib-only in-image SDK) is on the
# import path, the workspace layout and the /verdi/request.json contract are
# fixed, and the image runs unprivileged under an ARBITRARY uid:gid with
# --network none or the metering proxy.
#
# Base choice — python:3.12-slim (Debian / glibc), NOT alpine: the official stack
# images (anthropic-claude-code, openai-codex) EXTEND this base and install real
# CLIs (Node-based, native modules, glibc-linked wheels); musl/alpine breaks a
# large fraction of those prebuilt binaries and turns a trivial install into a
# from-source build. slim keeps apt + glibc for broad downstream compatibility
# while staying small and digest-pinnable. verdi_agent itself needs no packages
# (stdlib only), so the base carries no pip layer.
#
# Digest pinning: FROM is a tag, not a hardcoded upstream digest, because that
# digest is per-architecture (arm64 vs amd64) and would break a cross-arch build.
# The TRUST boundary is the BUILT image: `bench images build --pin` resolves it to
# a sha256 and records it in provenance, and harbor runs it with --pull=never on
# that exact digest [refactor 03 §7].
FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: no .pyc litter in the graded /workspace diff.
# PYTHONUNBUFFERED: a fail-visible agent's stdout/stderr reach the operator live.
# PYTHONPATH: verdi_agent is importable as `import verdi_agent`.
# HOME=/tmp: world-writable, so the container survives an ARBITRARY --user uid:gid
# (harbor overrides the image USER with the host uid) without a baked-in home dir;
# never /workspace, whose bytes are graded.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/verdi \
    HOME=/tmp

# The in-image SDK — stdlib-only, version-stamped, read-only. Copied into every
# base build so a trial agent.py just `import verdi_agent`.
COPY verdi_agent.py /opt/verdi/verdi_agent.py

# The fixed workspace layout of the harbor contract (docs/images.md §1):
#   /workspace              the graded workspace — harbor bind-mounts it rw and
#                           sets --workdir here; the agent writes its solution and
#   /workspace/artifacts/   telemetry (agent_log.json) here
#   /verdi/request.json     the task + arm config — bind-mounted READ-ONLY,
#                           OUTSIDE /workspace so it never pollutes the graded copy
# Made world-writable so an arbitrary uid can write even absent a bind mount
# (harbor/verify always bind-mount /workspace from a host-owned dir; this is
# defense in depth for the no-override case).
RUN mkdir -p /workspace/artifacts /verdi && chmod -R 0777 /workspace
WORKDIR /workspace

# Non-root by default (defense in depth). harbor OVERRIDES this with
# `--user <host-uid>:<host-gid>`, so a compliant agent must already tolerate an
# arbitrary uid:gid: it writes only under /workspace (host-owned, writable) and
# reads /verdi read-only, never assuming root or a fixed home. uid 65532 is the
# distroless "nonroot" convention.
RUN groupadd -g 65532 nonroot \
 && useradd -u 65532 -g 65532 -M -d /tmp -s /usr/sbin/nologin nonroot
USER 65532:65532

# No ENTRYPOINT: verdi-base is extend-only. An official/reference image COPYs its
# agent.py and sets the entrypoint convention verdi_agent.run_visible expects:
#     COPY agent.py /agent.py
#     ENTRYPOINT ["python", "/agent.py"]
# See images/official/*/Dockerfile and images/reference/multi-agent/Dockerfile.
