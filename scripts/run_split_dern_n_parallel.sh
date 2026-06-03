#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPP_BIN="${CPP_BIN:-$(cd "$SCRIPT_DIR/.." && pwd)/cpp/bin}"

if [[ $# -lt 1 || $# -gt 4 ]]; then
  echo "Usage: $0 <n> [min_output_dern=3] [out_csv=results/exceptions_nN.csv] [jobs=\${JOBS:-6}]" >&2
  echo "Env: NO_SHORTG=1 to skip split-graph dedup (disk-saver; counts include duplicates)." >&2
  echo "Env: PROGRESS_EVERY=N to log feed progress (default 0)." >&2
  exit 2
fi

n="$1"
min_output_dern="${2:-3}"
out="${3:-results/exceptions_n${n}.csv}"
jobs="${4:-${JOBS:-6}}"
progress_every="${PROGRESS_EVERY:-0}"

if [[ ! -x "$CPP_BIN/dern" || ! -x "$SCRIPT_DIR/gen_split_n.sh" ]]; then
  echo "Missing tools; build the C++ tools first (run: make -C cpp)" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found (required for parallel fan-out); install python3 or use scripts/run_split_dern_n.sh" >&2
  exit 1
fi

mkdir -p "$(dirname "$out")"

progress_args=()
if [[ "$progress_every" -gt 0 ]]; then
  progress_args=(--progress-every "$progress_every")
fi

"$SCRIPT_DIR/gen_split_n.sh" "$n" - \
  | python3 "$SCRIPT_DIR/dern_parallel.py" \
      --jobs "$jobs" \
      --min-output-dern "$min_output_dern" \
      --out "$out" \
      --dern-bin "$CPP_BIN/dern" \
      "${progress_args[@]}"

echo "Wrote parallel-filtered DERN output to $out (jobs=$jobs)" >&2
