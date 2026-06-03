#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPP_BIN="${CPP_BIN:-$(cd "$SCRIPT_DIR/.." && pwd)/cpp/bin}"

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <n> <out.g6>" >&2
  exit 2
fi

n="$1"
out="$2"

no_shortg="${NO_SHORTG:-${SKIP_SHORTG:-}}"

GENBG_BIN="${GENBG_BIN:-}"
if [[ -z "$GENBG_BIN" ]]; then
  if command -v genbg >/dev/null 2>&1; then
    GENBG_BIN="genbg"
  elif command -v nauty-genbg >/dev/null 2>&1; then
    GENBG_BIN="nauty-genbg"
  else
    echo "genbg not found; install nauty/gtools (macOS: brew install nauty; Ubuntu: apt-get install nauty)" >&2
    exit 1
  fi
fi

SHORTG_BIN="${SHORTG_BIN:-}"
if [[ -z "$SHORTG_BIN" ]]; then
  if [[ -z "$no_shortg" ]]; then
    if command -v shortg >/dev/null 2>&1; then
      SHORTG_BIN="shortg"
    elif command -v nauty-shortg >/dev/null 2>&1; then
      SHORTG_BIN="nauty-shortg"
    else
      echo "shortg not found; install nauty/gtools (macOS: brew install nauty; Ubuntu: apt-get install nauty)" >&2
      exit 1
    fi
  fi
fi

if [[ ! -x "$CPP_BIN/bip2split" ]]; then
  echo "$CPP_BIN/bip2split not built; run 'make -C cpp' (or 'make build') first" >&2
  exit 1
fi

shortg_opts=()
if [[ -n "${SHORTG_OPTS:-}" ]]; then
  # Intended for things like: SHORTG_OPTS="-T/path/to/tmp -Z8G"
  # shellcheck disable=SC2206
  shortg_opts=($SHORTG_OPTS)
fi

mkdir -p "$(dirname "$out")"

if [[ "$n" -lt 1 || "$n" -gt 16 ]]; then
  echo "n must be 1..16 (this pipeline targets n<=15)" >&2
  exit 2
fi

if [[ "$n" -eq 1 ]]; then
  # graph6 for single isolated vertex is '@'
  if [[ "$out" == "-" ]]; then
    printf "@\n"
  else
    printf "@\n" > "$out"
  fi
  exit 0
fi

# Split graphs from bipartite graphs between (k, n-k), then add a clique on the k side.
# We iterate k=1..n-1. (k=0 and k=n only contribute the edgeless/K_n graphs, already covered.)
if [[ "$out" == "-" ]]; then
  if [[ -n "$no_shortg" ]]; then
      for ((k=1; k<=n-1; k++)); do
        "$GENBG_BIN" -q -l "$k" "$((n-k))" | "$CPP_BIN/bip2split" --clique-size "$k"
      done
  else
    if [[ ${#shortg_opts[@]} -eq 0 ]]; then
      for ((k=1; k<=n-1; k++)); do
        "$GENBG_BIN" -q -l "$k" "$((n-k))" | "$CPP_BIN/bip2split" --clique-size "$k"
      done | "$SHORTG_BIN"
    else
      for ((k=1; k<=n-1; k++)); do
        "$GENBG_BIN" -q -l "$k" "$((n-k))" | "$CPP_BIN/bip2split" --clique-size "$k"
      done | "$SHORTG_BIN" "${shortg_opts[@]}"
    fi
  fi
else
  if [[ -n "$no_shortg" ]]; then
    for ((k=1; k<=n-1; k++)); do
      "$GENBG_BIN" -q -l "$k" "$((n-k))" | "$CPP_BIN/bip2split" --clique-size "$k"
    done > "$out"
    echo "Wrote $(wc -l < "$out" | tr -d ' ') split graphs (NOT deduplicated) to $out" >&2
  else
    if [[ ${#shortg_opts[@]} -eq 0 ]]; then
      for ((k=1; k<=n-1; k++)); do
        "$GENBG_BIN" -q -l "$k" "$((n-k))" | "$CPP_BIN/bip2split" --clique-size "$k"
      done | "$SHORTG_BIN" > "$out"
    else
      for ((k=1; k<=n-1; k++)); do
        "$GENBG_BIN" -q -l "$k" "$((n-k))" | "$CPP_BIN/bip2split" --clique-size "$k"
      done | "$SHORTG_BIN" "${shortg_opts[@]}" > "$out"
    fi
    echo "Wrote $(wc -l < "$out" | tr -d ' ') split graphs to $out" >&2
  fi
fi
