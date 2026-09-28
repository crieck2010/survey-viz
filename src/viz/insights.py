"""Data-driven story captions.

Computes headline statistics from the rendered field and turns them
into timed caption events — e.g. ``"Peak warmth: 28.4°C (Aug 2024)"`` —
so a reel tells a story instead of just showing maps.

Everything here is stdlib + numpy (no matplotlib): :func:`frame_stats`
reduces each frame's grid to scalars, :func:`suggest_captions` applies
deterministic, documented rules. :func:`render_viz` calls both when
``story_captions=True`` and burns the results in as lower-third
captions.

Honesty rules (deliberate limits, not bugs):

* captions describe the *rendered region and time window only* — never
  global records or claims beyond the data in hand;
* a trend caption needs the total change to reach 5% of the frames'
  mean range *and* a statistically significant slope (|t| > 2 against
  the least-squares standard error) — flat wiggles stay uncaptioned;
* a peak caption is skipped when the data are flat (nothing to be a
  "peak" of) or when the peak is the last frame — a peak at the reel's
  end is the trend's endpoint, not a story event;
* all-NaN frames (gap months) never carry captions;
* at most one trend + one peak caption, never overlapping (peak wins).

Derived anomaly products (``"<base>-anomaly"``, survey-derive v0.1.0+)
resolve to their base variable: the caption names the anomaly
explicitly (``"Peak sea surface temperature anomaly: …"``) so an
anomaly is never captioned as the raw variable.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

#: Caption rules only run on continuous scalar fields. Categorical
#: renderers (storm tracks, streamgages, earthquakes) keep their fixed
#: scientific encodings and get no auto-captions.
SUPPORTED_VARIABLES = frozenset({
    "sst", "t2m", "wind", "msl", "tp", "currents", "ocean-color",
    "chlorophyll", "sea-ice", "land-ice", "fire", "night-lights",
    "water-storage", "aod",
})

_VARIABLE_LABELS = {
    "sst": "Sea surface temperature",
    "t2m": "Air temperature",
    "wind": "Wind speed",
    "msl": "Sea-level pressure",
    "tp": "Precipitation",
    "currents": "Current speed",
    "ocean-color": "Chlorophyll-a",
    "chlorophyll": "Chlorophyll-a",
    "sea-ice": "Sea-ice concentration",
    "land-ice": "Ice-sheet elevation change",
    "fire": "Fire detections",
    "night-lights": "Night-light radiance",
    "water-storage": "Water-storage anomaly",
    "aod": "Aerosol optical depth",
}

#: (rising_word, falling_word) per variable; the trend caption reads
#: e.g. "Warming trend: +1.2°C across the reel".
_TREND_WORDS = {
    "sst": ("Warming", "Cooling"),
    "t2m": ("Warming", "Cooling"),
    "sea-ice": ("Growing", "Shrinking"),
    "night-lights": ("Brightening", "Dimming"),
    "water-storage": ("Wetting", "Drying"),
}

#: Suffix marking a derived anomaly product (survey-derive v0.1.0+).
#: A variable like ``"sst-anomaly"`` resolves to its base variable
#: (``"sst"``) for caption support checks, labels, and trend words —
#: the caption then reads e.g. ``"Peak sea surface temperature
#: anomaly: +2.1°C regional mean (Aug 2024)"`` instead of silently
#: dropping the caption or, worse, labeling an anomaly as the raw
#: variable.
_DERIVED_SUFFIX = "-anomaly"


def _resolve_variable(variable: str) -> tuple:
    """Split a possibly-derived variable into ``(base, is_anomaly)``."""
    if variable.endswith(_DERIVED_SUFFIX) and len(variable) > len(_DERIVED_SUFFIX):
        return variable[: -len(_DERIVED_SUFFIX)], True
    return variable, False


#: Minimum trend size, as a fraction of the frames' mean range.
_TREND_FRACTION = 0.05


@dataclass(frozen=True)
class FrameStat:
    """Scalar summary of one rendered frame's grid."""
    date: _dt.date
    mean: float   # NaN when the frame has no finite cells
    max: float
    min: float
    finite: int   # number of finite grid cells


@dataclass(frozen=True)
class Caption:
    """One burned-in story caption over a frame range (inclusive)."""
    frame_start: int
    frame_end: int
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return {"frame_start": self.frame_start,
                "frame_end": self.frame_end, "text": self.text}


def frame_stats(values: Any, frame_idx: Sequence[int],
                times: Sequence[_dt.date]) -> List[FrameStat]:
    """Reduce each rendered frame's grid to a :class:`FrameStat`.

    ``values`` is the ``(T, H, W)`` array, ``frame_idx`` the rendered
    frame -> time indices, ``times`` the per-time-step dates. numpy is
    imported lazily so this module stays import-light.
    """
    import numpy as np
    arr = np.asarray(values, dtype=float)
    stats: List[FrameStat] = []
    for i in frame_idx:
        grid = arr[i]
        finite = grid[np.isfinite(grid)]
        if finite.size:
            stats.append(FrameStat(
                date=times[i], mean=float(np.mean(finite)),
                max=float(np.max(finite)), min=float(np.min(finite)),
                finite=int(finite.size)))
        else:
            stats.append(FrameStat(date=times[i], mean=float("nan"),
                                   max=float("nan"), min=float("nan"),
                                   finite=0))
    return stats


def _fmt_value(value: float, unit: str) -> str:
    if unit == "detections":
        return f"{value:.0f}"
    magnitude = abs(value)
    if magnitude >= 100:
        text = f"{value:.0f}"
    elif magnitude >= 1:
        text = f"{value:.1f}"
    else:
        text = f"{value:.2f}"
    return f"{text}{unit}"


def _month_year(date: _dt.date) -> str:
    return date.strftime("%b %Y")


def suggest_captions(stats: Sequence[FrameStat],
                     variable: str, unit: str) -> List[Caption]:
    """Derive story captions from per-frame statistics.

    ``stats`` are aligned with the rendered frames (index i = frame i).
    Returns at most two captions: a peak caption and a trend caption,
    with non-overlapping ranges. Empty list when the variable is
    unsupported, fewer than 3 frames carry data, or nothing passes the
    honesty thresholds.
    """
    import numpy as np
    base_variable, is_anomaly = _resolve_variable(variable)
    if base_variable not in SUPPORTED_VARIABLES:
        return []
    data = [(i, s) for i, s in enumerate(stats) if s.finite > 0]
    if len(data) < 3:
        return []
    means = np.array([s.mean for _, s in data])
    label = _VARIABLE_LABELS.get(base_variable, base_variable.replace("-", " "))
    if is_anomaly:
        label = f"{label} anomaly"

    captions: List[Caption] = []

    # Peak frame: the highest-mean frame in the window. Skipped when the
    # data are flat (nothing to be a "peak" of) or when the peak is the
    # last frame — a peak at the reel's end is the trend's endpoint, not
    # a story event, and the trend caption tells that story better.
    n = len(means)
    span = float(np.max(means) - np.min(means))
    peak_pos = int(np.argmax(means))
    peak_occupied: set = set()
    if span > 0 and peak_pos < n - 1:
        peak_frame, peak_stat = data[peak_pos]
        peak_text = (f"Peak {label.lower()}: "
                     f"{_fmt_value(peak_stat.mean, unit)} regional mean "
                     f"({_month_year(peak_stat.date)})")
        peak_range = (max(0, peak_frame - 1),
                      min(len(stats) - 1, peak_frame + 1))
        captions.append(Caption(peak_range[0], peak_range[1], peak_text))
        peak_occupied = set(range(peak_range[0], peak_range[1] + 1))

    # Trend: least-squares slope of frame means. Two gates: the total
    # change must reach 5% of the means' range (effect size), and the
    # slope must be significant at |t| > 2 (not just noise around a
    # flat line — the relative gate alone fires on tiny spans).
    x = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(x, means, 1)
    slope = float(slope)
    total = slope * (n - 1)
    significant = False
    if span > 0 and abs(total) >= _TREND_FRACTION * span and n >= 4:
        yhat = slope * x + intercept
        ss_res = float(np.sum((means - yhat) ** 2))
        denom = float(np.sum((x - x.mean()) ** 2))
        se = np.sqrt(ss_res / (n - 2)) / np.sqrt(denom) if denom > 0 else 0.0
        significant = se == 0.0 or abs(slope) / se > 2.0
    if significant:
        up_word, down_word = _TREND_WORDS.get(base_variable,
                                              ("Rising", "Falling"))
        word = up_word if total > 0 else down_word
        sign = "+" if total > 0 else ""
        trend_text = (f"{word} trend: {sign}"
                      f"{_fmt_value(total, unit)} across the reel")
        trend_range = (max(0, len(stats) - 3), len(stats) - 1)
        # Never overlap the peak caption — the peak is more specific.
        trend_occupied = set(range(trend_range[0], trend_range[1] + 1))
        if trend_occupied.isdisjoint(peak_occupied):
            captions.append(Caption(trend_range[0], trend_range[1],
                                    trend_text))

    return captions
