"""Tests for the renderer (skipped entirely if matplotlib/numpy missing)."""

import datetime as dt
import json
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")

from viz.parser import parse_description
from viz.render import MAX_FRAMES, SCHEMA_ID, render_viz
from viz.spec import VizSpec

TODAY = dt.date(2026, 9, 26)
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
    series = {"dates": times, "values": [float(np.mean(values[i])) for i in range(n_months)]}
    field = {"times": times, "lats": lats, "lons": lons, "values": values}
    return field, series


class _FakeGlseaField:
    """GlseaField-shaped duck-type: .times/.lats/.lons + per-index .grid(i)."""

    def __init__(self, times, lats, lons, values):
        self.times = times
        self.lats = lats
        self.lons = lons
        self._values = values

    def grid(self, i):
        return self._values[i]


def test_render_dict_field_and_series(tmp_path):
    field, series = _field()
    frames, manifest_path = render_viz(_spec(), field, series, out_dir=tmp_path / "frames")
    assert len(frames) == 3
    assert all(Path(f).is_file() for f in frames)
    assert Path(manifest_path).is_file()


def test_manifest_schema_and_contents(tmp_path):
    field, series = _field()
    spec = _spec(vmin=0.0, vmax=20.0)
    frames, manifest_path = render_viz(spec, field, series, out_dir=tmp_path / "f")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["schema"] == SCHEMA_ID
    assert manifest["spec"] == spec.to_dict()
    assert len(manifest["frames"]) == len(manifest["timestamps"]) == 3
    assert manifest["render"]["layout"] == "reel-vertical"
    assert manifest["render"]["style"] == "reel-dark"
    assert manifest["render"]["vmin"] == 0.0
    assert manifest["render"]["vmax"] == 20.0
    assert manifest["render"]["n_frames"] == 3
    assert "survey-viz" in manifest["render"]["engine"]


def test_vmin_vmax_computed_from_data_when_absent(tmp_path):
    field, series = _field()
    _, manifest_path = render_viz(_spec(), field, series, out_dir=tmp_path / "f")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    data_min = float(np.min(field["values"]))
    data_max = float(np.max(field["values"]))
    assert manifest["render"]["vmin"] == pytest.approx(data_min)
    assert manifest["render"]["vmax"] == pytest.approx(data_max)


def test_series_none_still_renders(tmp_path):
    field, _ = _field()
    frames, manifest_path = render_viz(_spec(), field, None, out_dir=tmp_path / "f")
    assert len(frames) == 3 and Path(manifest_path).is_file()


def test_object_field_duck_type(tmp_path):
    field, series = _field()
    fake = _FakeGlseaField(field["times"], field["lats"], field["lons"], field["values"])
    frames, _ = render_viz(_spec(), fake, series, out_dir=tmp_path / "f")
    assert len(frames) == 3


def test_object_series_duck_type(tmp_path):
    field, series_dict = _field()

    class _S:
        def __init__(self):
            self.dates = series_dict["dates"]
            self.values = np.asarray(series_dict["values"])

    frames, _ = render_viz(_spec(), field, _S(), out_dir=tmp_path / "f")
    assert len(frames) == 3


def test_monthly_cadence_buckets_to_one_frame_per_month(tmp_path):
    # 3 timesteps in Jan + 2 in Feb -> 2 frames.
    lats = np.linspace(46.0, 48.8, 6)
    lons = np.linspace(-92.5, -84.5, 8)
    times = [dt.date(2026, 1, d) for d in (1, 10, 20)] + [dt.date(2026, 2, d) for d in (5, 20)]
    values = np.zeros((5, len(lats), len(lons)))
    field = {"times": times, "lats": lats, "lons": lons, "values": values}
    spec = _spec(start=dt.date(2026, 1, 1), end=dt.date(2026, 2, 28))
    frames, manifest_path = render_viz(spec, field, None, out_dir=tmp_path / "f")
    assert len(frames) == 2
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["timestamps"] == ["2026-01-10", "2026-02-20"]  # closest to the 15th


def test_daily_cadence_capped_at_max_frames(tmp_path):
    lats = np.linspace(46.0, 48.8, 4)
    lons = np.linspace(-92.5, -84.5, 5)
    n = MAX_FRAMES + 100
    base = dt.date(2024, 1, 1)
    times = [base + dt.timedelta(days=i) for i in range(n)]
    values = np.zeros((n, len(lats), len(lons)))
    field = {"times": times, "lats": lats, "lons": lons, "values": values}
    spec = _spec(cadence="daily", start=times[0], end=times[-1])
    frames, _ = render_viz(spec, field, None, out_dir=tmp_path / "f")
    assert len(frames) == MAX_FRAMES


def test_light_style_renders(tmp_path):
    field, series = _field()
    spec = _spec(style="light")
    frames, manifest_path = render_viz(spec, field, series, out_dir=tmp_path / "f")
    assert len(frames) == 3
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["render"]["style"] == "light"


def test_unknown_layout_raises(tmp_path):
    field, series = _field()
    with pytest.raises(ValueError):
        render_viz(_spec(), field, series, out_dir=tmp_path / "f", layout="square")


def test_unknown_style_raises(tmp_path):
    field, series = _field()
    with pytest.raises(ValueError):
        render_viz(_spec(), field, series, out_dir=tmp_path / "f", style="neon")


def test_frames_are_png(tmp_path):
    field, series = _field(n_months=1)
    frames, _ = render_viz(_spec(), field, series, out_dir=tmp_path / "f")
    with open(frames[0], "rb") as fh:
        magic = fh.read(8)
    assert magic == b"\x89PNG\r\n\x1a\n"


def test_frame_dimensions_1080x1920(tmp_path):
    field, series = _field(n_months=1)
    frames, _ = render_viz(_spec(), field, series, out_dir=tmp_path / "f")
    # PNG IHDR: width/height as big-endian uint32 at bytes 16..24.
    with open(frames[0], "rb") as fh:
        ihdr = fh.read(24)
    width = int.from_bytes(ihdr[16:20], "big")
    height = int.from_bytes(ihdr[20:24], "big")
    assert (width, height) == (1080, 1920)


def test_end_to_end_from_parser(tmp_path):
    spec = parse_description("Lake Superior surface temperature this year", today=TODAY)
    field, series = _field()
    frames, manifest_path = render_viz(spec, field, series, out_dir=tmp_path / "f")
    assert len(frames) == 3 and Path(manifest_path).is_file()
