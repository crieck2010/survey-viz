"""Tests for render_viz(canvas=...) — the survey-layout interop.

Canvas dicts are built by hand (duck-typed): viz must never import
survey-layout, so the tests pin the contract shape instead.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")
PIL_Image = pytest.importorskip("PIL.Image")

from viz.render import render_viz
from viz.spec import VizSpec

BBOX = (-92.5, 46.0, -84.5, 48.8)


def _spec(**over):
    kw = dict(
        title="Lake Superior — Surface Water Temperature, 2026",
        region_key="lake-superior",
        bbox=BBOX,
        variable="sst",
        start=dt.date(2026, 1, 1),
        end=dt.date(2026, 3, 31),
        cadence="monthly",
    )
    kw.update(over)
    return VizSpec(**kw)


def _field(n_months=3, seed=7):
    rng = np.random.default_rng(seed)
    lats = np.linspace(46.0, 48.8, 10)
    lons = np.linspace(-92.5, -84.5, 14)
    times = [dt.date(2026, m, 15) for m in range(1, n_months + 1)]
    values = rng.normal(10, 3, size=(n_months, len(lats), len(lons)))
    series = {"dates": times,
              "values": [float(np.mean(values[i])) for i in range(n_months)]}
    return {"times": times, "lats": lats, "lons": lons, "values": values}, series


def _canvas_916():
    # tiktok-shaped canvas, hand-written (the duck-typed contract)
    return {
        "platform": "tiktok",
        "width": 1080,
        "height": 1920,
        "regions": {
            "title": [0.02, 0.815, 0.82, 0.095],
            "map": [0.03, 0.48, 0.80, 0.32],
            "chart": [0.05, 0.31, 0.76, 0.15],
            "caption": [0.02, 0.245, 0.82, 0.055],
            "footer": [0.00, 0.219, 0.856, 0.02],
        },
        "unsafe": [[0.0, 0.0, 1.0, 0.1875]],
    }


def _png_size(path):
    with PIL_Image.open(path) as im:
        return im.size


def test_canvas_sets_frame_size_and_records_provenance(tmp_path):
    field, series = _field()
    frames, mp = render_viz(_spec(), field, series,
                            out_dir=tmp_path / "f", canvas=_canvas_916())
    assert len(frames) == 3
    assert _png_size(frames[0]) == (1080, 1920)
    with open(mp, encoding="utf-8") as fh:
        manifest = json.load(fh)
    canvas = manifest["render"]["canvas"]
    assert canvas["platform"] == "tiktok"
    assert canvas["width"] == 1080 and canvas["height"] == 1920
    assert canvas["regions"] == ["caption", "chart", "footer", "map", "title"]


def test_canvas_square(tmp_path):
    field, series = _field(n_months=2)
    canvas = {"platform": "square", "width": 1080, "height": 1080,
              "regions": {
                  "title": [0.0, 0.88, 1.0, 0.12],
                  "map": [0.04, 0.40, 0.92, 0.44],
                  "chart": [0.08, 0.10, 0.84, 0.24],
                  "caption": [0.0, 0.06, 1.0, 0.08],
                  "footer": [0.0, 0.0, 1.0, 0.055],
              }}
    frames, _ = render_viz(_spec(), field, series,
                           out_dir=tmp_path / "f", canvas=canvas)
    assert _png_size(frames[0]) == (1080, 1080)


def test_canvas_none_is_legacy(tmp_path):
    field, series = _field(n_months=2)
    frames, mp = render_viz(_spec(), field, series, out_dir=tmp_path / "f")
    assert _png_size(frames[0]) == (1080, 1920)
    with open(mp, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["render"]["canvas"] is None


def test_canvas_partial_regions_fall_back(tmp_path):
    # only "map" overridden: everything else keeps the legacy rects
    field, series = _field(n_months=2)
    canvas = {"width": 1080, "height": 1920,
              "regions": {"map": [0.03, 0.48, 0.80, 0.32]}}
    frames, mp = render_viz(_spec(), field, series,
                            out_dir=tmp_path / "f", canvas=canvas)
    assert len(frames) == 2
    with open(mp, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["render"]["canvas"]["platform"] is None


def test_canvas_malformed_fails_fast(tmp_path):
    field, series = _field(n_months=2)
    with pytest.raises(ValueError, match="width.*height"):
        render_viz(_spec(), field, series, out_dir=tmp_path / "f",
                   canvas={"width": 1080})
    with pytest.raises(ValueError, match="fractions"):
        render_viz(_spec(), field, series, out_dir=tmp_path / "f",
                   canvas={"width": 1080, "height": 1920,
                           "regions": {"map": [0, 0, 2, 2]}})
    with pytest.raises(ValueError, match="must be a dict"):
        render_viz(_spec(), field, series, out_dir=tmp_path / "f",
                   canvas="tiktok")
    # nothing rendered on failure
    assert list((tmp_path / "f").glob("frame_*.png")) == []


def _quake_spec():
    return VizSpec(
        title="t", region_key="lake-erie",
        bbox=(-83.5, 41.2, -78.8, 43.0), variable="earthquakes",
        start=dt.date(2026, 6, 1), end=dt.date(2026, 6, 10),
        cadence="daily", source="comcat")


def _quake_field():
    events = [
        {"event_id": "us1", "time": "2026-06-03T10:00:00",
         "lat": 42.5, "lon": -83.0, "depth_km": 10.0,
         "magnitude": 5.2, "mag_type": "ml",
         "place": "10km N of Testville", "event_type": "earthquake"},
        {"event_id": "us2", "time": "2026-06-05T03:00:00",
         "lat": 42.7, "lon": -82.9, "depth_km": 150.0,
         "magnitude": 4.1, "mag_type": "mb",
         "place": "20km E of Testville", "event_type": "earthquake"},
    ]
    return {
        "quake_events": events,
        "bbox": [-83.5, 41.2, -82.8, 43.0],
        "start": "2026-06-01", "end": "2026-06-10",
        "min_magnitude": 4.0, "event_type": None, "source": "usgs",
    }


def test_canvas_quake_flavor(tmp_path):
    canvas = {"platform": "tiktok", "flavor": "quake",
              "width": 1080, "height": 1920,
              "regions": {
                  "title": [0.02, 0.815, 0.82, 0.095],
                  "map": [0.03, 0.57, 0.80, 0.23],
                  "ranking": [0.03, 0.43, 0.80, 0.12],
                  "chart": [0.05, 0.31, 0.76, 0.10],
                  "caption": [0.02, 0.245, 0.82, 0.055],
                  "footer": [0.00, 0.219, 0.856, 0.02],
              }}
    frames, mp = render_viz(_quake_spec(), _quake_field(), None,
                            out_dir=tmp_path / "f", canvas=canvas)
    assert frames and _png_size(frames[0]) == (1080, 1920)
    with open(mp, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["render"]["canvas"]["regions"] == [
        "caption", "chart", "footer", "map", "ranking", "title"]


def test_canvas_quake_derives_ranking_when_absent(tmp_path):
    # hand-written canvas without "ranking": viz fills the band between
    # the chart top and the map bottom
    canvas = {"width": 1080, "height": 1920,
              "regions": {
                  "map": [0.04, 0.52, 0.92, 0.36],
                  "chart": [0.08, 0.08, 0.84, 0.18],
              }}
    frames, _ = render_viz(_quake_spec(), _quake_field(), None,
                           out_dir=tmp_path / "f", canvas=canvas)
    assert frames


def test_canvas_storm_tracks(tmp_path):
    t0 = dt.datetime(2024, 8, 1, tzinfo=dt.timezone.utc)
    field = {
        "storm_tracks": [{
            "sid": "2024212N12345", "name": "TESTALPHA", "season": 2024,
            "basin": "NA",
            "times": [(t0 + dt.timedelta(hours=6 * i)).isoformat()
                      for i in range(8)],
            "lats": [20.0 + 0.5 * i for i in range(8)],
            "lons": [-80.0 + 0.5 * i for i in range(8)],
            "winds": [35.0, 50.0, 70.0, 100.0, 120.0, 90.0, 60.0, 40.0],
            "press": [1000.0] * 8,
        }],
        "bbox": [-100.0, 10.0, -60.0, 40.0],
    }
    spec = VizSpec(title="t", region_key="gulf-of-maine",
                   bbox=(-100.0, 10.0, -60.0, 40.0),
                   variable="storm-tracks",
                   start=dt.date(2024, 8, 1), end=dt.date(2024, 8, 3),
                   cadence="daily", source="ibtracs")
    frames, mp = render_viz(spec, field, None, out_dir=tmp_path / "f",
                            canvas=_canvas_916())
    assert frames and _png_size(frames[0]) == (1080, 1920)
    with open(mp, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["render"]["canvas"]["platform"] == "tiktok"


def test_canvas_gage_field(tmp_path):
    dates = [dt.date(2026, 6, d) for d in range(1, 6)]
    field = {
        "gages": [{
            "site_no": "04183500",
            "name": "Test River",
            "lat": 43.0, "lon": -83.0,
            "series": {"dv": {
                "dates": [d.isoformat() for d in dates],
                "values": [100.0, 120.0, 110.0, 130.0, 125.0],
                "unit": "ft3/s",
            }},
        }],
        "bbox": [-84.0, 42.0, -82.0, 44.0],
        "start": "2026-06-01", "end": "2026-06-05",
        "source": "usgs",
    }
    spec = VizSpec(title="t", region_key="lake-erie",
                   bbox=(-84.0, 42.0, -82.0, 44.0), variable="streamflow",
                   start=dt.date(2026, 6, 1), end=dt.date(2026, 6, 5),
                   cadence="daily", source="usgs")
    frames, mp = render_viz(spec, field, None, out_dir=tmp_path / "f",
                            canvas=_canvas_916())
    assert frames and _png_size(frames[0]) == (1080, 1920)
    with open(mp, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["render"]["canvas"]["width"] == 1080
