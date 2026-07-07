# verdi-bench grader image — the trusted, network-less grade-container backend
# [verdi-go integration plan §2/§3 Track A1].
#
# It bundles the harness (so `python -m harness.grade.run_holdouts` / `run_plugin`
# are the grade entrypoints, refactor 03 §3) AND the pinned verdi-go toolchain
# (flowmap + groundwork) so the groundwork grader plugin and the
# verdi-groundwork-check command holdout can regenerate a call graph from the
# graded workspace and evaluate policy verdicts — all offline (`--network none`).
#
# Build from the REPO ROOT (the harness/ tree is the context):
#   docker build -f images/grader/Dockerfile \
#     --build-arg GROUNDWORK_REF=<tag|sha|pseudo-version> -t verdi-bench/grader:latest .
# then pin the built digest into VERDI_GRADER_IMAGE (harness/grade/runners.py).
#
# Go toolchain: pinned to verdi-go CI's version (1.25.11 — .github/workflows/*.yml).
# The analyzer's toolchain must be >= the highest `go` directive any task workspace
# declares (integration plan §3); GOTOOLCHAIN=local forbids an auto-fetch at grade
# time (the grade container has no network).
FROM golang:1.25.11-bookworm

# --- verdi-go toolchain: pin by ref, with a prebuilt COPY fallback -------------
# DEFAULT PATH (ref-pinned install): `go install ...@${GROUNDWORK_REF}` fetches the
# exact source (the setup-groundwork action refuses an empty ref; so do we below).
# Build-time network is required for this; the RESULT is digest-pinned and run
# offline. GROUNDWORK_REF has NO default on purpose — a floating install is a
# determinism hole (byte-stable graph output holds per flowmap BUILD only).
ARG GROUNDWORK_REF
ARG GROUNDWORK_MODULE=github.com/jyang234/golang-code-graph
# AIRGAPPED FALLBACK (prebuilt COPY): set GROUNDWORK_PREBUILT=1 and place
# already-built `flowmap` and `groundwork` binaries at images/grader/bin/ before
# building; they are copied to /usr/local/bin instead of `go install`. Build them
# from a pinned checkout with the SAME Go version as this image so grade-time
# graph bytes match the base graphs committed with each task.
ARG GROUNDWORK_PREBUILT=0
COPY images/grader/bin/ /tmp/groundwork-prebuilt/
RUN set -eu; \
    if [ "$GROUNDWORK_PREBUILT" = "1" ]; then \
        install -m 0755 /tmp/groundwork-prebuilt/flowmap /usr/local/bin/flowmap; \
        install -m 0755 /tmp/groundwork-prebuilt/groundwork /usr/local/bin/groundwork; \
    else \
        [ -n "${GROUNDWORK_REF:-}" ] || { echo "GROUNDWORK_REF must be set (an empty ref is a determinism hole); pass --build-arg GROUNDWORK_REF=<tag|sha|pseudo-version> or use GROUNDWORK_PREBUILT=1" >&2; exit 2; }; \
        GOBIN=/usr/local/bin go install "${GROUNDWORK_MODULE}/cmd/flowmap@${GROUNDWORK_REF}"; \
        GOBIN=/usr/local/bin go install "${GROUNDWORK_MODULE}/cmd/groundwork@${GROUNDWORK_REF}"; \
    fi; \
    rm -rf /tmp/groundwork-prebuilt; \
    flowmap version; groundwork version

# --- python + harness (the grade entrypoints) ----------------------------------
# bookworm ships Python 3.11 (the harness's supported floor; it stays
# 3.12-compatible). Only pydantic + pyyaml are needed (the run_holdouts/run_plugin
# import chain pulls in nothing heavier).
# Versions pinned to the EXACT resolutions in uv.lock (the single source of truth —
# bump both here in lockstep whenever uv.lock moves) so the grader image runs the
# same deps the harness is tested against; an unpinned float is a determinism hole.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip \
 && rm -rf /var/lib/apt/lists/* \
 && pip3 install --no-cache-dir --break-system-packages pydantic==2.13.4 pyyaml==6.0.3

COPY harness /app/harness
COPY images/grader/verdi-groundwork-check /usr/local/bin/verdi-groundwork-check
RUN chmod 0755 /usr/local/bin/verdi-groundwork-check

# GOTOOLCHAIN=local: use ONLY the baked 1.25.11 (no grade-time toolchain fetch).
# GOFLAGS/GOPROXY keep flowmap's type-check offline for the stdlib-only v0 corpus.
# GOCACHE: a world-writable default so flowmap works under an arbitrary --user
# even when HOME is unset; the plugin/wrapper still set their own per-grade cache.
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GOTOOLCHAIN=local \
    GOFLAGS=-mod=mod \
    GOPROXY=off \
    GOCACHE=/tmp/gocache
RUN mkdir -p /tmp/gocache && chmod 0777 /tmp/gocache

# CMD (not ENTRYPOINT): the holdout grade runs the default run_holdouts, while the
# plugin grade REPLACES it with `python -m harness.grade.run_plugin ...` (the argv
# the host appends after the image). An ENTRYPOINT would instead APPEND those
# tokens, so the plugin entrypoint would never run — CMD is load-bearing here.
CMD ["python3", "-m", "harness.grade.run_holdouts"]
