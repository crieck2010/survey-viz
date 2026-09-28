"""Tests for viz.insights (data-driven story captions)."""

from __future__ import annotations

import datetime as _dt
import json

import numpy as np
import pytest

from viz.insights import (Caption, frame_stats, suggest_captions,
                          SUPPORTED_VARIABLES)


def _dates(n, start=2020):
    return [_dt.date(start + i // 12, (i % 12) + 1, 15) for i in range(n)]


def _values(means, hw=(4, 5)):
    # constant grid per frame at the given mean
    return np.array([np.full(hw, m) for m in means])


def test_frame_stats():
    values = _values([10.0, 20.0])
    stats = frame_stats(values, [0, 1], _dates(2))
    assert stats[0].mean == pytest.approx(10.0)
    assert stats[1].max == pytest.approx(20.0)
    assert stats[0].finite == 20


def test_frame_stats_all_nan():
    values = np.full((1, 2, 2), np.nan)
    stats = frame_stats(values, [0], _dates(1))
    assert stats[0].finite == 0
    assert np.isnan(stats[0].mean)


def test_peak_caption():
    stats = frame_stats(_values([10.0, 30.0, 20.0]), [0, 1, 2], _dates(3))
    caps = suggest_captions(stats, "sst", "°C")
    peak = [c for c in caps if c.text.startswith("Peak")]
    assert len(peak) == 1
    assert "30.0°C" in peak[0].text
    assert "regional mean" in peak[0].text
    assert peak[0].frame_start == 0 and peak[0].frame_end == 2


def test_trend_caption_warming():
    # Clean monotonic rise: the peak is the last frame, so no peak
    # caption fires — the trend caption stands alone.
    means = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    stats = frame_stats(_values(means), list(range(6)), _dates(6))
    caps = suggest_captions(stats, "sst", "°C")
    trend = [c for c in caps if "trend" in c.text]
    assert len(trend) == 1
    assert trend[0].text.startswith("Warming trend: +")
    assert "across the reel" in trend[0].text


def test_trend_caption_falling_word():
    # Clean monotonic fall: peak is the first frame (no overlap with
    # the trend window), so both captions fire.
    means = [15.0, 14.0, 13.0, 12.0, 11.0, 10.0]
    stats = frame_stats(_values(means), list(range(6)), _dates(6))
    caps = suggest_captions(stats, "sst", "°C")
    trend = [c for c in caps if "trend" in c.text]
    assert len(trend) == 1
    assert trend[0].text.startswith("Cooling trend: -")


def test_weak_trend_stays_uncaptioned():
    # Alternating around a flat line: slope ~ 0, large standard error.
    means = [10.0, 10.1, 10.0, 10.1, 10.0, 10.1]
    stats = frame_stats(_values(means), list(range(6)), _dates(6))
    caps = suggest_captions(stats, "sst", "°C")
    assert not [c for c in caps if "trend" in c.text]


def test_too_few_frames():
    stats = frame_stats(_values([10.0, 11.0]), [0, 1], _dates(2))
    assert suggest_captions(stats, "sst", "°C") == []


def test_unsupported_variable():
    stats = frame_stats(_values([1.0, 2.0, 3.0]), [0, 1, 2], _dates(3))
    assert suggest_captions(stats, "storm-tracks", "") == []


def test_peak_wins_overlap():
    # Interior peak whose range overlaps the trend window: only the
    # peak caption survives.
    means = [10.05, 10.95, 12.26, 13.04, 17.79, 15.14, 16.52]
    stats = frame_stats(_values(means), list(range(7)), _dates(7))
    caps = suggest_captions(stats, "sst", "°C")
    assert len(caps) == 1 and caps[0].text.startswith("Peak")
    assert (caps[0].frame_start, caps[0].frame_end) == (3, 5)


def test_monotonic_rise_is_trend_only():
    # The peak is the last frame, so no peak caption fires — the
    # warming trend tells the story alone.
    means = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
    stats = frame_stats(_values(means), list(range(6)), _dates(6))
    caps = suggest_captions(stats, "sst", "°C")
    assert len(caps) == 1 and "trend" in caps[0].text


def test_flat_data_has_no_peak():
    means = [10.0] * 6
    stats = frame_stats(_values(means), list(range(6)), _dates(6))
    assert suggest_captions(stats, "sst", "°C") == []


def test_caption_serializes():
    cap = Caption(0, 2, "hello")
    assert cap.to_dict() == {"frame_start": 0, "frame_end": 2,
                             "text": "hello"}


def test_render_viz_story_captions_manifest(tmp_path):
    viz = pytest.importorskip("viz.render")
    from viz.parser import parse_description
    spec = parse_description("Lake Superior water temperature 2020 to 2022",
                             today=_dt.date(2026, 9, 27))
    times = _dates(6)
    field = {"times": [t.isoformat() for t in times],
             "lats": [46.0, 47.0], "lons": [-90.0, -89.0],
             "values": _values([10.0, 12.0, 14.0, 16.0, 18.0, 20.0],
                               hw=(2, 2)).tolist()}
    out = str(tmp_path / "frames")
    frames, manifest_path = viz.render_viz(
        spec, field, out_dir=out, story_captions=True)
    assert len(frames) == 6
    manifest = json.load(open(manifest_path))
    caps = manifest["render"]["story_captions"]
    assert isinstance(caps, list) and len(caps) >= 1
    assert all({"frame_start", "frame_end", "text"} <= set(c.keys())
               for c in caps)
