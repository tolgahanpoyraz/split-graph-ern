"""Command-line interface for the split-graph ERN solver.

``main`` wires the pieces together: it resolves the nauty tools, parses options,
and dispatches to either the exhaustive global solver or the scalable local
solver, with optional CSV output and resumable checkpointing.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ern.canonical import NautyCanonicalizer
from ern.checkpoint import (
    checkpoint_target_file,
    default_checkpoint_path,
    init_checkpoint_state,
    load_checkpoint_state,
    open_save_csv,
    safe_fsync,
    save_checkpoint_state,
    stats_from_checkpoint_dict,
    stats_to_checkpoint_dict,
    validate_checkpoint_state,
    write_split_target_cache,
)
from ern.global_solver import print_order_summary, process_n
from ern.local_solver import process_n_local
from ern.nauty import resolve_tool_command
from ern.split import default_split_generator_script
from ern.stats import LocalOrderStats, format_histogram, print_local_order_summary


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute edge-reconstruction numbers for split graphs up to n vertices. "
            "The default local mode generates split targets directly and solves "
            "each target from its own edge cards; global mode falls back to "
            "exhaustive all-graph enumeration."
        )
    )
    parser.add_argument("n", nargs="?", type=int, help="Maximum number of vertices")
    parser.add_argument(
        "--mode",
        choices=("local", "global"),
        default="local",
        help=(
            "`local` solves each split target independently; "
            "`global` enumerates all unlabeled graphs."
        ),
    )
    parser.add_argument(
        "--target-source",
        choices=("split", "all"),
        default="split",
        help="Target graph source for local mode: direct split generator or all unlabeled graphs.",
    )
    parser.add_argument(
        "--start-n",
        type=int,
        default=1,
        help="Start order for scan (default: 1)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=1000,
        help="Emit progress every K processed graphs per n (0 disables)",
    )
    parser.add_argument("--geng", default="geng", help="Path to geng binary")
    parser.add_argument("--labelg", default="labelg", help="Path to labelg binary")
    parser.add_argument(
        "--split-generator",
        default="",
        help="Path to scripts/gen_split_n.sh for local split-target generation",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=min(8, max(1, os.cpu_count() or 1)),
        help="Worker process count for local mode",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=32,
        help="How many target graphs each local worker processes per task",
    )
    parser.add_argument(
        "--blocker-chunk-size",
        type=int,
        default=256,
        help="How many candidate blockers a local worker processes at once inside one target graph",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20000,
        help="Batch size for labelg canonicalization",
    )
    parser.add_argument(
        "--labelg-jobs",
        type=int,
        default=min(8, max(1, os.cpu_count() or 1)),
        help="Number of parallel labelg worker processes in global mode",
    )
    parser.add_argument(
        "--deck-batch-cards",
        type=int,
        default=200000,
        help="How many edge-deleted cards to buffer before canonicalizing in global mode",
    )
    parser.add_argument(
        "--raw-card-cache",
        action="store_true",
        help="Enable raw graph6 canonicalization cache (faster, but uses more RAM)",
    )
    parser.add_argument(
        "--raw-cache-limit",
        type=int,
        default=250000,
        help="Per-worker raw-cache entry cap in local mode (0 means unbounded)",
    )
    parser.add_argument(
        "--parent-cache-limit",
        type=int,
        default=50000,
        help="Per-worker parent-candidate cache entry cap in local mode (0 means unbounded)",
    )
    parser.add_argument(
        "--min-edges",
        type=int,
        default=0,
        help="Only generate graphs with at least this many edges",
    )
    parser.add_argument(
        "--save-ern",
        default="",
        help="Comma-separated ERN targets to save (e.g. 3,4,inf or 2-5)",
    )
    parser.add_argument(
        "--save-csv",
        default="",
        help="CSV path to write graphs matching --save-ern",
    )
    parser.add_argument(
        "--checkpoint-file",
        default="",
        help="JSON checkpoint path for local split-mode resume support",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=5000,
        help="Write checkpoint after this many additional processed targets (default: 5000)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a local split-mode run from --checkpoint-file",
    )
    return parser.parse_args(argv)


def parse_ern_selector(spec: str) -> tuple[set[int], bool]:
    values: set[int] = set()
    want_inf = False
    if not spec.strip():
        return values, want_inf

    for raw in spec.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token in {"inf", "infty", "infinity"}:
            want_inf = True
            continue
        if "-" in token:
            parts = token.split("-", 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid ERN token: {raw!r}")
            lo = int(parts[0])
            hi = int(parts[1])
            if lo > hi:
                lo, hi = hi, lo
            if lo < 0:
                raise ValueError(f"Invalid ERN range: {raw!r}")
            values.update(range(lo, hi + 1))
            continue
        v = int(token)
        if v < 0:
            raise ValueError(f"Invalid ERN value: {raw!r}")
        values.add(v)
    return values, want_inf


def prompt_for_n() -> int:
    while True:
        raw = input("Enter maximum n (number of vertices): ").strip()
        try:
            n = int(raw)
        except ValueError:
            print("Please enter an integer.", file=sys.stderr)
            continue
        if n < 1:
            print("Please enter n >= 1.", file=sys.stderr)
            continue
        return n


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)

    labelg_cmd = resolve_tool_command(args.labelg, ("nauty-labelg", "labelg"))
    if labelg_cmd is None:
        print(
            f"Error: `{args.labelg}` not found in PATH (also tried `nauty-labelg`).",
            file=sys.stderr,
        )
        return 2

    needs_geng = args.mode == "global" or args.target_source == "all"
    geng_cmd = ""
    if needs_geng:
        geng_cmd = resolve_tool_command(args.geng, ("nauty-geng", "geng")) or ""
        if not geng_cmd:
            print(
                f"Error: `{args.geng}` not found in PATH (also tried `nauty-geng`).",
                file=sys.stderr,
            )
            return 2

    split_script: Path | None = None
    if args.mode == "local" and args.target_source == "split":
        split_script = (
            Path(args.split_generator).resolve()
            if args.split_generator.strip()
            else default_split_generator_script()
        )
        if not split_script.exists():
            print(
                f"Error: split target generator script not found at `{split_script}`.",
                file=sys.stderr,
            )
            return 2

    nmax = args.n if args.n is not None else prompt_for_n()
    if args.start_n < 1 or args.start_n > nmax:
        print("Error: --start-n must satisfy 1 <= start-n <= n.", file=sys.stderr)
        return 2
    if args.min_edges < 0:
        print("Error: --min-edges must be >= 0.", file=sys.stderr)
        return 2
    if args.jobs < 1:
        print("Error: --jobs must be >= 1.", file=sys.stderr)
        return 2
    if args.chunk_size < 1:
        print("Error: --chunk-size must be >= 1.", file=sys.stderr)
        return 2
    if args.blocker_chunk_size < 1:
        print("Error: --blocker-chunk-size must be >= 1.", file=sys.stderr)
        return 2
    if args.raw_cache_limit < 0:
        print("Error: --raw-cache-limit must be >= 0.", file=sys.stderr)
        return 2
    if args.parent_cache_limit < 0:
        print("Error: --parent-cache-limit must be >= 0.", file=sys.stderr)
        return 2
    if args.checkpoint_every < 1:
        print("Error: --checkpoint-every must be >= 1.", file=sys.stderr)
        return 2
    if args.mode == "local" and args.target_source == "split" and nmax > 16:
        print("Error: split target generation currently supports n <= 16.", file=sys.stderr)
        return 2
    if args.mode == "global" and nmax >= 11:
        print(
            "Warning: exhaustive graph generation grows extremely fast; "
            "n >= 11 may be impractical.",
            file=sys.stderr,
        )
    if args.mode == "local" and args.jobs > 1:
        raw_limit_text = (
            "disabled"
            if not args.raw_card_cache
            else ("unbounded" if args.raw_cache_limit == 0 else str(args.raw_cache_limit))
        )
        parent_limit_text = (
            "unbounded" if args.parent_cache_limit == 0 else str(args.parent_cache_limit)
        )
        print(
            "Warning: local-mode caches are per worker process; memory usage scales with --jobs. "
            f"Current per-worker limits: raw-cache={raw_limit_text}, "
            f"parent-cache={parent_limit_text}.",
            file=sys.stderr,
        )

    try:
        save_values, save_inf = parse_ern_selector(args.save_ern)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.resume and args.mode != "local":
        print("Error: --resume currently supports only --mode local.", file=sys.stderr)
        return 2
    if args.resume and args.target_source != "split":
        print("Error: --resume currently supports only --target-source split.", file=sys.stderr)
        return 2
    if args.checkpoint_file and (args.mode != "local" or args.target_source != "split"):
        print(
            "Error: --checkpoint-file is currently supported only for local split-target runs.",
            file=sys.stderr,
        )
        return 2
    if args.save_csv and not (save_values or save_inf):
        print("Error: --save-csv requires --save-ern.", file=sys.stderr)
        return 2
    save_path = args.save_csv.strip()
    if (save_values or save_inf) and not save_path:
        save_path = "saved_ern_graphs.csv"

    checkpoint_path: Path | None = None
    checkpoint_state: dict[str, Any] | None = None
    use_checkpoint = args.resume or bool(args.checkpoint_file.strip())
    if use_checkpoint:
        checkpoint_path = (
            Path(args.checkpoint_file).resolve()
            if args.checkpoint_file.strip()
            else default_checkpoint_path(
                save_path=save_path,
                mode=args.mode,
                target_source=args.target_source,
                start_n=args.start_n,
                nmax=nmax,
                min_edges=args.min_edges,
            )
        )
        if args.resume:
            if not checkpoint_path.exists():
                print(f"Error: checkpoint file not found: `{checkpoint_path}`.", file=sys.stderr)
                return 2
            try:
                checkpoint_state = load_checkpoint_state(checkpoint_path)
                validate_checkpoint_state(
                    checkpoint=checkpoint_state,
                    mode=args.mode,
                    target_source=args.target_source,
                    start_n=args.start_n,
                    nmax=nmax,
                    min_edges=args.min_edges,
                    save_path=save_path,
                    save_ern=args.save_ern,
                )
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 2
        else:
            checkpoint_state = init_checkpoint_state(
                mode=args.mode,
                target_source=args.target_source,
                start_n=args.start_n,
                nmax=nmax,
                min_edges=args.min_edges,
                save_path=save_path,
                save_ern=args.save_ern,
            )
            save_checkpoint_state(checkpoint_path, checkpoint_state)

    save_file, save_writer = open_save_csv(
        save_path=save_path,
        resume=args.resume,
        expected_rows=int(checkpoint_state.get("saved_rows_total", 0)) if checkpoint_state else 0,
    )

    wall_start = time.time()
    saved_rows = int(checkpoint_state.get("saved_rows_total", 0)) if checkpoint_state else 0

    try:
        if args.mode == "global":
            canonicalizer = NautyCanonicalizer(
                labelg_cmd=labelg_cmd,
                batch_size=args.batch_size,
                jobs=args.labelg_jobs,
                use_raw_cache=args.raw_card_cache,
            )
            try:
                overall_graphs = 0
                overall_split = 0
                overall_hist: Counter = Counter()

                print(
                    f"Running global split-graph ERN scan for "
                    f"n = {args.start_n}..{nmax}, min_edges = {args.min_edges}"
                )
                for n in range(args.start_n, nmax + 1):
                    total_graphs, split_count, hist, records, elapsed = process_n(
                        n=n,
                        geng_cmd=geng_cmd,
                        canonicalizer=canonicalizer,
                        progress_every=args.progress_every,
                        deck_batch_cards=args.deck_batch_cards,
                        min_edges=args.min_edges,
                    )
                    overall_graphs += total_graphs
                    overall_split += split_count
                    overall_hist.update(hist)
                    print_order_summary(n, total_graphs, split_count, hist, records, elapsed)
                    if save_writer is not None:
                        for rec in records:
                            if not rec.is_split:
                                continue
                            if rec.ern is None:
                                if not save_inf:
                                    continue
                                ern_str = "inf"
                            else:
                                if rec.ern not in save_values:
                                    continue
                                ern_str = str(rec.ern)
                            save_writer.writerow([n, rec.edge_count, ern_str, rec.g6])
                            saved_rows += 1

                total_elapsed = time.time() - wall_start
                print()
                print("Overall summary")
                print(f"  processed unlabeled graphs: {overall_graphs}")
                print(f"  processed split graphs: {overall_split}")
                print(f"  overall ern distribution: {format_histogram(overall_hist)}")
                print(
                    "  canonicalization cache stats: "
                    f"hits={canonicalizer.cache_hits}, misses={canonicalizer.cache_misses}, "
                    f"raw-cache-size={len(canonicalizer.raw_cache)}"
                )
                print(f"  unique canonical card types seen: {len(canonicalizer.canon_to_id)}")
                if save_path:
                    print(f"  saved matching graphs: {saved_rows} -> {save_path}")
                print(f"  total elapsed: {total_elapsed:.2f}s")
                return 0
            finally:
                canonicalizer.close()

        overall_split = 0
        overall_hist = Counter()
        if checkpoint_path is not None:
            print(f"Checkpoint file: {checkpoint_path}")
        print(
            f"Running local split-graph ERN scan for n = {args.start_n}..{nmax}, "
            f"target_source = {args.target_source}, jobs = {args.jobs}, "
            f"min_edges = {args.min_edges}"
        )
        for n in range(args.start_n, nmax + 1):
            resume_stats: LocalOrderStats | None = None
            resume_saved_rows = 0
            resume_elapsed = 0.0
            target_file: Path | None = None
            order_state: dict[str, Any] | None = None
            checkpoint_callback: Callable[[LocalOrderStats, int, bool, float], None] | None = None

            if checkpoint_state is not None and checkpoint_path is not None:
                order_state = checkpoint_state["orders"].get(str(n))
                if order_state is not None:
                    resume_stats = stats_from_checkpoint_dict(order_state.get("stats", {}))
                    resume_saved_rows = int(order_state.get("saved_rows", 0))
                    resume_elapsed = float(order_state.get("elapsed", 0.0))
                target_file = checkpoint_target_file(checkpoint_path, n)
                if (not args.resume) or (not target_file.exists()):
                    assert split_script is not None
                    print(
                        f"[n={n}] caching split targets to {target_file}...",
                        file=sys.stderr,
                    )
                    target_count = write_split_target_cache(
                        n=n,
                        split_script=split_script,
                        min_edges=args.min_edges,
                        target_path=target_file,
                    )
                    print(f"[n={n}] cached {target_count} target graphs.", file=sys.stderr)
                if order_state is not None and order_state.get("completed"):
                    stats = resume_stats if resume_stats is not None else LocalOrderStats()
                    overall_split += stats.processed
                    overall_hist.update(stats.hist)
                    print_local_order_summary(n, stats, resume_elapsed, args.target_source)
                    continue
                if resume_stats is not None and resume_stats.processed > 0:
                    print(
                        f"[n={n}] resuming from checkpoint after "
                        f"{resume_stats.processed} processed targets.",
                        file=sys.stderr,
                    )

                last_checkpoint_processed = (
                    resume_stats.processed if resume_stats is not None else 0
                )

                def _emit_checkpoint(
                    stats: LocalOrderStats,
                    saved_for_order: int,
                    completed: bool,
                    elapsed_total: float,
                    n: int = n,
                    target_file: Path | None = target_file,
                ) -> None:
                    nonlocal last_checkpoint_processed, checkpoint_state, saved_rows
                    if checkpoint_state is None or checkpoint_path is None:
                        return
                    if (
                        not completed
                        and (stats.processed - last_checkpoint_processed) < args.checkpoint_every
                    ):
                        return
                    if save_file is not None:
                        safe_fsync(save_file)
                    current_order = checkpoint_state["orders"].setdefault(str(n), {})
                    current_order["completed"] = completed
                    current_order["elapsed"] = elapsed_total
                    current_order["saved_rows"] = saved_for_order
                    current_order["stats"] = stats_to_checkpoint_dict(stats)
                    current_order["target_file"] = (
                        str(target_file) if target_file is not None else ""
                    )
                    checkpoint_state["saved_rows_total"] = sum(
                        int(item.get("saved_rows", 0))
                        for item in checkpoint_state["orders"].values()
                    )
                    checkpoint_state["updated_at"] = time.time()
                    save_checkpoint_state(checkpoint_path, checkpoint_state)
                    saved_rows = int(checkpoint_state["saved_rows_total"])
                    last_checkpoint_processed = stats.processed

                checkpoint_callback = _emit_checkpoint

            stats, elapsed, saved_now = process_n_local(
                n=n,
                labelg_cmd=labelg_cmd,
                geng_cmd=geng_cmd,
                target_source=args.target_source,
                split_script=split_script,
                target_file=target_file,
                progress_every=args.progress_every,
                batch_size=args.batch_size,
                use_raw_cache=args.raw_card_cache,
                raw_cache_limit=args.raw_cache_limit,
                parent_cache_limit=args.parent_cache_limit,
                blocker_chunk_size=args.blocker_chunk_size,
                jobs=args.jobs,
                chunk_size=args.chunk_size,
                min_edges=args.min_edges,
                save_writer=save_writer,
                save_values=save_values,
                save_inf=save_inf,
                resume_stats=resume_stats,
                resume_saved_rows=resume_saved_rows,
                resume_elapsed=resume_elapsed,
                checkpoint_callback=checkpoint_callback,
            )
            overall_split += stats.processed
            overall_hist.update(stats.hist)
            if checkpoint_state is None:
                saved_rows += saved_now
            else:
                saved_rows = int(checkpoint_state.get("saved_rows_total", saved_rows))
            print_local_order_summary(n, stats, elapsed, args.target_source)

        total_elapsed = time.time() - wall_start
        print()
        print("Overall summary")
        print(f"  processed split graphs: {overall_split}")
        print(f"  overall ern distribution: {format_histogram(overall_hist)}")
        if save_path:
            print(f"  saved matching graphs: {saved_rows} -> {save_path}")
        print(f"  total elapsed: {total_elapsed:.2f}s")
        return 0
    finally:
        if save_file is not None:
            save_file.close()
