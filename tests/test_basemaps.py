"""Basemaps feature (survey-viz v0.9.0): GEBCO variables, Natural Earth
underlay, honest country-border refusal.

All tests are offline: the peer (currents.basemaps) is monkeypatched
with a deterministic fake, so no test hits the network.
"""

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest

from viz.parser import parse_description
from viz.spec import KNOWN_SOURCES, KNOWN_VARIABLES, VizSpec
from viz.sources import (SOURCE_LABELS, default_source, explain_source,
                         resolve_source)
from viz import underlay as ul
from viz.render import render_viz


# --------------------------------------------------------------------------
# Spec / source registry
# --------------------------------------------------------------------------

class TestRegistry:
    def test_variables_and_sources_known(self):
        assert "bathymetry" in KNOWN_VARIABLES
        assert "elevation" in KNOWN_VARIABLES
        assert "country-borders" in KNOWN_VARIABLES
        assert "gebco" in KNOWN_SOURCES
        assert SOURCE_LABELS["gebco"] == "GEBCO 2024"

    def test_underlay_field_defaults_true(self):
        spec = VizSpec(title="t", region_key="great-lakes",
                       bbox=(-92.5, 41.5, -76.0, 49.0),
                       variable="sst", start=dt.date(2026, 1, 1),
                       end=dt.date(2026, 1, 31), cadence="monthly")
        assert spec.underlay is True

    def test_underlay_field_rejects_non_bool(self):
        with pytest.raises(TypeError):
            VizSpec(title="t", region_key="great-lakes",
                    bbox=(-92.5, 41.5, -76.0, 49.0),
                    variable="sst", start=dt.date(2026, 1, 1),
                    end=dt.date(2026, 1, 31), cadence="monthly",
                    underlay="yes")

    def test_underlay_round_trips(self):
        spec = VizSpec(title="t", region_key="great-lakes",
                       bbox=(-92.5, 41.5, -76.0, 49.0),
                       variable="sst", start=dt.date(2026, 1, 1),
                       end=dt.date(2026, 1, 31), cadence="monthly",
                       underlay=False)
        spec2 = VizSpec.from_dict(spec.to_dict())
        assert spec2.underlay is False
        assert spec2 == spec


    def test_underlay_topo_field_defaults_true(self):
        spec = VizSpec(title="t", region_key="great-lakes",
                       bbox=(-92.5, 41.5, -76.0, 49.0),
                       variable="sst", start=dt.date(2026, 1, 1),
                       end=dt.date(2026, 1, 31), cadence="monthly")
        assert spec.underlay_topo is True

    def test_underlay_topo_field_rejects_non_bool(self):
        with pytest.raises(TypeError):
            VizSpec(title="t", region_key="great-lakes",
                    bbox=(-92.5, 41.5, -76.0, 49.0),
                    variable="sst", start=dt.date(2026, 1, 1),
                    end=dt.date(2026, 1, 31), cadence="monthly",
                    underlay_topo="yes")

    def test_underlay_topo_round_trips(self):
        spec = VizSpec(title="t", region_key="great-lakes",
                       bbox=(-92.5, 41.5, -76.0, 49.0),
                       variable="sst", start=dt.date(2026, 1, 1),
                       end=dt.date(2026, 1, 31), cadence="monthly",
                       underlay_topo=False)
        spec2 = VizSpec.from_dict(spec.to_dict())
        assert spec2.underlay_topo is False
        assert spec2 == spec

    def test_underlay_topo_pre_v020_dicts_default_true(self):
        spec = VizSpec(title="t", region_key="great-lakes",
                       bbox=(-92.5, 41.5, -76.0, 49.0),
                       variable="sst", start=dt.date(2026, 1, 1),
                       end=dt.date(2026, 1, 31), cadence="monthly")
        data = spec.to_dict()
        del data["underlay_topo"]
        spec2 = VizSpec.from_dict(data)
        assert spec2.underlay_topo is True


# --------------------------------------------------------------------------
# Coastlines-only underlay (v0.20.0): underlay_topo=False skips GEBCO
# --------------------------------------------------------------------------

def _quake_spec(**kwargs):
    args = dict(title="t", region_key="global",
                bbox=(-180.0, -90.0, 180.0, 90.0),
                variable="earthquakes", start=dt.date(2026, 1, 1),
                end=dt.date(2026, 1, 31), cadence="monthly")
    args.update(kwargs)
    return VizSpec(**args)


class TestCoastlinesOnly:
    def _fetch_once(self, monkeypatch, **spec_kwargs):
        from viz import render as vr
        seen = {}

        def fake_fetch(bbox, include_topo=True, **kwargs):
            seen["include_topo"] = include_topo
            return {"status": "ok", "reason": "", "topo": None,
                    "coastlines": [[(-10.0, 42.0), (0.0, 43.0)]],
                    "n_coastline_segments": 1,
                    "resolution": None, "coastline_scale": "110m"}

        monkeypatch.setattr(ul, "fetch_underlay", fake_fetch)
        rgba, extent, coastlines, status = vr._fetch_underlay_once(
            _quake_spec(**spec_kwargs), None, None, want=True)
        return seen, rgba, extent, coastlines, status

    def test_include_topo_false_passes_through(self, monkeypatch):
        seen, rgba, extent, coastlines, status = self._fetch_once(
            monkeypatch, underlay_topo=False)
        assert seen["include_topo"] is False
        assert rgba is None and extent is None
        assert coastlines == [[(-10.0, 42.0), (0.0, 43.0)]]
        assert status["status"] == "ok"
        assert status["topo"] is False
        assert status["coastline_segments"] == 1

    def test_include_topo_true_by_default(self, monkeypatch):
        seen, *_ = self._fetch_once(monkeypatch)
        assert seen["include_topo"] is True

    def test_bathymetry_still_skips_topo(self, monkeypatch):
        # bathymetry/elevation never fetch GEBCO even with underlay_topo=True
        from viz import render as vr
        seen = {}

        def fake_fetch(bbox, include_topo=True, **kwargs):
            seen["include_topo"] = include_topo
            return {"status": "ok", "reason": "", "topo": None,
                    "coastlines": [], "n_coastline_segments": 0,
                    "resolution": None, "coastline_scale": "110m"}

        monkeypatch.setattr(ul, "fetch_underlay", fake_fetch)
        vr._fetch_underlay_once(
            _quake_spec(variable="bathymetry"), None, None, want=True)
        assert seen["include_topo"] is False


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

class TestParserBasemaps:
    @pytest.mark.parametrize("text,expected", [
        ("bathymetry of the north pacific", "bathymetry"),
        ("seafloor depth in the south pacific", "bathymetry"),
        ("map the trench depths of the north atlantic", "bathymetry"),
        ("abyssal plains of the north atlantic", "bathymetry"),
        ("elevation of california", "elevation"),
        ("terrain of the amazon basin", "elevation"),
        ("topography of the pacific northwest", "elevation"),
        ("mountains of the mediterranean sea", "elevation"),
    ])
    def test_bathymetry_elevation_terms(self, text, expected):
        assert parse_description(text).variable == expected

    def test_in_depth_does_not_trigger_bathymetry(self):
        # "in-depth" is an intensifier, not ocean depth.
        spec = parse_description(
            "an in-depth look at lake superior surface temperature")
        assert spec.variable == "sst"

    def test_bathymetry_pins_gebco_with_reason(self):
        spec = parse_description("bathymetry of the north pacific")
        assert spec.source == "gebco"
        assert spec.source_reason == (
            "bathymetry description -> "
            "GEBCO 2024 global topography/bathymetry (15 arc-second)")

    def test_elevation_pins_gebco_with_reason(self):
        spec = parse_description("elevation of california")
        assert spec.source == "gebco"
        assert spec.source_reason == (
            "elevation description -> "
            "GEBCO 2024 global topography/bathymetry (15 arc-second)")

    def test_static_variable_single_frame_by_default(self):
        spec = parse_description("seafloor depth of the north atlantic")
        assert spec.cadence == "yearly"
        assert spec.start == spec.end

    def test_explicit_time_phrase_respected_for_static(self):
        spec = parse_description("elevation of california, 2020 to 2022")
        assert spec.cadence == "yearly"
        assert spec.start.year == 2020 and spec.end.year == 2022

    def test_country_borders_alone_refuses_honestly(self):
        spec = parse_description("country borders of the mediterranean sea")
        assert spec.variable == "country-borders"
        assert default_source("country-borders", "mediterranean-sea") == ""
        assert resolve_source(spec) == ""
        reason = explain_source(spec)
        assert "underlay" in reason
        assert "refused" in reason

    def test_country_borders_never_defaults_to_sst(self):
        spec = parse_description("show country borders of the caribbean sea")
        assert spec.variable != "sst"

    def test_bathymetry_beats_elevation_on_tie(self):
        # "seafloor depth" appears before "mountain elevation" —
        # earliest keyword wins.
        spec = parse_description(
            "seafloor depth and mountain elevation of the north pacific")
        assert spec.variable == "bathymetry"


class TestSourcesGebco:
    def test_default_source(self):
        assert default_source("bathymetry", "north-pacific") == "gebco"
        assert default_source("elevation", "california") == "gebco"

    def test_reason_mentions_gebco(self):
        spec = parse_description("bathymetry of the north pacific")
        assert "GEBCO" in spec.source_reason

    def test_adapter_wiring(self):
        from viz.sources import _SOURCE_ADAPTERS, _SOURCE_MIN_VERSIONS
        assert _SOURCE_ADAPTERS["gebco"] == (
            "currents.basemaps", "fetch_gebco")
        assert _SOURCE_MIN_VERSIONS["gebco"] == "0.10.0"


# --------------------------------------------------------------------------
# Underlay module (peer monkeypatched — offline)
# --------------------------------------------------------------------------

class _FakeTopoField:
    source = "gebco-2024"
    resolution = 0.25
    provenance = {"grid": "GEBCO_2024", "tile": "fake"}

    def __init__(self):
        self.lats = np.linspace(45.0, 40.0, 21)   # north -> south
        self.lons = np.linspace(-10.0, 10.0, 81)

    @property
    def elevation(self):
        yy, xx = np.meshgrid(self.lats, self.lons, indexing="ij")
        return 1000.0 * np.sin(np.radians(yy)) - 2000.0 * (xx < 0)


class _FakeBasemaps:
    @staticmethod
    def fetch_gebco(bbox, resolution="15s", timeout=600.0):
        assert resolution == 0.25
        return _FakeTopoField()

    @staticmethod
    def fetch_naturalearth(bbox, scale="110m", layers=("coastline",),
                           timeout=600.0):
        assert layers == ("coastline",)
        return {"coastline": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[-10.0, 42.0], [0.0, 43.0],
                                             [10.0, 42.0]]},
            }],
        }}


@pytest.fixture()
def fake_peer(monkeypatch):
    import sys
    import types
    mod = types.ModuleType("currents.basemaps")
    mod.fetch_gebco = _FakeBasemaps.fetch_gebco
    mod.fetch_naturalearth = _FakeBasemaps.fetch_naturalearth
    pkg = types.ModuleType("currents")
    pkg.basemaps = mod
    monkeypatch.setitem(sys.modules, "currents", pkg)
    monkeypatch.setitem(sys.modules, "currents.basemaps", mod)
    ul.clear_cache()
    yield mod
    ul.clear_cache()


@pytest.mark.real_underlay
class TestUnderlay:
    def test_ok_status_with_topo_and_coastlines(self, fake_peer):
        info = ul.fetch_underlay((-10.0, 40.0, 10.0, 45.0))
        assert info["status"] == "ok"
        assert info["topo"] is not None
        assert len(info["topo"]["lats"]) == 21
        assert info["topo"]["source"] == "gebco-2024"
        assert info["n_coastline_segments"] == 1
        assert info["coastlines"][0]["lons"] == [-10.0, 0.0, 10.0]

    def test_include_topo_false_skips_gebco(self, fake_peer):
        info = ul.fetch_underlay((-10.0, 40.0, 10.0, 45.0),
                                 include_topo=False)
        assert info["status"] == "ok"
        assert info["topo"] is None
        assert info["n_coastline_segments"] == 1

    def test_missing_peer_is_unavailable_not_raise(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "currents.basemaps":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        ul.clear_cache()
        info = ul.fetch_underlay((-10.0, 40.0, 10.0, 45.0))
        assert info["status"] == "unavailable"
        assert "not installed" in info["reason"]

    def test_peer_exception_is_unavailable(self, fake_peer, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")
        monkeypatch.setattr(fake_peer, "fetch_gebco", boom)
        info = ul.fetch_underlay((-10.0, 40.0, 10.0, 45.0))
        assert info["status"] == "unavailable"
        assert "network down" in info["reason"]

    def test_cache_returns_same_object(self, fake_peer):
        a = ul.fetch_underlay((-10.0, 40.0, 10.0, 45.0))
        b = ul.fetch_underlay((-10.0, 40.0, 10.0, 45.0))
        assert a is b

    def test_hillshade_range_and_nan(self):
        elev = np.array([[0.0, 100.0], [np.nan, 50.0]])
        hs = ul.hillshade(elev, 0.25)
        assert np.isnan(hs[1, 0])
        valid = hs[~np.isnan(hs)]
        assert valid.min() >= 0.0 and valid.max() <= 1.0

    def test_multilinestring_segments(self):
        fc = {"features": [{"geometry": {
            "type": "MultiLineString",
            "coordinates": [[[0.0, 1.0], [2.0, 3.0]],
                            [[4.0, 5.0], [6.0, 7.0]]]}}]}
        segs = ul._fc_to_segments(fc)
        assert len(segs) == 2
        assert segs[1]["lats"] == [5.0, 7.0]


# --------------------------------------------------------------------------
# Render integration (offline via fake peer)
# --------------------------------------------------------------------------

def _spec(**over):
    kw = dict(title="Underlay test", region_key="great-lakes",
              bbox=(-92.5, 41.5, -76.0, 49.0), variable="sst",
              start=dt.date(2026, 1, 1), end=dt.date(2026, 3, 31),
              cadence="monthly")
    kw.update(over)
    return VizSpec(**kw)


def _field():
    rng = np.random.default_rng(3)
    lats = np.linspace(48.8, 41.5, 12)
    lons = np.linspace(-92.5, -76.0, 16)
    times = [dt.date(2026, m, 15) for m in (1, 2, 3)]
    values = rng.normal(10, 3, size=(3, len(lats), len(lons)))
    values[:, :4, :] = np.nan  # land in the north -> underlay shows
    return {"times": times, "lats": lats, "lons": lons, "values": values}


@pytest.mark.real_underlay
class TestRenderUnderlay:
    def test_underlay_ok_recorded_in_manifest(self, tmp_path, fake_peer):
        frames, manifest_path = render_viz(
            _spec(), _field(), None, out_dir=tmp_path / "f")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        u = manifest["render"]["underlay"]
        assert u["status"] == "ok"
        assert u["topo"] is True
        assert u["coastline_segments"] == 1
        assert frames and all(Path(f).is_file() for f in frames)

    def test_underlay_failure_falls_back_and_records(
            self, tmp_path, fake_peer, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("tile server down")
        monkeypatch.setattr(fake_peer, "fetch_gebco", boom)
        monkeypatch.setattr(fake_peer, "fetch_naturalearth", boom)
        frames, manifest_path = render_viz(
            _spec(), _field(), None, out_dir=tmp_path / "f")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        u = manifest["render"]["underlay"]
        assert u["status"] == "unavailable"
        assert "tile server down" in u["reason"]
        assert frames and all(Path(f).is_file() for f in frames)

    def test_underlay_false_disables(self, tmp_path, fake_peer):
        frames, manifest_path = render_viz(
            _spec(), _field(), None, out_dir=tmp_path / "f", underlay=False)
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        assert manifest["render"]["underlay"]["status"] == "disabled"
        assert frames

    def test_spec_underlay_false_respected(self, tmp_path, fake_peer):
        frames, manifest_path = render_viz(
            _spec(underlay=False), _field(), None, out_dir=tmp_path / "f")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        assert manifest["render"]["underlay"]["status"] == "disabled"

    def test_bathymetry_variable_skips_tint_keeps_coastlines(
            self, tmp_path, fake_peer):
        spec = _spec(variable="bathymetry")
        frames, manifest_path = render_viz(
            spec, _field(), None, out_dir=tmp_path / "f")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        u = manifest["render"]["underlay"]
        assert u["status"] == "ok"
        assert u["topo"] is False          # GEBCO *is* the variable
        assert u["coastline_segments"] == 1
        assert manifest["render"]["cmap"] == "Blues_r"
        assert frames

    def test_elevation_variable_cmap(self, tmp_path, fake_peer):
        spec = _spec(variable="elevation")
        _, manifest_path = render_viz(
            spec, _field(), None, out_dir=tmp_path / "f")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        assert manifest["render"]["cmap"] == "terrain"
