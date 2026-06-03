"""Locating and invoking nauty/gtools command-line programs.

The pipeline shells out to nauty's generators rather than reimplementing them.
:func:`resolve_tool_command` copes with the Debian/Ubuntu habit of prefixing the
binaries (``nauty-geng`` etc.); :func:`run_geng` streams all unlabeled graphs of
a given order, optionally restricted by edge count.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable, Sequence

from ern.graph6 import Graph6


def resolve_tool_command(user_cmd: str, fallback_names: Sequence[str]) -> str | None:
    """Resolve a tool executable name/path.

    First try the user-provided command; if it is not on ``PATH``, try known
    distro-specific fallback names (e.g. ``nauty-geng``). Returns ``None`` if
    nothing is found.
    """
    if shutil.which(user_cmd) is not None:
        return user_cmd
    for name in fallback_names:
        if shutil.which(name) is not None:
            return name
    return None


def run_geng(geng_cmd: str, n: int, min_edges: int = 0) -> Iterable[Graph6]:
    """Stream graph6 representatives of all unlabeled graphs on ``n`` vertices.

    If ``min_edges`` is positive, only graphs with at least that many edges are
    generated (via geng's edge-count range argument).
    """
    max_edges = n * (n - 1) // 2
    if min_edges > max_edges:
        return

    cmd = [geng_cmd, "-q", str(n)]
    if min_edges > 0:
        cmd.append(f"{min_edges}:{max_edges}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        g = line.strip()
        if g:
            yield g
    stderr = ""
    if proc.stderr is not None:
        stderr = proc.stderr.read().strip()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"geng failed for n={n} with exit code {rc}: {stderr}")
