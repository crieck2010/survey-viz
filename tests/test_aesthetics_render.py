"""Tests for the mapped.earth aesthetic preset path (viz.aesthetic_render).

Skipped entirely if matplotlib/numpy or the survey-aesthetics peer are
missing. The basemap underlay is stubbed offline by tests/conftest.py.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")
pytest.importorskip("aesthetics")

from viz.aesthetic_render import AESTHETIC_PRESETS, render_preset_viz
from viz.render import render_viz
from viz.spec import VizSpec

BBOX = (-92.5, 46.0, -84.5, 48.8)  # Lake Superior: elongated E-W


def _spec(**over):
    kw = dict(
        title="Lake Superior",
        region_key="lake-superior",
        bbox=BBOX,
        variable="sst",
        start=dt.date(2026, 9, 1),
        end=dt.date(2026, 9, 3),
        cadence="daily",
    )
    kw.update(over)
    return VizSpec(**kw)


def _scalar_field(n_days=3, ny=8, nx=10, seed=11):
    rng = np.random.default_rng(seed)
    lats = np.linspace(46.0, 48.8, ny)
    lons = np.linspace(-92.5, -84.5, nx)
    times = [dt.date(2026, 9, d) for d in range(1, n_days + 1)]
    values = rng.normal(10, 3, size=(n_days, ny, nx))
    return {"times": times, "lats": lats, "lons": lons, "values": values}


class _FakeCurrents:
    """CurrentField-shaped duck-type with u/v/temperature."""

    def __init__(self, n_days=2, ny=8, nx=10, temperature=True):
        rng = np.random.default_rng(5)
        self.lats = np.linspace(46.0, 48.8, ny)
        self.lons = np.linspace(-92.5, -84.5, nx)
        self.times = [f"2026-09-{d:02d}T00:00:00" for d in range(1, n_days + 1)]
        nt = len(self.times)
        self.u = rng.normal(0, 0.3, (nt, ny, nx))
        self.v = rng.normal(0, 0.3, (nt, ny, nx))
        self.temperature = (rng.normal(15, 3, (nt, ny, nx))
                            if temperature else None)
        self.provenance = {"source": "NOAA LMHOFS"}


def _storm_field():
    tracks = []
    for s in range(2):
        n = 4
        tracks.append({
            "name": f"Storm{s}", "sid": f"202626{s}N12345",
            # Deliberately timezone-naive ISO strings (IBTrACS-style).
            "times": [f"2026-09-0{d + 1}T{h:02d}:00:00"
                      for d, h in [(1, 0), (1, 12), (2, 0), (2, 12)][:n]],
            "lats": [25.0 + i + s for i in range(n)],
            "lons": [-80.0 + 2 * i for i in range(n)],
            "winds": [40.0 + 15 * i for i in range(n)],
        })
    return {"storm_tracks": tracks, "provenance": {"source": "IBTrACS"}}


def _quake_field():
    events = []
    for d in (1, 2, 3):
        for k in range(3):
            events.append({
                "event_id": f"e{d}{k}",
                "time": f"2026-09-{d:02d}T{k * 8:02d}:00:00Z",
                "lat": 46.5 + 0.1 * d, "lon": -89.0 + 0.1 * k,
                "depth_km": 10.0, "magnitude": 2.5 + 0.5 * k,
                "mag_type": "ml", "place": "test",
                "event_type": "earthquake"})
    return {"quake_events": events, "provenance": {"source": "USGS ComCat"}}


def _png_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Backwards compatibility: the legacy path is untouched
# ---------------------------------------------------------------------------

def test_legacy_path_byte_identical_with_new_kwargs(tmp_path):
    """preset=None + the new kwargs at defaults reproduces legacy output."""
    field = _scalar_field()
    spec = _spec()
    a, _ = render_viz(spec, field, None, out_dir=tmp_path / "a",
                      underlay=False)
    b, _ = render_viz(spec, field, None, out_dir=tmp_path / "b",
                      underlay=False, preset=None, rotation=None,
                      watermark=None, subtitle=None, encoding_line=True)
    assert len(a) == len(b) == 3
    for fa, fb in zip(a, b):
        assert _png_bytes(fa) == _png_bytes(fb)


def test_preset_names():
    assert set(AESTHETIC_PRESETS) == {"dark_flow", "dark_glow", "paper_prism",
                                      "dark_strands", "surface"}


# ---------------------------------------------------------------------------
# Fail-fast validation
# ---------------------------------------------------------------------------

def test_unknown_preset_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown preset"):
        render_viz(_spec(variable="currents"), _FakeCurrents(), None,
                   out_dir=tmp_path, preset="nope", underlay=False)


def test_preset_variable_mismatch_raises(tmp_path):
    # dark_flow needs vector data; sst has none.
    with pytest.raises(ValueError, match="no compatible data"):
        render_viz(_spec(variable="sst"), _scalar_field(), None,
                   out_dir=tmp_path, preset="dark_flow", underlay=False)
    # dark_glow needs events.
    with pytest.raises(ValueError, match="no compatible data"):
        render_viz(_spec(variable="sst"), _scalar_field(), None,
                   out_dir=tmp_path, preset="dark_glow", underlay=False)


def test_rotation_needs_preset(tmp_path):
    with pytest.raises(ValueError, match="need preset"):
        render_viz(_spec(), _scalar_field(), None, out_dir=tmp_path,
                   rotation="auto", underlay=False)
    with pytest.raises(ValueError, match="rotation"):
        render_viz(_spec(), _scalar_field(), None, out_dir=tmp_path,
                   rotation="bogus", preset="paper_prism", underlay=False)


def test_story_captions_and_canvas_rejected_with_preset(tmp_path):
    spec = _spec(variable="tp")
    with pytest.raises(ValueError, match="story_captions"):
        render_viz(spec, _scalar_field(), None, out_dir=tmp_path,
                   preset="paper_prism", story_captions=True,
                   underlay=False)
    with pytest.raises(ValueError, match="canvas"):
        render_viz(spec, _scalar_field(), None, out_dir=tmp_path,
                   preset="paper_prism", canvas={"platform": "x"},
                   underlay=False)


def test_missing_peer_raises_honest_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "aesthetics", None)
    with pytest.raises(RuntimeError, match="survey-aesthetics"):
        render_viz(_spec(variable="currents"), _FakeCurrents(), None,
                   out_dir=tmp_path, preset="dark_flow", underlay=False)


# ---------------------------------------------------------------------------
# dark_flow
# ---------------------------------------------------------------------------

def test_dark_flow_renders_and_manifest(tmp_path):
    spec = _spec(variable="currents")
    frames, manifest_path = render_viz(
        spec, _FakeCurrents(), None, out_dir=tmp_path / "f",
        preset="dark_flow", underlay=False)
    assert len(frames) == 2
    from PIL import Image
    assert Image.open(frames[0]).size == (1080, 1920)
    manifest = json.loads(Path(manifest_path).read_text())
    r = manifest["render"]
    assert r["preset"] == "dark_flow"
    assert r["preset_cmap"] == "turbo"
    assert r["rotation_deg"] == 0.0
    assert r["watermark"] is None
    assert r["vmin"] < r["vmax"]
    assert r["speed_max"] > 0
    assert r["scalar"] == "water temperature (°F)"
    assert "survey-aesthetics" in r["engine"]


def test_dark_flow_fixed_scales_from_full_dataset(tmp_path):
    # robust_scale=False pins the legacy min/max behavior; the default
    # (robust_scale=True) uses the autopilot p2/p98 limits — covered in
    # tests/test_autopilot_wiring.py.
    field = _FakeCurrents()
    spec = _spec(variable="currents")
    _, manifest_path = render_viz(spec, field, None, out_dir=tmp_path,
                                  preset="dark_flow", underlay=False,
                                  robust_scale=False)
    r = json.loads(Path(manifest_path).read_text())["render"]
    expected = np.asarray(field.temperature) * 9.0 / 5.0 + 32.0
    assert r["vmin"] == round(float(np.nanmin(expected)), 1)
    assert r["vmax"] == round(float(np.nanmax(expected)), 1)
    assert r["scale_method"] == "minmax"


def test_dark_flow_deterministic(tmp_path):
    spec = _spec(variable="currents")
    a, _ = render_viz(spec, _FakeCurrents(), None, out_dir=tmp_path / "a",
                      preset="dark_flow", underlay=False)
    b, _ = render_viz(spec, _FakeCurrents(), None, out_dir=tmp_path / "b",
                      preset="dark_flow", underlay=False)
    for fa, fb in zip(a, b):
        assert _png_bytes(fa) == _png_bytes(fb)


def test_dark_flow_without_temperature_falls_back_to_speed(tmp_path):
    spec = _spec(variable="currents")
    frames, manifest_path = render_viz(
        spec, _FakeCurrents(temperature=False), None, out_dir=tmp_path,
        preset="dark_flow", underlay=False)
    assert len(frames) == 2
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["scalar"] == "current speed (m/s)"


def test_dark_flow_explicit_vmin_vmax_win(tmp_path):
    spec = _spec(variable="currents", vmin=32.0, vmax=80.0)
    _, manifest_path = render_viz(spec, _FakeCurrents(), None,
                                  out_dir=tmp_path, preset="dark_flow",
                                  underlay=False)
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["vmin"] == 32.0 and r["vmax"] == 80.0


def test_dark_flow_cmap_override(tmp_path):
    spec = _spec(variable="currents")
    _, manifest_path = render_viz(spec, _FakeCurrents(), None,
                                  out_dir=tmp_path, preset="dark_flow",
                                  cmap="viridis", underlay=False)
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["cmap"] == "viridis"
    assert r["cmap_overridden"] is True


# ---------------------------------------------------------------------------
# dark_glow
# ---------------------------------------------------------------------------

def test_dark_glow_quakes(tmp_path):
    spec = _spec(variable="earthquakes")
    frames, manifest_path = render_viz(
        spec, _quake_field(), None, out_dir=tmp_path / "f",
        preset="dark_glow", underlay=False)
    assert len(frames) == 3  # one frame per day, Sep 1-3
    from PIL import Image
    assert Image.open(frames[0]).size == (1080, 1920)
    manifest = json.loads(Path(manifest_path).read_text())
    r = manifest["render"]
    assert r["preset"] == "dark_glow"
    assert r["preset_cmap"] == "inferno"
    assert "magnitude" in r["encoding"].lower()
    # Frames are cumulative: later frames differ from earlier ones.
    assert _png_bytes(frames[0]) != _png_bytes(frames[-1])


def test_dark_glow_deterministic(tmp_path):
    spec = _spec(variable="earthquakes")
    a, _ = render_viz(spec, _quake_field(), None, out_dir=tmp_path / "a",
                      preset="dark_glow", underlay=False)
    b, _ = render_viz(spec, _quake_field(), None, out_dir=tmp_path / "b",
                      preset="dark_glow", underlay=False)
    for fa, fb in zip(a, b):
        assert _png_bytes(fa) == _png_bytes(fb)


def test_dark_glow_empty_events(tmp_path):
    spec = _spec(variable="earthquakes")
    field = {"quake_events": [], "provenance": {}}
    frames, _ = render_viz(spec, field, None, out_dir=tmp_path,
                           preset="dark_glow", underlay=False)
    assert len(frames) == 3  # honest empty frames, never a crash


def test_dark_glow_storm_tracks(tmp_path):
    spec = _spec(variable="storm-tracks",
                 bbox=(-85.0, 20.0, -65.0, 35.0), region_key="gulf-of-mexico")
    frames, manifest_path = render_viz(
        spec, _storm_field(), None, out_dir=tmp_path,
        preset="dark_glow", underlay=False)
    assert len(frames) == 3
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert "wind" in r["encoding"]
    assert _png_bytes(frames[0]) != _png_bytes(frames[-1])  # cumulative


# ---------------------------------------------------------------------------
# paper_prism
# ---------------------------------------------------------------------------

def test_paper_prism_renders_and_manifest(tmp_path):
    spec = _spec(variable="tp")
    frames, manifest_path = render_viz(
        spec, _scalar_field(), None, out_dir=tmp_path / "f",
        preset="paper_prism", underlay=False)
    assert len(frames) == 3
    from PIL import Image
    assert Image.open(frames[0]).size == (1080, 1920)
    manifest = json.loads(Path(manifest_path).read_text())
    r = manifest["render"]
    assert r["preset"] == "paper_prism"
    assert r["preset_cmap"] == "Blues"
    assert r["vmin"] == 0.0  # precipitation is zero-based
    assert r["prism_grid"] == [8, 10]


def test_paper_prism_downsamples_large_grids(tmp_path):
    spec = _spec(variable="tp")
    field = _scalar_field(ny=100, nx=140)
    _, manifest_path = render_viz(spec, field, None, out_dir=tmp_path,
                                  preset="paper_prism", underlay=False)
    r = json.loads(Path(manifest_path).read_text())["render"]
    ny, nx = r["prism_grid"]
    assert ny <= 44 and nx <= 60


def test_paper_prism_deterministic(tmp_path):
    spec = _spec(variable="tp")
    a, _ = render_viz(spec, _scalar_field(), None, out_dir=tmp_path / "a",
                      preset="paper_prism", underlay=False)
    b, _ = render_viz(spec, _scalar_field(), None, out_dir=tmp_path / "b",
                      preset="paper_prism", underlay=False)
    for fa, fb in zip(a, b):
        assert _png_bytes(fa) == _png_bytes(fb)


# ---------------------------------------------------------------------------
# Rotation, watermark, subtitle, encoding line
# ---------------------------------------------------------------------------

def test_rotation_auto_elongated_bbox(tmp_path):
    spec = _spec(variable="tp")  # BBOX is ~3x wider than tall
    _, manifest_path = render_viz(spec, _scalar_field(), None,
                                  out_dir=tmp_path, preset="paper_prism",
                                  rotation="auto", underlay=False)
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["rotation_requested"] == "auto"
    assert r["rotation_deg"] == pytest.approx(90.0)


def test_rotation_auto_square_bbox_is_zero(tmp_path):
    spec = _spec(variable="tp", bbox=(-10.0, 40.0, 10.0, 60.0),
                 region_key="custom")
    _, manifest_path = render_viz(spec, _scalar_field(), None,
                                  out_dir=tmp_path, preset="paper_prism",
                                  rotation="auto", underlay=False)
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["rotation_deg"] == pytest.approx(0.0, abs=1.0)


def test_rotation_manual_degrees(tmp_path):
    spec = _spec(variable="tp")
    frames, manifest_path = render_viz(
        spec, _scalar_field(), None, out_dir=tmp_path,
        preset="paper_prism", rotation=45, underlay=False)
    from PIL import Image
    assert Image.open(frames[0]).size == (1080, 1920)  # still exact canvas
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["rotation_deg"] == 45.0


def test_watermark_and_subtitle(tmp_path):
    spec = _spec(variable="tp")
    _, manifest_path = render_viz(
        spec, _scalar_field(), None, out_dir=tmp_path,
        preset="paper_prism", watermark="test_handle",
        subtitle="A custom window", underlay=False)
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["watermark"] == "test_handle"
    assert r["subtitle"] == "A custom window"


def test_default_subtitle_names_window(tmp_path):
    spec = _spec(variable="tp")
    _, manifest_path = render_viz(spec, _scalar_field(), None,
                                  out_dir=tmp_path, preset="paper_prism",
                                  underlay=False)
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["subtitle"] == "1–3 September 2026"


def test_encoding_line_toggle(tmp_path):
    spec = _spec(variable="tp")
    _, manifest_path = render_viz(
        spec, _scalar_field(), None, out_dir=tmp_path,
        preset="paper_prism", encoding_line=False, underlay=False)
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["encoding_line"] is False


def _wind_field(n_days=2, ny=8, nx=10, seed=21):
    """GFS-wind-shaped dict: u10/v10 grids + 2 m air temperature (°F)."""
    rng = np.random.default_rng(seed)
    lats = np.linspace(30.0, 50.0, ny)
    lons = np.linspace(-100.0, -80.0, nx)
    times = [dt.date(2026, 9, 30) + dt.timedelta(days=d)
             for d in range(n_days)]
    shape = (n_days, ny, nx)
    return {
        "times": times,
        "lats": lats,
        "lons": lons,
        "grids": {
            "u10": rng.normal(0, 5, shape),
            "v10": rng.normal(0, 5, shape),
        },
        "air_temperature": rng.normal(60, 15, shape),
        "temperature_unit": "°F",
    }


def test_dark_strands_wind_encoding_names_air_temperature(tmp_path,
                                                         monkeypatch):
    """Regression: the wind-strand honesty line must say AIR TEMPERATURE.

    The strands are colored by 2 m air temperature (the warming.watch
    convention); an earlier build drew 'COLOR = WIND SPEED' while the
    legend said AIR TEMPERATURE. With bivariate=True (the default) the
    line is two-channel: COLOR = AIR TEMPERATURE, BRIGHTNESS = WIND
    SPEED. Assert both the drawn line (via a spy on the aesthetics
    helper) and the manifest agree.
    """
    pytest.importorskip("flow")  # survey-flow peer advects the strands
    import aesthetics

    drawn = {}

    def spy(ax, x, y, text, **kw):
        drawn["text"] = text

    monkeypatch.setattr(aesthetics, "encoding_statement", spy)
    spec = _spec(variable="wind", title="North American Winds",
                 region_key="north-america",
                 bbox=(-100.0, 30.0, -80.0, 50.0),
                 start=dt.date(2026, 9, 30), end=dt.date(2026, 10, 1))
    frames, manifest_path = render_viz(
        spec, _wind_field(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False, landmask=False)
    assert frames, "expected rendered strand frames"
    assert (drawn.get("text")
            == "COLOR = AIR TEMPERATURE, BRIGHTNESS = WIND SPEED")
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert (r["encoding"]
            == "COLOR = AIR TEMPERATURE, BRIGHTNESS = WIND SPEED")


def test_dark_strands_wind_encoding_flat_when_bivariate_false(
        tmp_path, monkeypatch):
    """bivariate=False restores the exact old single-variable look.

    No brightness channel is encoded: the honesty line is the old
    single-variable line and the manifest records bivariate=False.
    """
    pytest.importorskip("flow")  # survey-flow peer advects the strands
    import aesthetics

    drawn = {}

    def spy(ax, x, y, text, **kw):
        drawn["text"] = text

    monkeypatch.setattr(aesthetics, "encoding_statement", spy)
    spec = _spec(variable="wind", title="North American Winds",
                 region_key="north-america",
                 bbox=(-100.0, 30.0, -80.0, 50.0),
                 start=dt.date(2026, 9, 30), end=dt.date(2026, 10, 1))
    frames, manifest_path = render_viz(
        spec, _wind_field(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False, landmask=False,
        bivariate=False)
    assert frames, "expected rendered strand frames"
    assert drawn.get("text") == "COLOR = AIR TEMPERATURE"
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["encoding"] == "COLOR = AIR TEMPERATURE"
    assert r["bivariate"] is False


def test_dark_strands_bivariate_manifest_records_speed_scale(tmp_path):
    """Bivariate: the manifest carries the reel-wide fixed speed scale.

    speed_vmin/speed_vmax are the brightness channel's scale in m/s
    (never per-frame — that would flicker) and bivariate=True.
    """
    pytest.importorskip("flow")
    spec = _wind_spec()
    frames, manifest_path = render_viz(
        spec, _wind_field(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False, landmask=False,
        strand_count=100)
    assert frames, "expected rendered strand frames"
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["bivariate"] is True
    smin, smax = r["speed_vmin"], r["speed_vmax"]
    assert 0.0 <= smin < smax  # speeds are non-negative magnitudes
    assert smax - smin < 1e9  # finite scale


def test_dark_strands_brightness_reaches_engine(tmp_path, monkeypatch):
    """Bivariate: a per-trail (n,) brightness in [0,1] reaches render_strands.

    Sampled at the trail heads, on the reel-wide fixed speed scale.
    """
    pytest.importorskip("flow")
    import aesthetics

    seen = {}

    def spy(trails, values, **kw):
        seen["kwargs"] = kw
        seen["n_trails"] = len(trails)
        # Return a real RGBA array so the frame pipeline keeps working.
        return np.zeros((1920, 1080, 4), dtype=np.uint8)

    monkeypatch.setattr(aesthetics, "render_strands", spy)
    frames, _ = render_viz(
        _wind_spec(), _wind_field(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False, landmask=False,
        strand_count=100)
    assert frames, "expected rendered strand frames"
    kw = seen["kwargs"]
    assert "brightness" in kw, "bivariate must pass brightness to the engine"
    b = np.asarray(kw["brightness"], dtype=float)
    assert b.shape == (seen["n_trails"],)  # per-trail (n,)
    assert np.all(np.isfinite(b))
    assert b.min() >= 0.0 and b.max() <= 1.0


def test_dark_strands_flat_omits_brightness_kwarg(tmp_path, monkeypatch):
    """bivariate=False: NO brightness kwarg is passed to the engine.

    The engine renders the exact old flat look (brightness off: gain
    1.0 everywhere).
    """
    pytest.importorskip("flow")
    import aesthetics

    seen = {}

    def spy(trails, values, **kw):
        seen["kwargs"] = kw
        return np.zeros((1920, 1080, 4), dtype=np.uint8)

    monkeypatch.setattr(aesthetics, "render_strands", spy)
    frames, _ = render_viz(
        _wind_spec(), _wind_field(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False, landmask=False,
        strand_count=100, bivariate=False)
    assert frames, "expected rendered strand frames"
    assert "brightness" not in seen["kwargs"]


def test_dark_strands_currents_bivariate_encoding(tmp_path, monkeypatch):
    """dark_strands on currents: COLOR = WATER TEMPERATURE, BRIGHTNESS =
    CURRENT SPEED (OFS-THREDDS-shaped field: u/v m/s, temperature °C,
    land→NaN)."""
    pytest.importorskip("flow")
    import aesthetics

    drawn = {}

    def spy(ax, x, y, text, **kw):
        drawn["text"] = text

    monkeypatch.setattr(aesthetics, "encoding_statement", spy)
    spec = _spec(variable="currents", region_key="lake-superior")
    frames, manifest_path = render_viz(
        spec, _FakeCurrents(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False, strand_count=100)
    assert frames, "expected rendered strand frames"
    assert (drawn.get("text")
            == "COLOR = WATER TEMPERATURE, BRIGHTNESS = CURRENT SPEED")
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert (r["encoding"]
            == "COLOR = WATER TEMPERATURE, BRIGHTNESS = CURRENT SPEED")
    assert r["bivariate"] is True
    assert 0.0 <= r["speed_vmin"] < r["speed_vmax"]


def test_dark_strands_brightness_note_under_gradient_bar(tmp_path,
                                                         monkeypatch):
    """Bivariate: the BRIGHTNESS legend note is drawn under the gradient bar.

    The note names the brightness channel and its reel-wide fixed m/s
    scale so the second encoding is inspectable on the frame.
    """
    pytest.importorskip("flow")
    from matplotlib.axes import Axes

    notes = []
    orig_text = Axes.text

    def spy_text(self, x, y, s, **kw):
        if isinstance(s, str) and s.startswith("BRIGHTNESS ="):
            notes.append(s)
        return orig_text(self, x, y, s, **kw)

    monkeypatch.setattr(Axes, "text", spy_text)
    frames, _ = render_viz(
        _wind_spec(), _wind_field(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False, landmask=False,
        strand_count=100, place_labels=False)
    assert frames, "expected rendered strand frames"
    assert notes, "expected a BRIGHTNESS legend note under the bar"
    assert notes[0].startswith("BRIGHTNESS = WIND SPEED (")
    assert "M/S" in notes[0]


def _wind_spec(**over):
    kw = dict(variable="wind", title="North American Winds",
              region_key="north-america",
              bbox=(-100.0, 30.0, -80.0, 50.0),
              start=dt.date(2026, 9, 30), end=dt.date(2026, 10, 1))
    kw.update(over)
    return _spec(**kw)


def test_dark_strands_wind_landmask_manifest(tmp_path, monkeypatch):
    """dark_strands on wind clips to the land mask; the manifest records it."""
    pytest.importorskip("flow")  # survey-flow peer advects the strands
    import viz.underlay as _ul

    calls = []

    def fake_landmask(bbox, lats, lons, **kw):
        calls.append(tuple(bbox))
        ny = int(np.asarray(lats).ravel().size)
        nx = int(np.asarray(lons).ravel().size)
        return {
            "status": "ok", "reason": "",
            "mask": np.ones((ny, nx), dtype=bool),
            "provenance": {"scale": "50m", "n_polygons": 3,
                           "n_rings": 5, "cache": "Miss"},
        }

    monkeypatch.setattr(_ul, "fetch_landmask", fake_landmask)
    frames, manifest_path = render_viz(
        _wind_spec(), _wind_field(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False)
    assert frames, "expected rendered strand frames"
    assert calls == [(-100.0, 30.0, -80.0, 50.0)]
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["mask"] == "land"
    assert r["landmask"]["requested"] is True
    assert r["landmask"]["status"] == "ok"
    assert r["landmask"]["scale"] == "50m"
    assert r["landmask"]["n_polygons"] == 3


def test_dark_strands_wind_landmask_disabled(tmp_path, monkeypatch):
    """landmask=False: no land fetch attempted, no mask in the manifest."""
    pytest.importorskip("flow")
    import viz.underlay as _ul

    def _boom(*a, **k):
        raise AssertionError("fetch_landmask must not be called")

    monkeypatch.setattr(_ul, "fetch_landmask", _boom)
    frames, manifest_path = render_viz(
        _wind_spec(), _wind_field(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False, landmask=False)
    assert frames, "expected rendered strand frames"
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["mask"] is None
    assert r["landmask"]["requested"] is False
    assert r["landmask"]["status"] is None


def test_dark_strands_wind_landmask_unavailable_falls_back(tmp_path,
                                                           monkeypatch):
    """Land fetch unavailable -> unclipped strands, honestly recorded."""
    pytest.importorskip("flow")
    import viz.underlay as _ul

    def fake_unavailable(bbox, lats, lons, **kw):
        return {"status": "unavailable", "reason": "no network",
                "mask": None,
                "provenance": {"scale": "50m", "n_polygons": 0,
                               "n_rings": 0, "cache": "Miss"}}

    monkeypatch.setattr(_ul, "fetch_landmask", fake_unavailable)
    frames, manifest_path = render_viz(
        _wind_spec(), _wind_field(), None, out_dir=tmp_path,
        preset="dark_strands", underlay=False)
    assert frames, "expected rendered strand frames"
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["mask"] == "land (unavailable — unclipped)"
    assert r["landmask"]["requested"] is True
    assert r["landmask"]["status"] == "unavailable"


def test_robust_vmax_ignores_single_extreme_cell():
    """One extreme speed cell must not crush the brightness scale."""
    from viz.aesthetic_render import _robust_vmax
    arr = np.full((100, 100), 5.0)
    arr[0, 0] = 1000.0  # model gust front / cyclone cell
    vmax = _robust_vmax(np, arr)
    assert vmax == pytest.approx(5.0)
    assert _robust_vmax(np, np.full((4, 4), 0.0)) == 1.0  # degenerate


def test_dark_strands_brightness_scale_is_percentile_not_max(tmp_path):
    """Bivariate brightness vmax is the reel-wide p99, not the raw max."""
    pytest.importorskip("flow")
    field = _wind_field()
    field["grids"]["u10"][0, 0, 0] = 200.0  # extreme cell
    spec = _spec(variable="wind", title="Winds",
                 region_key="north-america",
                 bbox=(-100.0, 30.0, -80.0, 50.0),
                 start=dt.date(2026, 9, 30), end=dt.date(2026, 10, 1))
    frames, manifest_path = render_viz(
        spec, field, None, out_dir=tmp_path,
        preset="dark_strands", underlay=False, landmask=False)
    assert frames
    r = json.loads(Path(manifest_path).read_text())["render"]
    assert r["speed_vmax_percentile"] == 99.0
    assert r["speed_vmax"] < 200.0
    assert r["speed_vmax"] > 0
