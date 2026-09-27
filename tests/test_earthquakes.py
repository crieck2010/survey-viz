"""Tests for USGS Earthquake Catalog (ComCat) routing, parsing, rendering."""

import datetime as dt
import json

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")

from viz.render import (_VARIABLE_UNITS, _quake_daily_counts,
                        _quake_depth_bin, _quake_frame_dates,
                        _quake_largest, _quake_marker_area,
                        _normalize_quake_field, render_viz)
from viz.sources import (SOURCE_LABELS, default_source, explain_source,
                         fetch_for_source, is_fetchable, resolve_source)
from viz.spec import KNOWN_SOURCES, KNOWN_VARIABLES, VizSpec
from viz.parser import parse_description

TODAY = dt.date(2026, 9, 27)
CALI = "Earthquakes in California over the past 3 months"
SOJ = "the Sea of Japan"


def _parse(text):
    return parse_description(text, today=TODAY)


def _quake_dict(n_events=3, empty=False, bad_time=False):
    """A QuakeField.to_dict()-shaped field (no survey-currents needed)."""
    if empty:
        events = []
    else:
        events = [
            {"event_id": "us1", "time": "2026-06-03T10:00:00",
             "lat": 42.5, "lon": -83.0, "depth_km": 10.0,
             "magnitude": 5.2, "mag_type": "ml",
             "place": "10km N of Testville", "event_type": "earthquake"},
            {"event_id": "us2", "time": "2026-06-05T03:00:00",
             "lat": 42.7, "lon": -82.9, "depth_km": 150.0,
             "magnitude": 4.1, "mag_type": "mb",
             "place": "20km E of Testville", "event_type": "earthquake"},
            {"event_id": "us3", "time": "2026-06-05T03:05:00",
             "lat": 42.75, "lon": -82.85, "depth_km": 650.0,
             "magnitude": 6.3, "mag_type": "mw",
             "place": "deep event", "event_type": "quarry blast"},
        ][:n_events]
        if bad_time:
            events = events + [{"event_id": "bad", "time": "not-a-time",
                                "lat": 42.5, "lon": -83.0, "depth_km": 10.0,
                                "magnitude": 4.5, "mag_type": "ml",
                                "place": "x", "event_type": "earthquake"}]
    return {
        "quake_events": events,
        "bbox": [-83.5, 41.2, -82.8, 43.0],
        "start": "2026-06-01",
        "end": "2026-06-10",
        "min_magnitude": 4.0,
        "event_type": None,
        "source": "usgs",
        "provenance": {
            "source": "usgs",
            "n_events": len(events),
            "retrieved_at": "2026-09-27T00:00:00+00:00",
            "request_urls": ["https://earthquake.usgs.gov/fdsnws/event/1/query?x"],
            "empty_reason": "no events in this window" if empty else None,
        },
    }


def _spec(**kw):
    base = dict(title="t", region_key="lake-erie",
                bbox=(-83.5, 41.2, -78.8, 43.0),
                variable="earthquakes", start=dt.date(2026, 6, 1),
                end=dt.date(2026, 6, 10), cadence="daily",
                source="comcat")
    base.update(kw)
    return VizSpec(**base)


# ---------------------------------------------------------------------------
# Parser: earthquake wording -> comcat
# ---------------------------------------------------------------------------

def test_parse_earthquakes_pins_comcat():
    spec = _parse(CALI)
    assert spec.variable == "earthquakes"
    assert spec.source == "comcat"
    assert spec.cadence == "daily"
    assert "ComCat" in spec.source_reason
    assert resolve_source(spec) == "comcat"
    assert is_fetchable(spec)


def test_parse_keyword_variants():
    for text in (
        f"Quakes in {SOJ} over the past week",
        f"Seismic activity in {SOJ} over the past week",
        f"Tremors in {SOJ} over the past week",
        f"Aftershocks in {SOJ} over the past week",
        f"Foreshocks in {SOJ} over the past week",
        f"Magnitude 5 quakes in {SOJ} over the past week",
    ):
        spec = _parse(text)
        assert spec.variable == "earthquakes", text
        assert spec.source == "comcat", text


def test_parse_quake_alone():
    # Bare "quake" is earthquake intent, not a refusal.
    spec = _parse(f"A quake in {SOJ} last month")
    assert spec.variable == "earthquakes"
    assert spec.source == "comcat"


def test_parse_seismic_hazard_routes_to_earthquakes():
    # "seismic hazard" wording renders the OBSERVED catalog — the
    # hazard-vs-catalog honesty note goes in the render manifest
    # (asserted below), never in a refused parse.
    spec = _parse("Seismic hazard in California over the past year")
    assert spec.variable == "earthquakes"
    assert spec.source == "comcat"


def test_parse_depth_of_earthquake_beats_bathymetry():
    # The bathymetry group's bare "depth" pattern would otherwise win
    # here; the pre-race earthquake check guarantees quake intent wins.
    spec = _parse(f"The depth of the earthquake swarm in {SOJ} last month")
    assert spec.variable == "earthquakes"
    assert spec.variable != "bathymetry"


def test_parse_earthquakes_and_topography():
    # Topography stays the default GEBCO underlay: variable stays
    # "earthquakes" (no separate fetch) and the reason says so.
    spec = _parse("Earthquakes and topography in California this year")
    assert spec.variable == "earthquakes"
    assert spec.source == "comcat"
    assert "underlay" in spec.source_reason.lower()


def test_parse_swarm_over_time():
    spec = _parse(f"Earthquake swarm in {SOJ} over the past 3 months")
    assert spec.variable == "earthquakes"
    assert spec.cadence == "daily"
    assert spec.start == dt.date(2026, 6, 27)
    assert spec.end == TODAY


def test_parse_earthquakes_region_and_title():
    spec = _parse(CALI)
    assert spec.region_key == "california"
    assert "Earthquakes" in spec.title


def test_parse_bare_flow_stays_currents():
    # The earthquake pre-check must not steal ocean/current wording.
    spec = _parse(f"Ocean flow patterns in {SOJ} over the past month")
    assert spec.variable == "currents"


def test_parse_heat_still_air_temperature():
    # "heat" keeps its t2m reading; only seismic wording routes here.
    spec = _parse("The heat in California over the past month")
    assert spec.variable == "t2m"


def test_parse_bathymetry_depth_still_bathymetry():
    # The pre-check fires only on seismic wording: plain "depth"
    # requests still route to bathymetry.
    spec = _parse(f"Seafloor depth in {SOJ} over the past year")
    assert spec.variable == "bathymetry"


# ---------------------------------------------------------------------------
# spec / sources
# ---------------------------------------------------------------------------

def test_known_variables_and_sources():
    assert "earthquakes" in KNOWN_VARIABLES
    assert "comcat" in KNOWN_SOURCES
    assert SOURCE_LABELS["comcat"] == "USGS Earthquake Catalog (ComCat)"


def test_default_source_is_global():
    # The ComCat catalog is global: no regional restriction.
    for region in ("california", "sea-of-japan", "lake-erie"):
        assert default_source("earthquakes", region) == "comcat"


def test_resolve_source_explicit_and_default():
    assert resolve_source(_spec()) == "comcat"  # pinned
    assert resolve_source(_spec(source="")) == "comcat"  # regional default


def test_resolve_source_unknown_raises():
    # VizSpec itself rejects an unknown pinned source; resolve_source
    # rejects unknown explicit sources on duck-typed specs too.
    with pytest.raises(ValueError, match="not in"):
        resolve_source(_spec(source="nope"))

    class Duck:
        source = "nope"
        variable = "earthquakes"
        region_key = "california"

    with pytest.raises(ValueError, match="unknown source"):
        resolve_source(Duck())


def test_explain_source_mentions_comcat():
    msg = explain_source(_spec(source_reason=""))
    assert "comcat" in msg and "ComCat" in msg


def test_fetch_for_source_comcat_import_error_path(monkeypatch):
    # Honest upgrade message when the survey-currents peer (or the
    # module) is missing — mirrors the existing source tests.
    import importlib
    real = importlib.import_module

    def boom(name, *a, **k):
        if name == "currents.earthquakes":
            raise ImportError("No module named 'currents' (simulated)")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", boom)
    with pytest.raises(ImportError) as excinfo:
        fetch_for_source("comcat")
    msg = str(excinfo.value)
    assert "survey-currents>=0.15.0" in msg
    assert "currents.earthquakes.fetch_earthquakes" in msg
    assert "pip install" in msg


def test_fetch_for_source_comcat_unknown():
    with pytest.raises(ValueError, match="unknown source"):
        fetch_for_source("comcat-typo")


def test_variable_unit_registered():
    assert _VARIABLE_UNITS["earthquakes"] == "M"


# ---------------------------------------------------------------------------
# Pure helpers: sizing, depth bins, normalization, counts
# ---------------------------------------------------------------------------

def test_marker_area_power_law():
    # Documented rule: area = 80 * 10**(0.75*(M-4)); each +1 M unit
    # multiplies the area by 10**0.75 ≈ 5.62.
    a4, a5 = _quake_marker_area(4.0), _quake_marker_area(5.0)
    assert a4 == pytest.approx(80.0)
    assert a5 / a4 == pytest.approx(10.0 ** 0.75)
    assert _quake_marker_area(6.0) > _quake_marker_area(5.0)


def test_marker_area_unknown_magnitude():
    assert _quake_marker_area(None) == pytest.approx(40.0)
    assert _quake_marker_area(float("nan")) == pytest.approx(40.0)


def test_depth_bins():
    assert _quake_depth_bin(10.0)[0] == "shallow"
    assert _quake_depth_bin(69.9)[0] == "shallow"
    assert _quake_depth_bin(70.0)[0] == "intermediate"
    assert _quake_depth_bin(299.9)[0] == "intermediate"
    assert _quake_depth_bin(300.0)[0] == "deep"
    assert _quake_depth_bin(None)[0] == "unknown"
    assert _quake_depth_bin(float("nan"))[0] == "unknown"
    assert _quake_depth_bin(-5.0)[0] == "unknown"


def test_normalize_quake_field_dict():
    events, skipped = _normalize_quake_field(_quake_dict(n_events=3))
    assert skipped == 0
    assert len(events) == 3
    assert events[0]["event_id"] == "us1"
    assert events[0]["time"] == dt.datetime(2026, 6, 3, 10, 0, 0)
    assert events[2]["depth_km"] == pytest.approx(650.0)
    assert events[2]["event_type"] == "quarry blast"


def test_normalize_quake_field_object():
    class Ev:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class QF:
        events = [Ev(event_id="q1", time="2026-06-04T00:00:00Z",
                     lat=42.0, lon=-83.0, depth_km=5.0, magnitude=4.4,
                     mag_type="ml", place="p", event_type="earthquake")]

    events, skipped = _normalize_quake_field(QF())
    assert skipped == 0
    assert events[0]["event_id"] == "q1"
    assert events[0]["time"].date() == dt.date(2026, 6, 4)


def test_normalize_quake_field_skips_bad_time():
    events, skipped = _normalize_quake_field(_quake_dict(bad_time=True))
    assert skipped == 1
    assert len(events) == 3


def test_daily_counts_and_largest():
    events, _ = _normalize_quake_field(_quake_dict(n_events=3))
    dates, counts = _quake_daily_counts(events, dt.date(2026, 6, 1),
                                        dt.date(2026, 6, 10))
    assert counts[2] == 1 and counts[4] == 2 and sum(counts) == 3
    largest = _quake_largest(events, 5)
    assert [e["event_id"] for e in largest] == ["us3", "us1", "us2"]
    # None magnitude sorts last.
    events_none, _ = _normalize_quake_field(
        {"quake_events": [{"event_id": "n", "time": "2026-06-02T00:00:00",
                           "lat": 1.0, "lon": 1.0, "depth_km": None,
                           "magnitude": None, "mag_type": "", "place": "",
                           "event_type": "earthquake"}]})
    assert _quake_largest(events + events_none, 5)[-1]["event_id"] == "n"


def test_frame_dates_daily():
    dates = _quake_frame_dates(_spec())
    assert dates[0] == dt.date(2026, 6, 1)
    assert dates[-1] == dt.date(2026, 6, 10)
    assert len(dates) == 10


# ---------------------------------------------------------------------------
# Renderer smoke: frames, manifest, cumulative, empty
# ---------------------------------------------------------------------------

def test_render_quake_frames_and_manifest(tmp_path):
    frames, mp = render_viz(_spec(), _quake_dict(n_events=3),
                            out_dir=str(tmp_path / "f"))
    assert len(frames) == 10
    assert all(p.endswith(".png") for p in frames)
    m = json.load(open(mp))["render"]
    assert m["n_events"] == 3
    assert m["empty"] is False
    assert m["cumulative"] is True
    assert m["daily_counts"] == [0, 0, 1, 0, 2, 0, 0, 0, 0, 0]
    assert m["min_magnitude"] == 4.0
    assert "not a forecast" in m["catalog_note"]
    assert "observed events" in m["catalog_note"]
    # Largest-events readout: sorted by magnitude, with place + depth.
    largest = m["largest"]
    assert [e["event_id"] for e in largest] == ["us3", "us1", "us2"]
    assert largest[0]["place"] == "deep event"
    assert largest[0]["depth_km"] == pytest.approx(650.0)
    assert largest[0]["event_type"] == "quarry blast"
    # ComCat provenance rides through.
    assert m["comcat"]["n_events"] == 3
    assert m["comcat"]["request_urls"][0].startswith(
        "https://earthquake.usgs.gov/fdsnws/event/1/query")


def test_render_quake_empty_field(tmp_path):
    frames, mp = render_viz(_spec(), _quake_dict(empty=True),
                            out_dir=str(tmp_path / "f"))
    assert len(frames) == 10
    m = json.load(open(mp))["render"]
    assert m["empty"] is True
    assert m["n_events"] == 0
    assert m["largest"] == []
    assert m["daily_counts"] == [0] * 10
    assert "not a forecast" in m["catalog_note"]  # honesty note still present


def test_render_quake_cumulative_daily_counts(tmp_path):
    # Frame 3 (June 3) sees only the first event; the manifest's daily
    # counts place the June-5 doublet at index 4.
    events, _ = _normalize_quake_field(_quake_dict(n_events=3))
    shown_jun3 = [e for e in events if e["time"].date() <= dt.date(2026, 6, 3)]
    shown_jun5 = [e for e in events if e["time"].date() <= dt.date(2026, 6, 5)]
    assert [e["event_id"] for e in shown_jun3] == ["us1"]
    assert [e["event_id"] for e in shown_jun5] == ["us1", "us2", "us3"]
    frames, _ = render_viz(_spec(), _quake_dict(n_events=3),
                           out_dir=str(tmp_path / "f"))
    assert len(frames) == 10  # one frame per day


def test_render_quake_event_outside_window_ignored(tmp_path):
    field = _quake_dict(n_events=1)
    field["quake_events"].append(
        {"event_id": "far", "time": "2026-07-01T00:00:00",
         "lat": 42.0, "lon": -83.0, "depth_km": 10.0,
         "magnitude": 5.0, "mag_type": "ml", "place": "x",
         "event_type": "earthquake"})
    events, _ = _normalize_quake_field(field)
    _, counts = _quake_daily_counts(events, dt.date(2026, 6, 1),
                                    dt.date(2026, 6, 10))
    assert sum(counts) == 1  # the July event is never counted
    frames, mp = render_viz(_spec(), field, out_dir=str(tmp_path / "f"))
    m = json.load(open(mp))["render"]
    assert m["n_events"] == 2  # still listed honestly ...
    assert sum(m["daily_counts"]) == 1  # ... but only in-window events render


def test_render_quake_from_dict_round_trip(tmp_path):
    spec = _spec()
    assert VizSpec.from_dict(spec.to_dict()) == spec
    # Old (pre-0.14.0) serialized specs without earthquake wording
    # still load unchanged.
    old = {"title": "t", "region_key": "lake-erie",
           "bbox": [-83.5, 41.2, -78.8, 43.0], "variable": "streamflow",
           "start": "2026-06-01", "end": "2026-06-10",
           "cadence": "daily", "layout": "reel-vertical",
           "style": "reel-dark", "vmin": None, "vmax": None,
           "source": "usgs", "source_reason": "x", "overlays": [],
           "context": [], "underlay": True,
           "storm_name": "", "storm_rank": "", "storm_top_n": None}
    assert VizSpec.from_dict(old).variable == "streamflow"
