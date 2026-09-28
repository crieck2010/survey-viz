"""Tests for viz.refine.refine_spec (plain-language spec refinements)."""

from __future__ import annotations

import datetime as _dt

import pytest

from viz.parser import UnparseableDescription, parse_description
from viz.refine import refine_spec

TODAY = _dt.date(2026, 9, 27)


def _base() -> "VizSpec":
    # Auto-derived title: "Lake Superior — Sea surface temperature, 2020–2022".
    return parse_description(
        "Lake Superior water temperature 2020 to 2022", today=TODAY)


def _fields(result):
    return [c.field for c in result.applied]


def test_title_set():
    result = refine_spec(_base(), 'title it "Gulf Heat"', today=TODAY)
    assert result.spec.title == "Gulf Heat"
    assert _fields(result) == ["title"]


def test_title_call_it_form():
    result = refine_spec(_base(), "call it Big Chill", today=TODAY)
    assert result.spec.title == "Big Chill"


def test_quoted_title_does_not_switch_variable():
    # "Ocean Currents" is literal frame text, not an instruction.
    result = refine_spec(_base(), "title it 'Ocean Currents'", today=TODAY)
    assert result.spec.title == "Ocean Currents"
    assert result.spec.variable == "sst"


def test_caption_add_and_clear():
    added = refine_spec(_base(), "add a caption saying my channel",
                        today=TODAY)
    assert added.spec.caption == "my channel"
    cleared = refine_spec(added.spec, "remove the caption", today=TODAY)
    assert cleared.spec.caption is None


def test_colormap_request_carried_on_result():
    base = _base()
    result = refine_spec(base, "use the inferno colormap", today=TODAY)
    assert result.cmap == "inferno"
    assert "colormap" in _fields(result)
    # The spec itself is untouched (colormap is a render-time argument).
    assert result.spec.to_dict() == base.to_dict()


def test_colormap_mood_word():
    result = refine_spec(_base(), "warmer colors please", today=TODAY)
    assert result.cmap == "inferno"


def test_unknown_colormap_is_reported_not_guessed():
    result = refine_spec(_base(), "use the blorp colormap", today=TODAY)
    assert result.cmap is None
    assert any("blorp" in note for note in result.unparsed)


def test_style_and_underlay():
    dark = refine_spec(_base(), "switch to light mode", today=TODAY)
    assert dark.spec.style == "light"
    off = refine_spec(_base(), "turn off the basemap", today=TODAY)
    assert off.spec.underlay is False
    on = refine_spec(off.spec, "turn the basemap back on", today=TODAY)
    assert on.spec.underlay is True


def test_region_refocus_rederives_automatic_title():
    base = _base()
    result = refine_spec(base, "zoom in on the Gulf of Mexico", today=TODAY)
    assert result.spec.region_key == "gulf-of-mexico"
    assert result.spec.bbox != base.bbox
    assert result.spec.title.startswith("Gulf of Mexico")
    assert "Lake Superior" not in result.spec.title


def test_bare_zoom_in_halves_bbox():
    base = _base()
    result = refine_spec(base, "zoom in a bit", today=TODAY)
    lon_min, lat_min, lon_max, lat_max = base.bbox
    nlon_min, nlat_min, nlon_max, nlat_max = result.spec.bbox
    assert (nlon_max - nlon_min) == pytest.approx((lon_max - lon_min) / 2)
    assert (nlat_max - nlat_min) == pytest.approx((lat_max - lat_min) / 2)
    assert result.spec.region_key == base.region_key  # same region entry


def test_time_change():
    result = refine_spec(_base(), "run it from 2015 to 2020", today=TODAY)
    assert result.spec.start == _dt.date(2015, 1, 1)
    assert result.spec.end == _dt.date(2020, 12, 31)
    assert "2020" in result.spec.title  # auto title followed the dates


def test_variable_switch_recomputes_cadence():
    result = refine_spec(_base(), "show sea ice instead", today=TODAY)
    assert result.spec.variable == "sea-ice"
    assert result.spec.cadence == "daily"  # was monthly for sst
    assert "sea ice" in result.spec.title.lower()


def test_combined_instruction():
    result = refine_spec(
        _base(), "zoom in on the Gulf of Mexico and use a warmer colormap",
        today=TODAY)
    assert result.spec.region_key == "gulf-of-mexico"
    assert result.cmap == "inferno"
    assert set(_fields(result)) >= {"region_key", "bbox", "colormap", "title"}


def test_custom_title_is_never_clobbered():
    base = _base()
    titled = refine_spec(base, 'title it "My Custom Title"', today=TODAY)
    result = refine_spec(titled.spec, "zoom in on the Gulf of Mexico",
                         today=TODAY)
    assert result.spec.title == "My Custom Title"
    assert any("title it" in note for note in result.unparsed)


def test_source_pin():
    result = refine_spec(_base(), "use the MUR product for this",
                         today=TODAY)
    assert result.spec.source == "mur"
    assert result.spec.source_reason


def test_nothing_matched_raises_helpfully():
    with pytest.raises(UnparseableDescription) as excinfo:
        refine_spec(_base(), "make it pop", today=TODAY)
    assert "zoom in on" in str(excinfo.value)


def test_empty_instruction_raises():
    with pytest.raises(UnparseableDescription):
        refine_spec(_base(), "   ", today=TODAY)


def test_input_spec_is_not_mutated():
    base = _base()
    before = base.to_dict()
    refine_spec(base, "zoom in on the Gulf of Mexico, light mode",
                today=TODAY)
    assert base.to_dict() == before
