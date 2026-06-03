"""Checkpointing, resumable CSV output, and split-target caching.

Large local-mode runs take hours, so progress is checkpointed: the per-order
statistics, the number of saved CSV rows, and a cached list of target graphs are
written atomically (temp file + ``os.replace``) and validated on resume so a run
cannot silently continue against a mismatched configuration.
"""

from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from typing import Any, TextIO

from ern.graph6 import decode_graph6_with_edge_bits
from ern.split import iter_split_graphs
from ern.stats import LocalErnResult, LocalOrderStats

# A csv.writer instance; its concrete runtime type (_csv.writer) is not importable.
CsvWriter = Any

CSV_HEADER = ["n", "m", "ern", "g6"]
CHECKPOINT_VERSION = 1


def safe_fsync(fh: TextIO) -> None:
    fh.flush()
    try:
        os.fsync(fh.fileno())
    except OSError:
        pass


def stats_to_checkpoint_dict(stats: LocalOrderStats) -> dict[str, Any]:
    return {
        "processed": stats.processed,
        "hist": {str(key): int(value) for key, value in stats.hist.items()},
        "finite_sum": stats.finite_sum,
        "finite_count": stats.finite_count,
        "min_ern": stats.min_ern,
        "max_ern": stats.max_ern,
        "max_examples": list(stats.max_examples),
        "inf_examples": list(stats.inf_examples),
    }


def stats_from_checkpoint_dict(data: dict[str, Any]) -> LocalOrderStats:
    from collections import Counter

    hist: Counter = Counter()
    for raw_key, raw_value in data.get("hist", {}).items():
        key: Any = "inf" if raw_key == "inf" else int(raw_key)
        hist[key] = int(raw_value)
    return LocalOrderStats(
        processed=int(data.get("processed", 0)),
        hist=hist,
        finite_sum=int(data.get("finite_sum", 0)),
        finite_count=int(data.get("finite_count", 0)),
        min_ern=data.get("min_ern"),
        max_ern=data.get("max_ern"),
        max_examples=list(data.get("max_examples", [])),
        inf_examples=list(data.get("inf_examples", [])),
    )


def default_checkpoint_path(
    save_path: str,
    mode: str,
    target_source: str,
    start_n: int,
    nmax: int,
    min_edges: int,
) -> Path:
    if save_path:
        save_file = Path(save_path).resolve()
        return save_file.with_name(f"{save_file.stem}.checkpoint.json")
    return (
        Path.cwd()
        / f"ern_checkpoint_{mode}_{target_source}_n{start_n}_to_{nmax}_min{min_edges}.json"
    )


def checkpoint_target_file(checkpoint_path: Path, n: int) -> Path:
    return checkpoint_path.with_name(f"{checkpoint_path.stem}.n{n}.targets.g6")


def init_checkpoint_state(
    mode: str,
    target_source: str,
    start_n: int,
    nmax: int,
    min_edges: int,
    save_path: str,
    save_ern: str,
) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "mode": mode,
        "target_source": target_source,
        "start_n": start_n,
        "nmax": nmax,
        "min_edges": min_edges,
        "save_csv": save_path,
        "save_ern": save_ern.strip(),
        "saved_rows_total": 0,
        "orders": {},
        "updated_at": time.time(),
    }


def save_checkpoint_state(checkpoint_path: Path, state: dict[str, Any]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = checkpoint_path.with_name(f"{checkpoint_path.name}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
        safe_fsync(fh)
    os.replace(tmp_path, checkpoint_path)


def load_checkpoint_state(checkpoint_path: Path) -> dict[str, Any]:
    with open(checkpoint_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("version") != CHECKPOINT_VERSION:
        raise ValueError(
            f"Unsupported checkpoint version {data.get('version')!r}; "
            f"expected {CHECKPOINT_VERSION}."
        )
    if not isinstance(data.get("orders"), dict):
        raise ValueError("Checkpoint file is missing its `orders` mapping.")
    return data


def validate_checkpoint_state(
    checkpoint: dict[str, Any],
    mode: str,
    target_source: str,
    start_n: int,
    nmax: int,
    min_edges: int,
    save_path: str,
    save_ern: str,
) -> None:
    expected = {
        "mode": mode,
        "target_source": target_source,
        "start_n": start_n,
        "nmax": nmax,
        "min_edges": min_edges,
        "save_csv": save_path,
        "save_ern": save_ern.strip(),
    }
    mismatches = [
        f"{key}: checkpoint={checkpoint.get(key)!r}, current={value!r}"
        for key, value in expected.items()
        if checkpoint.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            "Checkpoint does not match the current run configuration:\n  " + "\n  ".join(mismatches)
        )


def count_csv_data_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return 0
        if header != CSV_HEADER:
            raise ValueError(f"Unexpected CSV header in `{path}`: {header!r}")
        return sum(1 for row in reader if row)


def truncate_csv_data_rows(path: Path, keep_rows: int) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    kept = 0
    with (
        open(path, newline="", encoding="utf-8") as src,
        open(tmp_path, "w", newline="", encoding="utf-8", buffering=1) as dst,
    ):
        reader = csv.reader(src)
        writer = csv.writer(dst)
        try:
            header = next(reader)
        except StopIteration:
            header = []
        if header and header != CSV_HEADER:
            raise ValueError(f"Unexpected CSV header in `{path}`: {header!r}")
        writer.writerow(CSV_HEADER)
        for row in reader:
            if not row:
                continue
            if kept >= keep_rows:
                break
            writer.writerow(row)
            kept += 1
        safe_fsync(dst)
    os.replace(tmp_path, path)


def open_save_csv(
    save_path: str,
    resume: bool,
    expected_rows: int,
) -> tuple[TextIO | None, CsvWriter | None]:
    if not save_path:
        return None, None

    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if resume:
        if not path.exists():
            if expected_rows != 0:
                raise ValueError(
                    f"Cannot resume: expected {expected_rows} saved rows in "
                    f"`{path}`, but the file is missing."
                )
            with open(path, "w", newline="", encoding="utf-8", buffering=1) as fh:
                csv.writer(fh).writerow(CSV_HEADER)
                safe_fsync(fh)
        else:
            actual_rows = count_csv_data_rows(path)
            if actual_rows < expected_rows:
                raise ValueError(
                    f"Cannot resume: checkpoint expects {expected_rows} saved rows in `{path}`, "
                    f"but only found {actual_rows}."
                )
            if actual_rows > expected_rows:
                truncate_csv_data_rows(path, expected_rows)
            elif path.stat().st_size == 0:
                with open(path, "w", newline="", encoding="utf-8", buffering=1) as fh:
                    csv.writer(fh).writerow(CSV_HEADER)
                    safe_fsync(fh)
        fh = open(path, "a", newline="", encoding="utf-8", buffering=1)
        return fh, csv.writer(fh)

    fh = open(path, "w", newline="", encoding="utf-8", buffering=1)
    writer = csv.writer(fh)
    writer.writerow(CSV_HEADER)
    safe_fsync(fh)
    return fh, writer


def write_split_target_cache(
    n: int,
    split_script: Path,
    min_edges: int,
    target_path: Path,
) -> int:
    """Materialize the split targets for order ``n`` to a g6 cache file."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f"{target_path.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    count = 0
    with open(tmp_path, "w", encoding="utf-8", buffering=1, newline="\n") as fh:
        for g6 in iter_split_graphs(n, split_script):
            if min_edges > 0:
                _, _, _, edge_positions = decode_graph6_with_edge_bits(g6)
                if len(edge_positions) < min_edges:
                    continue
            fh.write(g6)
            fh.write("\n")
            count += 1
        safe_fsync(fh)

    os.replace(tmp_path, target_path)
    return count


def write_saved_result(
    save_writer: CsvWriter | None,
    save_values: set[int],
    save_inf: bool,
    n: int,
    result: LocalErnResult,
) -> int:
    """Write one result row if its ern matches the save selector; return rows written."""
    if save_writer is None:
        return 0
    if result.ern is None:
        if not save_inf:
            return 0
        ern_str = "inf"
    else:
        if result.ern not in save_values:
            return 0
        ern_str = str(result.ern)
    save_writer.writerow([n, result.edge_count, ern_str, result.g6])
    return 1
