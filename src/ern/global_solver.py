"""Global (exhaustive) ERN mode: enumerate every unlabeled graph of order n.

This is the original strategy. For each order it generates all graphs with geng,
records each one's edge-deleted deck, then for every split target compares
against the other graphs of the same edge count (an exact pruning rule) to find
the ern. It is the simplest mode to reason about and serves as the small-order
cross-check for the local solver; it does not scale past roughly n=10 because the
universe of all graphs explodes.
"""

from __future__ import annotations

import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

from ern.canonical import NautyCanonicalizer
from ern.graph6 import (
    Graph6,
    decode_graph6_with_edge_bits,
    edge_deleted_card_counter,
)
from ern.nauty import run_geng
from ern.reconstruction import DeckCounter, edge_reconstruction_number
from ern.split import is_split_graph
from ern.stats import format_histogram


@dataclass(slots=True)
class GraphRecord:
    g6: Graph6
    edge_count: int
    is_split: bool
    deck: DeckCounter
    ern: int | None = None


def finalize_pending_decks(
    pending: list[tuple[int, Counter[Graph6]]],
    records: list[GraphRecord],
    canonicalizer: NautyCanonicalizer,
) -> None:
    """Canonicalize a batch of buffered raw decks into card-id decks on the records."""
    if not pending:
        return
    all_cards: set[Graph6] = set()
    for _, raw_counter in pending:
        all_cards.update(raw_counter.keys())
    if not all_cards:
        for rec_idx, _ in pending:
            records[rec_idx].deck = {}
        pending.clear()
        return
    card_ids = canonicalizer.canonicalize_many(list(all_cards))
    for rec_idx, raw_counter in pending:
        canon_counter: DeckCounter = {}
        for raw, mult in raw_counter.items():
            cid = card_ids[raw]
            canon_counter[cid] = canon_counter.get(cid, 0) + mult
        records[rec_idx].deck = canon_counter
    pending.clear()


def process_n(
    n: int,
    geng_cmd: str,
    canonicalizer: NautyCanonicalizer,
    progress_every: int,
    deck_batch_cards: int,
    min_edges: int,
) -> tuple[int, int, Counter, list[GraphRecord], float]:
    """Solve every split graph on ``n`` vertices exhaustively.

    Returns ``(total_graphs, split_count, hist, records, elapsed)``.
    """
    t0 = time.time()
    max_edges = n * (n - 1) // 2
    if min_edges > max_edges:
        return 0, 0, Counter(), [], time.time() - t0

    records: list[GraphRecord] = []
    total_graphs = 0
    pending: list[tuple[int, Counter[Graph6]]] = []
    pending_cards = 0

    for g6 in run_geng(geng_cmd, n, min_edges=min_edges):
        total_graphs += 1
        decoded_n, header_len, rows, edge_bit_positions = decode_graph6_with_edge_bits(g6)
        if decoded_n != n:
            raise RuntimeError(f"Decoded n mismatch: expected {n}, got {decoded_n}")
        split = is_split_graph(n, rows)
        raw_counter = edge_deleted_card_counter(g6, header_len, edge_bit_positions)
        rec_idx = len(records)
        records.append(
            GraphRecord(
                g6=g6 if split else "",
                edge_count=len(edge_bit_positions),
                is_split=split,
                deck={},
            )
        )
        pending.append((rec_idx, raw_counter))
        pending_cards += len(edge_bit_positions)

        if pending_cards >= deck_batch_cards:
            finalize_pending_decks(pending, records, canonicalizer)
            pending_cards = 0

        if progress_every > 0 and (total_graphs % progress_every) == 0:
            print(f"[n={n}] processed {total_graphs} graphs...", file=sys.stderr)

    finalize_pending_decks(pending, records, canonicalizer)

    by_edge_count: dict[int, list[int]] = defaultdict(list)
    split_indices: list[int] = []
    for i, rec in enumerate(records):
        by_edge_count[rec.edge_count].append(i)
        if rec.is_split:
            split_indices.append(i)

    needed_types_by_edge_count: dict[int, set[int]] = defaultdict(set)
    for idx in split_indices:
        rec = records[idx]
        needed_types_by_edge_count[rec.edge_count].update(rec.deck.keys())

    bucket_index: dict[
        int, tuple[list[int], list[DeckCounter], dict[int, int], dict[int, list[int]]]
    ] = {}
    for edge_count, split_types in needed_types_by_edge_count.items():
        bucket_ids = by_edge_count[edge_count]
        deck_list = [records[i].deck for i in bucket_ids]
        local_of_global = {gidx: lidx for lidx, gidx in enumerate(bucket_ids)}
        postings: dict[int, list[int]] = {t: [] for t in split_types}
        for lidx, deck in enumerate(deck_list):
            for t in deck.keys():
                lst = postings.get(t)
                if lst is not None:
                    lst.append(lidx)
        bucket_index[edge_count] = (bucket_ids, deck_list, local_of_global, postings)

    hist: Counter = Counter()
    for idx in split_indices:
        rec = records[idx]
        _, deck_list, local_of_global, postings = bucket_index[rec.edge_count]
        self_local_idx = local_of_global[idx]
        candidate_locals: set[int] = set()
        for t in rec.deck.keys():
            candidate_locals.update(postings.get(t, ()))
        candidate_locals.discard(self_local_idx)
        blockers = [deck_list[lidx] for lidx in candidate_locals]
        rec.ern = edge_reconstruction_number(rec.deck, blockers)
        if rec.ern is None:
            hist["inf"] += 1
        else:
            hist[rec.ern] += 1

    elapsed = time.time() - t0
    return total_graphs, len(split_indices), hist, records, elapsed


def print_order_summary(
    n: int,
    total_graphs: int,
    split_count: int,
    hist: Counter,
    records: list[GraphRecord],
    elapsed: float,
) -> None:
    ratio = (100.0 * split_count / total_graphs) if total_graphs else 0.0
    print(
        f"n={n}: unlabeled graphs={total_graphs}, "
        f"split graphs={split_count} ({ratio:.2f}%), elapsed={elapsed:.2f}s"
    )
    if split_count == 0:
        return

    finite_erns = [r.ern for r in records if r.is_split and r.ern is not None]
    print(f"  ern distribution: {format_histogram(hist)}")
    if finite_erns:
        min_ern = min(finite_erns)
        max_ern = max(finite_erns)
        mean_ern = sum(finite_erns) / len(finite_erns)
        print(f"  finite ern stats: min={min_ern}, max={max_ern}, mean={mean_ern:.3f}")
        max_examples = [r.g6 for r in records if r.is_split and r.ern == max_ern][:3]
        if max_examples:
            print(f"  example graph6 with max finite ern: {', '.join(max_examples)}")
    if hist.get("inf", 0):
        inf_examples = [r.g6 for r in records if r.is_split and r.ern is None][:3]
        print(f"  non-reconstructible by edge deck in this range: {hist['inf']} examples")
        if inf_examples:
            print(f"  example graph6 with ern=inf: {', '.join(inf_examples)}")
