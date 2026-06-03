"""Function-level tests for the global and local solvers.

Rather than pinning the exact distribution, these check structural properties
and, crucially, that the two independent solvers (exhaustive global vs.
split-target local) agree on the ern distribution for every order they share.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ern import global_solver, local_solver, split
from ern.canonical import NautyCanonicalizer

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BIP2SPLIT = _REPO_ROOT / "cpp" / "bin" / "bip2split"

needs_both = pytest.mark.skipif(
    shutil.which("geng") is None or shutil.which("labelg") is None or not _BIP2SPLIT.exists(),
    reason="needs geng, labelg, and the built split generator (make -C cpp)",
)


def _global_hist(n: int) -> dict[int, int]:
    canon = NautyCanonicalizer("labelg")
    _, _, hist, _, _ = global_solver.process_n(n, "geng", canon, 0, 200000, 4)
    canon.close()
    return dict(hist)


def _local_hist(n: int) -> dict[int, int]:
    stats, _, _ = local_solver.process_n_local(
        n=n,
        labelg_cmd="labelg",
        geng_cmd="geng",
        target_source="split",
        split_script=split.default_split_generator_script(),
        target_file=None,
        progress_every=0,
        batch_size=20000,
        use_raw_cache=False,
        raw_cache_limit=250000,
        parent_cache_limit=50000,
        blocker_chunk_size=256,
        jobs=1,
        chunk_size=32,
        min_edges=4,
        save_writer=None,
        save_values=set(),
        save_inf=False,
    )
    return dict(stats.hist)


@needs_both
@pytest.mark.parametrize("n", [4, 5, 6, 7])
def test_global_and_local_agree_and_are_bounded(n: int) -> None:
    g_hist = _global_hist(n)
    l_hist = _local_hist(n)
    # Two independent methods must produce the same ern distribution.
    assert g_hist == l_hist
    # Every split graph with m >= 4 has a small, finite ern.
    assert set(g_hist) <= {1, 2, 3}
