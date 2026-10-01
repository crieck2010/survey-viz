"""Tests for viz.underlay.fetch_landmask (never any live network).

The Natural Earth fetch is monkeypatched with synthetic GeoJSON; the
rasterization, hole-cutting, row-0=top normalization, cache, and the
never-raise discipline are all asserted for real.
"""

import sys

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("matplotlib")

from viz import underlay


@pytest.fixture(autouse=True)
def _clean_cache():
    underlay.clear_cache()
    yield
    underlay.clear_cache()


_SQUARE_WITH_HOLE = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                # exterior: lon -100..-90, lat 30..40
                [[-100.0, 30.0], [-90.0, 30.0], [-90.0, 40.0],
                 [-100.0, 40.0], [-100.0, 30.0]],
                # hole: lon -97..-93, lat 33..37
                [[-97.0, 33.0], [-93.0, 33.0], [-93.0, 37.0],
                 [-97.0, 37.0], [-97.0, 33.0]],
            ],
        },
    }],
}

_TWO_SQUARES = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0],
                  [0.0, 10.0], [0.0, 0.0]]],
                [[[20.0, 20.0], [30.0, 20.0], [30.0, 30.0],
                  [20.0, 30.0], [20.0, 30.0]]],
            ],
        },
    }],
}

_BBOX = (-100.0, 30.0, -90.0, 40.0)


def _fake_fetch(fc, seen=None):
    def _fetch(bbox, scale="110m", layers=("land",), timeout=600.0):
        if seen is not None:
            seen.append({"bbox": bbox, "scale": scale, "layers": layers})
        return {"land": fc}
    return _fetch


def test_square_mask_true_inside_false_outside(monkeypatch):
    seen = []
    monkeypatch.setattr("currents.basemaps.fetch_naturalearth",
                        _fake_fetch(_SQUARE_WITH_HOLE, seen))
    lats = np.linspace(30.0, 40.0, 11)  # ascending: row 0 = south
    lons = np.linspace(-100.0, -90.0, 11)
    res = underlay.fetch_landmask(_BBOX, lats, lons)
    assert res["status"] == "ok"
    mask = res["mask"]
    assert mask.shape == (11, 11) and mask.dtype == bool
    # row 0 = north (top): mask[0] <-> lats[-1] == 40
    # (-99, 31): inside exterior, outside the hole -> keep
    assert mask[9, 1]
    # (-91, 39): inside exterior, outside the hole -> keep
    assert mask[1, 9]
    # (-95, 35): inside the HOLE -> cut out
    assert not mask[5, 5]
    prov = res["provenance"]
    assert prov["n_polygons"] == 1 and prov["n_rings"] == 2
    assert prov["scale"] == "50m"  # 10° wide -> 50m
    assert prov["cache"] == "Miss"
    assert seen[0]["scale"] == "50m"


def test_descending_lats_row0_still_north(monkeypatch):
    monkeypatch.setattr("currents.basemaps.fetch_naturalearth",
                        _fake_fetch(_SQUARE_WITH_HOLE))
    lats = np.linspace(40.0, 30.0, 11)  # descending: row 0 = north
    lons = np.linspace(-100.0, -90.0, 11)
    res = underlay.fetch_landmask(_BBOX, lats, lons)
    assert res["status"] == "ok"
    mask = res["mask"]
    # mask[1, 1] <-> (lat 39, lon -99): inside exterior -> keep
    assert mask[1, 1]
    # mask[5, 5] <-> (lat 35, lon -95): inside the hole -> cut out
    assert not mask[5, 5]


def test_multipolygon_both_parts_filled(monkeypatch):
    monkeypatch.setattr("currents.basemaps.fetch_naturalearth",
                        _fake_fetch(_TWO_SQUARES))
    lats = np.linspace(0.0, 40.0, 41)
    lons = np.linspace(0.0, 40.0, 41)
    res = underlay.fetch_landmask((0.0, 0.0, 40.0, 40.0), lats, lons)
    assert res["status"] == "ok"
    mask = res["mask"]
    # (5, 5) in the first square; (25, 25) in the second (row 0 = top)
    assert mask[35, 5]
    assert mask[15, 25]
    # (15, 15): ocean between the squares
    assert not mask[25, 15]


def test_scale_override_reaches_fetch(monkeypatch):
    seen = []
    monkeypatch.setattr("currents.basemaps.fetch_naturalearth",
                        _fake_fetch(_SQUARE_WITH_HOLE, seen))
    lats = np.linspace(30.0, 40.0, 6)
    lons = np.linspace(-100.0, -90.0, 6)
    res = underlay.fetch_landmask(_BBOX, lats, lons, scale="50m")
    assert res["status"] == "ok"
    assert res["provenance"]["scale"] == "50m"
    assert seen[0]["scale"] == "50m"


def test_cache_hit_second_call(monkeypatch):
    calls = []
    monkeypatch.setattr("currents.basemaps.fetch_naturalearth",
                        _fake_fetch(_SQUARE_WITH_HOLE, calls))
    lats = np.linspace(30.0, 40.0, 6)
    lons = np.linspace(-100.0, -90.0, 6)
    first = underlay.fetch_landmask(_BBOX, lats, lons)
    second = underlay.fetch_landmask(_BBOX, lats, lons)
    assert len(calls) == 1
    assert second["provenance"]["cache"] == "Hit"
    assert np.array_equal(first["mask"], second["mask"])


def test_missing_peer_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "currents.basemaps", None)
    res = underlay.fetch_landmask(_BBOX, [30.0, 40.0], [-100.0, -90.0])
    assert res["status"] == "unavailable"
    assert res["mask"] is None
    assert "not installed" in res["reason"]


def test_fetch_raising_is_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("currents.basemaps.fetch_naturalearth", _boom)
    res = underlay.fetch_landmask(_BBOX, [30.0, 40.0], [-100.0, -90.0])
    assert res["status"] == "unavailable"
    assert res["mask"] is None
    assert "boom" in res["reason"]


def test_empty_layer_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "currents.basemaps.fetch_naturalearth",
        _fake_fetch({"type": "FeatureCollection", "features": []}))
    res = underlay.fetch_landmask(_BBOX, [30.0, 40.0], [-100.0, -90.0])
    assert res["status"] == "unavailable"
    assert res["mask"] is None
    assert "no polygons" in res["reason"]
