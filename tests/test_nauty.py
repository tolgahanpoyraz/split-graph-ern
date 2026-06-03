"""Tests for :mod:`ern.nauty`.

``run_geng`` is checked against the known counts of unlabeled graphs by order
(OEIS A000088), an oracle independent of the implementation.
"""

from __future__ import annotations

import shutil

import pytest

from ern import graph6, nauty

# Number of unlabeled simple graphs on n vertices (OEIS A000088).
GRAPH_COUNTS = {1: 1, 2: 2, 3: 4, 4: 11, 5: 34, 6: 156, 7: 1044}

geng_available = pytest.mark.skipif(shutil.which("geng") is None, reason="nauty geng not on PATH")


def test_resolve_tool_command() -> None:
    # A present tool resolves to itself; a missing one with no fallbacks is None.
    assert nauty.resolve_tool_command("geng", ("nauty-geng", "geng")) in {"geng", "nauty-geng"}
    assert nauty.resolve_tool_command("definitely-not-a-real-binary-xyz", ()) is None


@geng_available
@pytest.mark.parametrize("n", sorted(GRAPH_COUNTS))
def test_run_geng_produces_known_counts(n: int) -> None:
    assert len(list(nauty.run_geng("geng", n))) == GRAPH_COUNTS[n]


@geng_available
@pytest.mark.parametrize("n", [5, 6])
def test_run_geng_min_edges_filters(n: int) -> None:
    graphs = list(nauty.run_geng("geng", n, min_edges=4))
    assert graphs  # non-empty
    for g6 in graphs:
        _, _, _, edge_bits = graph6.decode_graph6_with_edge_bits(g6)
        assert len(edge_bits) >= 4
