"""Tests for survey-aesthetics v0.2.0 integration (survey-viz v0.24.0).

Covers: the dark_strands preset (determinism, ocean-mask clipping,
fail-fast on non-vector variables), basemap style controls
(unknown-name fail-fast, pixel-level application), the collision-aware
furniture layout (no-overlap property, title shrink-to-fit), and the
legacy preset=None path staying byte-identical with the new defaults.

Skipped entirely if the peers are missing. For the strand tests the
survey-flow peer must also be importable.
"""

import datetime as dt
import sys
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")
pytest.importorskip("aesthetics")
flow_advect = pytest.importorskip("flow.advect")
pytest.importorskip("flow.fields")

from viz.aesthetic_render import (
    AESTHETIC_PRESETS,
    BASEMAP_STYLES,
    _Furniture,
    _layout_furniture,
    render_preset_viz,
)
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


def _currents_spec(**over):
    return _spec(variable="currents", **over)


class _FakeCurrents:
    """CurrentField-shaped duck-type with u/v/temperature."""

    def __init__(self, n_days=2, ny=8, nx=10, temperature=True, all_land=False):
        rng = np.random.default_rng(5)
        self.lats = np.linspace(46.0, 48.8, ny)
        self.lons = np.linspace(-92.5, -84.5, nx)
        self.times = [f"2026-09-{d:02d}T00:00:00" for d in range(1, n_days + 1)]
        nt = len(self.times)
        self.u = rng.normal(0, 0.3, (nt, ny, nx))
        self.v = rng.normal(0, 0.3, (nt, ny, nx))
        self.temperature = (rng.normal(15, 3, (nt, ny, nx))
                            if temperature else None)
        if all_land:
            self.u[:] = np.nan
            self.v[:] = np.nan
            if self.temperature is not None:
                self.temperature[:] = np.nan
        self.provenance = {"source": "NOAA LMHOFS"}


class _FakeWind:
    """WindField-shaped duck-type with u/v/air_temperature."""

    def __init__(self, n_days=2, ny=8, nx=10):
        rng = np.random.default_rng(9)
        self.lats = np.linspace(46.0, 48.8, ny)
        self.lons = np.linspace(-92.5, -84.5, nx)
        self.times = [f"2026-09-{d:02d}T00:00:00" for d in range(1, n_days + 1)]
        nt = len(self.times)
        u = rng.normal(2, 3, (nt, ny, nx))
        v = rng.normal(0, 3, (nt, ny, nx))
        self.u, self.v = u, v
        self.grids = {"u10": u, "v10": v}
        self.air_temperature = rng.normal(60, 5, (nt, ny, nx))
        self.temperature_unit = "°F"
        self.provenance = {"source": "test"}


def _quake_field():
    quakes = [{
        "time": "2026-09-01T00:00:00", "lat": 47.0, "lon": -89.0,
        "mag": 3.0, "depth_km": 10.0, "place": "Test",
    }]
    return {"earthquakes": quakes, "provenance": {"source": "ComCat"}}


def _png_bytes(path):
    return Path(path).read_bytes()


class TestDarkStrandsPreset:
    def test_preset_registered_with_currents_and_wind(self):
        assert "dark_strands" in AESTHETIC_PRESETS
        from viz.aesthetic_render import PRESET_VARIABLES
        assert set(PRESET_VARIABLES["dark_strands"]) == {"currents", "wind"}

    def test_deterministic_same_seed(self, tmp_path):
        field = _FakeCurrents()
        frames1, _ = render_preset_viz(
            _currents_spec(), field, out_dir=tmp_path / "a", preset="dark_strands",
            strand_count=200, underlay=False)
        frames2, _ = render_preset_viz(
            _currents_spec(), field, out_dir=tmp_path / "b", preset="dark_strands",
            strand_count=200, underlay=False)
        assert len(frames1) == len(frames2) == 2
        for f1, f2 in zip(frames1, frames2):
            assert _png_bytes(f1) == _png_bytes(f2), (
                f"strand render not deterministic: {f1}")

    def test_currents_clipped_by_ocean_mask(self, tmp_path):
        frames, manifest = render_preset_viz(
            _currents_spec(), _FakeCurrents(), out_dir=tmp_path / "f",
            preset="dark_strands", strand_count=200, underlay=False)
        import json
        with open(manifest, encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["render"]["mask"] == "ocean"
        assert frames and all(Path(f).is_file() for f in frames)

    def test_all_land_field_fails_honestly(self, tmp_path):
        with pytest.raises(ValueError, match="no ocean"):
            render_preset_viz(
                _currents_spec(), _FakeCurrents(all_land=True),
                out_dir=tmp_path / "f", preset="dark_strands",
                strand_count=200, underlay=False)

    def test_wind_clipped_by_land_mask(self, tmp_path, monkeypatch):
        import numpy as _np
        import viz.underlay as _ul

        def fake_landmask(bbox, lats, lons, **kw):
            ny = int(_np.asarray(lats).ravel().size)
            nx = int(_np.asarray(lons).ravel().size)
            return {"status": "ok", "reason": "",
                    "mask": _np.ones((ny, nx), dtype=bool),
                    "provenance": {"scale": "50m", "n_polygons": 4,
                                   "n_rings": 6, "cache": "Miss"}}

        monkeypatch.setattr(_ul, "fetch_landmask", fake_landmask)
        spec = _spec(variable="wind")
        frames, manifest = render_preset_viz(
            spec, _FakeWind(), out_dir=tmp_path / "f",
            preset="dark_strands", strand_count=200, underlay=False)
        import json
        with open(manifest, encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["render"]["mask"] == "land"
        assert m["render"]["landmask"]["status"] == "ok"
        assert m["render"]["landmask"]["n_polygons"] == 4
        assert m["render"]["scalar_name"] == "air_temperature"
        assert m["render"]["scalar"].startswith("air temperature")
        assert frames

    def test_wind_renders_unclipped_when_landmask_disabled(self, tmp_path):
        spec = _spec(variable="wind")
        frames, manifest = render_preset_viz(
            spec, _FakeWind(), out_dir=tmp_path / "f",
            preset="dark_strands", strand_count=200, underlay=False,
            landmask=False)
        import json
        with open(manifest, encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["render"]["mask"] is None
        assert m["render"]["landmask"]["requested"] is False
        assert frames

    def test_non_vector_variable_fails_fast(self, tmp_path):
        spec = _spec(variable="earthquakes")
        with pytest.raises(ValueError, match="dark_strands.*currents, wind"):
            render_preset_viz(
                spec, _quake_field(), out_dir=tmp_path / "f",
                preset="dark_strands", underlay=False)

    def test_strand_controls_recorded_in_manifest(self, tmp_path):
        _, manifest = render_preset_viz(
            _currents_spec(), _FakeCurrents(), out_dir=tmp_path / "f",
            preset="dark_strands", strand_count=500,
            strand_linewidth=1.8, underlay=False)
        import json
        with open(manifest, encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["render"]["strand_count"] == 500
        assert m["render"]["strand_linewidth"] == 1.8
        assert m["render"]["furniture"], "furniture placements recorded"


class TestBasemapStyles:
    def test_styles_listed(self):
        assert set(BASEMAP_STYLES) == {"void_black", "no_basemap",
                                       "subtle_land"}

    def test_unknown_basemap_fails_fast(self, tmp_path):
        with pytest.raises(ValueError, match="unknown basemap"):
            render_preset_viz(
                _currents_spec(), _FakeCurrents(), out_dir=tmp_path / "f",
                preset="dark_flow", basemap="fancy", underlay=False)

    def test_basemap_needs_preset(self, tmp_path):
        with pytest.raises(ValueError, match="need preset="):
            render_viz(_spec(), _FakeCurrents(), None,
                       out_dir=tmp_path / "f", basemap="no_basemap",
                       underlay=False)

    def test_strand_options_need_preset(self, tmp_path):
        with pytest.raises(ValueError, match="need preset="):
            render_viz(_spec(), _FakeCurrents(), None,
                       out_dir=tmp_path / "f", strand_count=5000,
                       underlay=False)
        with pytest.raises(ValueError, match="need preset="):
            render_viz(_spec(), _FakeCurrents(), None,
                       out_dir=tmp_path / "f", strand_linewidth=2.0,
                       underlay=False)

    def test_basemap_changes_pixels(self, tmp_path, monkeypatch):
        # Same render, two styles: the coastline segments come from a
        # stubbed underlay (conftest stubs it to unavailable).
        import viz.render as vr

        segs = [{"lons": [-92.0, -88.0, -85.0],
                 "lats": [47.0, 47.5, 47.2]}]

        def fake_underlay(spec, plt, np, want):
            return None, None, segs, {"status": "stubbed", "topo": False,
                                      "coastline_segments": len(segs)}
        monkeypatch.setattr(vr, "_fetch_underlay_once", fake_underlay)
        frames_void, _ = render_preset_viz(
            _currents_spec(), _FakeCurrents(), out_dir=tmp_path / "v",
            preset="dark_flow", basemap="void_black", underlay=True)
        frames_none, _ = render_preset_viz(
            _currents_spec(), _FakeCurrents(), out_dir=tmp_path / "n",
            preset="dark_flow", basemap="no_basemap", underlay=True)
        assert (_png_bytes(frames_void[0])
                != _png_bytes(frames_none[0])), (
            "void_black should draw coastlines where no_basemap draws none")

    def test_default_basemap_recorded(self, tmp_path):
        _, manifest = render_preset_viz(
            _currents_spec(), _FakeCurrents(), out_dir=tmp_path / "f",
            preset="dark_strands", strand_count=200, underlay=False)
        import json
        with open(manifest, encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["render"]["basemap"] == "no_basemap"


class TestFurnitureLayout:
    def _ctx(self, **over):
        import aesthetics as ae_mod
        preset = ae_mod.get_preset("dark_flow")
        ctx = {
            "ae": ae_mod,
            "preset": preset,
            "spec": _spec(),
            "render_size": (1080, 1920),
            "subtitle": "1-3 September 2026",
            "angle": 0.0,
            "basemap_style": ae_mod.get_basemap("void_black"),
            "watermark": None,
            "encoding_line": True,
        }
        ctx.update(over)
        return ctx

    @staticmethod
    def _rects_overlap(a, b):
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        return (ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1)

    def test_no_overlap_reference_set(self):
        placed = _layout_furniture(self._ctx())
        rects = [(k, tuple(p.rect)) for k, p in placed.items()
                 if p.placed]
        assert rects, "nothing placed"
        for i, (ka, ra) in enumerate(rects):
            for kb, rb in rects[i + 1:]:
                assert not self._rects_overlap(ra, rb), (
                    f"furniture overlap: {ka} x {kb}")

    def test_no_overlap_with_watermark_and_rotation(self):
        ctx = self._ctx(watermark="@maps", angle=17.0,
                        spec=_spec(variable="wind"))
        placed = _layout_furniture(ctx)
        rects = [(k, tuple(p.rect)) for k, p in placed.items()
                 if p.placed]
        for i, (ka, ra) in enumerate(rects):
            for kb, rb in rects[i + 1:]:
                assert not self._rects_overlap(ra, rb), (
                    f"furniture overlap: {ka} x {kb}")

    def test_long_title_shrinks_to_fit(self):
        # The engine's shrink floor (0.70) cannot fit very long titles;
        # the viz-side clamp guarantees the drawn title fits the canvas.
        from viz.aesthetic_render import _title_fit_scale
        long_title = ("A Very Long Lake Name That Cannot Possibly Fit "
                      "In The Allotted Title Block Without Shrinking")
        placed = _layout_furniture(
            self._ctx(spec=_currents_spec(title=long_title)))
        p = placed["title"]
        assert p.rect[2] > 1.0  # the engine's last resort: overflow
        assert _title_fit_scale(p.rect) < 1.0
        short = _layout_furniture(self._ctx())
        assert _title_fit_scale(short["title"].rect) == 1.0

    def test_subtitle_hidden_when_no_window(self):
        placed = _layout_furniture(self._ctx(subtitle=""))
        assert "subtitle" not in placed

    def test_furniture_draw_calls_run(self, tmp_path):
        # End-to-end: all three vector-capable presets draw through
        # the placed furniture without exceptions.
        field = _FakeCurrents()
        for preset in ("dark_flow", "dark_strands"):
            frames, _ = render_preset_viz(
                _currents_spec(), field, out_dir=tmp_path / preset,
                preset=preset, strand_count=200, underlay=False)
            assert frames


class TestLegacyRegression:
    def _scalar_field(self, n_days=2, ny=8, nx=10, seed=11):
        rng = np.random.default_rng(seed)
        lats = np.linspace(46.0, 48.8, ny)
        lons = np.linspace(-92.5, -84.5, nx)
        times = [dt.date(2026, 9, d) for d in range(1, n_days + 1)]
        values = rng.normal(10, 3, size=(n_days, ny, nx))
        return {"times": times, "lats": lats, "lons": lons,
                "values": values}

    def test_new_defaults_do_not_trigger_preset_path(self, tmp_path):
        # The new render_viz params default to the legacy behavior:
        # preset=None still renders the legacy path (no PeerMissing),
        # and the new kwargs at defaults reproduce legacy output.
        spec, field = _spec(), self._scalar_field()
        a, _ = render_viz(spec, field, None, out_dir=tmp_path / "a",
                          underlay=False)
        b, _ = render_viz(spec, field, None, out_dir=tmp_path / "b",
                          underlay=False, preset=None, basemap=None,
                          strand_count=3000, strand_linewidth=1.4)
        assert len(a) == len(b) > 0
        for fa, fb in zip(a, b):
            assert _png_bytes(fa) == _png_bytes(fb), (
                f"legacy output changed: {fa}")
        import json
        with open(tmp_path / "b" / "manifest.json",
                  encoding="utf-8") as fh:
            m = json.load(fh)
        assert "preset" not in m["render"]
