"""Split-graph recognition and split-target generation.

A *split graph* is one whose vertices partition into a clique and an independent
set. :func:`is_split_graph` recognises them in linear time from the degree
sequence (the Hammer-Simeone criterion); :func:`iter_split_graphs` streams every
unlabeled split graph on ``n`` vertices from the external generation pipeline in
``scripts/gen_split_n.sh`` (genbg | bip2split | shortg).
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

from ern.graph6 import Graph6, decode_graph6_with_edge_bits
from ern.nauty import run_geng


def is_split_graph(n: int, rows: Sequence[int]) -> bool:
    """Return ``True`` iff the graph is a split graph (Hammer-Simeone test).

    With degrees sorted as ``d_1 >= ... >= d_n`` and ``m`` the largest index with
    ``d_m >= m - 1``, the graph is split iff
    ``sum_{i<=m} d_i == m(m-1) + sum_{i>m} d_i``.
    """
    deg = sorted((rows[i].bit_count() for i in range(n)), reverse=True)
    m = 0
    for i, d in enumerate(deg, start=1):
        if d >= i - 1:
            m = i
    lhs = sum(deg[:m])
    rhs = m * (m - 1) + sum(deg[m:])
    return lhs == rhs


def default_split_generator_script() -> Path:
    """Path to the bundled split-graph generation script."""
    return Path(__file__).resolve().parents[2] / "scripts" / "gen_split_n.sh"


def iter_split_graphs(n: int, split_script: Path) -> Iterator[Graph6]:
    """Stream unlabeled split graphs on ``n`` vertices from ``split_script``.

    stdout and stderr are read separately so informational ``>`` lines from
    ``shortg`` never leak into the graph stream; a non-zero exit raises.
    """
    proc = subprocess.Popen(
        ["bash", str(split_script), str(n), "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None
    try:
        for line in proc.stdout:
            g6 = line.strip()
            if g6 and not g6.startswith(">"):
                yield g6
    finally:
        proc.stdout.close()
    stderr = proc.stderr.read().strip()
    proc.stderr.close()
    rc = proc.wait()
    if rc != 0:
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(f"split target generator failed for n={n} with exit code {rc}{detail}")


def iter_graphs_from_file(path: Path) -> Iterator[Graph6]:
    """Yield non-empty graph6 lines from a file."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            g6 = line.strip()
            if g6:
                yield g6


def iter_target_graphs(
    n: int,
    target_source: str,
    geng_cmd: str,
    split_script: Path | None,
    min_edges: int,
) -> Iterator[Graph6]:
    """Yield the split graphs to solve for order ``n``.

    With ``target_source == "split"`` they come from the dedicated generator;
    otherwise all unlabeled graphs from geng are produced and filtered to the
    split ones. ``min_edges`` drops graphs with too few edges.
    """
    if target_source == "split":
        if split_script is None:
            raise RuntimeError(
                "Split target generation requested, but no split generator script was found."
            )
        for g6 in iter_split_graphs(n, split_script):
            if min_edges > 0:
                _, _, _, edge_positions = decode_graph6_with_edge_bits(g6)
                if len(edge_positions) < min_edges:
                    continue
            yield g6
        return

    for g6 in run_geng(geng_cmd, n, min_edges=min_edges):
        decoded_n, _, rows, _ = decode_graph6_with_edge_bits(g6)
        if is_split_graph(decoded_n, rows):
            yield g6
