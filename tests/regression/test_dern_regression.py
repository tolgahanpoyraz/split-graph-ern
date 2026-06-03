"""End-to-end checks on the compiled DERN pipeline.

Structural only: every split graph with at least 4 edges gets dern in {1, 2},
and each per-order summary is internally consistent. Skipped unless the C++
tools have been built (`make -C cpp`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness as H  # noqa: E402

pytestmark = pytest.mark.skipif(
    not H.cpp_tools_available(),
    reason="C++ tools not built; run 'make -C cpp' (or 'make build')",
)


@pytest.mark.parametrize("n", [5, 6, 7, 8])
def test_dern_is_one_or_two_when_m_ge_4(n: int) -> None:
    offenders = [r for r in H.dern_rows(n) if r["m"] >= 4 and r["dern"] not in (1, 2)]
    assert not offenders, f"split graphs with m>=4 and dern not in {{1,2}}: {offenders[:5]}"


@pytest.mark.parametrize("n", [4, 5, 6, 7, 8])
def test_dern_summary_is_internally_consistent(n: int) -> None:
    s = H.dern_summary(n)
    assert s["processed"] == s["dern0"] + s["dern1"] + s["dern2"] + s["dern3p"]
    assert s["processed"] > 0
