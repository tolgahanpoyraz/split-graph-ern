"""Shared helpers for the ERN/DERN regression fixtures and tests.

The regression suite treats the computational pipeline as a black box: it runs
the tools, captures their CSV/summary output, and compares against frozen
known-good fixtures. This keeps the suite agnostic to the internal refactoring
that the rest of the project performs.

``ERN_CMD`` is the single place that knows how to invoke the ERN solver. It is
updated once, when the original monolithic script is replaced by the ``ern``
package, and nothing else in the suite changes.
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parents[1]

# Invocation of the ERN command-line entry point: the `ern` package.
ERN_CMD: list[str] = [sys.executable, "-m", "ern"]

# Compiled C++ tools and the split-graph generator (built with `make -C cpp`).
DERN_BIN = REPO_ROOT / "cpp" / "bin" / "dern"
BIP2SPLIT_BIN = REPO_ROOT / "cpp" / "bin" / "bip2split"
GEN_SCRIPT = REPO_ROOT / "scripts" / "gen_split_n.sh"

CSV_HEADER = ["n", "m", "ern", "g6"]

# Save selector that captures every graph (all finite ern values plus inf).
SAVE_ALL = "0-9999,inf"

_SUMMARY_RE = re.compile(
    r"processed=(\d+)\s+dern0=(\d+)\s+dern1=(\d+)\s+dern2=(\d+)\s+dern>=3=(\d+)"
)


# --------------------------------------------------------------------------
# ERN (Python pipeline)
# --------------------------------------------------------------------------
def run_ern_csv(
    n: int,
    out_path: Path,
    *,
    mode: str = "global",
    target_source: str | None = None,
    min_edges: int = 0,
    save_ern: str = SAVE_ALL,
    extra: Iterable[str] = (),
) -> None:
    """Run the ERN solver up to ``n`` vertices and write its CSV to ``out_path``."""
    cmd = [*ERN_CMD, str(n), "--mode", mode]
    if target_source is not None:
        cmd += ["--target-source", target_source]
    if min_edges:
        cmd += ["--min-edges", str(min_edges)]
    cmd += ["--save-ern", save_ern, "--save-csv", str(out_path), *extra]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _row_key(row: dict[str, str]) -> tuple[int, int, tuple[int, int], str]:
    ern = row["ern"]
    ern_key = (1, 0) if ern == "inf" else (0, int(ern))
    return (int(row["n"]), int(row["m"]), ern_key, row["g6"])


def read_rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def normalized_csv_text(rows: list[dict[str, str]]) -> str:
    """Render rows to CSV text in a canonical, order-independent form."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_HEADER, lineterminator="\n")
    writer.writeheader()
    for row in sorted(rows, key=_row_key):
        writer.writerow({key: row[key] for key in CSV_HEADER})
    return buf.getvalue()


def histogram_by_order(rows: list[dict[str, str]], min_edges: int = 4) -> dict[int, Counter]:
    """Per-order ern histogram restricted to graphs with at least ``min_edges`` edges."""
    hist: dict[int, Counter] = defaultdict(Counter)
    for row in rows:
        if int(row["m"]) >= min_edges:
            hist[int(row["n"])][row["ern"]] += 1
    return hist


# --------------------------------------------------------------------------
# DERN (compiled C++ pipeline)
# --------------------------------------------------------------------------
def cpp_tools_available() -> bool:
    """True once `make -C cpp` has produced the dern + bip2split binaries."""
    return DERN_BIN.exists() and BIP2SPLIT_BIN.exists()


def _generate_split_g6(n: int) -> str:
    proc = subprocess.run(
        ["bash", str(GEN_SCRIPT), str(n), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def dern_summary(n: int) -> dict[str, int]:
    """Run the full DERN pipeline for order ``n`` and return its summary counts."""
    proc = subprocess.run(
        [str(DERN_BIN), "--no-header", "--min-output-dern", "0", "--summary"],
        input=_generate_split_g6(n),
        check=True,
        capture_output=True,
        text=True,
    )
    match = _SUMMARY_RE.search(proc.stderr)
    if match is None:
        raise AssertionError(f"could not parse dern summary from: {proc.stderr!r}")
    keys = ("processed", "dern0", "dern1", "dern2", "dern3p")
    return dict(zip(keys, (int(value) for value in match.groups()), strict=False))


def dern_rows(n: int) -> list[dict[str, object]]:
    """Per-graph DERN output for order ``n`` as dicts with n, m, g6, dern."""
    proc = subprocess.run(
        [str(DERN_BIN), "--no-header", "--min-output-dern", "0"],
        input=_generate_split_g6(n),
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[dict[str, object]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(",")
        rows.append({"n": int(parts[0]), "m": int(parts[1]), "g6": parts[2], "dern": int(parts[3])})
    return rows


def dern_of_g6(g6: str) -> int:
    """Compute dern of a single graph given as a graph6 string."""
    proc = subprocess.run(
        [str(DERN_BIN), "--no-header", "--min-output-dern", "0"],
        input=g6 + "\n",
        check=True,
        capture_output=True,
        text=True,
    )
    line = proc.stdout.strip().splitlines()[0]
    return int(line.split(",")[3])
