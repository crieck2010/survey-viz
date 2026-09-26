"""Tests for VizSpec: validation + to_dict/from_dict."""

import datetime as dt

import pytest

from viz.spec import VizSpec


def _base(**over):
    kw = dict(
        title="Lake Superior — Surface Water Temperature, 2021–2026",
        region_key="lake-superior",
        bbox=(-92.5, 46.0, -84.5, 48.8),
        variable="sst",
        start=dt.date(2021, 9, 26),
        end=dt.date(2026, 9, 26),
        cadence="monthly",
    )
    kw.update(over)
    return VizSpec(**kw)


def test_defaults_layout_and_style():
    spec = _base()
    assert spec.layout == "reel-vertical"
    assert spec.style == "reel-dark"
    assert spec.vmin is None and spec.vmax is None


def test_to_dict_from_dict_roundtrip():
    spec = _base(vmin=0.0, vmax=25.0, style="light")
    d = spec.to_dict()
    assert d["start"] == "2021-09-26"
    assert d["bbox"] == [-92.5, 46.0, -84.5, 48.8]
    back = VizSpec.from_dict(d)
    assert back == spec


def test_from_dict_accepts_iso_date_strings():
    d = _base().to_dict()
    assert isinstance(d["start"], str)
    spec = VizSpec.from_dict(d)
    assert spec.start == dt.date(2021, 9, 26)


def test_bbox_lon_min_must_be_less_than_lon_max():
    with pytest.raises(ValueError):
        _base(bbox=(-84.5, 46.0, -92.5, 48.8))


def test_bbox_lat_out_of_world_bounds():
    with pytest.raises(ValueError):
        _base(bbox=(-92.5, -95.0, -84.5, 48.8))


def test_bbox_wrong_arity():
    with pytest.raises(ValueError):
        _base(bbox=(-92.5, 46.0, -84.5))


def test_bbox_non_numeric():
    with pytest.raises(ValueError):
        _base(bbox=("a", "b", "c", "d"))


def test_start_after_end_raises():
    with pytest.raises(ValueError):
        _base(start=dt.date(2026, 1, 1), end=dt.date(2025, 1, 1))


def test_bad_cadence_raises():
    with pytest.raises(ValueError):
        _base(cadence="hourly")


def test_bad_style_raises():
    with pytest.raises(ValueError):
        _base(style="neon")


def test_bad_layout_raises():
    with pytest.raises(ValueError):
        _base(layout="square")


def test_empty_title_raises():
    with pytest.raises(ValueError):
        _base(title="   ")


def test_empty_variable_raises():
    with pytest.raises(ValueError):
        _base(variable="")


def test_vmin_must_be_less_than_vmax():
    with pytest.raises(ValueError):
        _base(vmin=25.0, vmax=0.0)


def test_datetime_coerced_to_date():
    spec = _base(
        start=dt.datetime(2021, 9, 26, 12, 30), end=dt.datetime(2026, 9, 26, 1, 0)
    )
    assert spec.start == dt.date(2021, 9, 26)
    assert spec.end == dt.date(2026, 9, 26)
