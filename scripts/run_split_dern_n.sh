#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPP_BIN="${CPP_BIN:-$(cd "$SCRIPT_DIR/.." && pwd)/cpp/bin}"

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 <n> [min_output_dern=3] [out_csv=results/exceptions_nN.csv]" >&2
  echo "Env: NO_SHORTG=1 to skip split-graph dedup (disk-saver; counts include duplicates)." >&2
  exit 2
fi

n="$1"
min_output_dern="${2:-3}"
out="${3:-results/exceptions_n${n}.csv}"

if [[ ! -x "$CPP_BIN/dern" || ! -x "$SCRIPT_DIR/gen_split_n.sh" ]]; then
  echo "Missing tools; build the C++ tools first (run: make -C cpp)" >&2
  exit 1
fi

mkdir -p "$(dirname "$out")"

"$SCRIPT_DIR/gen_split_n.sh" "$n" - \
  | "$CPP_BIN/dern" --no-header --min-output-dern "$min_output_dern" --summary --progress-every 1000000 \
  > "$out"

echo "Wrote filtered DERN output to $out" >&2
