"""Per-order ERN statistics and human-readable summaries.

These accumulate the distribution of ern values as graphs are solved, and format
the end-of-order reports. Kept separate from the solvers so both the local and
global pipelines share one reporting path.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ern.graph6 import Graph6


@dataclass(slots=True)
class LocalErnResult:
    """Result of solving one target graph in local mode."""

    g6: Graph6
    edge_count: int
    ern: int | None


@dataclass(slots=True)
class LocalOrderStats:
    """Running ern distribution and exemplars for a single vertex count."""

    processed: int = 0
    hist: Counter = field(default_factory=Counter)
    finite_sum: int = 0
    finite_count: int = 0
    min_ern: int | None = None
    max_ern: int | None = None
    max_examples: list[Graph6] = field(default_factory=list)
    inf_examples: list[Graph6] = field(default_factory=list)


def copy_local_order_stats(stats: LocalOrderStats) -> LocalOrderStats:
    return LocalOrderStats(
        processed=stats.processed,
        hist=Counter(stats.hist),
        finite_sum=stats.finite_sum,
        finite_count=stats.finite_count,
        min_ern=stats.min_ern,
        max_ern=stats.max_ern,
        max_examples=list(stats.max_examples),
        inf_examples=list(stats.inf_examples),
    )


def update_local_order_stats(stats: LocalOrderStats, result: LocalErnResult) -> None:
    """Fold one solver result into the running statistics."""
    stats.processed += 1
    if result.ern is None:
        stats.hist["inf"] += 1
        if len(stats.inf_examples) < 3:
            stats.inf_examples.append(result.g6)
        return

    stats.hist[result.ern] += 1
    stats.finite_sum += result.ern
    stats.finite_count += 1
    if stats.min_ern is None or result.ern < stats.min_ern:
        stats.min_ern = result.ern
    if stats.max_ern is None or result.ern > stats.max_ern:
        stats.max_ern = result.ern
        stats.max_examples = [result.g6]
    elif result.ern == stats.max_ern and len(stats.max_examples) < 3:
        stats.max_examples.append(result.g6)


def format_histogram(hist: Counter) -> str:
    """Render an ern histogram as ``1:.., 2:.., .., inf:..``."""
    keys_int = sorted(k for k in hist.keys() if isinstance(k, int))
    parts = [f"{k}:{hist[k]}" for k in keys_int]
    if "inf" in hist:
        parts.append(f"inf:{hist['inf']}")
    return ", ".join(parts) if parts else "(empty)"


def print_local_order_summary(
    n: int, stats: LocalOrderStats, elapsed: float, target_source: str
) -> None:
    label = "split targets" if target_source == "split" else "split graphs"
    print(f"n={n}: processed {stats.processed} {label}, elapsed={elapsed:.2f}s")
    if stats.processed == 0:
        return

    print(f"  ern distribution: {format_histogram(stats.hist)}")
    if stats.finite_count:
        mean_ern = stats.finite_sum / stats.finite_count
        print(f"  finite ern stats: min={stats.min_ern}, max={stats.max_ern}, mean={mean_ern:.3f}")
        if stats.max_examples:
            print(f"  example graph6 with max finite ern: {', '.join(stats.max_examples)}")
    if stats.hist.get("inf", 0):
        print(f"  non-reconstructible by edge deck in this range: {stats.hist['inf']} examples")
        if stats.inf_examples:
            print(f"  example graph6 with ern=inf: {', '.join(stats.inf_examples)}")
