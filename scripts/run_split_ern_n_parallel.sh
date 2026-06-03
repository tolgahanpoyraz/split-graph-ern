#!/usr/bin/env bash
set -euo pipefail

# Convenience wrapper for the scalable local ERN path. Requires the `ern`
# package to be importable: run `uv sync` first and invoke inside the project
# environment (e.g. `uv run scripts/run_split_ern_n_parallel.sh ...`), or set
# PYTHON to a interpreter that has the package installed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

if [[ $# -lt 1 || $# -gt 4 ]]; then
  echo "Usage: $0 <n> [jobs=\${JOBS:-6}] [save_ern=] [out_csv=results/ern_nN.csv]" >&2
  exit 2
fi

n="$1"
jobs="${2:-${JOBS:-6}}"
save_ern="${3:-}"
out_csv="${4:-results/ern_n${n}.csv}"

if [[ ! -x "$SCRIPT_DIR/gen_split_n.sh" ]]; then
  echo "Missing $SCRIPT_DIR/gen_split_n.sh" >&2
  exit 1
fi

cmd=(
  "$PYTHON"
  -m ern
  "$n"
  --mode local
  --target-source split
  --split-generator "$SCRIPT_DIR/gen_split_n.sh"
  --jobs "$jobs"
)

if [[ -n "$save_ern" ]]; then
  mkdir -p "$(dirname "$out_csv")"
  cmd+=(--save-ern "$save_ern" --save-csv "$out_csv")
fi

"${cmd[@]}"
