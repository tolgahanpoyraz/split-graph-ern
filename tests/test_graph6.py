"""Tests for :mod:`ern.graph6`.

Decoding is checked against hand-specified edge sets for small graphs, plus
structural invariants (card-multiset size, extension count) over a geng-generated
sample of graphs.
"""

from __future__ import annotations

import pytest

from ern import graph6

# (graph6, n, edge count) for graphs whose structure is easy to verify by hand.
KNOWN = [
    ("@", 1, 0),
    ("A?", 2, 0),
    ("A_", 2, 1),  # K2
    ("Bw", 3, 3),  # K3
    ("C~", 4, 6),  # K4
]

# Full edge sets for the small graphs we can enumerate by hand.
EDGE_SETS = {
    "A?": set(),
    "A_": {(0, 1)},
    "Bw": {(0, 1), (0, 2), (1, 2)},
    "C~": {(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)},
}


def _edges(n: int, rows: tuple[int, ...]) -> set[tuple[int, int]]:
    return {(i, j) for j in range(n) for i in range(j) if (rows[i] >> j) & 1}


@pytest.mark.parametrize("g6, n, m", KNOWN)
def test_decode_known_values(g6: str, n: int, m: int) -> None:
    decoded_n, _, rows, edge_bits = graph6.decode_graph6_with_edge_bits(g6)
    assert decoded_n == n
    assert len(rows) == n
    assert len(edge_bits) == m


@pytest.mark.parametrize("g6", sorted(EDGE_SETS))
def test_decode_edge_sets(g6: str) -> None:
    n, _, rows, _ = graph6.decode_graph6_with_edge_bits(g6)
    assert _edges(n, rows) == EDGE_SETS[g6]


def test_edge_card_multiset_size_equals_edge_count(sample_graph6: list[str]) -> None:
    for g6 in sample_graph6:
        _, header_len, _, edge_bits = graph6.decode_graph6_with_edge_bits(g6)
        cards = graph6.edge_deleted_card_counter(g6, header_len, edge_bits)
        assert sum(cards.values()) == len(edge_bits)


def test_single_edge_extension_count_fills_to_complete(sample_graph6: list[str]) -> None:
    for g6 in sample_graph6:
        n, header_len, _, edge_bits = graph6.decode_graph6_with_edge_bits(g6)
        extensions = graph6.single_edge_extensions(g6, n, header_len, edge_bits)
        assert len(extensions) == n * (n - 1) // 2 - len(edge_bits)


def test_extensions_decode_to_one_more_edge(sample_graph6: list[str]) -> None:
    for g6 in sample_graph6:
        n, header_len, _, edge_bits = graph6.decode_graph6_with_edge_bits(g6)
        for ext in graph6.single_edge_extensions(g6, n, header_len, edge_bits):
            _, _, _, ext_edges = graph6.decode_graph6_with_edge_bits(ext)
            assert len(ext_edges) == len(edge_bits) + 1
