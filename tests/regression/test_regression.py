"""End-to-end checks on the ERN CLI.

These exercise the whole pipeline as a black box and verify two things without
pinning any specific result:

1. every split graph with at least 4 edges gets a small, finite ern; and
2. the exhaustive ``global`` mode and the split-target ``local`` mode produce
   the same ern distribution (a strong cross-implementation correctness check).

Larger orders are gated behind ``--run-slow``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as H  # noqa: E402

needs_cpp = pytest.mark.skipif(
    not H.cpp_tools_available(),
    reason="C++ tools not built; run 'make -C cpp' (or 'make build')",
)


@pytest.fixture(scope="module")
def global_rows(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, str]]:
    out = tmp_path_factory.mktemp("reg") / "ern_global.csv"
    H.run_ern_csv(8, out, mode="global")
    return H.read_rows(out)


@pytest.fixture(scope="module")
def local_rows(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, str]]:
    out = tmp_path_factory.mktemp("reg") / "ern_local.csv"
    H.run_ern_csv(8, out, mode="local", target_source="split", min_edges=4, extra=["--jobs", "1"])
    return H.read_rows(out)


@pytest.mark.parametrize("n", [4, 5, 6, 7, 8])
def test_global_ern_is_small_and_finite(global_rows: list[dict[str, str]], n: int) -> None:
    hist = H.histogram_by_order(global_rows)[n]
    assert set(hist) <= {"1", "2", "3"}, dict(hist)


@needs_cpp
def test_global_and_local_distributions_match(
    global_rows: list[dict[str, str]], local_rows: list[dict[str, str]]
) -> None:
    g = {n: dict(c) for n, c in H.histogram_by_order(global_rows).items()}
    local = {n: dict(c) for n, c in H.histogram_by_order(local_rows).items()}
    assert g == local


@pytest.mark.slow
@needs_cpp
@pytest.mark.parametrize("n", [9, 10])
def test_local_ern_is_small_and_finite_large(
    tmp_path_factory: pytest.TempPathFactory, n: int
) -> None:
    out = tmp_path_factory.mktemp("reg") / f"ern_local_n{n}.csv"
    H.run_ern_csv(n, out, mode="local", target_source="split", min_edges=4, extra=["--jobs", "4"])
    hist = H.histogram_by_order(H.read_rows(out))[n]
    assert set(hist) <= {"1", "2", "3"}, dict(hist)
