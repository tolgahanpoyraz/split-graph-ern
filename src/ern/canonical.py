"""Batch canonical labeling of graph6 strings via nauty's ``labelg``.

Two non-isomorphic graphs have different canonical forms, so canonicalizing the
edge-deleted cards turns "are these two cards the same graph?" into a string
comparison. Both canonicalizers batch many graphs into a single ``labelg`` call
to amortize process-launch cost; they differ only in what they hand back:

* :class:`NautyCanonicalizer` assigns each canonical form an integer id (used by
  the global solver, which indexes cards by id).
* :class:`Graph6BatchCanonicalizer` returns canonical graph6 strings directly
  (used by the local solver).
"""

from __future__ import annotations

import subprocess
from collections import OrderedDict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from ern.graph6 import Graph6


def canonicalize_graph6_chunk(
    labelg_cmd: str, chunk: Sequence[Graph6]
) -> list[tuple[Graph6, Graph6]]:
    """Canonicalize one chunk of graph6 strings, returning (raw, canonical) pairs."""
    payload = "\n".join(chunk) + "\n"
    proc = subprocess.run(
        [labelg_cmd, "-q"],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"labelg failed with exit code {proc.returncode}: {proc.stderr.strip()}")
    out = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if len(out) != len(chunk):
        raise RuntimeError(
            f"labelg output line count mismatch: expected {len(chunk)}, got {len(out)}"
        )
    return list(zip(chunk, out, strict=False))


class NautyCanonicalizer:
    """Canonicalize graph6 strings to integer card ids, batching ``labelg`` calls."""

    def __init__(
        self,
        labelg_cmd: str,
        batch_size: int = 5000,
        jobs: int = 1,
        use_raw_cache: bool = False,
    ) -> None:
        self.labelg_cmd = labelg_cmd
        self.batch_size = batch_size
        self.jobs = max(1, jobs)
        self.use_raw_cache = use_raw_cache
        self.raw_cache: dict[Graph6, int] = {}
        self.canon_to_id: dict[Graph6, int] = {}
        self.next_card_id = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self._pool: ThreadPoolExecutor | None = None
        if self.jobs > 1:
            self._pool = ThreadPoolExecutor(max_workers=self.jobs)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def _canonicalize_chunk(self, chunk: Sequence[Graph6]) -> list[tuple[Graph6, Graph6]]:
        payload = "\n".join(chunk) + "\n"
        proc = subprocess.run(
            [self.labelg_cmd, "-q"],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"labelg failed with exit code {proc.returncode}: {proc.stderr.strip()}"
            )
        out = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if len(out) != len(chunk):
            raise RuntimeError(
                f"labelg output line count mismatch: expected {len(chunk)}, got {len(out)}"
            )
        return list(zip(chunk, out, strict=False))

    def _card_id(self, canonical_graph6: Graph6) -> int:
        card_id = self.canon_to_id.get(canonical_graph6)
        if card_id is None:
            card_id = self.next_card_id
            self.next_card_id += 1
            self.canon_to_id[canonical_graph6] = card_id
        return card_id

    def canonicalize_many(self, graph6_list: Sequence[Graph6]) -> dict[Graph6, int]:
        unique = list(dict.fromkeys(graph6_list))
        if not unique:
            return {}

        if self.use_raw_cache:
            missing = [g for g in unique if g not in self.raw_cache]
            self.cache_hits += len(unique) - len(missing)
            self.cache_misses += len(missing)
            if missing:
                chunks = [
                    missing[start : start + self.batch_size]
                    for start in range(0, len(missing), self.batch_size)
                ]
                if self._pool is None or len(chunks) < 2:
                    for chunk in chunks:
                        for raw, canon in self._canonicalize_chunk(chunk):
                            self.raw_cache[raw] = self._card_id(canon)
                else:
                    futures = [
                        self._pool.submit(self._canonicalize_chunk, chunk) for chunk in chunks
                    ]
                    for fut in futures:
                        for raw, canon in fut.result():
                            self.raw_cache[raw] = self._card_id(canon)
            return {g: self.raw_cache[g] for g in unique}

        self.cache_hits += len(graph6_list) - len(unique)
        self.cache_misses += len(unique)
        out: dict[Graph6, int] = {}
        chunks = [
            unique[start : start + self.batch_size]
            for start in range(0, len(unique), self.batch_size)
        ]
        if self._pool is None or len(chunks) < 2:
            for chunk in chunks:
                for raw, canon in self._canonicalize_chunk(chunk):
                    out[raw] = self._card_id(canon)
            return out

        futures = [self._pool.submit(self._canonicalize_chunk, chunk) for chunk in chunks]
        for fut in futures:
            for raw, canon in fut.result():
                out[raw] = self._card_id(canon)
        return out


class Graph6BatchCanonicalizer:
    """Canonicalize graph6 strings to canonical graph6 strings, with an LRU cache."""

    def __init__(
        self,
        labelg_cmd: str,
        batch_size: int = 5000,
        use_raw_cache: bool = False,
        raw_cache_limit: int = 250000,
    ) -> None:
        self.labelg_cmd = labelg_cmd
        self.batch_size = batch_size
        self.use_raw_cache = use_raw_cache
        self.raw_cache_limit = max(0, raw_cache_limit)
        self.raw_cache: OrderedDict[Graph6, Graph6] = OrderedDict()
        self.cache_hits = 0
        self.cache_misses = 0

    def _trim_raw_cache(self) -> None:
        if self.raw_cache_limit <= 0:
            return
        while len(self.raw_cache) > self.raw_cache_limit:
            self.raw_cache.popitem(last=False)

    def canonicalize_many(self, graph6_list: Sequence[Graph6]) -> dict[Graph6, Graph6]:
        unique = list(dict.fromkeys(graph6_list))
        if not unique:
            return {}

        if self.use_raw_cache:
            missing: list[Graph6] = []
            for g in unique:
                if g in self.raw_cache:
                    self.raw_cache.move_to_end(g)
                else:
                    missing.append(g)
            self.cache_hits += len(unique) - len(missing)
            self.cache_misses += len(missing)
            for start in range(0, len(missing), self.batch_size):
                for raw, canon in canonicalize_graph6_chunk(
                    self.labelg_cmd, missing[start : start + self.batch_size]
                ):
                    self.raw_cache[raw] = canon
                    self.raw_cache.move_to_end(raw)
                self._trim_raw_cache()
            return {g: self.raw_cache[g] for g in unique}

        self.cache_hits += len(graph6_list) - len(unique)
        self.cache_misses += len(unique)
        out: dict[Graph6, Graph6] = {}
        for start in range(0, len(unique), self.batch_size):
            for raw, canon in canonicalize_graph6_chunk(
                self.labelg_cmd, unique[start : start + self.batch_size]
            ):
                out[raw] = canon
        return out
