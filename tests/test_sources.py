"""Tests for source routing (viz.sources), the VizSpec.source field,
parser quality-keyword pinning, and the new ocean gazetteer regions."""

import datetime as dt

import pytest

import viz
from viz import VizSpec
from viz.parser import parse_description
from viz.sources import (default_source, fetch_for_source, is_fetchable,
                         resolve_source)


def _spec(**kw):
    base = dict(title="T", region_key="north-atlantic",
                bbox=(-80.0, 0.0, 20.0, 66.0), variable="sst",
                start=dt.date(2020, 1, 1), end=dt.date(2020, 12, 31),
                cadence="monthly")
    base.update(kw)
    return VizSpec(**base)


# ---------------------------------------------------------------------------
# default_source / resolve_source
# ---------------------------------------------------------------------------

def test_default_source_great_lakes_glsea():
    assert default_source("sst", "lake-superior") == "glsea"
    assert default_source("sst", "lake-erie") == "glsea"


def test_default_source_ocean_oisst():
    assert default_source("sst", "north-atlantic") == "oisst"
    assert default_source("sst", "global") == "oisst"
    assert default_source("sst", "caribbean-sea") == "oisst"


def test_default_source_non_sst_empty():
    assert default_source("currents", "lake-superior") == ""
    assert default_source("chlorophyll", "north-atlantic") == ""


def test_resolve_source_explicit_wins():
    assert resolve_source(_spec(source="mur")) == "mur"
    assert resolve_source(_spec(source="glsea")) == "glsea"
    assert resolve_source(_spec(source="oisst")) == "oisst"


def test_resolve_source_empty_falls_back_to_default():
    assert resolve_source(_spec(source="")) == "oisst"
    assert resolve_source(_spec(source="", region_key="lake-michigan")) == "glsea"


def test_resolve_source_unknown_raises():
    class Fake:
        source = "modis"
        variable = "sst"
        region_key = "north-atlantic"
    with pytest.raises(ValueError, match="unknown source"):
        resolve_source(Fake())


def test_resolve_source_duck_typed():
    class Fake:
        source = ""
        variable = "sst"
        region_key = "indian-ocean"
    assert resolve_source(Fake()) == "oisst"


def test_is_fetchable_spec_aware():
    assert is_fetchable(_spec()) is True            # sst + ocean -> oisst
    assert is_fetchable(_spec(region_key="lake-superior")) is True
    assert is_fetchable(_spec(variable="currents")) is False


def test_fetch_for_source_lazy_imports():
    currents = pytest.importorskip(
        "currents", reason="optional survey-currents peer not installed")
    assert callable(fetch_for_source("oisst"))
    assert callable(fetch_for_source("mur"))
    assert callable(fetch_for_source("glsea"))
    assert currents.__version__ >= "0.3.0"


def test_fetch_for_source_missing_peer_is_actionable(monkeypatch):
    import importlib

    real_import_module = importlib.import_module

    def fake_import_module(name, *args, **kwargs):
        if name.startswith("currents"):
            raise ImportError("No module named 'currents' (simulated)")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    with pytest.raises(ImportError, match="pip install"):
        fetch_for_source("oisst")


def test_fetch_for_source_unknown_raises():
    with pytest.raises(ValueError, match="unknown source"):
        fetch_for_source("modis")


def test_package_exports():
    for name in ("resolve_source", "fetch_for_source", "default_source",
                 "is_source_fetchable", "KNOWN_SOURCES", "SOURCE_LABELS"):
        assert name in viz.__all__
        assert hasattr(viz, name)
    assert viz.__version__ == "0.3.0"


# ---------------------------------------------------------------------------
# VizSpec.source field
# ---------------------------------------------------------------------------

def test_spec_source_defaults_empty():
    assert _spec().source == ""


def test_spec_source_roundtrip():
    s = _spec(source="mur")
    assert VizSpec.from_dict(s.to_dict()).source == "mur"


def test_spec_from_dict_without_source_backward_compatible():
    # a v0.1.0 spec dict has no "source" key
    d = _spec().to_dict()
    del d["source"]
    assert VizSpec.from_dict(d).source == ""


def test_spec_source_invalid_raises():
    with pytest.raises(ValueError, match="not in"):
        _spec(source="modis")


def test_spec_source_normalized():
    assert _spec(source=" MUR ").source == "mur"


# ---------------------------------------------------------------------------
# parser quality keywords
# ---------------------------------------------------------------------------

def test_parser_pins_mur_on_high_resolution():
    s = parse_description("High resolution North Atlantic sea surface temperature over the past 2 years")
    assert s.source == "mur"
    assert s.region_key == "north-atlantic"


def test_parser_pins_mur_on_ultra():
    s = parse_description("Ultra high-res SST in the Mediterranean last summer",
                          today=dt.date(2026, 9, 26))
    assert s.source == "mur"


def test_parser_pins_mur_on_coastal_detail():
    s = parse_description("Caribbean sea surface temperature with coastal detail, 2020 to 2022")
    assert s.source == "mur"


def test_parser_no_quality_keywords_leaves_source_empty():
    s = parse_description("North Atlantic sea surface temperature over the past 2 years")
    assert s.source == ""
    assert resolve_source(s) == "oisst"


def test_parser_quality_keywords_ignored_for_non_sst():
    s = parse_description("High resolution North Atlantic currents over the past 2 years")
    assert s.variable == "currents"
    assert s.source == ""


def test_parser_great_lakes_default_empty_source():
    s = parse_description("Lake Superior surface temperature over the past 5 years")
    assert s.source == ""
    assert resolve_source(s) == "glsea"


# ---------------------------------------------------------------------------
# ocean gazetteer regions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,key", [
    ("Global ocean sea surface temperature last year", "global"),
    ("North Atlantic SST over the past 3 years", "north-atlantic"),
    ("South Atlantic sea surface temperature, 2020 to 2022", "south-atlantic"),
    ("North Pacific SST over the past year", "north-pacific"),
    ("South Pacific temperature last summer", "south-pacific"),
    ("Indian Ocean SST over the past 5 years", "indian-ocean"),
    ("Southern Ocean sea surface temperature", "southern-ocean"),
    ("Arctic Ocean SST, 2015 to 2020", "arctic-ocean"),
    ("Caribbean sea surface temperature over the past 2 years", "caribbean-sea"),
    ("Mediterranean SST last year", "mediterranean-sea"),
])
def test_ocean_regions_parse(text, key):
    s = parse_description(text, today=dt.date(2026, 9, 26))
    assert s.region_key == key
    assert s.variable == "sst"
    assert resolve_source(s) == "oisst"


def test_ocean_region_bboxes_valid():
    from viz import get_region
    for key in ("global", "north-atlantic", "south-atlantic",
                "north-pacific", "south-pacific", "indian-ocean",
                "southern-ocean", "arctic-ocean"):
        r = get_region(key)
        lon_min, lat_min, lon_max, lat_max = r["bbox"]
        assert lon_min < lon_max and lat_min < lat_max
        assert -180.0 <= lon_min <= 180.0 and -180.0 <= lon_max <= 180.0


def test_global_bbox_is_full_globe():
    from viz import get_region
    assert tuple(get_region("global")["bbox"]) == (-180.0, -90.0, 180.0, 90.0)
