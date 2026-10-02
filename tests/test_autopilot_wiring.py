"""Tests for the survey-autopilot first-pass wiring (v0.27.0).

Three optional knobs on the preset path — ``robust_scale``,
``strand_count="auto"``, ``salience_labels`` — each degrades to the
pre-autopilot behavior when the survey-autopilot peer is absent.
Tests that need the peer are marked ``needs_autopilot``; the
missing-peer fallback tests run everywhere (they monkeypatch
``sys.modules["autopilot"]`` to simulate absence).

The basemap underlay is stubbed offline by tests/conftest.py.
"""

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")
pytest.importorskip("aesthetics")

needs_autopilot = pytest.mark.skipif(
    importlib.util.find_spec("autopilot") is None,
    reason="survey-autopilot peer not installed")

from viz.aesthetic_render import _resolve_strand_count
from viz.render import render_viz
from viz.spec import VizSpec

BBOX = (-92.5, 46.0, -84.5, 48.8)  # Lake Superior


def _spec(variable="currents", bbox=BBOX, **over):
    kw = dict(
        title="t",
        region_key="test-region",
        bbox=bbox,
        variable=variable,
        start=dt.date(2026, 9, 1),
        end=dt.date(2026, 9, 3),
        cadence="daily",
    )
    kw.update(over)
    return VizSpec(**kw)


class _FakeCurrents:
    """CurrentField-shaped duck-type with u/v/temperature."""

    def __init__(self, ny=8, nx=10, temperature=True, outlier=None):
        rng = np.random.default_rng(5)
        self.lats = np.linspace(46.0, 48.8, ny)
        self.lons = np.linspace(-92.5, -84.5, nx)
        self.times = ["2026-09-01T00:00:00", "2026-09-02T00:00:00"]
        nt = len(self.times)
        self.u = rng.normal(0, 0.3, (nt, ny, nx))
        self.v = rng.normal(0, 0.3, (nt, ny, nx))
        t = rng.normal(15, 3, (nt, ny, nx))
        if outlier is not None:
            t[0, 0, 0] = outlier
        self.temperature = t if temperature else None
        self.provenance = {"source": "test"}


def _manifest_render(manifest_path):
    return json.loads(Path(manifest_path).read_text())["render"]


# ---------------------------------------------------------------------------
# robust_scale
# ---------------------------------------------------------------------------

@needs_autopilot
def test_robust_scale_uses_p2_p98_on_outlier_field(tmp_path):
    field = _FakeCurrents(outlier=400.0)  # 400 C = 752 F, one bad cell
    _, mp = render_viz(_spec(), field, None, out_dir=tmp_path,
                       preset="dark_flow", underlay=False,
                       place_labels=False)
    r = _manifest_render(mp)
    assert r["scale_method"] == "autopilot-p2-p98"
    assert r["scale_percentiles"] == [2.0, 98.0]
    expected = np.asarray(field.temperature) * 9.0 / 5.0 + 32.0
    finite = expected[np.isfinite(expected)]
    assert r["vmin"] == round(float(np.percentile(finite, 2.0)), 1)
    assert r["vmax"] == round(float(np.percentile(finite, 98.0)), 1)
    assert r["vmax"] < 752.0  # the outlier is clipped, not the scale top


@needs_autopilot
def test_robust_scale_off_restores_minmax(tmp_path):
    field = _FakeCurrents(outlier=400.0)
    _, mp = render_viz(_spec(), field, None, out_dir=tmp_path,
                       preset="dark_flow", underlay=False,
                       place_labels=False, robust_scale=False)
    r = _manifest_render(mp)
    assert r["scale_method"] == "minmax"
    assert "scale_percentiles" not in r
    expected = np.asarray(field.temperature) * 9.0 / 5.0 + 32.0
    assert r["vmin"] == round(float(np.nanmin(expected)), 1)
    assert r["vmax"] == round(float(np.nanmax(expected)), 1)
    assert r["vmax"] == 752.0  # the raw max is back


@needs_autopilot
def test_robust_scale_explicit_spec_vmin_vmax_win(tmp_path):
    field = _FakeCurrents(outlier=400.0)
    _, mp = render_viz(_spec(vmin=32.0, vmax=80.0), field, None,
                       out_dir=tmp_path, preset="dark_flow",
                       underlay=False, place_labels=False)
    r = _manifest_render(mp)
    assert r["vmin"] == 32.0 and r["vmax"] == 80.0
    assert r["scale_method"] == "minmax"
    assert "scale_percentiles" not in r


@needs_autopilot
def test_dark_strands_records_autopilot_scale(tmp_path):
    field = _FakeCurrents(outlier=400.0)
    _, mp = render_viz(_spec(), field, None, out_dir=tmp_path,
                       preset="dark_strands", underlay=False,
                       place_labels=False)
    r = _manifest_render(mp)
    assert r["scale_method"] == "autopilot-p2-p98"
    assert r["scale_percentiles"] == [2.0, 98.0]
    assert r["vmax"] < 752.0


def test_missing_peer_scale_falls_back_to_minmax(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "autopilot", None)
    field = _FakeCurrents(outlier=400.0)
    _, mp = render_viz(_spec(), field, None, out_dir=tmp_path,
                       preset="dark_flow", underlay=False,
                       place_labels=False)
    r = _manifest_render(mp)
    assert r["scale_method"] == "minmax-fallback"
    assert "scale_percentiles" not in r
    expected = np.asarray(field.temperature) * 9.0 / 5.0 + 32.0
    assert r["vmin"] == round(float(np.nanmin(expected)), 1)
    assert r["vmax"] == round(float(np.nanmax(expected)), 1)


# ---------------------------------------------------------------------------
# strand_count
# ---------------------------------------------------------------------------

@needs_autopilot
def test_strand_count_auto_resolves_by_area(tmp_path):
    # 60 x 25 deg = 1500 deg^2 at 0.5 strands/deg^2 -> 750.
    bbox = (-125.0, 25.0, -65.0, 50.0)
    _, mp = render_viz(_spec(bbox=bbox), _FakeCurrents(), None,
                       out_dir=tmp_path, preset="dark_flow",
                       underlay=False, place_labels=False)
    r = _manifest_render(mp)
    assert r["strand_count"] == 750
    assert r["strand_count_method"] == "autopilot-area"


@needs_autopilot
def test_strand_count_int_passes_through(tmp_path):
    _, mp = render_viz(_spec(), _FakeCurrents(), None, out_dir=tmp_path,
                       preset="dark_flow", underlay=False,
                       place_labels=False, strand_count=1234)
    r = _manifest_render(mp)
    assert r["strand_count"] == 1234
    assert r["strand_count_method"] == "explicit"


@needs_autopilot
def test_strand_count_auto_clamps_to_peer_floor(tmp_path):
    # The tiny Lake Superior test bbox (22.4 deg^2 -> 11 strands)
    # hits the peer's 500 floor; the method still says area.
    _, mp = render_viz(_spec(), _FakeCurrents(), None, out_dir=tmp_path,
                       preset="dark_flow", underlay=False,
                       place_labels=False)
    r = _manifest_render(mp)
    assert r["strand_count"] == 500
    assert r["strand_count_method"] == "autopilot-area"


def test_strand_count_auto_missing_peer_falls_back(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "autopilot", None)
    count, method = _resolve_strand_count("auto", _spec())
    assert (count, method) == (3000, "default-fallback")


def test_strand_count_rejects_garbage():
    with pytest.raises(ValueError, match="strand_count"):
        _resolve_strand_count("bogus", _spec())
    with pytest.raises(ValueError, match="strand_count"):
        _resolve_strand_count(True, _spec())
    with pytest.raises(ValueError, match="positive"):
        render_viz(_spec(), _FakeCurrents(), None, out_dir="/tmp/x",
                    preset="dark_flow", underlay=False, strand_count=0)


# ---------------------------------------------------------------------------
# salience_labels
# ---------------------------------------------------------------------------

class _FakeGazetteer:
    """Gazetteer stub: Westburg (cold/slow) vs Eastport (warm/fast)."""

    def labels_for_bbox(self, lon0, lon1, lat0, lat1, *, max_labels=8,
                         min_population=0, kinds=("city", "town")):
        labels = [
            {"x": -92.0, "y": 47.4, "text": "Westburg", "priority": 1},
            {"x": -85.0, "y": 47.4, "text": "Eastport", "priority": 1},
        ]
        return labels[:max(0, int(max_labels))]


def _hot_east_field():
    """Currents: east half fast + warm, west half slow + cool."""
    field = _FakeCurrents()
    ny, nx = field.u.shape[1], field.u.shape[2]
    field.u[:, :, :nx // 2] = 0.1
    field.u[:, :, nx // 2:] = 3.0
    field.v[:] = 0.0
    field.temperature[:, :, :nx // 2] = 10.0
    field.temperature[:, :, nx // 2:] = 25.0
    return field


def _stub_gazetteer(monkeypatch):
    monkeypatch.setattr("viz.aesthetic_render._gazetteer",
                        lambda: _FakeGazetteer())


@needs_autopilot
def test_salience_ranking_prefers_fast_warm_region(tmp_path, monkeypatch):
    _stub_gazetteer(monkeypatch)
    _, mp = render_viz(_spec(), _hot_east_field(), None, out_dir=tmp_path,
                       preset="dark_flow", underlay=False, max_labels=1)
    r = _manifest_render(mp)
    pl = r["place_labels"]
    assert pl["mode"] == "auto"
    assert pl["label_ranking"] == "autopilot-salience"
    assert [lb["text"] for lb in pl["labels"]] == ["Eastport"]


@needs_autopilot
def test_salience_off_keeps_legacy_order(tmp_path, monkeypatch):
    _stub_gazetteer(monkeypatch)
    _, mp = render_viz(_spec(), _hot_east_field(), None, out_dir=tmp_path,
                       preset="dark_flow", underlay=False, max_labels=1,
                       salience_labels=False)
    r = _manifest_render(mp)
    pl = r["place_labels"]
    assert pl["label_ranking"] == "legacy"
    assert [lb["text"] for lb in pl["labels"]] == ["Westburg"]


@needs_autopilot
def test_salience_skipped_without_temperature(tmp_path, monkeypatch):
    # Currents without a temperature field: the scalar falls back to
    # speed, so there is no honest salience signal — legacy order.
    _stub_gazetteer(monkeypatch)
    field = _FakeCurrents(temperature=False)
    _, mp = render_viz(_spec(), field, None, out_dir=tmp_path,
                       preset="dark_flow", underlay=False, max_labels=1)
    r = _manifest_render(mp)
    assert r["place_labels"]["label_ranking"] == "legacy"


def test_salience_missing_peer_keeps_legacy(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "autopilot", None)
    _stub_gazetteer(monkeypatch)
    _, mp = render_viz(_spec(), _hot_east_field(), None, out_dir=tmp_path,
                       preset="dark_flow", underlay=False, max_labels=1)
    r = _manifest_render(mp)
    pl = r["place_labels"]
    assert pl["label_ranking"] == "legacy"
    assert [lb["text"] for lb in pl["labels"]] == ["Westburg"]


# ---------------------------------------------------------------------------
# fail-fast: the new knobs are preset-path only
# ---------------------------------------------------------------------------

def test_new_knobs_need_preset(tmp_path):
    field = _FakeCurrents()
    with pytest.raises(ValueError, match="robust_scale"):
        render_viz(_spec(), field, None, out_dir=tmp_path,
                   robust_scale=False, underlay=False)
    with pytest.raises(ValueError, match="salience_labels"):
        render_viz(_spec(), field, None, out_dir=tmp_path,
                   salience_labels=False, underlay=False)
    with pytest.raises(ValueError, match="strand_count"):
        render_viz(_spec(), field, None, out_dir=tmp_path,
                   strand_count=100, underlay=False)
