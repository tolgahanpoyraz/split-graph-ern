"""Local (split-target) ERN mode: solve one split graph at a time.

Instead of enumerating the whole graph universe, this mode reasons locally: any
graph that could be confused with the target ``G`` must share an edge-deleted
card with it, hence must be a one-edge extension of one of ``G``'s own cards.
That shrinks the blocker universe from "all graphs of order n" to "parents of
``G``'s cards", which is what lets ERN scale to larger orders. The exact ERN
decision (``ern.reconstruction``) is unchanged; only the candidate set differs.

Targets are solved in chunks across a process pool, with each worker keeping
persistent canonicalization and parent-set caches.
"""

from __future__ import annotations

import sys
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from itertools import islice
from pathlib import Path

from ern.canonical import Graph6BatchCanonicalizer
from ern.checkpoint import write_saved_result
from ern.graph6 import (
    Graph6,
    decode_graph6_with_edge_bits,
    edge_deleted_card_counter,
    single_edge_extensions,
)
from ern.reconstruction import (
    add_maximal_blocker_vector,
    edge_reconstruction_number_from_counts,
)
from ern.split import iter_graphs_from_file, iter_target_graphs
from ern.stats import (
    LocalErnResult,
    LocalOrderStats,
    copy_local_order_stats,
    update_local_order_stats,
)


def chunked(items: Iterable[Graph6], chunk_size: int) -> Iterator[list[Graph6]]:
    chunk: list[Graph6] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


class LocalErnSolver:
    """Solve ern for one split target at a time using one-edge-parent candidates."""

    def __init__(
        self,
        labelg_cmd: str,
        batch_size: int,
        use_raw_cache: bool,
        raw_cache_limit: int,
        parent_cache_limit: int,
        blocker_chunk_size: int,
    ) -> None:
        self.canonicalizer = Graph6BatchCanonicalizer(
            labelg_cmd=labelg_cmd,
            batch_size=batch_size,
            use_raw_cache=use_raw_cache,
            raw_cache_limit=raw_cache_limit,
        )
        self.parent_cache_limit = max(0, parent_cache_limit)
        self.parent_cache: OrderedDict[Graph6, tuple[Graph6, ...]] = OrderedDict()
        self.blocker_chunk_size = max(1, blocker_chunk_size)

    def _trim_parent_cache(self) -> None:
        if self.parent_cache_limit <= 0:
            return
        while len(self.parent_cache) > self.parent_cache_limit:
            self.parent_cache.popitem(last=False)

    def _parent_candidates(self, card_canon: Graph6) -> tuple[Graph6, ...]:
        cached = self.parent_cache.get(card_canon)
        if cached is not None:
            self.parent_cache.move_to_end(card_canon)
            return cached

        n, header_len, _, edge_bit_positions = decode_graph6_with_edge_bits(card_canon)
        raw_extensions = single_edge_extensions(card_canon, n, header_len, edge_bit_positions)
        if not raw_extensions:
            cached = ()
        else:
            canon_extensions = self.canonicalizer.canonicalize_many(raw_extensions)
            cached = tuple(sorted(set(canon_extensions.values())))
        self.parent_cache[card_canon] = cached
        self.parent_cache.move_to_end(card_canon)
        self._trim_parent_cache()
        return cached

    def solve(self, g6: Graph6) -> LocalErnResult:
        # `geng` and `shortg` already emit canonical representatives.
        gcanon = g6
        n, header_len, _, edge_bit_positions = decode_graph6_with_edge_bits(gcanon)
        edge_count = len(edge_bit_positions)

        raw_counter = edge_deleted_card_counter(gcanon, header_len, edge_bit_positions)
        if not raw_counter:
            return LocalErnResult(g6=gcanon, edge_count=edge_count, ern=0)

        raw_to_canon = self.canonicalizer.canonicalize_many(list(raw_counter.keys()))
        deck_by_canon: Counter = Counter()
        for raw, mult in raw_counter.items():
            deck_by_canon[raw_to_canon[raw]] += mult

        candidate_sets: dict[Graph6, set[Graph6]] = {}
        candidate_union: set[Graph6] = set()
        for card_canon in deck_by_canon.keys():
            parents = set(self._parent_candidates(card_canon))
            candidate_sets[card_canon] = parents
            candidate_union.update(parents)

        candidate_union.discard(gcanon)
        if not candidate_union:
            return LocalErnResult(g6=gcanon, edge_count=edge_count, ern=1)

        for parents in candidate_sets.values():
            if len(parents) == 1 and gcanon in parents:
                return LocalErnResult(g6=gcanon, edge_count=edge_count, ern=1)

        ordered_types = sorted(deck_by_canon.keys(), key=lambda card: len(candidate_sets[card]))
        for idx, first in enumerate(ordered_types):
            first_set = candidate_sets[first]
            for second in ordered_types[idx + 1 :]:
                overlap = first_set.intersection(candidate_sets[second])
                if len(overlap) == 1 and gcanon in overlap:
                    return LocalErnResult(g6=gcanon, edge_count=edge_count, ern=2)

        type_order = sorted(
            deck_by_canon.keys(), key=lambda card: deck_by_canon[card], reverse=True
        )
        type_ids = {card: idx for idx, card in enumerate(type_order)}
        g_counts = [deck_by_canon[card] for card in type_order]
        maximal_vectors: list[tuple[int, ...]] = []
        for candidate_chunk in chunked(sorted(candidate_union), self.blocker_chunk_size):
            chunk_raw_decks: list[Counter[Graph6]] = []
            chunk_raw_cards: set[Graph6] = set()
            for candidate in candidate_chunk:
                _, cand_header_len, _, cand_edge_positions = decode_graph6_with_edge_bits(candidate)
                raw_deck = edge_deleted_card_counter(
                    candidate, cand_header_len, cand_edge_positions
                )
                chunk_raw_decks.append(raw_deck)
                chunk_raw_cards.update(raw_deck.keys())

            blocker_card_canon = self.canonicalizer.canonicalize_many(list(chunk_raw_cards))
            for raw_deck in chunk_raw_decks:
                projected = [0] * len(type_order)
                for raw, mult in raw_deck.items():
                    card_canon = blocker_card_canon[raw]
                    type_id = type_ids.get(card_canon)
                    if type_id is None:
                        continue
                    projected[type_id] += mult
                if any(projected):
                    add_maximal_blocker_vector(maximal_vectors, tuple(projected))

        ern = edge_reconstruction_number_from_counts(g_counts, maximal_vectors)
        return LocalErnResult(g6=gcanon, edge_count=edge_count, ern=ern)


_LOCAL_SOLVER: LocalErnSolver | None = None


def init_local_solver_worker(
    labelg_cmd: str,
    batch_size: int,
    use_raw_cache: bool,
    raw_cache_limit: int,
    parent_cache_limit: int,
    blocker_chunk_size: int,
) -> None:
    global _LOCAL_SOLVER
    _LOCAL_SOLVER = LocalErnSolver(
        labelg_cmd=labelg_cmd,
        batch_size=batch_size,
        use_raw_cache=use_raw_cache,
        raw_cache_limit=raw_cache_limit,
        parent_cache_limit=parent_cache_limit,
        blocker_chunk_size=blocker_chunk_size,
    )


def solve_local_chunk(graphs: Sequence[Graph6]) -> list[LocalErnResult]:
    if _LOCAL_SOLVER is None:
        raise RuntimeError("Local ERN worker not initialized")
    return [_LOCAL_SOLVER.solve(g6) for g6 in graphs]


def process_n_local(
    n: int,
    labelg_cmd: str,
    geng_cmd: str,
    target_source: str,
    split_script: Path | None,
    target_file: Path | None,
    progress_every: int,
    batch_size: int,
    use_raw_cache: bool,
    raw_cache_limit: int,
    parent_cache_limit: int,
    blocker_chunk_size: int,
    jobs: int,
    chunk_size: int,
    min_edges: int,
    save_writer,
    save_values: set[int],
    save_inf: bool,
    resume_stats: LocalOrderStats | None = None,
    resume_saved_rows: int = 0,
    resume_elapsed: float = 0.0,
    checkpoint_callback: Callable[[LocalOrderStats, int, bool, float], None] | None = None,
) -> tuple[LocalOrderStats, float, int]:
    """Solve all split targets for order ``n`` (optionally in parallel).

    Returns ``(stats, total_elapsed, saved_rows)``. Supports resuming from a
    checkpoint and periodically invoking ``checkpoint_callback``.
    """
    t0 = time.time()
    max_edges = n * (n - 1) // 2
    if min_edges > max_edges:
        return LocalOrderStats(), time.time() - t0, 0

    stats = copy_local_order_stats(resume_stats) if resume_stats is not None else LocalOrderStats()
    saved_rows = resume_saved_rows
    if target_file is not None:
        targets: Iterator[Graph6] = iter_graphs_from_file(target_file)
    else:
        targets = iter_target_graphs(
            n=n,
            target_source=target_source,
            geng_cmd=geng_cmd,
            split_script=split_script,
            min_edges=min_edges,
        )
    if stats.processed > 0:
        targets = islice(targets, stats.processed, None)

    def emit_checkpoint(completed: bool) -> None:
        if checkpoint_callback is None:
            return
        checkpoint_callback(stats, saved_rows, completed, resume_elapsed + (time.time() - t0))

    if jobs <= 1:
        solver = LocalErnSolver(
            labelg_cmd=labelg_cmd,
            batch_size=batch_size,
            use_raw_cache=use_raw_cache,
            raw_cache_limit=raw_cache_limit,
            parent_cache_limit=parent_cache_limit,
            blocker_chunk_size=blocker_chunk_size,
        )
        for chunk in chunked(targets, chunk_size):
            for result in (solver.solve(g6) for g6 in chunk):
                update_local_order_stats(stats, result)
                saved_rows += write_saved_result(save_writer, save_values, save_inf, n, result)
                if progress_every > 0 and (stats.processed % progress_every) == 0:
                    print(f"[n={n}] processed {stats.processed} target graphs...", file=sys.stderr)
            emit_checkpoint(completed=False)
        total_elapsed = resume_elapsed + (time.time() - t0)
        emit_checkpoint(completed=True)
        return stats, total_elapsed, saved_rows

    with ProcessPoolExecutor(
        max_workers=jobs,
        initializer=init_local_solver_worker,
        initargs=(
            labelg_cmd,
            batch_size,
            use_raw_cache,
            raw_cache_limit,
            parent_cache_limit,
            blocker_chunk_size,
        ),
    ) as pool:
        chunk_iter = iter(chunked(targets, chunk_size))
        pending: set = set()
        max_pending = max(2, jobs * 2)

        while True:
            while len(pending) < max_pending:
                try:
                    chunk = next(chunk_iter)
                except StopIteration:
                    break
                pending.add(pool.submit(solve_local_chunk, chunk))

            if not pending:
                break

            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                for result in fut.result():
                    update_local_order_stats(stats, result)
                    saved_rows += write_saved_result(save_writer, save_values, save_inf, n, result)
                    if progress_every > 0 and (stats.processed % progress_every) == 0:
                        print(
                            f"[n={n}] processed {stats.processed} target graphs...", file=sys.stderr
                        )
                emit_checkpoint(completed=False)

    total_elapsed = resume_elapsed + (time.time() - t0)
    emit_checkpoint(completed=True)
    return stats, total_elapsed, saved_rows
