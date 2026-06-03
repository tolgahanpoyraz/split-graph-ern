# Edge Reconstruction Numbers of Split Graphs

<!-- After pushing to GitHub, replace OWNER/REPO below to activate the CI badge. -->
[![CI](https://github.com/tolgahanpoyraz/split-graph-ern/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

A research toolkit, accompanying an in-progress honors thesis in graph theory,
for computing the **edge reconstruction number** (`ern`) and the
**degree-associated edge reconstruction number** (`dern`) of *split graphs*.

It exhaustively generates split graphs, computes `ern(G)` and `dern(G)` exactly,
and studies how those values are distributed across the class.

> **Note.** The specific classification results are part of an unpublished thesis
> and are intentionally withheld from this repository until the work is
> published. This repo is the verified *computational machinery*; the findings
> live elsewhere. What's published here is the engineering and a test suite that
> checks structural correctness (e.g. that the two solvers agree).

## Background

The **Reconstruction Conjecture** (Kelly, Ulam) asks whether a graph is
determined by its multiset of single-vertex-deleted subgraphs. Its edge analogue,
the **Edge Reconstruction Conjecture** (Harary), uses the *edge-deck*
`{G − e : e ∈ E(G)}`. The **edge reconstruction number** `ern(G)` refines this to
a quantity: the minimum number of edge-cards that pin down `G` up to isomorphism.
Almost all graphs have `ern(G) = 2`, so the interesting question is to classify
the exceptions.

A **degree-associated edge card** additionally records the degree-sum
`d(u) + d(v)` of the deleted edge `uv`; the corresponding `dern(G) ≤ ern(G)`.

A **split graph** is one whose vertices partition into a clique `C` and an
independent set `I` (equivalently, it is `{2K₂, C₄, C₅}`-free). Split graphs are
a well-structured class, which makes an exact study of `ern` and `dern` tractable
both in theory and by exhaustive computation.

## How it works

Two pipelines share the [nauty](https://pallini.di.uniroma1.it/) graph-isomorphism
toolkit and a common split-graph generator:

```
                         scripts/gen_split_n.sh
            genbg ──▶ cpp/bin/bip2split ──▶ shortg ──▶ split graphs (graph6)
                                                          │
                        ┌─────────────────────────────────┴───────────────────┐
                        ▼                                                       ▼
        ERN pipeline (Python, `ern` package)                 DERN pipeline (C++, cpp/dern)
   labelg canonicalization + exact reconstruction        densenauty + degree-constrained
   core; local (split-target) and global modes           candidate generation; fan-out driver
```

- **ERN** is implemented in the Python package `ern` (a pure decision core plus a
  nauty subprocess layer). In the scalable *local* mode, the candidate "blockers"
  for a target `G` are restricted to one-edge extensions of `G`'s own cards.
- **DERN** is a compiled C++ solver (`cpp/dern.cpp`): the degree-sum label on each
  card sharply constrains candidate parents, which keeps it fast.

The design and the specific optimizations are documented in
[docs/PIPELINE_OPTIMIZATIONS.md](docs/PIPELINE_OPTIMIZATIONS.md).

## Repository layout

```
src/ern/             Python package (graph6, split, canonical, nauty,
                     reconstruction core, global/local solvers, CLI)
cpp/                 C++ tools: dern, bip2split, small_graph.hpp, Makefile
scripts/             shell wrappers: split generation + DERN/ERN runners
tests/               unit + integration tests (structural correctness)
docs/                pipeline optimization notes
```

## Requirements

- Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/)
- [nauty/gtools](https://pallini.di.uniroma1.it/) on `PATH` (`geng`, `labelg`,
  `shortg`, `genbg`; Debian/Ubuntu's `nauty-*` names are auto-detected)
  - macOS: `brew install nauty` · Ubuntu/Debian: `apt-get install nauty`
- A C++20 compiler (for the DERN tools), with nauty's headers/library

## Quickstart

```bash
uv sync          # create the venv and install the package + dev tools
make build       # compile the C++ tools into cpp/bin (needs nauty)
make test        # run the test suite (fast subset)
```

## Usage

```bash
# ERN for all split graphs up to n=10 (scalable local mode):
uv run ern 10 --mode local --target-source split --jobs 8

# Cross-check with the exhaustive global mode (small n):
uv run ern 8 --mode global

# Save the split graphs whose ern is 1 or 3 up to n=12 to a CSV:
uv run ern 12 --save-ern 1,3 --save-csv out.csv

# DERN of a single graph (graph6 on stdin); e.g. K4:
echo "C~" | ./cpp/bin/dern

# DERN over all split graphs up to n=10, in parallel:
./scripts/run_split_dern_n_parallel.sh 10 0 dern_n10.csv 8
```

## Testing

```bash
uv run pytest                # fast subset
uv run pytest --run-slow     # includes larger-order checks
```

The suite is built around *independent* oracles and does not encode any
particular result:

- **Unit tests** check each module against an independent reference — a
  brute-force ERN solver, a brute-force split-partition test, and the known
  counts of unlabeled graphs (OEIS A000088).
- **Integration tests** verify structural correctness end to end: every split
  graph with at least 4 edges gets a small, finite `ern`/`dern`, and the
  exhaustive *global* solver and the split-target *local* solver produce the
  **same** ern distribution (a strong cross-implementation check).

## License

[MIT](LICENSE) © Tolgahan Poyraz. Built on the nauty/gtools toolkit by Brendan McKay
and Adolfo Piperno.
