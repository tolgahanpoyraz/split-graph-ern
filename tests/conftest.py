"""Shared pytest configuration and fixtures.

Slow checks (large vertex counts) run only when ``--run-slow`` is passed. The
``sample_graph6`` fixture provides a corpus of small graphs straight from geng,
so the graph6/canonical tests need no committed graph data.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="run slow checks (large vertex counts)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="needs --run-slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture(scope="session")
def sample_graph6() -> list[str]:
    """All graph6 strings for 2 <= n <= 6, generated on the fly with geng."""
    if shutil.which("geng") is None:
        pytest.skip("nauty geng not on PATH")
    graphs: list[str] = []
    for n in range(2, 7):
        out = subprocess.run(
            ["geng", "-q", str(n)], check=True, capture_output=True, text=True
        ).stdout
        graphs.extend(line.strip() for line in out.splitlines() if line.strip())
    return graphs
