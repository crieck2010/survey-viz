"""Tests for place labels on the aesthetic preset path (survey-gazetteer peer).

Skipped entirely if matplotlib/numpy or the survey-aesthetics peer are
missing. The basemap underlay is stubbed offline by tests/conftest.py.

Contract under test (see survey-gazetteer docs/INTEROP.md):
- labels are fetched ONCE per reel (never per frame) for the reel's
  north-up bbox;
- they are drawn with the rest of the furniture on the final canvas,
  upright after rotation, via the engine's ``place_labels`` (which owns
  the final on-canvas decluttering);
- ``render_viz(place_labels=...)``: True = auto (default with a
  preset), False = off, an explicit list wins over auto and is used
  verbatim;
- the peer stays optional: labels requested without the peer installed
  raise an honest RuntimeError with the install command;
- the legacy (preset=None) path is untouched.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")
aesthetics = pytest.importorskip("aesthetics")

from viz.aesthetic_render import (
    _northup_frac_to_final_frac,
    render_preset_viz,
)
from viz.render import render_viz
from viz.spec import VizSpec

BBOX = (-92.5, 46.0, -84.5, 48.8)  # Lake Superior: has real gazetteer places


def _spec(**over):
    kw = dict(
        title="Lake Superior",
        region_key="lake-superior",
        bbox=BBOX,
        variable="tp",
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


def _manifest(out_dir):
    with open(Path(out_dir) / "manifest.json", encoding="utf-8") as fh:
        return json.load(fh)


def _counting_labels_for_bbox(monkeypatch, fixed):
    """Wrap gazetteer.labels_for_bbox with a call counter."""
    import gazetteer as gz

    calls = []

    def _wrapped(lon0, lon1, lat0, lat1, **kw):
        calls.append((lon0, lon1, lat0, lat1, kw))
        return [dict(lb) for lb in fixed]

    monkeypatch.setattr(gz, "labels_for_bbox", _wrapped)
    return calls


def _capture_engine_place_labels(monkeypatch):
    """Capture the label lists the engine's place_labels receives."""
    seen = []
    real = aesthetics.place_labels

    def _wrapped(ax, labels, **kw):
        seen.append([dict(lb) for lb in labels])
        return real(ax, labels, **kw)

    monkeypatch.setattr(aesthetics, "place_labels", _wrapped)
    return seen


FIXED_LABELS = [
    {"x": -88.0, "y": 46.8, "text": "Testville", "priority": 10},
    {"x": -87.0, "y": 47.5, "text": "Mockton", "priority": 5},
]


# ---------------------------------------------------------------------------
# Fetch-once semantics
# ---------------------------------------------------------------------------

def test_labels_fetched_once_per_reel_not_per_frame(tmp_path, monkeypatch):
    calls = _counting_labels_for_bbox(monkeypatch, FIXED_LABELS)
    frames, _ = render_viz(
        _spec(), _scalar_field(), None, out_dir=tmp_path,
        preset="paper_prism", underlay=False)
    assert len(frames) == 3
    assert len(calls) == 1
    lon0, lon1, lat0, lat1, kw = calls[0]
    # Fetched for the reel's north-up bbox (pre-rotation coordinates).
    assert (lon0, lon1, lat0, lat1) == (BBOX[0], BBOX[2], BBOX[1], BBOX[3])
    assert kw["max_labels"] == 8 and kw["min_population"] == 0


def test_explicit_list_wins_over_auto(tmp_path, monkeypatch):
    calls = _counting_labels_for_bbox(monkeypatch, FIXED_LABELS)
    explicit = [{"x": -88.5, "y": 47.0, "text": "Only Me", "priority": 99}]
    frames, _ = render_viz(
        _spec(), _scalar_field(), None, out_dir=tmp_path,
        preset="paper_prism", underlay=False, place_labels=explicit)
    assert len(frames) == 3
    assert calls == []  # the peer is never consulted
    man = _manifest(tmp_path)
    assert man["render"]["place_labels"]["mode"] == "explicit"
    assert man["render"]["place_labels"]["labels"] == explicit


def test_off_means_no_labels_and_no_peer_import(tmp_path, monkeypatch):
    # Peer "missing": with labels off, the render must not touch it.
    monkeypatch.setitem(sys.modules, "gazetteer", None)
    seen = _capture_engine_place_labels(monkeypatch)
    frames, _ = render_viz(
        _spec(), _scalar_field(), None, out_dir=tmp_path,
        preset="paper_prism", underlay=False, place_labels=False)
    assert len(frames) == 3
    assert seen == []
    man = _manifest(tmp_path)
    assert man["render"]["place_labels"]["mode"] == "off"
    assert man["render"]["place_labels"]["n_labels"] == 0


def test_labels_identical_across_frames(tmp_path, monkeypatch):
    _counting_labels_for_bbox(monkeypatch, FIXED_LABELS)
    seen = _capture_engine_place_labels(monkeypatch)
    render_viz(_spec(), _scalar_field(), None, out_dir=tmp_path,
               preset="paper_prism", underlay=False)
    assert len(seen) == 3  # once per frame, same reel-static list
    assert seen[0] == seen[1] == seen[2]
    assert len(seen[0]) == 2
    # Projected onto final-canvas fractions in [0, 1].
    for lb in seen[0]:
        assert 0.0 <= lb["x"] <= 1.0 and 0.0 <= lb["y"] <= 1.0
        assert lb["text"] in ("Testville", "Mockton")


# ---------------------------------------------------------------------------
# Missing peer / validation
# ---------------------------------------------------------------------------

def test_missing_peer_raises_honest_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "gazetteer", None)
    with pytest.raises(RuntimeError, match="survey-gazetteer"):
        render_viz(_spec(), _scalar_field(), None, out_dir=tmp_path,
                   preset="paper_prism", underlay=False)
    # ... and names the install command.
    with pytest.raises(RuntimeError, match=r"pip install 'survey-viz\[aesthetics\]'"):
        render_viz(_spec(), _scalar_field(), None, out_dir=tmp_path,
                   preset="paper_prism", underlay=False)


def test_label_opts_need_preset(tmp_path):
    with pytest.raises(ValueError, match="need preset"):
        render_viz(_spec(), _scalar_field(), None, out_dir=tmp_path,
                   max_labels=5, underlay=False)
    with pytest.raises(ValueError, match="need preset"):
        render_viz(_spec(), _scalar_field(), None, out_dir=tmp_path,
                   min_population=1000, underlay=False)
    with pytest.raises(ValueError, match="need preset"):
        render_viz(_spec(), _scalar_field(), None, out_dir=tmp_path,
                   place_labels=[{"x": 0.0, "y": 0.0, "text": "X"}],
                   underlay=False)


def test_place_labels_false_without_preset_keeps_legacy(tmp_path):
    # Explicit off with no preset: the legacy path, untouched.
    a, _ = render_viz(_spec(), _scalar_field(), None,
                      out_dir=tmp_path / "a", underlay=False)
    b, _ = render_viz(_spec(), _scalar_field(), None,
                      out_dir=tmp_path / "b", underlay=False,
                      place_labels=False, max_labels=8, min_population=0)
    assert len(a) == len(b) == 3
    for fa, fb in zip(a, b):
        assert Path(fa).read_bytes() == Path(fb).read_bytes()


def test_explicit_labels_validated(tmp_path):
    bad = [{"x": -88.0, "y": 46.8}]  # missing text
    with pytest.raises(ValueError, match="text"):
        render_preset_viz(_spec(), _scalar_field(), tmp_path,
                          preset="paper_prism", underlay=False,
                          place_labels=bad)
    with pytest.raises(ValueError, match="JSON-serializable"):
        render_preset_viz(_spec(), _scalar_field(), tmp_path,
                          preset="paper_prism", underlay=False,
                          place_labels=[{"x": 0.0, "y": 0.0, "text": "X",
                                         "weird": object()}])
    with pytest.raises(ValueError, match="max_labels"):
        render_preset_viz(_spec(), _scalar_field(), tmp_path,
                          preset="paper_prism", underlay=False,
                          max_labels=-1)
    with pytest.raises(ValueError, match="place_labels"):
        render_preset_viz(_spec(), _scalar_field(), tmp_path,
                          preset="paper_prism", underlay=False,
                          place_labels="yes please")


# ---------------------------------------------------------------------------
# Manifest provenance
# ---------------------------------------------------------------------------

def test_manifest_records_auto_labels(tmp_path, monkeypatch):
    pytest.importorskip("gazetteer")
    frames, _ = render_viz(
        _spec(), _scalar_field(), None, out_dir=tmp_path,
        preset="paper_prism", underlay=False,
        max_labels=5, min_population=1000)
    assert len(frames) == 3
    man = _manifest(tmp_path)
    pl = man["render"]["place_labels"]
    assert pl["mode"] == "auto"
    assert pl["max_labels"] == 5
    assert pl["min_population"] == 1000
    assert pl["kinds"] == ["city", "town"]
    assert pl["n_labels"] == len(pl["labels"]) <= 5
    assert pl["gazetteer"] != "not-installed"
    # JSON-serializable label dicts, exactly the fingerprint input.
    json.dumps(pl["labels"])
    for lb in pl["labels"]:
        assert set(lb) >= {"x", "y", "text", "priority"}


def test_auto_labels_drawn_through_engine(tmp_path, monkeypatch):
    pytest.importorskip("gazetteer")
    seen = _capture_engine_place_labels(monkeypatch)
    render_viz(_spec(), _scalar_field(), None, out_dir=tmp_path,
               preset="paper_prism", underlay=False)
    assert len(seen) == 3
    assert len(seen[0]) > 0  # the real gazetteer finds Lake Superior places


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------

def test_cli_flag_defaults():
    from viz.cli import build_parser
    args = build_parser().parse_args(
        ["render", "--spec", "s.json", "--field-npz", "f.npz"])
    assert args.place_labels is True
    assert args.max_labels == 8
    assert args.min_population == 0


def test_cli_flag_overrides():
    from viz.cli import build_parser
    args = build_parser().parse_args(
        ["render", "--spec", "s.json", "--field-npz", "f.npz",
         "--preset", "paper_prism", "--no-place-labels",
         "--max-labels", "5", "--min-population", "1000"])
    assert args.place_labels is False
    assert args.max_labels == 5
    assert args.min_population == 1000
    args = build_parser().parse_args(
        ["render", "--spec", "s.json", "--field-npz", "f.npz",
         "--place-labels"])
    assert args.place_labels is True


def test_cli_bivariate_flag_defaults():
    from viz.cli import build_parser
    args = build_parser().parse_args(
        ["render", "--spec", "s.json", "--field-npz", "f.npz"])
    assert args.bivariate is True


def test_cli_bivariate_flag_overrides():
    from viz.cli import build_parser
    args = build_parser().parse_args(
        ["render", "--spec", "s.json", "--field-npz", "f.npz",
         "--preset", "dark_strands", "--no-bivariate"])
    assert args.bivariate is False
    args = build_parser().parse_args(
        ["render", "--spec", "s.json", "--field-npz", "f.npz",
         "--preset", "dark_strands", "--bivariate"])
    assert args.bivariate is True

def test_northup_frac_to_final_frac_matches_engine():
    """Empirical check: a bright marker at a known north-up fraction,
    rotated by the real engine, lands where the transform predicts."""
    for angle in (30.0, -45.0, 90.0, 12.5):
        rw, rh = aesthetics.rotated_render_size(angle, (1080, 1920))
        # Near-center points: they always survive the center crop, at a
        # meaningful offset so the rotation is actually exercised.
        for fx, fy in ((0.5, 0.5), (0.56, 0.44)):
            rgba = np.zeros((rh, rw, 4))
            rgba[..., 3] = 1.0
            cx, cy = int(fx * rw), int((1.0 - fy) * rh)
            rgba[cy - 2:cy + 3, cx - 2:cx + 3, :3] = 1.0
            out = aesthetics.rotate_frame_fill(rgba, angle, (1080, 1920))
            bright = out[..., 0] > 0.5
            ys, xs = np.nonzero(bright)
            assert len(xs) > 0, (angle, fx, fy)
            dx, dy = _northup_frac_to_final_frac(fx, fy, rw, rh, angle)
            ex, ey = dx * 1080, (1.0 - dy) * 1920
            assert abs(xs.mean() - ex) < 2.0, (angle, fx, fy, xs.mean(), ex)
            assert abs(ys.mean() - ey) < 2.0, (angle, fx, fy, ys.mean(), ey)


def test_northup_frac_identity_without_rotation():
    assert _northup_frac_to_final_frac(0.25, 0.75, 1080, 1920, 0.0) == (0.25, 0.75)
    assert _northup_frac_to_final_frac(0.25, 0.75, 1080, 1920, None) == (0.25, 0.75)


def test_rotated_labels_stay_upright_and_registered(tmp_path, monkeypatch):
    """With rotation on, labels are projected (not drawn tilted on the
    north-up map): the engine receives final-canvas fractions."""
    _counting_labels_for_bbox(monkeypatch, FIXED_LABELS)
    seen = _capture_engine_place_labels(monkeypatch)
    frames, _ = render_viz(
        _spec(), _scalar_field(), None, out_dir=tmp_path,
        preset="paper_prism", rotation=30, underlay=False)
    assert len(frames) == 3
    assert len(seen) == 3
    for frame_labels in seen:
        for lb in frame_labels:
            assert 0.0 <= lb["x"] <= 1.0 and 0.0 <= lb["y"] <= 1.0
    # The projection is reel-static: identical across frames.
    assert seen[0] == seen[1] == seen[2]
