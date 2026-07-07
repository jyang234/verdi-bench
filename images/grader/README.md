# verdi-bench grader image

The trusted, network-less backend of the `grade` stage (`VERDI_GRADER_IMAGE`,
`harness/grade/runners.py`). It bundles the harness grade entrypoints
(`python -m harness.grade.run_holdouts` / `run_plugin`) **and** the pinned
verdi-go toolchain (`flowmap` + `groundwork`) so the groundwork grader plugin and
the `verdi-groundwork-check` command holdout can regenerate a call graph from the
graded workspace and evaluate policy verdicts — all offline (`--network none`).

See the [integration plan](../../docs/design/verdi-go-integration-plan.md) §2–§3.

## Build

Build from the **repo root** (the `harness/` tree is the build context):

```bash
# Default: ref-pinned install (build-time network required; result is offline).
docker build -f images/grader/Dockerfile \
  --build-arg GROUNDWORK_REF=<tag|sha|pseudo-version> \
  -t verdi-bench/grader:latest .
```

`GROUNDWORK_REF` has **no default** on purpose: byte-stable graph output holds per
`flowmap` *build* only, so a floating install is a determinism hole. Pin the same
ref everywhere (grader image, trial images, and the committed base graphs a task
ships). The Go toolchain is pinned to verdi-go CI's version (1.25.11).

### Airgapped / prebuilt fallback

When build-time network is unavailable, build `flowmap` and `groundwork` from a
pinned checkout with the **same Go version** as this image, drop them into
`images/grader/bin/`, and build with:

```bash
docker build -f images/grader/Dockerfile --build-arg GROUNDWORK_PREBUILT=1 \
  -t verdi-bench/grader:latest .
```

The binaries in `bin/` are git-ignored (only `.gitkeep`/`.gitignore` are tracked)
— they are never committed.

## Contents

- `Dockerfile` — the image definition.
- `verdi-groundwork-check` — the command-holdout wrapper installed at
  `/usr/local/bin/verdi-groundwork-check`. It regenerates the branch graph from
  `/workspace` and runs `groundwork verify`, resolving the policy + base graph
  from `/holdouts/groundwork/` **only** (never `/workspace`). Exit `0` clean, `1`
  gate-fail (BLOCK), `3` operational failure (distinct + loud).
