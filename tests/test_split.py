"""Tests for :mod:`ern.split`.

:func:`is_split_graph` (the linear-time Hammer-Simeone test) is checked against
an independent brute-force oracle that searches all vertex partitions for a
clique / independent-set split, over every graph up to n=6.
"""

from __future__ import annotations

import shutil
import subprocess
from itertools import combinations

import pytest

from ern import graph6, split

# Known split / non-split graphs.
SPLIT = ["@", "A_", "Bw", "C~"]  # K1, K2, K3, K4
NON_SPLIT = ["Cl"]  # C4, a forbidden induced subgraph for split graphs


def _adjacency_sets(n: int, rows: tuple[int, ...]) -> list[set[int]]:
    return [{j for j in range(n) if (rows[i] >> j) & 1} for i in range(n)]


def is_split_bruteforce(n: int, rows: tuple[int, ...]) -> bool:
    """Independent reference: some vertex subset is a clique whose complement is independent."""
    adj = _adjacency_sets(n, rows)
    verts = range(n)
    for size in range(n + 1):
        for clique in combinations(verts, size):
            cset = set(clique)
            if not all(b in adj[a] for a, b in combinations(clique, 2)):
                continue
            rest = [v for v in verts if v not in cset]
            if all(b not in adj[a] for a, b in combinations(rest, 2)):
                return True
    return False


@pytest.mark.parametrize("g6", SPLIT)
def test_known_split(g6: str) -> None:
    n, _, rows, _ = graph6.decode_graph6_with_edge_bits(g6)
    assert split.is_split_graph(n, rows) is True


@pytest.mark.parametrize("g6", NON_SPLIT)
def test_known_non_split(g6: str) -> None:
    n, _, rows, _ = graph6.decode_graph6_with_edge_bits(g6)
    assert split.is_split_graph(n, rows) is False


@pytest.mark.skipif(shutil.which("geng") is None, reason="nauty geng not on PATH")
@pytest.mark.parametrize("n", [3, 4, 5, 6])
def test_is_split_matches_bruteforce_over_all_graphs(n: int) -> None:
    out = subprocess.run(["geng", "-q", str(n)], check=True, capture_output=True, text=True).stdout
    for line in out.splitlines():
        g6 = line.strip()
        if not g6:
            continue
        decoded_n, _, rows, _ = graph6.decode_graph6_with_edge_bits(g6)
        assert split.is_split_graph(decoded_n, rows) == is_split_bruteforce(decoded_n, rows)


def test_default_generator_script_exists() -> None:
    script = split.default_split_generator_script()
    assert script.name == "gen_split_n.sh"
    assert script.exists()
