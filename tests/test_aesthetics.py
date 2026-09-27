"""Tests for the v0.15.0 aesthetics controls: spec.caption and the
render_viz ``cmap`` override.

Skipped entirely if matplotlib/numpy are missing.
"""

import datetime as dt
import json
import types
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")

from viz import CURATED_CMAPS
from viz.render import _STYLE, _draw_footer, render_viz
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
    field = {"times": times, "lats": lats, "lons": lons, "values": values}
    return field


def _quake_field():
    return {
        "quake_events": [
            {"event_id": "us1", "time": "2026-06-03T10:00:00",
             "lat": 42.5, "lon": -83.0, "depth_km": 10.0,
             "magnitude": 5.2, "mag_type": "ml",
             "place": "10km N of Testville", "event_type": "earthquake"},
        ],
        "bbox": [-83.5, 41.2, -82.8, 43.0],
        "start": "2026-06-01",
        "end": "2026-06-10",
        "min_magnitude": 4.0,
        "event_type": None,
        "source": "usgs",
        "provenance": {
            "source": "usgs",
            "n_events": 1,
            "retrieved_at": "2026-09-27T00:00:00+00:00",
            "request_urls": ["https://earthquake.usgs.gov/fdsnws/event/1/query?x"],
            "empty_reason": None,
        },
    }


def _manifest(manifest_path):
    with open(manifest_path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# CURATED_CMAPS
# ---------------------------------------------------------------------------

def test_curated_cmaps_all_registered():
    import matplotlib.pyplot as plt
    assert len(CURATED_CMAPS) >= 20
    registered = set(plt.colormaps())
    missing = [c for c in CURATED_CMAPS if c not in registered]
    assert not missing, f"curated cmaps not registered: {missing}"


# ---------------------------------------------------------------------------
# cmap override on the continuous (main) renderer
# ---------------------------------------------------------------------------

def test_cmap_override_applies_to_continuous_map(tmp_path):
    frames, manifest_path = render_viz(
        _spec(), _field(), None, out_dir=tmp_path / "f", cmap="inferno")
    assert len(frames) == 3
    manifest = _manifest(manifest_path)
    assert manifest["render"]["cmap"] == "inferno"
    assert manifest["render"]["cmap_requested"] == "inferno"
    assert manifest["render"]["cmap_overridden"] is True


def test_cmap_default_keeps_variable_default(tmp_path):
    frames, manifest_path = render_viz(
        _spec(), _field(), None, out_dir=tmp_path / "f")
    manifest = _manifest(manifest_path)
    # sst has no _VARIABLE_CMAPS entry -> style default for reel-dark.
    assert manifest["render"]["cmap"] == _STYLE["reel-dark"]["cmap"]
    assert manifest["render"]["cmap_requested"] is None
    assert manifest["render"]["cmap_overridden"] is False


def test_cmap_unknown_name_raises_with_guidance():
    with pytest.raises(ValueError, match="unknown colormap"):
        render_viz(_spec(), _field(), None, out_dir="/tmp/nope",
                   cmap="not-a-colormap")


def test_cmap_non_string_raises():
    with pytest.raises(ValueError, match="cmap must be"):
        render_viz(_spec(), _field(), None, out_dir="/tmp/nope", cmap=123)


# ---------------------------------------------------------------------------
# cmap is validated but NOT applied on categorical renderers
# ---------------------------------------------------------------------------

def test_cmap_ignored_but_recorded_on_quake_renderer(tmp_path):
    spec = _spec(variable="earthquakes", region_key="lake-erie",
                 bbox=(-83.5, 41.2, -82.8, 43.0),
                 start=dt.date(2026, 6, 1), end=dt.date(2026, 6, 10),
                 cadence="daily", source="comcat")
    frames, manifest_path = render_viz(
        spec, _quake_field(), None, out_dir=tmp_path / "q", cmap="plasma")
    assert len(frames) >= 1
    manifest = _manifest(manifest_path)
    # Fixed scientific encoding survives; the request is honestly recorded.
    assert manifest["render"]["cmap"] == "quake-magnitude"
    assert manifest["render"]["cmap_requested"] == "plasma"


def test_cmap_typo_still_fails_fast_on_categorical_renderer():
    spec = _spec(variable="earthquakes", region_key="lake-erie",
                 bbox=(-83.5, 41.2, -82.8, 43.0),
                 start=dt.date(2026, 6, 1), end=dt.date(2026, 6, 10),
                 cadence="daily", source="comcat")
    with pytest.raises(ValueError, match="unknown colormap"):
        render_viz(spec, _quake_field(), None, out_dir="/tmp/nope",
                   cmap="not-a-colormap")


# ---------------------------------------------------------------------------
# spec.caption
# ---------------------------------------------------------------------------

def test_caption_round_trip_and_defaults():
    assert _spec().caption is None
    spec = _spec(caption="  My custom footer  ")
    assert spec.caption == "My custom footer"
    assert VizSpec.from_dict(spec.to_dict()).caption == "My custom footer"
    # Pre-v0.15.0 dicts without the key load with None.
    old = spec.to_dict()
    del old["caption"]
    assert VizSpec.from_dict(old).caption is None
    # Empty string normalizes to None.
    assert _spec(caption="").caption is None
    assert _spec(caption="   ").caption is None


def test_caption_wrong_type_raises():
    with pytest.raises(TypeError, match="caption"):
        _spec(caption=123)


def test_caption_prepended_in_footer_not_replacing_honesty_text():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    st = _STYLE["reel-dark"]
    fake_spec = types.SimpleNamespace(
        caption="Charlie's Japan reel",
        start=dt.date(2016, 1, 1), end=dt.date(2026, 1, 1))
    fig = plt.figure()
    _draw_footer(fig, fake_spec, st,
                 "earthquakes · 9 events · observed events — not a forecast")
    text = fig.axes[0].texts[0].get_text()
    plt.close(fig)
    assert text.startswith("Charlie's Japan reel · ")
    assert "observed events — not a forecast" in text
    assert "2016-01-01 → 2026-01-01" in text


def test_footer_unchanged_without_caption():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    st = _STYLE["reel-dark"]
    fake_spec = types.SimpleNamespace(
        caption=None,
        start=dt.date(2026, 1, 1), end=dt.date(2026, 3, 31))
    fig = plt.figure()
    _draw_footer(fig, fake_spec, st, "sst")
    text = fig.axes[0].texts[0].get_text()
    plt.close(fig)
    assert text == "sst · 2026-01-01 → 2026-03-31"


def test_caption_render_end_to_end_and_manifest(tmp_path):
    spec = _spec(caption="Charlie's reel")
    frames, manifest_path = render_viz(
        spec, _field(), None, out_dir=tmp_path / "f")
    assert len(frames) == 3
    assert all(Path(f).is_file() for f in frames)
    manifest = _manifest(manifest_path)
    assert manifest["spec"]["caption"] == "Charlie's reel"
