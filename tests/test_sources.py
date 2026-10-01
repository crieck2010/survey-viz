"""Tests for source routing (viz.sources), the VizSpec.source field,
parser quality-keyword pinning, and the new ocean gazetteer regions."""

import datetime as dt
from pathlib import Path

import pytest

import viz
from viz import VizSpec
from viz.parser import parse_description
from viz.sources import (KNOWN_SOURCES, SOURCE_LABELS, default_source,
                         fetch_for_source, is_fetchable, resolve_source)


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
    assert default_source("chlorophyll", "north-atlantic") == "oceancolor"  # legacy alias -> ocean color


def test_default_source_currents_oscar():
    assert default_source("currents", "north-atlantic") == "oscar"
    assert default_source("currents", "gulf-stream") == "oscar"
    assert default_source("currents", "caribbean-sea") == "oscar"
    # Great Lakes currents stay an honest refusal.
    assert default_source("currents", "lake-michigan") == ""


def test_resolve_source_currents_oscar():
    s = _spec(variable="currents", source="")
    assert resolve_source(s) == "oscar"
    assert resolve_source(_spec(variable="currents", source="cmems-currents")) == "cmems-currents"
    assert resolve_source(_spec(variable="currents", region_key="lake-erie", source="")) == ""


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
    assert is_fetchable(_spec(variable="currents")) is True   # -> oscar
    assert is_fetchable(_spec(variable="currents", region_key="lake-superior")) is False


def _version_tuple(version: str):
    return tuple(int(p) for p in version.split(".")[:3])


def test_fetch_for_source_lazy_imports():
    currents = pytest.importorskip(
        "currents", reason="optional survey-currents peer not installed")
    assert callable(fetch_for_source("oisst"))
    assert callable(fetch_for_source("mur"))
    assert callable(fetch_for_source("glsea"))
    assert _version_tuple(currents.__version__) >= (0, 5, 0)


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
                 "is_source_fetchable", "KNOWN_SOURCES", "SOURCE_LABELS",
                 "CURATED_CMAPS"):
        assert name in viz.__all__
        assert hasattr(viz, name)
    # __version__ comes from installed distribution metadata, which is
    # written from pyproject.toml at install time — compare against the
    # declared version so this test cannot go stale. Skipped when the
    # package isn't installed (dev checkout without `pip install`).
    import tomllib
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    declared = tomllib.load(open(pyproject, "rb"))["project"]["version"]
    if viz.__version__ == "0.0.0+unknown":
        pytest.skip("survey-viz not installed; version check needs install")
    assert viz.__version__ == declared


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
    assert s.source == "cmems-currents"  # quality pin applies to currents
    s = parse_description("High resolution North Atlantic winds over the past 2 years")
    assert s.variable == "wind"
    assert s.source == ""  # quality keywords ignored for non-sst/non-currents


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


# ---------------------------------------------------------------------------
# oscar / cmems-currents routing (v0.4.0)
# ---------------------------------------------------------------------------

def test_fetch_for_source_currents_lazy_imports():
    currents = pytest.importorskip(
        "currents", reason="optional survey-currents peer not installed")
    assert callable(fetch_for_source("oscar"))
    assert callable(fetch_for_source("cmems-currents"))
    assert _version_tuple(currents.__version__) >= (0, 5, 0)


def test_source_labels_for_currents():
    from viz.sources import SOURCE_LABELS
    assert SOURCE_LABELS["oscar"] == "NASA PODAAC OSCAR v2.0"
    assert "CMEMS" in SOURCE_LABELS["cmems-currents"]


def test_parser_currents_examples():
    s = parse_description("North Atlantic currents over the past year")
    assert s.variable == "currents" and s.region_key == "north-atlantic"
    assert s.source == "" and resolve_source(s) == "oscar"

    s = parse_description("Gulf Stream eddies last summer")
    assert s.variable == "currents" and s.region_key == "gulf-stream"
    assert s.source == "" and resolve_source(s) == "oscar"

    s = parse_description("ultra high resolution currents in the Caribbean")
    assert s.variable == "currents" and s.region_key == "caribbean-sea"
    assert s.source == "cmems-currents"


def test_parser_eddy_keyword_maps_to_currents():
    s = parse_description("Gulf Stream eddy tracking last summer")
    assert s.variable == "currents"


def test_gulf_stream_region_registered():
    from viz.gazetteer import get_region, load_regions
    r = get_region("gulf-stream")
    assert r is not None
    assert tuple(r["bbox"]) == (-81.0, 25.0, -55.0, 43.0)
    # 40 regions: 35 from v0.4.0 + 5 fire regions (v0.5.0).
    assert len(load_regions()) == 42


# --- FIRMS / fire routing (v0.5.0) ----------------------------------------------

def test_default_source_fire_any_region():
    from viz.sources import default_source
    assert default_source("fire", "california") == "firms"
    assert default_source("fire", "amazon-basin") == "firms"
    assert default_source("fire", "global") == "firms"
    assert default_source("fire", "lake-superior") == "firms"


def test_default_source_burn_scar_empty():
    from viz.sources import default_source
    assert default_source("burn-scar", "california") == ""
    assert default_source("chlorophyll", "california") == "oceancolor"  # legacy alias -> ocean color


def test_resolve_source_fire_firms():
    from viz.sources import resolve_source
    from viz.spec import VizSpec
    import datetime as dt
    spec = VizSpec(
        title="t", region_key="california",
        bbox=(-124.5, 32.5, -114.0, 42.0), variable="fire",
        start=dt.date(2020, 1, 1), end=dt.date(2022, 12, 31),
        cadence="daily", layout="reel-vertical", style="reel-dark",
        source="", overlays=(),
    )
    assert resolve_source(spec) == "firms"


def test_is_fetchable_fire():
    from viz.sources import is_fetchable
    from viz.spec import VizSpec
    import datetime as dt
    def _spec(variable):
        return VizSpec(
            title="t", region_key="california",
            bbox=(-124.5, 32.5, -114.0, 42.0), variable=variable,
            start=dt.date(2020, 1, 1), end=dt.date(2022, 12, 31),
            cadence="daily", layout="reel-vertical", style="reel-dark",
            source="", overlays=(),
        )
    assert is_fetchable(_spec("fire"))
    assert not is_fetchable(_spec("burn-scar"))


def test_source_labels_for_firms():
    from viz.sources import SOURCE_LABELS
    assert SOURCE_LABELS["firms"] == "NASA FIRMS"


def test_fetch_for_source_firms_lazy_imports(monkeypatch):
    import sys, types
    from viz import sources
    calls = []

    def fake_import(name):
        calls.append(name)
        mod = types.ModuleType(name)
        mod.fetch_firms = lambda *a, **k: "FIRMS-FIELD"
        return mod

    monkeypatch.setattr("importlib.import_module", fake_import)
    fn = sources.fetch_for_source("firms")
    assert fn() == "FIRMS-FIELD"
    assert calls == ["currents.fires"]


def test_fetch_for_source_firms_missing_peer_is_actionable(monkeypatch):
    import importlib
    from viz import sources

    def boom(name):
        raise ImportError("No module named 'currents'")

    monkeypatch.setattr(importlib, "import_module", boom)
    with __import__("pytest").raises(ImportError) as excinfo:
        sources.fetch_for_source("firms")
    msg = str(excinfo.value)
    assert "survey-currents>=0.6.0" in msg
    assert "currents.fires.fetch_firms" in msg


def test_known_variables_include_fire_and_burn_scar():
    from viz.spec import KNOWN_VARIABLES, KNOWN_SOURCES
    assert "fire" in KNOWN_VARIABLES
    assert "burn-scar" in KNOWN_VARIABLES
    assert "firms" in KNOWN_SOURCES


def test_gfs_wind_source_registered():
    # survey-viz 0.25.0: the keyless GFS wind source is a known,
    # explicitly-pinnable source; the wind default stays era5.
    assert "gfs-wind" in KNOWN_SOURCES
    assert SOURCE_LABELS["gfs-wind"] == "NOAA GFS 10m winds (NOMADS), keyless"
    assert resolve_source(_spec(source="gfs-wind")) == "gfs-wind"
    assert resolve_source(_spec(source="", variable="wind")) == "era5"


def test_gfs_wind_spec_source_validates():
    spec = _spec(region_key="north-america", variable="wind",
                 bbox=(-130.0, 25.0, -65.0, 50.0), source="gfs-wind")
    assert spec.source == "gfs-wind"


def test_ofs_thredds_source_registered():
    # survey-viz 0.26.0: the keyless NOAA OFS THREDDS source is a known,
    # explicitly-pinnable source; the currents default stays oscar.
    assert "ofs-thredds" in KNOWN_SOURCES
    assert (SOURCE_LABELS["ofs-thredds"]
            == "NOAA OFS surface currents (CO-OPS THREDDS), keyless")
    assert resolve_source(_spec(source="ofs-thredds")) == "ofs-thredds"
    assert resolve_source(_spec(source="", variable="currents")) == "oscar"


def test_ofs_thredds_spec_source_validates():
    spec = _spec(region_key="north-atlantic", variable="currents",
                 bbox=(-74.0, 38.0, -73.0, 39.0), source="ofs-thredds")
    assert spec.source == "ofs-thredds"


def test_ofs_thredds_min_version_pin():
    # The honest upgrade message names survey-currents>=0.18.0.
    from viz.sources import _SOURCE_MIN_VERSIONS
    assert _SOURCE_MIN_VERSIONS["ofs-thredds"] == "0.18.0"


def test_ofs_thredds_fetch_adapter_wire(monkeypatch):
    """fetch_for_source("ofs-thredds") returns the THREDDS adapter with
    its own call convention: the caller pins the OFS model code FIRST
    (explicit pin — no silent global default), then (bbox, start, end).
    No network: the currents.ofs_thredds module is faked in sys.modules.
    """
    import sys
    import types

    mod = types.ModuleType("currents.ofs_thredds")
    calls = {}

    def fake_fetch(ofs_code, bbox, start, end, **kw):
        calls.update(ofs_code=ofs_code, bbox=bbox, start=start, end=end,
                     kw=kw)
        return "FIELD"

    mod.fetch_ofs_thredds = fake_fetch
    monkeypatch.setitem(sys.modules, "currents.ofs_thredds", mod)

    fetch = fetch_for_source("ofs-thredds")
    bbox = (-74.0, 38.0, -73.0, 39.0)
    out = fetch("SSCOFS", bbox, "2026-09-30", "2026-10-01",
                cadence_hours=6)
    assert out == "FIELD"
    assert calls["ofs_code"] == "SSCOFS"
    assert calls["bbox"] == bbox
    assert calls["start"] == "2026-09-30"
    assert calls["end"] == "2026-10-01"
    assert calls["kw"]["cadence_hours"] == 6


def test_ofs_thredds_unknown_source_still_refused():
    with pytest.raises(ValueError, match="unknown source"):
        fetch_for_source("ofs-thredds-typo")
