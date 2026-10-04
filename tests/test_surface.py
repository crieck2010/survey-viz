"""Tests for the surface preset + counter + headline beats (v0.28.0).

Skipped entirely if matplotlib/numpy or the survey-aesthetics peer are
missing. The basemap underlay is stubbed offline by tests/conftest.py;
surface renders pass ``landmask=False`` (or monkeypatch the landmask
fetch) so no test touches the network.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")
pytest.importorskip("aesthetics")

from viz.aesthetic_render import (
    AESTHETIC_PRESETS,
    _counter_stats,
    _format_counter_value,
    _interpolate_counter_stat,
    _normalize_headline_beats,
    _beat_text_for_frame,
    render_preset_viz,
)
from viz.render import render_viz
from viz.spec import VizSpec

BBOX = (6.5, 35.0, 18.5, 47.5)  # Italy-ish


def _spec(**over):
    kw = dict(
        title="Where Italy lives",
        region_key="italy",
        bbox=BBOX,
        variable="sst",
        start=dt.date(2026, 9, 1),
        end=dt.date(2026, 9, 6),
        cadence="daily",
    )
    kw.update(over)
    return VizSpec(**kw)


def _field(n_days=3, ny=12, nx=14, seed=7):
    rng = np.random.default_rng(seed)
    # Ascending lats (south -> north), like _normalize_field inputs.
    lats = np.linspace(35.0, 47.5, ny)
    lons = np.linspace(6.5, 18.5, nx)
    times = [dt.date(2026, 9, d) for d in range(1, n_days + 1)]
    lon2d, lat2d = np.meshgrid(lons, lats)
    base = np.exp(-(((lat2d - 45.5) ** 2) / 4.0 + ((lon2d - 9.2) ** 2) / 4.0)) * 100
    base += np.exp(-(((lat2d - 41.9) ** 2) / 3.0 + ((lon2d - 12.5) ** 2) / 3.0)) * 60
    values = np.stack([base * (1.0 + 0.1 * i) + rng.normal(0, 0.5, base.shape)
                       for i in range(n_days)])
    values[:, :2, :] = np.nan  # ocean strip
    return {"times": times, "lats": lats, "lons": lons, "values": values}


def _render(tmp_path, spec=None, field=None, **kw):
    kw.setdefault("underlay", False)
    kw.setdefault("landmask", False)
    kw.setdefault("place_labels", False)
    return render_viz(spec or _spec(), field or _field(), None,
                      out_dir=tmp_path, preset="surface", **kw)


# ---------------------------------------------------------------------------
# Preset registration + rendering
# ---------------------------------------------------------------------------

def test_surface_in_presets():
    assert "surface" in AESTHETIC_PRESETS


def test_surface_renders_frames_and_manifest(tmp_path):
    frames, manifest_path = _render(tmp_path)
    assert len(frames) == 3
    from PIL import Image
    assert Image.open(frames[0]).size == (1080, 1920)
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["preset"] == "surface"
    assert r["surface"]["cmap"] == "teal_pink"
    assert r["surface"]["scale"] == "linear"
    assert r["surface"]["contours"] is True
    assert r["surface"]["shadow"] is True
    assert r["surface"]["peer_fallback"] is False
    assert r["vmin"] < r["vmax"]


def test_surface_fixed_vmin_vmax_across_frames(tmp_path):
    # The manifest scale is computed once from the whole stack.
    field = _field()
    _, manifest_path = _render(tmp_path, field=field)
    r = json.loads(Path(manifest_path).read_text())["render"]
    vals = np.asarray(field["values"], dtype=float)
    assert r["vmin"] == pytest.approx(float(np.nanmin(vals)))
    assert r["vmax"] == pytest.approx(float(np.nanmax(vals)))
    assert r["surface"]["vmin"] == r["vmin"]
    assert r["surface"]["vmax"] == r["vmax"]


def test_surface_log_scale(tmp_path):
    frames, manifest_path = _render(tmp_path, surface_scale="log")
    assert len(frames) == 3
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["surface"]["scale"] == "log"
    assert r["surface"]["vmin"] > 0


def test_surface_contours_off_and_on(tmp_path):
    fa, _ = _render(tmp_path / "off", surface_contours=False)
    fb, _ = _render(tmp_path / "on", surface_contours=True)
    assert len(fa) == len(fb) == 3
    # Contours change the pixels.
    assert Path(fa[0]).read_bytes() != Path(fb[0]).read_bytes()


def test_surface_unknown_scale_raises(tmp_path):
    with pytest.raises(ValueError, match="surface_scale"):
        _render(tmp_path, surface_scale="sqrt")


def test_surface_cmap_override(tmp_path):
    _, manifest_path = _render(tmp_path, cmap="viridis")
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["cmap"] == "viridis"
    assert r["surface"]["cmap"] == "viridis"


def test_surface_orientation_north_up(tmp_path):
    # A hotspot only in the northernmost row must render in the top
    # half of the map, not the bottom (row 0 = top after the flip).
    field = _field()
    vals = np.zeros_like(np.asarray(field["values"], dtype=float))
    vals[:, 6:, :] = 100.0  # lats ascending: northern half rows
    vals[:, :6, :] = 1.0    # southern half
    field = dict(field, values=vals)
    frames, _ = _render(tmp_path, field=field, surface_contours=False,
                        surface_shadow=False)
    from PIL import Image
    img = np.asarray(Image.open(frames[0]).convert("RGB"), dtype=float)
    # teal_pink: high values are pink (high red), low values are dark
    # teal (low red). The northern (top) strip must be the pink one.
    top_red = img[400:700, 400:700, 0].mean()
    bottom_red = img[1300:1600, 400:700, 0].mean()
    assert top_red > bottom_red + 20


def test_orient_north_up_flips_ascending_lats():
    from viz.aesthetic_render import _orient_north_up
    grid = np.array([[1.0, 1.0], [2.0, 2.0]])  # row 0 = south
    out = _orient_north_up(np, grid, np.array([35.0, 47.0]))
    assert out[0, 0] == 2.0 and out[1, 0] == 1.0
    # Descending lats already north-first: no flip.
    out2 = _orient_north_up(np, grid, np.array([47.0, 35.0]))
    assert out2[0, 0] == 1.0


def test_surface_fallback_when_render_surface_missing(tmp_path, monkeypatch):
    import viz.aesthetic_render as ar
    monkeypatch.setattr(ar, "_render_surface_fn", lambda ae: None)
    frames, manifest_path = _render(tmp_path)
    assert len(frames) == 3
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["surface"]["peer_fallback"] is True


# ---------------------------------------------------------------------------
# Counter math
# ---------------------------------------------------------------------------

def test_counter_stats_nan_aware():
    arr = np.array([[[1.0, 2.0], [np.nan, 3.0]],
                    [[np.nan, np.nan], [np.nan, np.nan]],
                    [[10.0, -5.0], [2.0, np.nan]]])
    sums = _counter_stats(np, arr, "sum")
    assert sums[0] == pytest.approx(6.0)
    assert np.isnan(sums[1])  # no finite cells: contributes nothing
    assert sums[2] == pytest.approx(7.0)
    assert _counter_stats(np, arr, "mean")[0] == pytest.approx(2.0)
    assert _counter_stats(np, arr, "max")[0] == pytest.approx(3.0)
    assert _counter_stats(np, arr, "max")[2] == pytest.approx(10.0)


def test_counter_interpolation_midpoint_and_clamp():
    moments = [dt.datetime(2026, 9, d, tzinfo=dt.timezone.utc) for d in (1, 2, 3)]
    stats = [100.0, 200.0, 400.0]
    mid = _interpolate_counter_stat(stats, moments,
                                    dt.datetime(2026, 9, 1, 12, tzinfo=dt.timezone.utc))
    assert mid == pytest.approx(150.0)
    before = _interpolate_counter_stat(stats, moments,
                                       dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc))
    after = _interpolate_counter_stat(stats, moments,
                                      dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc))
    assert before == 100.0 and after == 400.0


def test_counter_interpolation_skips_all_nan_timestep():
    moments = [dt.datetime(2026, 9, d, tzinfo=dt.timezone.utc) for d in (1, 2, 3)]
    stats = [100.0, float("nan"), 300.0]
    mid = _interpolate_counter_stat(stats, moments,
                                    dt.datetime(2026, 9, 2, tzinfo=dt.timezone.utc))
    assert mid == pytest.approx(200.0)
    assert _interpolate_counter_stat([float("nan")], moments[:1], moments[0]) is None


def test_counter_formatting():
    assert _format_counter_value(1234567.0) == "1,234,567"
    assert _format_counter_value(1234.6) == "1,235"
    assert _format_counter_value(42.0) == "42"
    assert _format_counter_value(12.3456) == "12.3"
    assert _format_counter_value(None) == "\u2014"


def test_counter_applied_on_surface(tmp_path):
    _, manifest_path = _render(
        tmp_path, counter={"stat": "sum", "unit": "people", "label": "TOTAL"})
    r = json.loads(Path(manifest_path).read_text())["render"]
    c = r["counter"]
    assert c["applied"] is True
    assert c["spec"] == {"stat": "sum", "unit": "people", "label": "TOTAL"}
    assert c["stat_first"] is not None and c["stat_last"] is not None
    assert len(c["series"]) == 3
    assert "interpolated" in c["note"]


def test_counter_invalid_stat_raises(tmp_path):
    with pytest.raises(ValueError, match="counter"):
        _render(tmp_path, counter={"stat": "median"})


def test_counter_on_non_surface_not_applied(tmp_path):
    # paper_prism: counter is recorded as requested-but-not-applied.
    field = _field()
    _, manifest_path = render_viz(
        _spec(variable="tp"), field, None, out_dir=tmp_path,
        preset="paper_prism", underlay=False, place_labels=False,
        counter={"stat": "sum", "unit": "people", "label": "TOTAL"})
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["counter"]["applied"] is False
    assert "not applied" in r["counter"]["note"]


# ---------------------------------------------------------------------------
# Headline beats
# ---------------------------------------------------------------------------

def test_beats_normalization_fraction_and_timestamp():
    times = [dt.datetime(2026, 9, d, tzinfo=dt.timezone.utc) for d in range(1, 7)]
    beats = _normalize_headline_beats(
        [(0.0, "Start"), (0.6, "Middle"), ("2026-09-05T00:00:00Z", "Late")],
        6, times)
    assert beats[0] == {"fraction": 0.0, "frame": 0, "text": "Start"}
    assert beats[1]["frame"] == 3 and beats[1]["text"] == "Middle"  # round(0.6*5)=3
    assert beats[2]["frame"] == 4 and beats[2]["text"] == "Late"


def test_beats_clean_cut():
    beats = [{"fraction": 0.0, "frame": 0, "text": "A"},
             {"fraction": 0.6, "frame": 3, "text": "B"}]
    assert _beat_text_for_frame(beats, 0, "Default") == "A"
    assert _beat_text_for_frame(beats, 2, "Default") == "A"
    assert _beat_text_for_frame(beats, 3, "Default") == "B"
    assert _beat_text_for_frame(beats, 5, "Default") == "B"
    assert _beat_text_for_frame([], 2, "Default") == "Default"


def test_beats_same_frame_later_wins():
    beats = _normalize_headline_beats([(0.5, "First"), (0.51, "Second")], 6)
    # round(0.5*5)=2 (banker's rounding in Python: round(2.5)=2), round(0.51*5)=3
    # Use beats that definitely share a frame:
    beats = _normalize_headline_beats([(0.4, "First"), (0.41, "Second")], 6)
    assert len(beats) == 1 and beats[0]["text"] == "Second"


def test_beats_invalid():
    with pytest.raises(ValueError, match="fraction"):
        _normalize_headline_beats([(1.5, "X")], 6)
    with pytest.raises(ValueError, match="non-empty"):
        _normalize_headline_beats([(0.5, "")], 6)
    with pytest.raises(ValueError, match="timestamp"):
        _normalize_headline_beats([("not-a-date", "X")], 6,
                                  [dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)])


def test_beats_render_on_surface_and_manifest(tmp_path):
    frames, manifest_path = _render(
        tmp_path, field=_field(n_days=4),
        headline_beats=[(0.0, "Where Italy lives (synthetic demo)"),
                        (0.6, "The north pulls ahead")])
    assert len(frames) == 4
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["headline_beats"] == [
        {"fraction": 0.0, "frame": 0, "text": "Where Italy lives (synthetic demo)"},
        {"fraction": pytest.approx(0.6), "frame": 2, "text": "The north pulls ahead"},
    ]
    # Clean cut: frame before the beat differs from the beat frame
    # (title swap changes pixels in the title band).
    assert Path(frames[1]).read_bytes() != Path(frames[2]).read_bytes()


def test_beats_render_on_paper_prism(tmp_path):
    frames, manifest_path = render_viz(
        _spec(variable="tp"), _field(n_days=4), None, out_dir=tmp_path,
        preset="paper_prism", underlay=False, place_labels=False,
        headline_beats=[(0.5, "Halfway headline")])
    assert len(frames) == 4
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["headline_beats"][0]["text"] == "Halfway headline"
    assert r["headline_beats"][0]["frame"] == 2  # round(0.5*3)=2


def test_beats_invalid_via_render_raises(tmp_path):
    with pytest.raises(ValueError):
        _render(tmp_path, headline_beats=[(2.0, "Too late")])
