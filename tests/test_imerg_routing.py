"""Tests for GPM IMERG precipitation source-selection routing (v0.7.0).

Covers the ``variable == "tp"`` selection matrix (explicit / recent /
long-record / default), ``VizSpec.source_reason``, ``explain_source``,
old-dict backward compatibility, and unchanged MUR / CMEMS-currents
behavior.
"""

from __future__ import annotations

import pytest

from viz import explain_source, parse_description, resolve_source
from viz.spec import KNOWN_SOURCES, VizSpec
from viz.sources import SOURCE_LABELS, fetch_for_source


REGION = "Gulf of Mexico"


def _parse(desc: str) -> VizSpec:
    spec = parse_description(f"{desc} over the {REGION}")
    assert spec.variable == "tp", f"expected tp, got {spec.variable}"
    return spec


# ---------------------------------------------------------------------------
# selection matrix: explicit
# ---------------------------------------------------------------------------


def test_explicit_imerg_pins():
    spec = _parse("IMERG rainfall")
    assert spec.source == "imerg"
    assert spec.source_reason == "explicit IMERG request"


def test_explicit_era5_pins():
    spec = _parse("ERA5 precipitation")
    assert spec.source == "era5"
    assert spec.source_reason == "explicit ERA5 request"


def test_explicit_imerg_beats_long_record_wording():
    spec = _parse("IMERG rainfall trend analysis")
    assert spec.source == "imerg"
    assert spec.source_reason == "explicit IMERG request"


def test_explicit_era5_beats_recent_wording():
    spec = _parse("ERA5 recent rainfall")
    assert spec.source == "era5"
    assert spec.source_reason == "explicit ERA5 request"


# ---------------------------------------------------------------------------
# selection matrix: recent / observed / event -> IMERG
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wording", [
    "recent rainfall",
    "rainfall last week",
    "rainfall event",
    "rainfall from the storm",
    "rainfall from the hurricane",   # rainfall keyword first (see below)
    "observed rainfall",
    "satellite rainfall",
    "high resolution rainfall",
    "ultra rainfall",
])
def test_recent_signals_pin_imerg(wording):
    spec = _parse(wording)
    assert spec.source == "imerg"
    assert "IMERG" in spec.source_reason


def test_hurricane_first_still_parses_as_wind():
    # Documented parser behavior: a leading "hurricane" wins the variable
    # race as the (wind + msl isobars) storm combination — precipitation
    # examples must put the rainfall keyword first (tested above).
    spec = parse_description(f"hurricane rainfall over the {REGION}")
    assert spec.variable == "wind"
    assert spec.source == ""


def test_word_boundary_no_false_pin():
    # "stormwater" must not trigger the storm -> IMERG signal.
    spec = _parse("stormwater runoff rainfall")
    assert spec.source == ""
    assert spec.source_reason == ""


# ---------------------------------------------------------------------------
# selection matrix: long record -> ERA5
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wording", [
    "rainfall trend",
    "precipitation climatology",
    "precipitation since 1980",
    "rainfall since 1950",
    "rainfall across the decades",
    "long-term rainfall",
])
def test_long_record_signals_pin_era5(wording):
    spec = _parse(wording)
    assert spec.source == "era5"
    assert "ERA5" in spec.source_reason


# ---------------------------------------------------------------------------
# default: unpinned tp resolves regionally to ERA5
# ---------------------------------------------------------------------------


def test_default_tp_resolves_to_era5():
    spec = _parse("rainfall")
    assert spec.source == ""
    assert spec.source_reason == ""
    assert resolve_source(spec) == "era5"


# ---------------------------------------------------------------------------
# reasons, explanations
# ---------------------------------------------------------------------------


def test_reason_populated_whenever_pinned():
    for wording, want in [("IMERG rainfall", "imerg"),
                          ("recent rainfall", "imerg"),
                          ("rainfall trend", "era5")]:
        spec = _parse(wording)
        assert spec.source == want
        assert spec.source_reason.strip(), wording


def test_explain_source_pinned_quotes_reason():
    spec = _parse("recent satellite rainfall")
    text = explain_source(spec)
    assert "'imerg'" in text
    assert "NASA GPM IMERG V07" in text
    assert spec.source_reason in text


def test_explain_source_default():
    spec = _parse("rainfall")
    text = explain_source(spec)
    assert "'era5'" in text
    assert "regional default" in text


def test_explain_source_refusal_sea_ice():
    spec = parse_description(f"sea ice over the {REGION}")
    assert resolve_source(spec) == ""
    text = explain_source(spec)
    assert text.startswith("no source")
    assert "polar" in text


def test_explain_source_refusal_land_ice():
    spec = parse_description(f"glacier ice over the {REGION}")
    assert spec.variable == "land-ice"
    assert resolve_source(spec) == ""
    assert "different physical product" in explain_source(spec)


# ---------------------------------------------------------------------------
# spec serialization
# ---------------------------------------------------------------------------


def test_source_reason_roundtrip():
    spec = _parse("recent rainfall")
    clone = VizSpec.from_dict(spec.to_dict())
    assert clone.source == "imerg"
    assert clone.source_reason == spec.source_reason


def test_old_dict_without_source_reason_loads():
    spec = _parse("recent rainfall")
    data = spec.to_dict()
    del data["source_reason"]
    clone = VizSpec.from_dict(data)
    assert clone.source_reason == ""
    assert clone.source == "imerg"


def test_source_reason_defaults_empty():
    spec = _parse("rainfall")
    assert spec.source_reason == ""
    assert VizSpec.from_dict(spec.to_dict()).source_reason == ""


# ---------------------------------------------------------------------------
# registry + lazy adapter
# ---------------------------------------------------------------------------


def test_imerg_registered():
    assert "imerg" in KNOWN_SOURCES
    assert SOURCE_LABELS["imerg"] == "NASA GPM IMERG V07"


def test_fetch_for_source_imerg_lazy():
    fetch = fetch_for_source("imerg")
    assert fetch.__module__ == "currents.imerg"
    assert fetch.__name__ == "fetch_imerg"


def test_mur_and_cmems_pins_unchanged():
    sst = parse_description("ultra high resolution sea surface "
                            f"temperature over the {REGION}")
    assert sst.variable == "sst"
    assert sst.source == "mur"
    assert "MUR" in sst.source_reason
    cur = parse_description(f"ultra high resolution currents over the {REGION}")
    assert cur.variable == "currents"
    assert cur.source == "cmems-currents"
    assert "CMEMS" in cur.source_reason


def test_fire_still_does_not_pin():
    from viz.parser import parse_description as p
    spec = p(f"wildfire over the {REGION}")
    assert spec.variable == "fire"
    assert spec.source == ""
    assert spec.source_reason == ""
