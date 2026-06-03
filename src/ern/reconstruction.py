"""The exact edge-reconstruction-number decision core.

This module is pure and deterministic: it depends only on the *counts* of card
types, never on graphs, nauty, or I/O, which is what makes the ERN logic
unit-testable in isolation.

The setup: a target graph contributes a multiset of edge-deleted card types with
multiplicities ``g_counts``. Each competing graph ("blocker") contributes a
vector giving how many copies of each of those card types it can supply. The
edge reconstruction number is the smallest ``k`` such that some size-``k``
sub-multiset of the target's deck cannot be matched by any blocker -- i.e. the
target is forced. The solver:

1. drops dominated blocker vectors (a blocker weaker in every coordinate never
   helps);
2. resolves ``k = 1`` and ``k = 2`` with tuned bitset tests; and
3. for ``k >= 3`` runs a memoized DP over (card type, cards remaining, set of
   still-compatible blockers).

``None`` means no finite ``k`` works within this candidate set (reported as
"inf").
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

# Multiplicity of each canonical card type in a graph's edge deck.
DeckCounter = dict[int, int]


def dominates(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    """True if ``a`` is componentwise >= ``b`` (so ``a`` is at least as strong)."""
    return all(x >= y for x, y in zip(a, b, strict=False))


def maximal_blocker_vectors(vectors) -> list[tuple[int, ...]]:
    """Keep only componentwise-maximal blocker vectors (dominance pruning)."""
    unique = list(set(vectors))
    unique.sort(key=sum, reverse=True)
    maximal: list[tuple[int, ...]] = []
    for v in unique:
        is_dominated = False
        keep: list[tuple[int, ...]] = []
        for m in maximal:
            if dominates(m, v):
                is_dominated = True
                break
            if not dominates(v, m):
                keep.append(m)
        if not is_dominated:
            keep.append(v)
            maximal = keep
    return maximal


def add_maximal_blocker_vector(maximal: list[tuple[int, ...]], candidate: tuple[int, ...]) -> None:
    """Insert ``candidate`` into a maximal set in place, preserving maximality."""
    keep: list[tuple[int, ...]] = []
    for existing in maximal:
        if dominates(existing, candidate):
            return
        if not dominates(candidate, existing):
            keep.append(existing)
    keep.append(candidate)
    maximal[:] = keep


def fast_ern_k1_k2(g_counts: Sequence[int], vectors: Sequence[tuple[int, ...]]) -> int | None:
    """Resolve ern in {1, 2} with bitset tests, or return ``None`` if k >= 3.

    Builds, per card type, the set of blockers holding at least 1 and at least 2
    copies, then checks: a card type unique to the target (ern 1); two copies of
    one type unmatched (ern 2); or a pair of types with no common blocker (ern 2).
    """
    if not vectors:
        return 1
    r = len(g_counts)
    ge1 = [0] * r
    ge2 = [0] * r
    for j, vec in enumerate(vectors):
        bit = 1 << j
        for i, cnt in enumerate(vec):
            if cnt >= 1:
                ge1[i] |= bit
            if cnt >= 2:
                ge2[i] |= bit

    for i, gi in enumerate(g_counts):
        if gi >= 1 and ge1[i] == 0:
            return 1

    if sum(g_counts) < 2:
        return None

    for i, gi in enumerate(g_counts):
        if gi >= 2 and ge2[i] == 0:
            return 2

    present = [i for i, gi in enumerate(g_counts) if gi >= 1]
    for idx, i in enumerate(present):
        mi = ge1[i]
        for j in present[idx + 1 :]:
            if (mi & ge1[j]) == 0:
                return 2
    return None


def exists_distinguishing_subset_of_size(
    g_counts: Sequence[int], blocker_vectors: Sequence[tuple[int, ...]], k: int
) -> bool:
    """Is there a size-``k`` sub-multiset of the deck that every blocker misses?

    Memoized DFS over ``(i, remaining, mask)``: card type index ``i``, cards still
    to choose ``remaining``, and the bitmask ``mask`` of blockers still able to
    match everything chosen so far. A blocker is eliminated once we pick more
    copies of some type than it can supply; the target is forced when ``mask``
    becomes empty.
    """
    if not blocker_vectors:
        return True
    r = len(g_counts)
    b = len(blocker_vectors)
    all_mask = (1 << b) - 1

    ge_masks: list[list[int]] = []
    for i in range(r):
        gi = g_counts[i]
        exact = [0] * (gi + 1)
        for j, vec in enumerate(blocker_vectors):
            cnt = vec[i]
            if cnt > gi:
                cnt = gi
            exact[cnt] |= 1 << j
        ge = [0] * (gi + 1)
        running = 0
        for c in range(gi, -1, -1):
            running |= exact[c]
            ge[c] = running
        ge_masks.append(ge)

    suffix = [0] * (r + 1)
    for i in range(r - 1, -1, -1):
        suffix[i] = suffix[i + 1] + g_counts[i]

    @cache
    def dfs(i: int, remaining: int, mask: int) -> bool:
        if remaining == 0:
            return mask == 0
        if mask == 0:
            return remaining <= suffix[i]
        if i == r:
            return False
        if remaining > suffix[i]:
            return False

        lower = max(0, remaining - suffix[i + 1])
        upper = min(g_counts[i], remaining)
        for c in range(upper, lower - 1, -1):
            if dfs(i + 1, remaining - c, mask & ge_masks[i][c]):
                return True
        return False

    return dfs(0, k, all_mask)


def edge_reconstruction_number_from_counts(
    g_counts: Sequence[int], vectors: Sequence[tuple[int, ...]]
) -> int | None:
    """Edge reconstruction number from deck counts and blocker vectors.

    Returns 0 for the edgeless graph, a positive ``k`` for a reconstructible
    graph, or ``None`` if no finite ``k`` distinguishes it within this candidate
    set.
    """
    m = sum(g_counts)
    if m == 0:
        return 0
    if not vectors:
        return 1

    vectors = maximal_blocker_vectors(vectors)
    if not vectors:
        return 1

    fast = fast_ern_k1_k2(g_counts, vectors)
    if fast is not None:
        return fast

    overlaps = [sum(min(g_counts[i], v[i]) for i in range(len(g_counts))) for v in vectors]
    for k in range(3, m + 1):
        active = [v for v, ov in zip(vectors, overlaps, strict=False) if ov >= k]
        if not active:
            return k
        if exists_distinguishing_subset_of_size(g_counts, active, k):
            return k
    return None


def edge_reconstruction_number(deck: DeckCounter, blockers: Sequence[DeckCounter]) -> int | None:
    """Edge reconstruction number from a deck and a list of blocker decks."""
    types = sorted(deck.keys(), key=lambda k: deck[k], reverse=True)
    g_counts = [deck[t] for t in types]
    vectors = [tuple(bd.get(t, 0) for t in types) for bd in blockers]
    return edge_reconstruction_number_from_counts(g_counts, vectors)
