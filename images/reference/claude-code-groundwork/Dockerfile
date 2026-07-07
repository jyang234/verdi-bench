# claude-code-groundwork — the payload-gated groundwork TRIAL image
# [verdi-go integration plan §4, Track A2].
#
# A fork of images/official/anthropic-claude-code: it drives the SAME pinned
# Claude Code CLI and extends verdi-base (so verdi_agent, HOME=/tmp, the non-root
# posture, and the /verdi + /workspace layout are inherited), but it ALSO bakes in
# the pinned verdi-go toolchain (flowmap + groundwork) at a NON-PATH staging path
# and the vendored groundwork-workflow skill. agent.py arms all of it ONLY when the
# arm's payload.tools includes "groundwork" — one image, both arms, the asymmetry
# realized in the entrypoint (docs/usage-guide.md §9). The control arm is
# byte-for-byte the official agent; nothing groundwork-branded reaches a control
# session or the graded /workspace diff.
#
# Build from THIS directory (the context is self-contained — the skill is vendored
# and the prebuilt drop lives in bin/, so the build never reaches into verdi-go):
#   # default: ref-pinned install (build-time network required; result is offline)
#   docker build -f images/reference/claude-code-groundwork/Dockerfile \
#     --build-arg GROUNDWORK_REF=<tag|sha|pseudo-version> \
#     -t verdi-bench/claude-code-groundwork:latest \
#     images/reference/claude-code-groundwork
# verdi-base must exist first (FROM verdi-base): `bench images build base` or
# `docker build -t verdi-base images/base`. Like the grader image, this takes a
# GROUNDWORK_REF build-arg, so it is built with `docker build`, NOT the arg-less
# `bench images build` (which is why it is a reference image, not a registry entry).

# --- stage 1: the pinned verdi-go toolchain --------------------------------------
# The grader image's proven pinning pattern, reused EXACTLY (images/grader/
# Dockerfile): a ref-pinned `go install` with a fail-closed empty-ref check, or a
# prebuilt COPY fallback for airgapped builds. The Go toolchain here is also the
# one the final image reuses (COPY --from below), so both are pinned to verdi-go
# CI's version (1.25.11 — .github/workflows/*.yml) and match the grader + the
# committed base graphs byte-for-byte (byte-stable graph output holds per flowmap
# BUILD only — integration plan §9).
FROM golang:1.25.11-bookworm AS groundwork-build

# DEFAULT PATH (ref-pinned install): `go install ...@${GROUNDWORK_REF}` fetches the
# exact source. GROUNDWORK_REF has NO default on purpose — a floating install is a
# determinism hole. Staged to /opt/groundwork/bin so the final image COPYs one dir.
ARG GROUNDWORK_REF
ARG GROUNDWORK_MODULE=github.com/jyang234/golang-code-graph
# AIRGAPPED FALLBACK (prebuilt COPY): set GROUNDWORK_PREBUILT=1 and place already-
# built `flowmap` + `groundwork` at bin/ before building; build them from a pinned
# checkout with the SAME Go version as this image so runtime graph bytes match the
# task's committed base graph and the grader's.
ARG GROUNDWORK_PREBUILT=0
COPY bin/ /tmp/groundwork-prebuilt/
RUN set -eu; \
    mkdir -p /opt/groundwork/bin; \
    if [ "$GROUNDWORK_PREBUILT" = "1" ]; then \
        install -m 0755 /tmp/groundwork-prebuilt/flowmap /opt/groundwork/bin/flowmap; \
        install -m 0755 /tmp/groundwork-prebuilt/groundwork /opt/groundwork/bin/groundwork; \
    else \
        [ -n "${GROUNDWORK_REF:-}" ] || { echo "GROUNDWORK_REF must be set (an empty ref is a determinism hole); pass --build-arg GROUNDWORK_REF=<tag|sha|pseudo-version> or use GROUNDWORK_PREBUILT=1" >&2; exit 2; }; \
        GOBIN=/opt/groundwork/bin go install "${GROUNDWORK_MODULE}/cmd/flowmap@${GROUNDWORK_REF}"; \
        GOBIN=/opt/groundwork/bin go install "${GROUNDWORK_MODULE}/cmd/groundwork@${GROUNDWORK_REF}"; \
    fi; \
    rm -rf /tmp/groundwork-prebuilt; \
    /opt/groundwork/bin/flowmap version; /opt/groundwork/bin/groundwork version

# --- stage 2: the trial image ----------------------------------------------------
FROM verdi-base

# Pinned CLI version — part of the arm's identity, shared by BOTH arms (this image
# is the substrate; the arm is the treatment). Unlike the official image's 1.0.44
# default, this MUST support user-scope skills ($HOME/.claude/skills, first
# auto-loaded in @anthropic-ai/claude-code 2.1.157) AND the --mcp-config flag — the
# treatment arm's whole mechanism depends on both. UNVERIFIED in an offline build;
# confirm the pin + the CLI's flag spelling against your target (README honesty notes).
ARG CLAUDE_CODE_VERSION=2.1.202

USER root
# Node.js + npm for the CLI, then the pinned CLI itself (as in the official image).
# Clean the apt lists so the layer stays lean and digest-stable.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

# The pinned Go toolchain, reused from stage 1 (no second download; pinned to the
# same 1.25.11). The treatment agent regenerates graph.json after its edits
# (`flowmap graph` type-checks the module and needs `go`), so the ground -> edit ->
# verify loop can re-check fresh state via the MCP `reload` tool. Both arms carry
# `go` (a Go coding task needs it regardless) — symmetric, no per-arm leak.
COPY --from=groundwork-build /usr/local/go /usr/local/go

# The pinned verdi-go binaries at a NON-PATH staging location. agent.py symlinks
# them onto a writable PATH dir at runtime ONLY for the treatment arm; a control
# session never sees them on PATH.
COPY --from=groundwork-build /opt/groundwork/bin /opt/groundwork/bin

# The vendored groundwork-workflow skill (skill/PROVENANCE pins the source commit),
# staged read-only. agent.py copies it into $HOME/.claude/skills for the treatment
# arm; it is absent from a control session.
COPY skill/groundwork-workflow /opt/groundwork/skills/groundwork-workflow

# GOTOOLCHAIN=local: use ONLY the baked 1.25.11 (no runtime toolchain fetch).
# GOPROXY=off + a stdlib-only corpus keep `flowmap graph` offline. GOCACHE is a
# world-writable default so flowmap works under an arbitrary --user even when HOME
# is set to /tmp. /usr/local/go/bin joins PATH so flowmap can shell out to `go`;
# the groundwork binaries deliberately do NOT (the entrypoint exposes them per-arm).
ENV PATH="/usr/local/go/bin:${PATH}" \
    GOTOOLCHAIN=local \
    GOFLAGS=-mod=mod \
    GOPROXY=off \
    GOCACHE=/tmp/gocache
RUN mkdir -p /tmp/gocache && chmod 0777 /tmp/gocache

USER 65532:65532

COPY agent.py /agent.py
ENTRYPOINT ["python", "/agent.py"]
