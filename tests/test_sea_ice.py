"""Tests for the sea-ice variable, land-ice disambiguation, and the
NSIDC source routing (survey-viz v0.6.0)."""

import datetime as dt

import pytest

import viz
from viz import VizSpec
from viz.parser import parse_description
from viz.sources import (POLAR_REGION_KEYS, default_source, fetch_for_source,
                         is_fetchable, resolve_source)

TODAY = dt.date(2026, 9, 26)


def _parse(text):
    return parse_description(text, today=TODAY)


# ---------------------------------------------------------------------------
# parser: sea-ice variable keywords
# ---------------------------------------------------------------------------

def test_parse_arctic_sea_ice():
    spec = _parse("Arctic Ocean sea ice over the past 5 years")
    assert spec.variable == "sea-ice"
    assert spec.region_key == "arctic-ocean"
    assert spec.cadence == "daily"  # ice moves fast -> daily frames
    assert spec.start == dt.date(2021, 9, 26)
    assert "Sea Ice" in spec.title


def test_parse_southern_ocean_ice_concentration():
    spec = _parse("Southern Ocean ice concentration last summer")
    assert spec.variable == "sea-ice"
    assert spec.region_key == "southern-ocean"
    assert (spec.start, spec.end) == (dt.date(2026, 6, 1), dt.date(2026, 8, 31))


def test_parse_antarctic_pack_ice_alias():
    spec = _parse("Antarctic pack ice this year")
    assert spec.variable == "sea-ice"
    assert spec.region_key == "southern-ocean"


def test_parse_arctic_alias():
    spec = _parse("Arctic ice extent this year")
    assert spec.variable == "sea-ice"
    assert spec.region_key == "arctic-ocean"


def test_parse_sea_ice_keyword_variants():
    for text in ("Arctic Ocean ice cover 2020 to 2022",
                 "Arctic Ocean ice extent 2020 to 2022",
                 "Southern Ocean sea-ice this year"):
        assert _parse(text).variable == "sea-ice"


# ---------------------------------------------------------------------------
# parser: land-ice disambiguation (never routes to sea ice)
# ---------------------------------------------------------------------------

def test_parse_antarctic_ice_sheet_is_land_ice():
    spec = _parse("Antarctic ice sheet this year")
    assert spec.variable == "land-ice"
    assert spec.region_key == "southern-ocean"


def test_parse_land_ice_keyword_variants():
    assert _parse("Southern Ocean glaciers this year").variable == "land-ice"
    assert _parse("Southern Ocean icebergs this year").variable == "land-ice"
    assert _parse("Arctic Ocean land ice this year").variable == "land-ice"


def test_parse_ice_sheet_wins_tie_against_bare_ice():
    # "ice sheet" matches both ``\bice\b`` (sea-ice) and
    # ``ice\s*sheets?`` (land-ice) at the same position; land-ice is
    # listed first so the honest reading wins.
    assert _parse("Antarctic ice sheet extent this year").variable == "land-ice"


def test_parse_sea_ice_not_confused_by_ice_sheet_nearby():
    spec = _parse("Arctic Ocean sea ice and the Greenland ice sheet this year")
    # earliest keyword wins: "sea ice" (position ~13) beats "ice sheet"
    assert spec.variable == "sea-ice"


# ---------------------------------------------------------------------------
# sources: NSIDC routing (polar regions only)
# ---------------------------------------------------------------------------

def test_default_source_sea_ice_polar():
    assert default_source("sea-ice", "arctic-ocean") == "nsidc"
    assert default_source("sea-ice", "southern-ocean") == "nsidc"


def test_default_source_sea_ice_non_polar_empty():
    assert default_source("sea-ice", "north-atlantic") == ""
    assert default_source("sea-ice", "lake-superior") == ""


def test_default_source_land_ice_never_fetchable():
    assert default_source("land-ice", "southern-ocean") == ""
    assert default_source("land-ice", "arctic-ocean") == ""


def test_polar_region_keys():
    assert POLAR_REGION_KEYS == frozenset({"arctic-ocean", "southern-ocean"})


def test_resolve_source_sea_ice_end_to_end():
    spec = _parse("Arctic Ocean sea ice over the past 5 years")
    assert resolve_source(spec) == "nsidc"
    assert is_fetchable(spec) is True


def test_resolve_source_land_ice_unfetchable():
    spec = _parse("Antarctic ice sheet this year")
    assert resolve_source(spec) == ""
    assert is_fetchable(spec) is False


def test_resolve_source_explicit_nsidc():
    spec = _parse("Arctic Ocean sea ice over the past 5 years")
    spec.source = "nsidc"
    assert resolve_source(spec) == "nsidc"


def test_fetch_for_source_nsidc_lazy_imports(monkeypatch):
    import sys, types
    from viz import sources
    calls = []

    def fake_import(name):
        calls.append(name)
        mod = types.ModuleType(name)
        mod.fetch_nsidc_sic = lambda *a, **k: "NSIDC-FIELD"
        return mod

    monkeypatch.setattr("importlib.import_module", fake_import)
    fn = sources.fetch_for_source("nsidc")
    assert fn() == "NSIDC-FIELD"
    assert calls == ["currents.sea_ice"]


def test_fetch_for_source_nsidc_missing_peer_is_actionable(monkeypatch):
    import importlib
    from viz import sources

    def boom(name):
        raise ImportError("nope")

    monkeypatch.setattr(importlib, "import_module", boom)
    with pytest.raises(ImportError, match=r"survey-currents>=0\.7\.0"):
        sources.fetch_for_source("nsidc")


def test_known_variables_and_sources():
    from viz.spec import KNOWN_VARIABLES, KNOWN_SOURCES
    assert "sea-ice" in KNOWN_VARIABLES
    assert "land-ice" in KNOWN_VARIABLES
    assert "nsidc" in KNOWN_SOURCES


def test_spec_validates_sea_ice_and_nsidc():
    spec = _parse("Arctic Ocean sea ice over the past 5 years")
    assert spec.variable == "sea-ice"
    spec.source = "nsidc"  # must not raise
    assert spec.source == "nsidc"


def test_spec_rejects_unknown_source_still():
    import dataclasses
    spec = _parse("Arctic Ocean sea ice over the past 5 years")
    with pytest.raises(ValueError):
        dataclasses.replace(spec, source="modis")
