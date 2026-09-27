"""Tests for the IBTrACS storm-tracks variable (offline).

Parser routing, source routing, VizSpec storm fields, the
Saffir-Simpson renderer helpers, and the dedicated storm-track render
path (matplotlib Agg; the basemap underlay is stubbed offline by
tests/conftest.py).
"""

import datetime as dt

import pytest

from viz import render as vr
from viz.parser import parse_description
from viz.sources import (default_source, explain_source, fetch_for_source,
                         resolve_source)
from viz.spec import VizSpec

TODAY = dt.date(2026, 9, 27)


def _spec(text, **kw):
    return parse_description(text, today=TODAY, **kw)


# ---------------------------------------------------------------------------
# Parser routing
# ---------------------------------------------------------------------------

def test_named_storm_track_routes_to_ibtracs():
    spec = _spec("Hurricane Katrina's track across the Atlantic")
    assert spec.variable == "storm-tracks"
    assert spec.source == "ibtracs"
    assert "ibtracs" in spec.source_reason.lower()
    assert spec.storm_name == "katrina"
    assert spec.region_key == "north-atlantic"
    assert spec.cadence == "daily"


def test_named_storm_without_track_word():
    spec = _spec("Tropical Storm Milton over the Gulf of Mexico last summer")
    assert spec.variable == "storm-tracks"
    assert spec.storm_name == "milton"
    assert spec.source == "ibtracs"


def test_generic_hurricane_conditions_keep_era5():
    # The documented distinction: ambient storm environment stays
    # ERA5 wind + msl — IBTrACS is tracks/identity, ERA5 is the field.
    spec = _spec("Hurricane conditions in the Gulf of Mexico last summer")
    assert spec.variable == "wind"
    assert spec.overlays == ("msl",)
    assert spec.source == ""  # regional default -> era5
    assert spec.storm_name == ""


def test_hurricane_pressure_not_a_name():
    spec = _spec("Hurricane pressure drops over the Gulf of Mexico last summer")
    assert spec.variable == "wind"
    assert spec.storm_name == ""


def test_hurricane_season_not_a_name():
    spec = _spec("Hurricane season outlook for the Atlantic")
    assert spec.variable == "wind"
    assert spec.storm_name == ""


def test_track_wording_routes_to_ibtracs():
    spec = _spec("Typhoon tracks in the North Pacific last summer")
    assert spec.variable == "storm-tracks"
    assert spec.source == "ibtracs"
    assert spec.storm_name == ""


def test_strongest_hurricanes_ranking():
    spec = _spec("Strongest hurricanes of the 2024 season in the Atlantic")
    assert spec.variable == "storm-tracks"
    assert spec.storm_rank == "strongest"
    assert spec.storm_top_n is None  # parser default (5) applied downstream
    assert spec.source == "ibtracs"


def test_top_n_strongest():
    spec = _spec("Top 3 strongest hurricanes in the Atlantic, 2020 to 2024")
    assert spec.variable == "storm-tracks"
    assert spec.storm_rank == "strongest"
    assert spec.storm_top_n == 3


def test_storm_track_with_wind_field_context():
    # The IBTrACS+ERA5 combination: tracks from IBTrACS, ambient wind
    # contours from ERA5.
    spec = _spec("Hurricane Milton's track with the wind field, Gulf of Mexico")
    assert spec.variable == "storm-tracks"
    assert spec.overlays == ("wind",)
    assert spec.source == "ibtracs"


def test_hurricane_rainfall_stays_tp_routing():
    # "rainfall" before "hurricane" -> tp wins the earliest-wins race,
    # then the hurricane wording pins IMERG (existing behavior).
    spec = _spec("Rainfall from hurricanes in the Gulf of Mexico last summer")
    assert spec.variable == "tp"
    assert spec.source == "imerg"


# ---------------------------------------------------------------------------
# Source routing
# ---------------------------------------------------------------------------

def test_default_source_storm_tracks_global():
    assert default_source("storm-tracks", "gulf-of-mexico") == "ibtracs"
    assert default_source("storm-tracks", "north-pacific") == "ibtracs"
    assert default_source("storm-tracks", "global") == "ibtracs"


def test_resolve_source_pinned_ibtracs():
    spec = _spec("Hurricane Katrina's track across the Atlantic")
    assert resolve_source(spec) == "ibtracs"


def test_explain_source_mentions_ibtracs():
    spec = _spec("Hurricane Katrina's track across the Atlantic")
    text = explain_source(spec)
    assert "ibtracs" in text
    assert "IBTrACS" in text or "best tracks" in text


def test_fetch_for_source_ibtracs_lazy():
    # Environment-sensitive by design (like the era5/imerg lazy tests):
    # when survey-currents>=0.11.0 is importable the adapter must
    # resolve to the real fetch_ibtracs; when it is not, the ImportError
    # must name the minimum peer version.
    try:
        import currents.storms as st  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError, match="0.11.0"):
            fetch_for_source("ibtracs")
    else:
        assert fetch_for_source("ibtracs") is st.fetch_ibtracs


# ---------------------------------------------------------------------------
# VizSpec storm fields
# ---------------------------------------------------------------------------

def _base_kwargs():
    return dict(title="t", region_key="gulf-of-mexico",
                bbox=(-97.0, 18.0, -82.0, 31.0), variable="storm-tracks",
                start=dt.date(2024, 8, 1), end=dt.date(2024, 8, 10),
                cadence="daily")


def test_storm_fields_default():
    spec = VizSpec(**_base_kwargs())
    assert spec.storm_name == ""
    assert spec.storm_rank == ""
    assert spec.storm_top_n is None


def test_storm_fields_round_trip():
    spec = VizSpec(**_base_kwargs(), storm_name="katrina",
                   storm_rank="strongest", storm_top_n=3)
    spec2 = VizSpec.from_dict(spec.to_dict())
    assert (spec2.storm_name, spec2.storm_rank, spec2.storm_top_n) == \
        ("katrina", "strongest", 3)


def test_from_dict_backward_compat():
    data = _base_kwargs()
    data["start"] = data["start"].isoformat()
    data["end"] = data["end"].isoformat()
    spec = VizSpec.from_dict(data)  # pre-v0.10.0 dict, no storm keys
    assert spec.storm_name == "" and spec.storm_rank == "" \
        and spec.storm_top_n is None


def test_invalid_storm_rank():
    with pytest.raises(ValueError):
        VizSpec(**_base_kwargs(), storm_rank="weakest")
    with pytest.raises(ValueError):
        VizSpec(**_base_kwargs(), storm_top_n=0)


# ---------------------------------------------------------------------------
# Saffir-Simpson helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wind,cat", [
    (20, "TD"), (33.9, "TD"), (34, "TS"), (63.9, "TS"),
    (64, "C1"), (82.9, "C1"), (83, "C2"), (95.9, "C2"),
    (96, "C3"), (112.9, "C3"), (113, "C4"), (136.9, "C4"),
    (137, "C5"), (175, "C5"),
])
def test_sshs_category_boundaries(wind, cat):
    assert vr._sshs_category(wind) == cat


def test_sshs_category_unknown():
    assert vr._sshs_category(float("nan")) == "unknown"
    assert vr._sshs_category(None) == "unknown"


def test_storm_runs_break_dateline():
    track = {
        "times": [dt.datetime(2024, 8, 1, tzinfo=dt.timezone.utc) + dt.timedelta(hours=6 * i)
                  for i in range(4)],
        "lats": __import__("numpy").array([10.0, 11.0, 12.0, 13.0]),
        "lons": __import__("numpy").array([170.0, 175.0, -175.0, -170.0]),
        "winds": __import__("numpy").array([40.0, 50.0, 60.0, 70.0]),
    }
    runs = vr._storm_runs(track, dt.date(2024, 8, 2))
    # The 175 -> -175 jump breaks the run: two runs, never one line
    # across the Pacific.
    assert len(runs) == 2
    for _code, pts in runs:
        for (lo0, _), (lo1, _) in zip(pts, pts[1:]):
            assert abs(lo1 - lo0) <= 180.0


# ---------------------------------------------------------------------------
# Storm-track rendering
# ---------------------------------------------------------------------------

def _storm_field_dict():
    import numpy as np
    t0 = dt.datetime(2024, 8, 1, tzinfo=dt.timezone.utc)
    return {
        "storm_tracks": [
            {
                "sid": "2024212N12345",
                "name": "TESTALPHA",
                "season": 2024,
                "basin": "NA",
                "times": [(t0 + dt.timedelta(hours=6 * i)).isoformat()
                          for i in range(8)],
                "lats": [20.0 + 0.5 * i for i in range(8)],
                "lons": [-80.0 + 0.5 * i for i in range(8)],
                "winds": [35.0, 50.0, 70.0, 100.0, 120.0, 90.0, 60.0, 40.0],
                "press": [1000.0] * 8,
            },
            {
                "sid": "2024213N12346",
                "name": "TESTBRAVO",
                "season": 2024,
                "basin": "NA",
                "times": [(t0 + dt.timedelta(hours=6 * i)).isoformat()
                          for i in range(6)],
                # Dateline-crossing track in 0..360 longitudes.
                "lats": [-10.0 - 0.5 * i for i in range(6)],
                "lons": [np.nan, 170.0, 175.0, 185.0, 190.0, 195.0],
                "winds": [30.0, 45.0, 65.0, 85.0, 75.0, 55.0],
                "press": [1005.0] * 6,
            },
        ],
        "bbox": [-100.0, 10.0, -60.0, 40.0],
    }


def _storm_spec(**kw):
    args = dict(title="Test storm tracks", region_key="gulf-of-mexico",
                bbox=(-100.0, 10.0, -60.0, 40.0), variable="storm-tracks",
                start=dt.date(2024, 8, 1), end=dt.date(2024, 8, 4),
                cadence="daily", source="ibtracs",
                source_reason="storm track/identity request -> NOAA IBTrACS v04r01 best tracks")
    args.update(kw)
    return VizSpec(**args)


def test_render_storm_tracks(tmp_path):
    pytest.importorskip("matplotlib")
    spec = _storm_spec()
    frames, manifest_path = vr.render_viz(
        spec, _storm_field_dict(), None, out_dir=str(tmp_path))
    assert len(frames) == 4  # daily, Aug 1..4
    assert all(str(p).endswith(".png") for p in frames)
    import json
    manifest = json.loads(open(manifest_path).read())
    assert manifest["render"]["cmap"] == "sshs-categorical"
    assert manifest["render"]["n_frames"] == 4
    storms = manifest["render"]["storms"]
    assert [s["name"] for s in storms] == ["TESTALPHA", "TESTBRAVO"]
    assert storms[0]["max_wind_kt"] == 120.0
    assert storms[0]["max_category"] == "C4"
    assert manifest["spec"]["storm_name"] == ""


def test_render_storm_tracks_with_era5_context(tmp_path):
    pytest.importorskip("matplotlib")
    import numpy as np
    field = _storm_field_dict()
    grid = np.full((4, 5, 6), 1012.0)
    field["overlay_grids"] = {
        "msl": {
            "times": [dt.date(2024, 8, 1) + dt.timedelta(days=i)
                      for i in range(4)],
            "lats": [10.0 + i for i in range(5)],
            "lons": [-100.0 + i for i in range(6)],
            "grid": grid,
        }
    }
    spec = _storm_spec(overlays=("msl",))
    frames, manifest_path = vr.render_viz(
        spec, field, None, out_dir=str(tmp_path))
    import json
    manifest = json.loads(open(manifest_path).read())
    assert manifest["render"]["era5_context"] == {"msl": "ok"}
    assert len(frames) == 4


def test_render_storm_tracks_missing_context_degrades(tmp_path):
    pytest.importorskip("matplotlib")
    spec = _storm_spec(overlays=("wind",))  # field has no wind grid
    frames, manifest_path = vr.render_viz(
        spec, _storm_field_dict(), None, out_dir=str(tmp_path))
    import json
    manifest = json.loads(open(manifest_path).read())
    assert manifest["render"]["era5_context"] == {"wind": "absent"}
    assert len(frames) == 4  # still renders


def test_render_storm_tracks_empty(tmp_path):
    pytest.importorskip("matplotlib")
    spec = _storm_spec()
    frames, manifest_path = vr.render_viz(
        spec, {"storm_tracks": []}, None, out_dir=str(tmp_path))
    assert len(frames) == 4  # graceful: "No storm fixes in this window"
