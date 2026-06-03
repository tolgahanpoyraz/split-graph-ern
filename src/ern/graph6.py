"""graph6 decoding and edge-level operations.

graph6 is nauty's compact ASCII encoding for small graphs. These helpers decode
a graph6 line into adjacency-row bitsets together with the bit positions of its
edges, and build edge-deleted "cards" and single-edge extensions by mutating the
graph6 payload bytes directly. Mutating the encoding in place is far cheaper than
rebuilding and re-encoding a graph object once per edge, which matters because
the solvers do this for every edge of every graph.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

Graph6 = str


def parse_n_from_graph6_values(values: Sequence[int]) -> tuple[int, int]:
    """Decode the vertex count from graph6 header bytes (already offset by -63).

    Returns ``(n, header_len)`` where ``header_len`` is the number of leading
    bytes consumed by the size header (1, 4, or 8).
    """
    if not values:
        raise ValueError("Empty graph6 string")
    if values[0] <= 62:
        return values[0], 1
    if len(values) < 4:
        raise ValueError("Malformed graph6 header")
    if values[1] <= 62:
        n = (values[1] << 12) | (values[2] << 6) | values[3]
        return n, 4
    if len(values) < 8:
        raise ValueError("Malformed graph6 extended header")
    n = (
        (values[2] << 30)
        | (values[3] << 24)
        | (values[4] << 18)
        | (values[5] << 12)
        | (values[6] << 6)
        | values[7]
    )
    return n, 8


def decode_graph6_with_edge_bits(
    line: Graph6,
) -> tuple[int, int, tuple[int, ...], list[int]]:
    """Decode a graph6 line into ``(n, header_len, rows, edge_bit_positions)``.

    ``rows[i]`` is a bitset of the neighbours of vertex ``i``; ``edge_bit_positions``
    lists the payload bit index of each present edge, in graph6 upper-triangle
    order ``(0,1), (0,2), (1,2), (0,3), ...``.
    """
    s = line.strip()
    vals = [ord(c) - 63 for c in s]
    n, header_len = parse_n_from_graph6_values(vals)
    sextets = vals[header_len:]
    bit_pos = 0
    rows = [0] * n
    edge_bit_positions: list[int] = []
    for j in range(1, n):
        for i in range(j):
            sextet_idx = bit_pos // 6
            shift = 5 - (bit_pos % 6)
            bit = 0
            if sextet_idx < len(sextets):
                bit = (sextets[sextet_idx] >> shift) & 1
            if bit:
                rows[i] |= 1 << j
                rows[j] |= 1 << i
                edge_bit_positions.append(bit_pos)
            bit_pos += 1
    return n, header_len, tuple(rows), edge_bit_positions


def edge_deleted_card_counter(
    graph6_line: Graph6, header_len: int, edge_bit_positions: Sequence[int]
) -> Counter[Graph6]:
    """Multiset of single-edge-deleted cards, keyed by raw graph6 string.

    Each present edge is cleared in turn by flipping its payload bit, the
    resulting graph6 string is recorded, and the bit is restored.
    """
    if not edge_bit_positions:
        return Counter()
    b = bytearray(graph6_line.encode("ascii"))
    cards: Counter[Graph6] = Counter()
    for bit_pos in edge_bit_positions:
        payload_idx = header_len + (bit_pos // 6)
        mask = 1 << (5 - (bit_pos % 6))
        b[payload_idx] -= mask
        cards[b.decode("ascii")] += 1
        b[payload_idx] += mask
    return cards


def single_edge_extensions(
    graph6_line: Graph6,
    n: int,
    header_len: int,
    edge_bit_positions: Sequence[int],
) -> list[Graph6]:
    """Every graph6 string obtained by adding one absent edge to the graph.

    Walks all upper-triangle bit positions, skipping the ones that already hold
    an edge, and sets each remaining bit in turn.
    """
    total_bits = n * (n - 1) // 2
    b = bytearray(graph6_line.encode("ascii"))
    out: list[Graph6] = []

    next_edge_idx = 0
    next_edge = edge_bit_positions[0] if edge_bit_positions else None
    for bit_pos in range(total_bits):
        if next_edge is not None and bit_pos == next_edge:
            next_edge_idx += 1
            next_edge = (
                edge_bit_positions[next_edge_idx]
                if next_edge_idx < len(edge_bit_positions)
                else None
            )
            continue
        payload_idx = header_len + (bit_pos // 6)
        mask = 1 << (5 - (bit_pos % 6))
        b[payload_idx] += mask
        out.append(b.decode("ascii"))
        b[payload_idx] -= mask
    return out
