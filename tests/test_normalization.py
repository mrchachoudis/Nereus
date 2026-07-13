"""Tests for the spatial and taxonomy normalization utilities (DESIGN_PROMPT §6, §15)."""

from __future__ import annotations

import pytest

from fra.models import SpatialUnit
from fra.spatial import area_contains, label_for_fao_area, normalize_area
from fra.taxonomy import TaxonomyResolver

# -- spatial -----------------------------------------------------------------


def test_normalize_fao_code_embedded_in_text() -> None:
    su = normalize_area("Landings from FAO 37.2.1")
    assert su.fao_area == "37.2.1"
    assert su.gsa is None
    assert "Mediterranean" in su.label


def test_normalize_gsa_maps_to_fao_subarea() -> None:
    su = normalize_area("GSA 17")
    assert su.fao_area == "37.2.1"
    assert su.gsa == "17"


def test_normalize_gsa_explicit_arg() -> None:
    su = normalize_area("some Adriatic dataset", gsa="18")
    assert su.fao_area == "37.2.1"
    assert su.gsa == "18"


def test_normalize_unknown_gsa_raises() -> None:
    with pytest.raises(ValueError, match="unknown GFCM GSA"):
        normalize_area("GSA 99")


def test_normalize_no_code_raises() -> None:
    with pytest.raises(ValueError, match="no FAO area or GSA"):
        normalize_area("the North Sea somewhere")


def test_label_for_fao_area_nested() -> None:
    assert "subarea 37.2" in label_for_fao_area("37.2")
    assert label_for_fao_area("27") == "Atlantic, Northeast"


def test_area_contains_nesting() -> None:
    parent = SpatialUnit(fao_area="37", label="Med")
    child = SpatialUnit(fao_area="37.2.1", label="Adriatic")
    other = SpatialUnit(fao_area="27", label="NE Atlantic")
    assert area_contains(parent, child)
    assert area_contains(parent, parent)
    assert not area_contains(child, parent)
    assert not area_contains(parent, other)


# -- taxonomy ----------------------------------------------------------------


def test_taxonomy_resolves_seed_species_offline(tmp_path) -> None:
    resolver = TaxonomyResolver(tmp_path, allow_network=False)
    taxon = resolver.resolve("Merluccius merluccius")
    assert taxon.aphia_id == 126484
    assert taxon.common_name == "European hake"


def test_taxonomy_unknown_species_returns_valid_taxon(tmp_path) -> None:
    resolver = TaxonomyResolver(tmp_path, allow_network=False)
    taxon = resolver.resolve("Fakus fictus")
    assert taxon.scientific_name == "Fakus fictus"
    assert taxon.aphia_id is None  # unresolved, but never blocks the pipeline


def test_taxonomy_is_case_insensitive_and_cached(tmp_path) -> None:
    resolver = TaxonomyResolver(tmp_path, allow_network=False)
    a = resolver.resolve("GADUS MORHUA")
    b = resolver.resolve("gadus morhua")
    assert a.aphia_id == b.aphia_id == 126436
    # cache file was written to disk
    assert (tmp_path / "taxonomy.json").exists()


def test_taxonomy_disk_cache_persists_across_instances(tmp_path) -> None:
    TaxonomyResolver(tmp_path, allow_network=False).resolve("Sardina pilchardus")
    # a fresh resolver (network disabled) still finds it via the disk cache
    fresh = TaxonomyResolver(tmp_path, allow_network=False)
    assert fresh.resolve("Sardina pilchardus").aphia_id == 126421
