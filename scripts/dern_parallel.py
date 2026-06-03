#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

SUMMARY_RE = re.compile(
    r"^Summary: processed=(\d+) dern0=(\d+) dern1=(\d+) dern2=(\d+) dern>=3=(\d+)\s*$"
)


@dataclass
class Summary:
    processed: int = 0
    dern0: int = 0
    dern1: int = 0
    dern2: int = 0
    dern3p: int = 0


def parse_summary_line(line: str) -> Summary | None:
    m = SUMMARY_RE.match(line)
    if not m:
        return None
    return Summary(
        processed=int(m.group(1)),
        dern0=int(m.group(2)),
        dern1=int(m.group(3)),
        dern2=int(m.group(4)),
        dern3p=int(m.group(5)),
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Fan-out a graph6 stream across multiple `bin/dern` processes and merge the outputs. "
            "This is a multiprocessing (not multithreading) speedup."
        )
    )
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--min-output-dern", type=int, default=3)
    ap.add_argument(
        "--out", required=True, help="Output CSV (no header) containing only emitted rows."
    )
    ap.add_argument(
        "--dern-bin",
        default="bin/dern",
        help="Path to dern binary (default: bin/dern).",
    )
    ap.add_argument(
        "--keep-parts",
        action="store_true",
        help="Keep per-worker part files next to --out.",
    )
    ap.add_argument(
        "--progress-every",
        type=int,
        default=0,
        help="If >0, print input-line progress every N graphs (to stderr).",
    )
    args = ap.parse_args()

    jobs = max(1, args.jobs)
    dern_bin = args.dern_bin
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    parts = [out_path.with_suffix(out_path.suffix + f".part{i}") for i in range(jobs)]
    for p in parts:
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    summaries: list[Summary | None] = [None] * jobs

    def stderr_reader(idx: int, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            try:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
            except Exception:
                line = "<stderr decode error>"
            s = parse_summary_line(line)
            if s is not None:
                summaries[idx] = s
            else:
                sys.stderr.write(f"[dern#{idx}] {line}\n")

    procs: list[subprocess.Popen[bytes]] = []
    part_files = []
    threads: list[threading.Thread] = []

    for i in range(jobs):
        f = open(parts[i], "wb")
        part_files.append(f)
        proc = subprocess.Popen(
            [dern_bin, "--no-header", "--min-output-dern", str(args.min_output_dern), "--summary"],
            stdin=subprocess.PIPE,
            stdout=f,
            stderr=subprocess.PIPE,
        )
        procs.append(proc)
        t = threading.Thread(target=stderr_reader, args=(i, proc), daemon=True)
        t.start()
        threads.append(t)

    # Fan-out input stream round-robin.
    total_in = 0
    try:
        for line in sys.stdin.buffer:
            procs[total_in % jobs].stdin.write(line)  # type: ignore[union-attr]
            total_in += 1
            if args.progress_every > 0 and (total_in % args.progress_every) == 0:
                sys.stderr.write(f"[dern_parallel] fed {total_in} graphs\n")
    except BrokenPipeError:
        # One of the workers died; we'll surface it in return codes below.
        pass

    for proc in procs:
        if proc.stdin is not None:
            proc.stdin.close()

    rc = 0
    for i, proc in enumerate(procs):
        proc.wait()
        if proc.returncode != 0:
            sys.stderr.write(f"[dern_parallel] worker {i} exited {proc.returncode}\n")
            rc = 1

    for t in threads:
        t.join(timeout=5)

    for f in part_files:
        f.close()

    # Merge parts.
    with open(out_path, "wb") as out_f:
        for p in parts:
            if p.exists():
                with open(p, "rb") as in_f:
                    shutil.copyfileobj(in_f, out_f)

    # Aggregate summary.
    agg = Summary()
    missing = 0
    for s in summaries:
        if s is None:
            missing += 1
            continue
        agg.processed += s.processed
        agg.dern0 += s.dern0
        agg.dern1 += s.dern1
        agg.dern2 += s.dern2
        agg.dern3p += s.dern3p

    sys.stderr.write(
        f"[dern_parallel] Summary: fed={total_in} processed={agg.processed} "
        f"dern0={agg.dern0} dern1={agg.dern1} dern2={agg.dern2} dern>=3={agg.dern3p}\n"
    )
    if missing:
        sys.stderr.write(f"[dern_parallel] Warning: missing {missing} worker summaries\n")
        rc = 1

    if not args.keep_parts:
        for p in parts:
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
