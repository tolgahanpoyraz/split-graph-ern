"""Tests for :mod:`ern.canonical`.

The canonicalizers wrap nauty's ``labelg``; the properties worth checking are
that isomorphic graphs map to one form, non-isomorphic graphs do not, and
canonicalization is idempotent. Sample graphs come from geng (see conftest).
"""

from __future__ import annotations

import shutil

import pytest

from ern import canonical

pytestmark = pytest.mark.skipif(shutil.which("labelg") is None, reason="nauty labelg not on PATH")


def test_chunk_preserves_order_and_length(sample_graph6: list[str]) -> None:
    pairs = canonical.canonicalize_graph6_chunk("labelg", sample_graph6)
    assert [raw for raw, _ in pairs] == sample_graph6


def test_canonicalization_is_idempotent(sample_graph6: list[str]) -> None:
    canon = canonical.Graph6BatchCanonicalizer("labelg")
    forms = sorted(set(canon.canonicalize_many(sample_graph6).values()))
    again = canonical.Graph6BatchCanonicalizer("labelg").canonicalize_many(forms)
    for form in forms:
        assert again[form] == form


def test_isomorphic_graphs_share_canonical_form() -> None:
    mapping = canonical.Graph6BatchCanonicalizer("labelg").canonicalize_many(["Bw", "Bw"])
    assert len(set(mapping.values())) == 1


def test_non_isomorphic_graphs_differ() -> None:
    # K4 (6 edges) and C4 (4 edges) cannot share a canonical form.
    mapping = canonical.Graph6BatchCanonicalizer("labelg").canonicalize_many(["C~", "Cl"])
    assert mapping["C~"] != mapping["Cl"]


def test_nauty_canonicalizer_assigns_stable_ids(sample_graph6: list[str]) -> None:
    canon = canonical.NautyCanonicalizer("labelg", jobs=1)
    ids = canon.canonicalize_many(sample_graph6)
    # Same input twice -> identical id mapping; ids are dense from 0.
    assert canon.canonicalize_many(sample_graph6) == ids
    assert set(ids.values()) == set(range(len(set(ids.values()))))
