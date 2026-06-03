"""Tests for the pure ERN core (:mod:`ern.reconstruction`).

The solver is checked against an independent, deliberately naive brute-force
oracle: enumerate every size-``k`` sub-multiset of the deck and test whether some
choice is unmatched by every blocker. This is exponential but correct for small
inputs, and shares no code with the optimized implementation.
"""

from __future__ import annotations

import random
from itertools import product

import pytest

from ern import reconstruction as R


def brute_force_ern(g_counts, vectors):
    """Independent reference: smallest k forcing the target, or None."""
    m = sum(g_counts)
    if m == 0:
        return 0
    r = len(g_counts)
    ranges = [range(g_counts[i] + 1) for i in range(r)]
    for k in range(1, m + 1):
        for selection in product(*ranges):
            if sum(selection) != k:
                continue
            # The selection forces the target iff every blocker fails to supply it.
            if all(any(selection[i] > v[i] for i in range(r)) for v in vectors):
                return k
    return None


# --------------------------------------------------------------------------
# Hand-checked base cases
# --------------------------------------------------------------------------
def test_edgeless_graph_is_zero() -> None:
    assert R.edge_reconstruction_number_from_counts([], []) == 0
    assert R.edge_reconstruction_number_from_counts([0, 0], []) == 0


def test_no_blockers_is_one() -> None:
    assert R.edge_reconstruction_number_from_counts([3, 2], []) == 1


def test_card_type_unique_to_target_is_one() -> None:
    assert R.edge_reconstruction_number_from_counts([1, 2], [(0, 2), (0, 1)]) == 1


def test_two_card_types_no_common_blocker_is_two() -> None:
    assert R.edge_reconstruction_number_from_counts([1, 1], [(1, 0), (0, 1)]) == 2


def test_perfect_blocker_gives_infinite() -> None:
    assert R.edge_reconstruction_number_from_counts([2, 2], [(2, 2)]) is None


def test_deck_dict_interface() -> None:
    deck = {10: 2, 20: 1}
    blockers = [{10: 1, 20: 1}, {10: 2}]
    # types sorted by count desc -> g_counts (2, 1); vectors (1, 1) and (2, 0).
    assert R.edge_reconstruction_number(deck, blockers) == (
        R.edge_reconstruction_number_from_counts([2, 1], [(1, 1), (2, 0)])
    )


# --------------------------------------------------------------------------
# Randomized testing against the brute-force oracle
# --------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(8))
def test_matches_brute_force(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(250):
        r = rng.randint(1, 4)
        g_counts = [rng.randint(0, 3) for _ in range(r)]
        vectors = [tuple(rng.randint(0, 3) for _ in range(r)) for _ in range(rng.randint(0, 6))]
        assert R.edge_reconstruction_number_from_counts(g_counts, vectors) == (
            brute_force_ern(g_counts, vectors)
        ), (g_counts, vectors)


def test_maximal_blocker_vectors_is_a_covering_antichain() -> None:
    rng = random.Random(2024)
    for _ in range(1000):
        r = rng.randint(1, 5)
        vectors = [tuple(rng.randint(0, 3) for _ in range(r)) for _ in range(rng.randint(0, 6))]
        maximal = R.maximal_blocker_vectors(vectors)
        # A subset of the inputs.
        assert set(maximal) <= set(vectors)
        # An antichain: no distinct element dominates another.
        for i, a in enumerate(maximal):
            for j, b in enumerate(maximal):
                if i != j:
                    assert not R.dominates(a, b)
        # Covering: every input is dominated by some kept vector.
        for v in vectors:
            assert any(R.dominates(mx, v) for mx in maximal)
