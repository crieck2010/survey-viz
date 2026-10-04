"""viz.aesthetic_render: mapped.earth-style preset rendering via survey-aesthetics.

This module is the survey-viz side of the aesthetics engine integration
(survey-aesthetics v0.1.0+). It implements the ``preset`` render path of
:func:`viz.render.render_viz`: instead of the standard matplotlib data
map, frames are rendered through the aesthetics engine's curated layers
(LIC flow-field streaks, additive event glow, 3D prism extrusion),
rotated auto-fit framing, editorial typography, and custom legends.

The legacy path (``preset=None``) is untouched — see the regression
test in ``tests/test_aesthetics_render.py``.

Presets and the variables they support::

    dark_flow   LIC streaks on black (hue = scalar, brightness = speed):
                ``currents`` (u/v + water temperature), ``wind`` (ERA5 u10/v10)
    dark_glow   additive event glow with bloom on black:
                ``earthquakes`` (cumulative epicenters), ``storm-tracks``
    paper_prism 3D prism extrusion on warm paper (height = value):
                any continuous gridded variable (``tp`` is the reference case)
    surface     colored scalar surface over a shadowed land/footprint
                silhouette (survey-aesthetics v0.4.0 ``render_surface``):
                any continuous gridded variable; default cmap
                ``teal_pink``, optional log scale, contours and drop
                shadow. Falls back to a plain colormapped grid when
                ``render_surface`` is unavailable (peer < 0.4.0).

Honest rules this module enforces (it never guesses):

* reel-wide FIXED scales — ``vmin``/``vmax``/``speed_max`` are computed
  once from the full dataset and reused for every frame (per-frame
  scaling would flicker);
* a fixed LIC seed per reel (a varying seed shimmers the texture);
* a preset on a variable without the data it needs is a ``ValueError``
  with the reason — never a silently wrong picture (e.g. ``dark_flow``
  on ``sst`` has no vector field to streak);
* ``rotation`` only applies to the preset path (rotating the legacy
  path would rotate its burned-in titles too);
* the watermark furniture is opt-in (``watermark=None`` draws nothing);
* explicit user choices win: ``spec.title`` is never rewritten,
  ``spec.vmin``/``vmax`` beat the data range, an explicit ``subtitle``
  beats the auto window label.

The survey-aesthetics peer is optional: it is imported only when this
module is used. Without it, requesting a preset raises a ``RuntimeError``
with the install command — the rest of survey-viz never notices.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

#: Preset names accepted by ``render_viz(preset=...)``.
AESTHETIC_PRESETS = ("dark_flow", "dark_glow", "paper_prism", "dark_strands",
                     "surface")

#: Variables each preset can render. ``None`` means "any continuous
#: gridded variable" (the paper_prism case — height = value works for
#: any scalar field; the surface case — a colored scalar surface works
#: for any scalar field).
PRESET_VARIABLES: Dict[str, Optional[Tuple[str, ...]]] = {
    "dark_flow": ("currents", "wind"),
    "dark_glow": ("earthquakes", "storm-tracks"),
    "paper_prism": None,
    "dark_strands": ("currents", "wind"),
    "surface": None,
}

#: Basemap styles accepted by ``render_viz(basemap=...)``. ``None``
#: keeps the preset's bundled default.
BASEMAP_STYLES = ("void_black", "no_basemap", "subtle_land")

#: Fixed LIC noise seed per reel (the engine rule: a fixed seed keeps
#: the streak texture identical across frames).
_LIC_SEED = 7

#: Prism grids are downsampled to at most this size (the engine's
#: documented cost limit).
_PRISM_MAX_NY, _PRISM_MAX_NX = 44, 60

#: Human-readable labels for the prism scale bar (fallback: the
#: variable name with dashes as spaces).
_VARIABLE_LABELS = {
    "tp": "precipitation",
    "t2m": "air temperature",
    "sst": "sea surface temperature",
    "msl": "sea-level pressure",
    "wind": "wind speed",
    "ocean-color": "chlorophyll-a",
    "sea-ice": "sea-ice concentration",
    "fire": "fire radiative power",
    "night-lights": "night lights",
    "water-storage": "water storage anomaly",
}


def _variable_label(variable: str) -> str:
    return _VARIABLE_LABELS.get(variable, variable.replace("-", " "))

_CANVAS_W, _CANVAS_H = 1080, 1920


def _engine():
    """Import the survey-aesthetics peer (lazy, with an honest error).

    Returns a namespace over the engine's public surface. Names the
    engine's docs promise but that are missing from the installed
    top-level namespace are resolved from submodules here so call
    sites use one namespace. The engine release itself is never
    modified.
    """
    import types

    try:
        import aesthetics as ae
        from aesthetics.backgrounds import close as _close
        from aesthetics.typography import draw_north_arrow as _north_arrow
        from aesthetics.typography import letterspace as _letterspace
    except ImportError as exc:
        raise RuntimeError(
            "render_viz(preset=...) needs the survey-aesthetics peer "
            "engine, which is not installed. Install it with: "
            "pip install 'survey-viz[aesthetics]'"
        ) from exc
    ns = types.SimpleNamespace(
        **{k: getattr(ae, k) for k in dir(ae) if not k.startswith("_")})
    ns.draw_north_arrow = _north_arrow
    ns.close = _close
    # letterspace is submodule-only; needed to measure the subtitle the
    # same way draw_subtitle renders it (upper + thin-spaced).
    if not hasattr(ns, "letterspace"):
        ns.letterspace = _letterspace
    ns.__version__ = str(getattr(ae, "__version__", "unknown"))
    return ns


def _np():
    """numpy, via the same lazy path as viz.render."""
    from .render import _require_plotting

    _, _, np = _require_plotting()
    return np


def _aesthetics_version() -> str:
    try:
        ae = _engine()
        return str(getattr(ae, "__version__", "unknown"))
    except RuntimeError:
        return "not-installed"


def _gazetteer():
    """Import the survey-gazetteer peer (lazy, with an honest error).

    Mirrors :func:`_engine`: the peer stays optional — the legacy
    renderer and the label-free preset path never touch it.
    """
    try:
        import gazetteer as gz
    except ImportError as exc:
        raise RuntimeError(
            "render_viz(place_labels=...) needs the survey-gazetteer peer "
            "engine, which is not installed. Install it with: "
            "pip install 'survey-viz[aesthetics]'"
        ) from exc
    return gz


def _gazetteer_version() -> str:
    try:
        gz = _gazetteer()
        return str(getattr(gz, "__version__", "unknown"))
    except RuntimeError:
        return "not-installed"


def _flow():
    """Import the survey-flow peer (lazy, with an honest error).

    Mirrors :func:`_engine`: the peer stays optional — only the
    ``dark_strands`` preset touches it.
    """
    try:
        from flow import advect as _advect
        from flow import fields as _fields
    except ImportError as exc:
        raise RuntimeError(
            "render_viz(preset='dark_strands') needs the survey-flow peer "
            "engine, which is not installed. Install it with: "
            "pip install 'survey-viz[aesthetics]'"
        ) from exc
    return _advect, _fields


def _flow_version() -> str:
    try:
        advect, _ = _flow()
        return str(getattr(advect, "__version__", "unknown"))
    except RuntimeError:
        return "not-installed"


def _autopilot():
    """Import the survey-autopilot peer (lazy, optional, graceful fallback).

    Returns the ``autopilot`` module when importable, else ``None``.
    Callers degrade feature-by-feature to the pre-autopilot behavior
    when this returns ``None`` — a missing peer never raises, never
    silently changes a picture. Uses plain ``import autopilot`` plus
    attribute access (not ``from autopilot.x import y``) so tests can
    monkeypatch ``sys.modules["autopilot"]``.
    """
    try:
        import autopilot
        return autopilot
    except ImportError:
        return None


def _autopilot_version() -> str:
    ap = _autopilot()
    if ap is None:
        return "not-installed"
    return str(getattr(ap, "__version__", "unknown"))


# ---------------------------------------------------------------------------
# surface preset + animated counter + headline beats (v0.28.0)
#
# Viz does NOT depend on survey-narrate: headline beats arrive as plain
# ``(when, text)`` data from any source (reel-studio may draft them
# with ``narrate.headline_beats``). The counter is plain data too.
# ---------------------------------------------------------------------------

#: Counter statistics accepted by ``render_viz(counter={"stat": ...})``.
_COUNTER_STATS = ("sum", "mean", "max")


def _render_surface_fn(ae) -> Any:
    """Return the peer's ``render_surface`` callable, or ``None``.

    Capability check for the graceful fallback: aesthetics < 0.4.0 has
    no ``render_surface``. Tests can monkeypatch this helper (or delete
    ``aesthetics.render_surface``) to exercise the fallback path.
    """
    fn = getattr(ae, "render_surface", None)
    return fn if callable(fn) else None


def _has_render_surface(ae) -> bool:
    return _render_surface_fn(ae) is not None


def _surface_preset_obj(ae):
    """Preset object for ``surface`` (local until the engine bundles one).

    survey-aesthetics 0.4.0 ships ``render_surface`` but no ``surface``
    entry in ``get_preset``; build a Preset-shaped bundle locally with
    the surface look: near-black teal background (matching
    ``render_surface``'s default background), ``teal_pink`` cmap,
    white editorial text. If a future engine bundles its own surface
    preset, that one wins.
    """
    try:
        return ae.get_preset("surface")
    except Exception:
        pass
    # Construct the engine's own Preset dataclass when importable so
    # attribute access behaves exactly like the bundled presets.
    try:
        from aesthetics.presets import Preset as _Preset
        from aesthetics.basemap import VOID_BLACK as _VOID
        return _Preset(
            name="surface",
            background="black",
            bg_rgb=(0.012, 0.043, 0.055),
            cmap="teal_pink",
            text_color="white",
            muted_color="#9aa0aa",
            encoding="COLOR = VALUE",
            legend_layout={
                "title": (0.06, 0.94),
                "subtitle": (0.06, 0.875),
                "gradient_bar": (0.60, 0.20, 0.30, 0.012),
                "timeline": (0.60, 0.28, 0.30, 0.010),
                "counter": (0.06, 0.16),
                "north_arrow": (0.06, 0.80),
            },
            basemap=_VOID,
        )
    except Exception:
        import types
        return types.SimpleNamespace(
            name="surface", background="black",
            bg_rgb=(0.012, 0.043, 0.055), cmap="teal_pink",
            text_color="white", muted_color="#9aa0aa",
            title_size=64, subtitle_size=26, readout_size=20,
            encoding="COLOR = VALUE",
            legend_layout={
                "title": (0.06, 0.94),
                "subtitle": (0.06, 0.875),
                "gradient_bar": (0.60, 0.20, 0.30, 0.012),
                "timeline": (0.60, 0.28, 0.30, 0.010),
                "counter": (0.06, 0.16),
                "north_arrow": (0.06, 0.80),
            },
            basemap=None,
        )


def _validate_counter(counter: Any) -> Optional[Dict[str, Any]]:
    """Validate a ``counter`` spec; return a plain dict copy or None."""
    if counter is None:
        return None
    if not isinstance(counter, dict):
        raise ValueError(
            f"counter: expected None or a dict like "
            f"{{'stat': 'sum', 'unit': 'people', 'label': 'TOTAL'}}, "
            f"got {counter!r}")
    stat = counter.get("stat")
    if stat not in _COUNTER_STATS:
        raise ValueError(
            f"counter['stat']: expected one of {list(_COUNTER_STATS)}, "
            f"got {stat!r}")
    out: Dict[str, Any] = {"stat": stat}
    for key in ("unit", "label"):
        val = counter.get(key)
        if val is not None:
            if not isinstance(val, str):
                raise ValueError(
                    f"counter[{key!r}]: expected a string, got {val!r}")
            out[key] = val
    return out


def _validate_headline_beats(headline_beats: Any) -> List[Tuple[Any, str]]:
    """Validate raw ``headline_beats``; return [(when, text), ...].

    ``when`` is a float fraction in [0, 1] of the reel, or an ISO-8601
    timestamp string. Text must be a non-empty string. Timestamp
    strings are only *parsed* here (frame assignment needs the reel's
    frame times, which each preset renderer knows); an unparseable
    timestamp is a ValueError here, before any frame renders.
    """
    if headline_beats is None:
        return []
    if not isinstance(headline_beats, (list, tuple)):
        raise ValueError(
            f"headline_beats: expected None or a list of (when, text) "
            f"pairs, got {headline_beats!r}")
    out: List[Tuple[Any, str]] = []
    for i, item in enumerate(headline_beats):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(
                f"headline_beats[{i}]: expected a (when, text) pair, "
                f"got {item!r}")
        when, text = item
        if not isinstance(text, str) or not text.strip():
            raise ValueError(
                f"headline_beats[{i}]: text must be a non-empty string, "
                f"got {text!r}")
        if isinstance(when, bool):
            raise ValueError(
                f"headline_beats[{i}]: 'when' must be a fraction in "
                f"[0, 1] or an ISO-8601 timestamp, got {when!r}")
        if isinstance(when, (int, float)):
            frac = float(when)
            if not (0.0 <= frac <= 1.0):
                raise ValueError(
                    f"headline_beats[{i}]: fraction must be in [0, 1], "
                    f"got {when!r}")
            out.append((frac, text))
        elif isinstance(when, str):
            _parse_beat_timestamp(when, i)
            out.append((when, text))
        else:
            raise ValueError(
                f"headline_beats[{i}]: 'when' must be a fraction in "
                f"[0, 1] or an ISO-8601 timestamp string, got {when!r}")
    return out


def _parse_beat_timestamp(text: str, idx: int = 0) -> _dt.datetime:
    """Parse an ISO-8601 beat timestamp (aware UTC); ValueError if bad."""
    raw = str(text).strip()
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = _dt.date.fromisoformat(raw[:10])
            parsed = _dt.datetime(d.year, d.month, d.day)
        except ValueError:
            raise ValueError(
                f"headline_beats[{idx}]: unparseable timestamp "
                f"{text!r}; expected ISO-8601 (e.g. '2026-09-16T00:00:00Z' "
                f"or '2026-09-16')") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _normalize_headline_beats(headline_beats: Any, n_frames: int,
                              frame_times: Any = None
                              ) -> List[Dict[str, Any]]:
    """Normalize raw beats to [{fraction, frame, text}, ...] for a reel.

    Fraction beats: ``frame = round(fraction * (n_frames - 1))``.
    Timestamp beats: the nearest frame at/after the timestamp (the
    last frame when the timestamp is past the reel's end, frame 0 when
    before its start); ``frame_times`` are the frames' datetimes.
    Beats are sorted by frame; when two beats land on the same frame
    the later-listed one wins (the earlier is dropped). The default
    title (``spec.title``) shows until the first beat.
    """
    raw = _validate_headline_beats(headline_beats)
    if not raw or int(n_frames) <= 0:
        return []
    n = int(n_frames)
    times: List[_dt.datetime] = []
    if frame_times is not None:
        times = _as_datetimes(list(frame_times))
    staged: List[Tuple[int, int, float, str]] = []  # frame, order, frac, text
    for order, (when, text) in enumerate(raw):
        if isinstance(when, str):
            if not times:
                raise ValueError(
                    "headline_beats: timestamp beats need the field's "
                    "frame times to resolve against")
            beat_t = _parse_beat_timestamp(when, order)
            frame = n - 1
            for j, ft in enumerate(times):
                if ft >= beat_t:
                    frame = j
                    break
            frac = (frame / (n - 1)) if n > 1 else 0.0
        else:
            frac = float(when)
            frame = int(round(frac * (n - 1))) if n > 1 else 0
            frame = max(0, min(n - 1, frame))
        staged.append((frame, order, float(frac), text))
    staged.sort(key=lambda s: (s[0], s[1]))
    out: List[Dict[str, Any]] = []
    for frame, _order, frac, text in staged:
        rec = {"fraction": float(frac), "frame": int(frame), "text": text}
        if out and out[-1]["frame"] == frame:
            out[-1] = rec  # same frame: later-listed wins
        else:
            out.append(rec)
    return out


def _beat_text_for_frame(normalized: Any, frame_idx: int,
                         default: str) -> str:
    """Title text active at ``frame_idx`` (clean cut at each beat)."""
    text = default
    for beat in normalized or []:
        if int(beat["frame"]) <= int(frame_idx):
            text = str(beat["text"])
        else:
            break
    return text


def _counter_stats(np, values: Any, stat: str) -> List[float]:
    """Per-timestep NaN-aware stat over finite cells (NaN if none)."""
    if stat not in _COUNTER_STATS:
        raise ValueError(
            f"counter stat: expected one of {list(_COUNTER_STATS)}, "
            f"got {stat!r}")
    arr = np.asarray(values, dtype=float)
    if arr.ndim == 2:
        arr = arr[np.newaxis, :, :]
    out: List[float] = []
    for t in range(arr.shape[0]):
        finite = arr[t][np.isfinite(arr[t])]
        if finite.size == 0:
            out.append(float("nan"))
        elif stat == "sum":
            out.append(float(np.sum(finite)))
        elif stat == "mean":
            out.append(float(np.mean(finite)))
        else:  # max
            out.append(float(np.max(finite)))
    return out


def _interpolate_counter_stat(stats: Any, moments: Any,
                              moment: Any) -> Optional[float]:
    """Linearly interpolated counter value at ``moment``.

    Interpolation runs between the bracketing *valid* (finite-stat)
    timesteps, weighted by time; before the first / after the last
    valid timestep the value clamps to that timestep's stat. A
    timestep with no finite cells contributes nothing (it is skipped,
    so the interpolation spans it). All-NaN series -> ``None``.
    Between observed timesteps the result is a linear *estimate*, not
    a measurement — docs and the manifest say so.
    """
    valid = [(m, float(s)) for m, s in zip(moments or [], stats or [])
             if s is not None and s == s and _is_finite_number(s)]
    if not valid:
        return None
    if moment is None:
        return valid[0][1]
    # Coerce everything to aware datetimes for safe comparison.
    def _aware(m):
        if isinstance(m, _dt.datetime):
            return m if m.tzinfo else m.replace(tzinfo=_dt.timezone.utc)
        if isinstance(m, _dt.date):
            return _dt.datetime(m.year, m.month, m.day,
                                 tzinfo=_dt.timezone.utc)
        return m
    t = _aware(moment)
    pts = [(_aware(m), s) for m, s in valid]
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            span = (t1 - t0).total_seconds()
            if span <= 0:
                return s1
            w = (t - t0).total_seconds() / span
            return s0 + (s1 - s0) * w
    return pts[-1][1]


def _is_finite_number(x: Any) -> bool:
    try:
        import math
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _format_counter_value(value: Any) -> str:
    """Format a counter value with thousands separators.

    Rule (documented): integral-ish values, and any value with
    magnitude >= 1000, render as a thousands-separated integer
    (``1,234,567``); smaller non-integral values render with up to 3
    significant decimals via ``,.3g`` (``12.3``, ``0.456``). ``None`` /
    non-finite renders as an em dash.
    """
    if value is None or not _is_finite_number(value):
        return "\u2014"
    v = float(value)
    if abs(v) >= 1000 or float(v).is_integer():
        return f"{int(round(v)):,}"
    return f"{v:,.3g}"


def _orient_north_up(np, grid: Any, lats: Any):
    """Return ``grid`` with row 0 = north (top), flipping if needed.

    ``render_surface`` expects row 0 = top. Field grids follow the
    ``_normalize_field`` convention: rows follow ``lats`` as given, so
    ascending lats (south first) need a vertical flip and descending
    lats (north first) pass through. This matches how the other
    presets end up north-up on the canvas (``origin="upper"``).
    """
    arr = np.asarray(grid, dtype=float)
    lat_arr = np.asarray(lats, dtype=float).ravel()
    if lat_arr.size >= 2 and lat_arr[0] < lat_arr[-1]:
        return arr[::-1, :].copy()
    return arr.copy()


def _fallback_surface_rgba(np, grid: Any, cmap_name: str, vmin: float,
                           vmax: float, scale: str, width_px: int,
                           height_px: int):
    """Plain colormapped grid as RGBA (the old-peer fallback).

    No contours, no shadow: NaN cells are transparent over the
    surface background colour, finite cells are colormapped on the
    reel-wide fixed scale (log-aware). Used only when the peer's
    ``render_surface`` is unavailable.
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import colors as _mcolors
    try:
        cmap_obj = matplotlib.colormaps.get_cmap(cmap_name)
    except Exception:
        cmap_obj = matplotlib.colormaps.get_cmap("viridis")
    arr = np.asarray(grid, dtype=float)
    # Nearest-neighbour resize to the exact output size (deterministic).
    ny, nx = arr.shape
    yi = np.minimum((np.arange(height_px) * ny // max(height_px, 1)), ny - 1)
    xi = np.minimum((np.arange(width_px) * nx // max(width_px, 1)), nx - 1)
    small = arr[np.ix_(yi, xi)]
    if scale == "log":
        norm = _mcolors.LogNorm(vmin=vmin, vmax=vmax)
    else:
        norm = _mcolors.Normalize(vmin=vmin, vmax=vmax)
    rgba = cmap_obj(norm(np.ma.masked_invalid(small)))
    rgba = (np.asarray(rgba) * 255.0).astype(np.uint8)
    # Background where there is no data (render_surface's default bg).
    bg = np.array([0.012, 0.043, 0.055, 1.0])
    nodata = ~np.isfinite(small)
    if np.any(nodata):
        rgba[nodata] = (bg * 255.0).astype(np.uint8)
    return rgba


# Default place kinds for automatic labels (the gazetteer's own default).
_LABEL_KINDS = ("city", "town")

#: Strand advection tuning (fixed per reel — a varying seed shimmers).
_STRAND_SEED = 7
_STRAND_TRAIL_LENGTH = 12
_STRAND_SPINUP_STEPS = 12
_STRAND_DT_SECONDS = 3600.0
#: Default strand count (the engine's cost note: ~1.3 s/frame at 4000).
_DEFAULT_STRAND_COUNT = 3000
_DEFAULT_STRAND_LINEWIDTH = 1.4


def _resolve_place_labels(place_labels: Any, spec,
                          max_labels: int, min_population: int,
                          candidate_pool: Optional[int] = None
                          ) -> Tuple[List[Dict[str, Any]], str]:
    """Resolve the reel's place labels ONCE (never per frame).

    Returns ``(label_dicts, mode)`` where each dict is
    ``{"x": lon, "y": lat, "text": str, "priority": int}`` and ``mode``
    is ``"auto"`` / ``"explicit"`` / ``"off"``.

    - ``True`` (the default when a preset is active): auto-fetch from
      the survey-gazetteer peer for the spec's north-up bbox.
    - ``False``: no labels (the peer is never imported).
    - a list/tuple of label dicts: used verbatim — explicit labels win
      over automatic ones, so they are never merged with auto results.
      Each dict needs numeric ``x``/``y`` and a string ``text`` and
      must be JSON-serializable (it lands in the frame manifest, which
      is what the survey-cache fingerprint hashes).

    ``candidate_pool`` (salience ranking): when set, the gazetteer
    fetches this many candidates instead of ``max_labels`` — the
    caller then ranks them (e.g. by the survey-autopilot salience
    map) and applies the ``max_labels`` cut itself, so the ranking
    decides which labels survive. ``mode`` is still ``"auto"``.
    """
    if place_labels is False:
        return [], "off"
    if isinstance(place_labels, (list, tuple)):
        out: List[Dict[str, Any]] = []
        for i, lb in enumerate(place_labels):
            if not isinstance(lb, dict):
                raise ValueError(
                    f"place_labels[{i}]: expected a label dict, got {lb!r}")
            try:
                x, y = float(lb["x"]), float(lb["y"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    f"place_labels[{i}]: needs numeric 'x'/'y', got {lb!r}"
                ) from None
            text = lb.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError(
                    f"place_labels[{i}]: needs a non-empty string 'text', "
                    f"got {lb!r}")
            import json as _json
            try:
                _json.dumps(lb)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"place_labels[{i}]: must be JSON-serializable for the "
                    f"manifest/fingerprint, got {lb!r}: {exc}") from None
            out.append(dict(lb))
        return out, "explicit"
    if place_labels is not True:
        raise ValueError(
            f"place_labels: expected True/False or a list of label dicts, "
            f"got {place_labels!r}")
    if not isinstance(max_labels, int) or isinstance(max_labels, bool) \
            or max_labels < 0:
        raise ValueError(
            f"max_labels: expected a non-negative int, got {max_labels!r}")
    if not isinstance(min_population, int) or isinstance(min_population, bool) \
            or min_population < 0:
        raise ValueError(
            f"min_population: expected a non-negative int, got "
            f"{min_population!r}")
    gz = _gazetteer()
    lon0, lat0, lon1, lat1 = (float(v) for v in spec.bbox)
    labels = gz.labels_for_bbox(
        lon0, lon1, lat0, lat1,
        max_labels=(candidate_pool if candidate_pool else max_labels),
        min_population=min_population,
        kinds=_LABEL_KINDS)
    return [dict(lb) for lb in labels], "auto"


def _northup_frac_to_final_frac(fx: float, fy: float,
                                rw: int, rh: int,
                                angle_deg: float) -> Tuple[float, float]:
    """Project a north-up map fraction point onto the final canvas.

    The preset path renders the north-up map on a ``(rw, rh)`` canvas
    whose data coordinates are axes fractions in [0, 1] (y up), then
    rotates it with the engine's ``rotate_frame_fill`` (PIL
    ``rotate(angle, expand=True)`` + center crop to 1080x1920) and
    draws the furniture — including place labels — on the final
    canvas, upright. This replicates the engine's pixel transform
    exactly (verified against ``rotate_frame_fill`` in the test
    suite): labels stay registered to the geography they name.
    """
    if not angle_deg:
        return fx, fy
    import math
    theta = math.radians(angle_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    w, h = float(rw), float(rh)
    # Expanded canvas size, exactly as PIL's Image.rotate(expand=True)
    # computes it: bounding box of the rotated source corners.
    corners = ((0.0, 0.0), (w, 0.0), (w, h), (0.0, h))
    xs = [cos_t * x - sin_t * y for x, y in corners]
    ys = [sin_t * x + cos_t * y for x, y in corners]
    nw = math.ceil(max(xs)) - math.floor(min(xs))
    nh = math.ceil(max(ys)) - math.floor(min(ys))
    # Forward map: north-up pixel -> expanded-canvas pixel. The data
    # point (fx, fy) (y up) is pixel (fx*w, (1-fy)*h) (y down); the
    # rotation is center-preserving, so the expanded center is
    # (nw/2, nh/2).
    px, py = fx * w, (1.0 - fy) * h
    dx = cos_t * (px - w / 2.0) + sin_t * (py - h / 2.0) + nw / 2.0
    dy = -sin_t * (px - w / 2.0) + cos_t * (py - h / 2.0) + nh / 2.0
    # Center crop to the final canvas (mirrors rotate_frame_fill).
    left = (nw - _CANVAS_W) // 2
    top = (nh - _CANVAS_H) // 2
    qx, qy = dx - left, dy - top
    return qx / _CANVAS_W, 1.0 - qy / _CANVAS_H


def _project_labels_to_final(label_dicts: List[Dict[str, Any]],
                             bbox: Tuple[float, ...],
                             angle: float,
                             render_size: Tuple[int, int]) -> List[Dict[str, Any]]:
    """Convert lon/lat label dicts to final furniture-canvas fractions.

    The gazetteer returns ``x``/``y`` in degrees on the north-up map;
    the engine's ``place_labels`` reads data coordinates of the canvas
    it draws on — the final (possibly rotated) furniture canvas, whose
    data coordinates are axes fractions. Called once per reel: the
    bbox, angle, and canvas are reel-static, so the projection is
    identical for every frame.
    """
    lon0, lat0, lon1, lat1 = (float(v) for v in bbox)
    rw, rh = render_size
    out: List[Dict[str, Any]] = []
    for lb in label_dicts:
        fx = (float(lb["x"]) - lon0) / (lon1 - lon0)
        fy = (float(lb["y"]) - lat0) / (lat1 - lat0)
        dx, dy = _northup_frac_to_final_frac(fx, fy, rw, rh, angle)
        placed = dict(lb)
        placed["x"], placed["y"] = dx, dy
        out.append(placed)
    return out


def _viz_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("survey-viz")
    except Exception:
        return "0.0.0+unknown"


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _resolve_rotation(rotation: Any, bbox: Tuple[float, ...]) -> float:
    """Normalize the ``rotation`` parameter to degrees (0.0 = none)."""
    if rotation is None:
        return 0.0
    if isinstance(rotation, str):
        if rotation.strip().lower() == "auto":
            ae = _engine()
            lon0, lat0, lon1, lat1 = (float(v) for v in bbox)
            return float(ae.optimal_rotation(
                lon0, lon1, lat0, lat1,
                canvas_wh=(_CANVAS_W, _CANVAS_H)))
        raise ValueError(
            f"rotation: expected 'auto' or degrees, got {rotation!r}")
    try:
        return float(rotation)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"rotation: expected 'auto' or degrees, got {rotation!r}"
        ) from exc


def _window_subtitle(spec) -> str:
    """Editorial subtitle naming the time window, e.g. '16–23 September 2026'."""
    start = spec.start
    end = spec.end

    def _d(d):
        return _dt.date(d.year, d.month, d.day) if isinstance(
            d, _dt.datetime) else d

    start, end = _d(start), _d(end)
    month = start.strftime("%B")
    if start == end:
        return f"{start.day} {month} {start.year}"
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {month} {start.year}"
    if start.year == end.year:
        return (f"{start.day} {month} – {end.day} "
                f"{end.strftime('%B')} {start.year}")
    return (f"{start.day} {month} {start.year} – "
            f"{end.day} {end.strftime('%B')} {end.year}")


def _readout_label(moment: _dt.datetime) -> str:
    """Mono timestamp readout: '17 SEP 10:00' when hourly, else '17 SEP 2026'."""
    if moment.hour or moment.minute or moment.second:
        return moment.strftime("%d %b %H:%M").upper()
    return moment.strftime("%d %b %Y").upper()


def _as_datetimes(times: List[Any]) -> List[_dt.datetime]:
    """Coerce a field's time axis to datetimes (dates -> midnight UTC)."""
    out = []
    for t in times:
        if isinstance(t, _dt.datetime):
            out.append(t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc))
        elif isinstance(t, _dt.date):
            out.append(_dt.datetime(t.year, t.month, t.day,
                                    tzinfo=_dt.timezone.utc))
        else:
            text = str(t).strip().replace("Z", "+00:00")
            try:
                parsed = _dt.datetime.fromisoformat(text)
            except ValueError:
                parsed = _dt.datetime.combine(
                    _dt.date.fromisoformat(text[:10]),
                    _dt.time(tzinfo=_dt.timezone.utc))
            out.append(parsed if parsed.tzinfo
                       else parsed.replace(tzinfo=_dt.timezone.utc))
    return out


def _frac_coords(lons, lats, bbox) -> Tuple[Any, Any]:
    """Map lon/lat arrays to axes fractions in [0, 1] for the bbox."""
    np = _np()
    lon0, lat0, lon1, lat1 = (float(v) for v in bbox)
    lons = np.asarray(lons, dtype=float)
    lats = np.asarray(lats, dtype=float)
    fx = (lons - lon0) / (lon1 - lon0)
    fy = (lats - lat0) / (lat1 - lat0)
    return fx, fy


def _draw_coastlines_frac(ax, segments: List[Dict[str, Any]],
                          bbox: Tuple[float, ...], color: str) -> None:
    """Hairline coastlines in axes-fraction space (aspect-safe)."""
    np = _np()
    for seg in segments or []:
        if isinstance(seg, dict):
            lons, lats = seg.get("lons", []), seg.get("lats", [])
        else:
            lons, lats = seg
        lons = np.asarray(lons, dtype=float)
        lats = np.asarray(lats, dtype=float)
        if lons.size == 0:
            continue
        fx, fy = _frac_coords(lons, lats, bbox)
        ax.plot(fx, fy, transform=ax.transAxes, color=color,
                linewidth=0.8, alpha=0.9, zorder=3,
                solid_capstyle="round", solid_joinstyle="round")


def _draw_basemap_frac(ax, ae, segments: List[Dict[str, Any]],
                       bbox: Tuple[float, ...],
                       style: Any,
                       land_mask: Any = None) -> None:
    """Styled basemap on the preset path, in axes-fraction space.

    The engine's :func:`draw_basemap` plots segments in the axes' data
    transform; the chrome-free preset canvases use axes-fraction
    coordinates in [0, 1], so lon/lat segments are projected to
    fractions first (data == fraction on these axes).
    ``land_mask`` is a 2D bool array, row 0 = top, ``True`` = land
    (only used by styles with a land fill, e.g. ``subtle_land``).
    """
    np = _np()
    frac_segs = []
    for seg in segments or []:
        if isinstance(seg, dict):
            lons, lats = seg.get("lons", []), seg.get("lats", [])
        else:
            lons, lats = seg
        lons = np.asarray(lons, dtype=float)
        lats = np.asarray(lats, dtype=float)
        if lons.size == 0:
            continue
        fx, fy = _frac_coords(lons, lats, bbox)
        frac_segs.append({"lons": fx, "lats": fy})
    ae.draw_basemap(ax, frac_segs, style, mask=land_mask, zorder=1)


# ---------------------------------------------------------------------------
# Furniture layout: collision-aware placement, once per reel
# ---------------------------------------------------------------------------

#: Padding factor around the date dial's circle (month letters ring it).
_DIAL_PAD = 1.35


def _layout_furniture(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Run the engine's collision-aware placer once per reel.

    Builds FurnitureSpecs from the preset's ``legend_layout`` (preferred
    rects) with the priority/alternative contract from the engine's
    docs/LAYOUT.md, measures real glyph extents on a probe canvas, and
    returns ``{kind: PlacedItem}``. Static per reel: per-frame draw
    functions reuse these rects (dial/timeline/counter *contents*
    change per frame; their boxes don't).

    Must run after the preset renderer sets ``ctx["encoding"]``.
    """
    ae = ctx["ae"]
    preset = ctx["preset"]
    spec = ctx["spec"]
    lay = preset.legend_layout
    subtitle = ctx["subtitle"]
    name = preset.name
    angle = ctx["angle"]

    fig, ax = ae.new_canvas(_CANVAS_W, _CANVAS_H, background=preset.bg_rgb)
    try:
        specs = []
        tx, ty = lay["title"][:2]
        specs.append(ae.text_spec(
            ax, "title", spec.title, tx, ty,
            family="DejaVu Serif", size=preset.title_size, weight="bold",
            priority=10))
        sx, sy = lay["subtitle"][:2]
        from aesthetics.typography import letterspace as _letterspace
        if subtitle:
            sub_measured = _letterspace(subtitle.upper())
            specs.append(ae.text_spec(
                ax, "subtitle", sub_measured, sx, sy,
                family="DejaVu Sans", size=preset.subtitle_size,
                priority=9, shrink=False,
                alternatives=[(0.06, 0.78, 0.55, 0.05),
                              (0.60, 0.875, 0.34, 0.05)]))
        if name in ("dark_flow", "dark_strands", "surface"):
            gx, gy, gw, gh = lay.get(
                "gradient_bar", (0.60, 0.20, 0.30, 0.012))[:4]
            specs.append(ae.box_spec(
                "gradient_bar", (gx, gy, gw, gh), priority=6,
                alternatives=[(0.60, 0.18, gw, gh),
                              (0.06, 0.08, 0.60, gh)]))
            lx, ly, lw, lh = lay.get(
                "timeline", (0.60, 0.28, 0.30, 0.010))[:4]
            specs.append(ae.box_spec(
                "timeline", (lx, ly, lw, lh), priority=6,
                alternatives=[(0.06, 0.05, lw, lh),
                              (0.62, 0.26, lw, lh)]))
        if name == "dark_glow" or (
                name == "surface" and ctx.get("counter") is not None):
            cx0, cy0 = lay.get("counter", (0.06, 0.16))[:2]
            _cw = 0.40 if name == "surface" else 0.32
            specs.append(ae.box_spec(
                "counter", (cx0, cy0 - 0.05, _cw, 0.115), priority=7,
                alternatives=[(0.55 if name == "surface" else 0.62,
                               0.05, _cw, 0.115)]))
        if name in ("dark_glow", "paper_prism"):
            dx, dy, dr = lay["date_dial"][:3]
            dw = dh = 2 * dr * _DIAL_PAD
            specs.append(ae.box_spec(
                "date_dial",
                (dx - dw / 2, dy - dh / 2, dw, dh), priority=5,
                alternatives=[(0.80 - dw / 2, 0.52 - dh / 2, dw, dh),
                              (0.78 - dw / 2, 0.10 - dh / 2, dw, dh)],
                optional=True))
        if name == "paper_prism":
            specs.append(ae.box_spec(
                "vertical_scale", (0.10, 0.24, 0.14, 0.34), priority=6,
                alternatives=[(0.80, 0.24, 0.14, 0.34)]))
        if angle:
            nx, ny = lay.get("north_arrow", (0.06, 0.80))[:2]
            specs.append(ae.box_spec(
                "north_arrow", (nx, ny - 0.02, 0.10, 0.10), priority=8))
        # Standalone honesty line (when not inside the watermark block).
        enc = ctx.get("encoding")
        if (ctx.get("watermark") is None
                and ctx.get("encoding_line", True) and enc):
            w, h = ae.text_extent_frac(ax, enc, family="DejaVu Sans Mono",
                                       size=18)
            specs.append(ae.text_spec(
                ax, "encoding", enc, 0.5 - w / 2, 0.075,
                family="DejaVu Sans Mono", size=18,
                priority=4, optional=True, shrink=False,
                alternatives=[(0.5 - w / 2, 0.115 - h, w, h)]))
        placed = ae.place_furniture(specs, pad=0.012)
        return {p.kind: p for p in placed}
    finally:
        import matplotlib.pyplot as _plt
        _plt.close(fig)


def _title_fit_scale(rect) -> float:
    """Extra shrink-to-fit clamp for the title.

    The engine's placer offers shrink steps down to 0.70, which cannot
    fit very long titles; this clamps the drawn size so the title
    never runs off the canvas edge.
    """
    _x, _y, w, _h = rect
    max_w = 1.0 - _x - 0.02
    if w > max_w > 0:
        return max_w / w
    return 1.0


class _Furniture:
    """Placed furniture for one reel: rects plus per-frame draw calls.

    Built once per reel (after the preset renderer sets
    ``ctx["encoding"]``); the per-frame ``draw_furniture`` closures
    call these instead of using fixed ``legend_layout`` positions.
    """

    def __init__(self, ctx: Dict[str, Any]):
        self._ctx = ctx
        self.placed = _layout_furniture(ctx)

    def _rect(self, kind: str):
        p = self.placed.get(kind)
        if p is None or not p.placed:
            return None
        return p.rect

    def obstacle_rects(self) -> List[Tuple[float, float, float, float]]:
        """Placed rects, for place_labels obstacles.

        The gradient bar's drawn assembly (label above, ticks below)
        and the timeline's readout above extend past the placed box;
        the obstacles cover the full assembly so labels don't land
        on them.
        """
        rects = []
        for kind, p in self.placed.items():
            if not p.placed:
                continue
            x, y, w, h = p.rect
            if kind == "gradient_bar":
                y -= 0.028
                h += 0.056
            elif kind == "timeline":
                h += 0.030
            rects.append((x, y, w, h))
        return rects

    def _colors(self):
        preset = self._ctx["preset"]
        return preset.text_color

    def draw_title(self, ax, text=None):
        ae = self._ctx["ae"]
        preset, spec = self._ctx["preset"], self._ctx["spec"]
        p = self.placed["title"]
        # Shrink-to-fit: the engine's placed scale, then the extra clamp
        # for titles longer than the engine's shrink floor can fit.
        scale = p.scale * _title_fit_scale(
            (p.rect[0], p.rect[1], p.rect[2] * p.scale, p.rect[3]))
        ae.draw_title(ax, spec.title if text is None else text,
                      p.rect[0], p.rect[1] + p.rect[3],
                      size=preset.title_size * scale,
                      color=preset.text_color)

    def draw_subtitle(self, ax):
        ae = self._ctx["ae"]
        preset = self._ctx["preset"]
        p = self.placed["subtitle"]
        ae.draw_subtitle(ax, self._ctx["subtitle"],
                         p.rect[0], p.rect[1] + p.rect[3],
                         size=preset.subtitle_size,
                         color=preset.text_color)

    def draw_dial(self, ax, when):
        ae = self._ctx["ae"]
        r = self._rect("date_dial")
        if r is None:
            return
        x, y, w, h = r
        rad = min(w, h) / 2.0 / _DIAL_PAD
        ae.date_dial(ax, x + w / 2.0, y + h / 2.0, rad, when,
                     color=self._colors())

    def draw_counter(self, ax, value, label):
        ae = self._ctx["ae"]
        r = self._rect("counter")
        if r is None:
            return
        # Box top holds the label; the number's top anchor sits 0.05 in.
        if isinstance(value, str):
            # Pre-formatted string (surface counter): ae.counter only
            # accepts numbers, so draw the same look directly.
            if label:
                ax.text(r[0], r[1] + 0.05 + 0.045, str(label).upper(),
                        transform=ax.transAxes, fontsize=18,
                        color=self._colors(), alpha=0.7, ha="left",
                        va="bottom", family="DejaVu Sans Mono", zorder=10)
            ax.text(r[0], r[1] + 0.05, value, transform=ax.transAxes,
                    fontsize=56, color=self._colors(), alpha=0.95,
                    ha="left", va="top", family="DejaVu Sans Mono",
                    weight="bold", zorder=10)
            return
        ae.counter(ax, r[0], r[1] + 0.05, value, label=label,
                   color=self._colors())

    def draw_surface_counter(self, ax, readout, value_text, label=""):
        """Surface counter: big date readout, stat + unit + label beneath.

        Uses the existing counter rect (see ``draw_counter``). The
        readout reuses ``_readout_label`` formatting (passed in by the
        caller); the stat line is the pre-formatted
        ``_format_counter_value`` output plus unit/label.
        """
        r = self._rect("counter")
        if r is None:
            return
        x, y, _w, h = r
        color = self._colors()
        # Date/timestamp readout on the top line of the counter box.
        ax.text(x, y + h, readout, transform=ax.transAxes, fontsize=30,
                color=color, alpha=0.95, ha="left", va="top",
                family="DejaVu Sans Mono", weight="bold", zorder=10)
        # Formatted stat (+ unit) beneath it.
        ax.text(x, y + h - 0.048, value_text, transform=ax.transAxes,
                fontsize=44, color=color, alpha=0.95, ha="left",
                va="top", family="DejaVu Sans Mono", weight="bold",
                zorder=10)
        if label:
            ax.text(x, y + h - 0.095, str(label).upper(),
                    transform=ax.transAxes, fontsize=16, color=color,
                    alpha=0.7, ha="left", va="top",
                    family="DejaVu Sans Mono", zorder=10)

    def draw_gradient_bar(self, ax, vmin, vmax, label, unit):
        ae = self._ctx["ae"]
        r = self._rect("gradient_bar")
        if r is None:
            return
        ae.gradient_bar(ax, r, self._ctx["cmap"], vmin, vmax,
                        label=label, unit=unit, color=self._colors())

    def draw_surface_gradient_bar(self, ax, vmin, vmax, label, unit,
                                  scale="linear"):
        """Surface colorbar: gradient bar plus log decade ticks if log.

        Linear: the gradient bar's endpoint labels are the ticks. Log:
        interior powers of ten between vmin/vmax are labelled under
        the bar at their log positions (decade ticks), in addition to
        the endpoints.
        """
        self.draw_gradient_bar(ax, vmin, vmax, label, unit)
        if scale != "log":
            return
        r = self._rect("gradient_bar")
        if r is None or not (vmin > 0 and vmax > vmin):
            return
        import math
        x, y, w, _h = r
        lo, hi = math.log10(vmin), math.log10(vmax)
        for decade in range(math.ceil(lo), math.floor(hi) + 1):
            val = 10.0 ** decade
            if val <= vmin or val >= vmax:
                continue
            frac = (math.log10(val) - lo) / (hi - lo) if hi > lo else 0.5
            ax.text(x + frac * w, y - 0.006, f"{val:g}{unit}",
                    transform=ax.transAxes, fontsize=13,
                    color=self._colors(), alpha=0.9, ha="center",
                    va="top", family="DejaVu Sans Mono", zorder=10)

    def draw_brightness_note(self, ax, text):
        """Bivariate legend note for dark_strands (placement documented).

        Drawn centered directly UNDER the gradient bar (inside the bar's
        obstacle extension, so place_labels avoids it): the bar itself
        shows the color channel (temperature, with its vmin/vmax ticks)
        and this line names the brightness channel plus its reel-wide
        fixed scale, e.g. ``BRIGHTNESS = WIND SPEED (0.00–12.40 M/S)``.
        """
        ae = self._ctx["ae"]
        r = self._rect("gradient_bar")
        if r is None or not text:
            return
        x, y, w, _h = r
        ax.text(x + w / 2, y - 0.030, text, transform=ax.transAxes,
                fontsize=15, color=self._colors(), alpha=0.9, ha="center",
                va="top", zorder=10)

    def draw_timeline(self, ax, t_start, t_end, t_now):
        ae = self._ctx["ae"]
        r = self._rect("timeline")
        if r is None:
            return
        ae.timeline(ax, r, t_start, t_end, t_now, color=self._colors())

    def draw_scale_bar(self, ax, vmin, vmax, label, unit):
        ae = self._ctx["ae"]
        r = self._rect("vertical_scale")
        if r is None:
            return
        ae.vertical_scale_bar(ax, r[0], r[1], r[3] - 0.06, vmin, vmax,
                              label=label, unit=unit,
                              color=self._colors())

    def draw_north_arrow(self, ax):
        ae = self._ctx["ae"]
        r = self._rect("north_arrow")
        if r is None:
            return
        ae.draw_north_arrow(ax, r[0], r[1] + 0.02,
                            angle_deg=self._ctx["angle"],
                            color=self._colors())

    def draw_encoding(self, ax):
        ae = self._ctx["ae"]
        r = self._rect("encoding")
        if r is None:
            return
        ae.encoding_statement(ax, r[0] + r[2] / 2.0, r[1] + r[3],
                              self._ctx["encoding"], color=self._colors())


def _data_source_label(spec, field) -> str:
    """Uppercase data-source line for the furniture footer."""
    if isinstance(field, dict):
        prov = field.get("provenance", {}) or {}
    else:
        prov = getattr(field, "provenance", {}) or {}
    source = (prov.get("source")
              or getattr(field, "source", "")
              or getattr(spec, "source", "")
              or spec.variable)
    return str(source).strip().upper() or spec.variable.upper()


def _nanminmax(np, *arrays) -> Tuple[float, float]:
    finite = np.concatenate(
        [np.asarray(a, dtype=float).ravel() for a in arrays])
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0, 1.0
    return float(np.min(finite)), float(np.max(finite))


def _robust_vmax(np, arr, pct: float = 99.0) -> float:
    """Reel-wide robust maximum for brightness scales.

    The given percentile of finite values (default p99), not the raw
    max — a single extreme cell (model gust front, tropical cyclone)
    must not crush the whole reel's brightness range into black.
    Values above the percentile clip, exactly like a colorbar's top
    end; the percentile is recorded in the manifest.
    """
    flat = np.asarray(arr, dtype=float).ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return 1.0
    vmax = float(np.percentile(flat, pct))
    return vmax if vmax > 0 else 1.0


# ---------------------------------------------------------------------------
# survey-autopilot assists (optional peer; graceful fallback everywhere)
#
# Three first-pass knobs — robust color scales, area-proportional
# strand counts, salience-ranked labels — each degrades to the
# pre-autopilot behavior when the peer is absent. Manifest records
# which path each render took (scale_method, strand_count_method,
# label_ranking) so a first pass is always auditable.
# ---------------------------------------------------------------------------

def _resolve_strand_count(strand_count: Union[int, str],
                          spec) -> Tuple[int, str]:
    """Resolve ``render_viz(strand_count=...)`` to ``(count, method)``.

    ``"auto"`` (the default) asks the survey-autopilot peer for an
    area-proportional count for the spec's bbox; an int passes through
    verbatim. ``method`` is ``"autopilot-area"`` / ``"explicit"`` /
    ``"default-fallback"`` (peer absent — the historical 3000).
    """
    if isinstance(strand_count, str):
        if strand_count.strip().lower() != "auto":
            raise ValueError(
                f"strand_count: expected 'auto' or a positive int, got "
                f"{strand_count!r}")
        ap = _autopilot()
        if ap is not None:
            try:
                res = ap.density.strand_count_for_bbox(
                    tuple(float(v) for v in spec.bbox))
                count = int(res["count"])
                if count > 0:
                    return count, "autopilot-area"
            except Exception:
                # Importable but misbehaving — fall back honestly.
                pass
        return _DEFAULT_STRAND_COUNT, "default-fallback"
    if isinstance(strand_count, bool) or not isinstance(strand_count, int):
        raise ValueError(
            f"strand_count: expected 'auto' or a positive int, got "
            f"{strand_count!r}")
    return strand_count, "explicit"


def _resolve_scale_limits(np, values, spec,
                          robust_scale: bool
                          ) -> Tuple[float, float, Dict[str, Any]]:
    """Reel-wide hue/scalar limits, honoring explicit spec.vmin/vmax.

    Returns ``(vmin, vmax, info)`` where ``info`` records the method
    for the manifest: ``"autopilot-p2-p98"`` (the survey-autopilot
    robust p2/p98 limits — one outlier cell no longer washes out the
    whole reel's colors), ``"minmax-fallback"`` (robust requested but
    the peer is absent — legacy min/max), or ``"minmax"`` (robust off,
    or both ends explicitly set so no data range is consulted at all).
    Explicit ``spec.vmin``/``spec.vmax`` always win for the end they
    set.
    """
    vmin_set = spec.vmin is not None
    vmax_set = spec.vmax is not None
    if vmin_set and vmax_set:
        return (float(spec.vmin), float(spec.vmax),
                {"method": "minmax", "percentiles": None})
    ap = _autopilot() if robust_scale else None
    used_autopilot = False
    if ap is not None:
        try:
            lim = ap.scaling.robust_limits(values, lo_pct=2.0, hi_pct=98.0)
            lo, hi = float(lim["vmin"]), float(lim["vmax"])
            used_autopilot = True
        except Exception:
            # Importable but misbehaving — fall back honestly.
            lo, hi = _nanminmax(np, values)
    else:
        lo, hi = _nanminmax(np, values)
    if vmin_set:
        lo = float(spec.vmin)
    if vmax_set:
        hi = float(spec.vmax)
    if used_autopilot:
        info = {"method": "autopilot-p2-p98", "percentiles": [2.0, 98.0]}
    elif robust_scale:
        info = {"method": "minmax-fallback", "percentiles": None}
    else:
        info = {"method": "minmax", "percentiles": None}
    return lo, hi, info


def _scale_manifest_keys(scale_info: Dict[str, Any]) -> Dict[str, Any]:
    """Manifest keys for the resolved scale method.

    ``scale_method`` is always recorded; ``scale_percentiles`` only
    when the autopilot robust limits were actually used.
    """
    keys = {"scale_method": scale_info["method"]}
    if scale_info["method"] == "autopilot-p2-p98":
        keys["scale_percentiles"] = list(scale_info["percentiles"])
    return keys


#: Presets whose renders carry a vector flow field (speed +
#: temperature grids) that salience ranking can use.
_SALIENCE_PRESETS = ("dark_flow", "dark_strands")


def _salience_pool(max_labels: int) -> int:
    """Gazetteer candidate pool size when salience ranking is active.

    A multiple of ``max_labels`` (floor 32, cap 256) so the salience
    map — not the gazetteer's population order — decides which labels
    survive the final cut. Bounded: a global bbox could otherwise
    fetch thousands of places.
    """
    return min(max(4 * max_labels, 32), 256)


def _time_mean(np, arr):
    """NaN-aware time-mean of a (T, ny, nx) stack -> (ny, nx).

    Salience ranking uses the time-mean, not any single timestep: a
    label sitting on the reel's TYPICAL hotspot outranks one that only
    flares in one frame. 2D input passes through unchanged.
    """
    a = np.asarray(arr, dtype=float)
    if a.ndim == 3:
        return np.nanmean(a, axis=0)
    return a


def _salience_speed_temp(spec, field):
    """Speed + temperature grids for salience ranking, or ``None``.

    Returns ``(speed, temp, lats, lons)`` — the full (T, ny, nx)
    stacks — when the flow field genuinely carries BOTH a speed array
    and a temperature array (currents with a temperature field; wind
    with the air_temperature scalar). ``None`` means "no honest
    salience signal": the caller keeps legacy label order rather than
    ranking on a speed-only field.
    """
    var = spec.variable
    if var == "currents":
        temp = (field.get("temperature")
                if isinstance(field, dict)
                else getattr(field, "temperature", None))
        if temp is None:
            # _extract_flow's scalar falls back to current speed here —
            # not a temperature field, so no honest salience signal.
            return None
        flow = _extract_flow(spec, field)
        return flow["speed"], flow["scalar"], flow["lats"], flow["lons"]
    if var == "wind":
        # The warming.watch convention colors wind strands by air
        # temperature — dark_strands already requires it on the field.
        temp = (field.get("air_temperature")
                if isinstance(field, dict)
                else getattr(field, "air_temperature", None))
        if temp is None:
            return None
        flow = _extract_flow(spec, field)
        np = _np()
        return (flow["speed"], np.asarray(temp, dtype=float),
                flow["lats"], flow["lons"])
    return None


# ---------------------------------------------------------------------------
# dark_flow: LIC streaks (currents / wind)
# ---------------------------------------------------------------------------

def _extract_flow(spec, field) -> Dict[str, Any]:
    """Return u/v/scalar grids + metadata for the dark_flow preset.

    * ``currents`` (CurrentField): u/v in m/s, scalar = water temperature
      converted to °F (the mapped.earth encoding). When the field carries
      no temperature, the scalar falls back to current speed.
    * ``wind`` (Era5Field): u10/v10, scalar = wind speed.
    """
    np = _np()
    var = spec.variable

    def _clean(arr):
        return np.ma.filled(np.ma.asanyarray(arr, dtype=float), np.nan)

    if var == "currents":
        u = _clean(field.u)
        v = _clean(field.v)
        temp = getattr(field, "temperature", None)
        if temp is not None:
            temp = _clean(temp)
            scalar = temp * 9.0 / 5.0 + 32.0  # °C -> °F, the reference unit
            scalar_label, scalar_unit = "water temperature", "°F"
        else:
            scalar = np.hypot(u, v)
            scalar_label, scalar_unit = "current speed", "m/s"
    elif var == "wind":
        grids = field.grids if not isinstance(field, dict) else field["grids"]
        u = _clean(grids["u10"])
        v = _clean(grids["v10"])
        scalar = np.hypot(u, v)
        scalar_label, scalar_unit = "wind speed", "m/s"
    else:  # pragma: no cover - guarded by the preset/variable check
        raise ValueError(
            f"preset 'dark_flow' needs vector (u, v) data; variable "
            f"{var!r} has none")

    if isinstance(field, dict):
        times = [_dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                 if not isinstance(t, (_dt.date, _dt.datetime)) else t
                 for t in field["times"]]
        lats = np.asarray(field["lats"], dtype=float)
        lons = np.asarray(field["lons"], dtype=float)
    else:
        times = list(field.times)
        lats = np.asarray(field.lats, dtype=float)
        lons = np.asarray(field.lons, dtype=float)

    speed = np.hypot(np.nan_to_num(u), np.nan_to_num(v))
    return {
        "u": u, "v": v, "scalar": scalar, "speed": speed,
        "times": times, "lats": lats, "lons": lons,
        "scalar_label": scalar_label, "scalar_unit": scalar_unit,
    }


def _render_dark_flow(ctx: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Render LIC streak frames. ``ctx`` carries the shared render context."""
    ae = ctx["ae"]
    np = _np()
    spec, field = ctx["spec"], ctx["field"]
    preset, cname = ctx["preset"], ctx["cmap"]
    angle = ctx["angle"]
    out = Path(ctx["out_dir"])
    out.mkdir(parents=True, exist_ok=True)

    flow = _extract_flow(spec, field)
    u, v, scalar, speed = (flow["u"], flow["v"], flow["scalar"], flow["speed"])
    moments = _as_datetimes(flow["times"])
    dates = [m.date() for m in moments]
    from .render import _bucket_indices
    frame_idx = _bucket_indices(dates, spec.cadence)

    # Reel-wide fixed scales (never per-frame — that flickers).
    # robust_scale (default): survey-autopilot p2/p98 limits when the
    # peer is importable, legacy min/max otherwise (see
    # _resolve_scale_limits). Explicit spec.vmin/vmax always win.
    vmin, vmax, scale_info = _resolve_scale_limits(
        np, scalar, spec, bool(ctx.get("robust_scale", True)))
    if not vmax > vmin:
        vmax = vmin + 1.0
    # Clean legend labels: data-derived scales are rounded to one
    # decimal (explicit spec.vmin/vmax pass through untouched).
    if spec.vmin is None:
        vmin = round(vmin, 1)
    if spec.vmax is None:
        vmax = round(vmax, 1)
    if not vmax > vmin:
        vmax = vmin + 1.0
    _, smax = _nanminmax(np, speed)
    # Robust: p99, not the raw max (see _robust_vmax) — one extreme
    # cell must not crush the LIC brightness range.
    speed_max = _robust_vmax(np, speed)

    _, _, coastline_segs, underlay_status = ctx["underlay_parts"]
    basemap_style = ctx["basemap_style"]
    subtitle = ctx["subtitle"]
    rw, rh = ctx["render_size"]
    beats = _normalize_headline_beats(
        ctx.get("headline_beats"), len(frame_idx),
        [moments[i] for i in frame_idx])
    ctx["headline_beats_normalized"] = beats
    # Furniture placed once per reel (static boxes; dial/timeline contents
    # vary per frame).
    ctx["furniture"] = furn = _Furniture(ctx)

    frames: List[str] = []
    timestamps: List[str] = []
    for n, i in enumerate(frame_idx):
        moment = moments[i]
        tex = ae.lic_texture(
            u[i], v[i], scalar[i],
            vmin=vmin, vmax=vmax, cmap=cname,
            seed=_LIC_SEED, speed_max=speed_max)

        def draw_map(ax):
            ax.imshow(tex, extent=[0, 1, 0, 1], origin="upper",
                      transform=ax.transAxes, aspect="auto", zorder=2)
            _draw_basemap_frac(ax, ae, coastline_segs, spec.bbox,
                               basemap_style)

        def draw_furniture(ax, _bt=_beat_text_for_frame(beats, n, spec.title)):
            furn.draw_title(ax, _bt)
            furn.draw_subtitle(ax)
            furn.draw_timeline(ax, moments[frame_idx[0]],
                               moments[frame_idx[-1]], moment)
            furn.draw_gradient_bar(ax, vmin, vmax, flow["scalar_label"],
                                   flow["scalar_unit"])
            furn.draw_north_arrow(ax)
            furn.draw_encoding(ax)
            ctx["draw_furniture_common"](ax)

        _write_frame(ctx, n, draw_map, draw_furniture)
        frames.append(str((out / f"frame_{n + 1:04d}.png").resolve()))
        timestamps.append(moment.isoformat())

    render_info = {
        "vmin": vmin, "vmax": vmax, "speed_max": speed_max,
        "scalar": f"{flow['scalar_label']} ({flow['scalar_unit']})",
        "underlay": underlay_status,
        **_scale_manifest_keys(scale_info),
    }
    return frames, timestamps, render_info


# ---------------------------------------------------------------------------
# dark_strands: advected particle trails (wind / currents)
# ---------------------------------------------------------------------------

def _lonlat_to_frac(lon: float, lat: float,
                    bbox: Tuple[float, float, float, float]
                    ) -> Tuple[float, float]:
    """Single lon/lat -> axes-fraction point (y down)."""
    lon0, lat0, lon1, lat1 = bbox
    fx = (lon - lon0) / (lon1 - lon0) if lon1 != lon0 else 0.5
    fy = 1.0 - (lat - lat0) / (lat1 - lat0) if lat1 != lat0 else 0.5
    return fx, fy


def _render_dark_strands(ctx: Dict[str, Any]
                         ) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Render advected strand frames (the warming.watch look).

    Per data timestep: seed ``strand_count`` particles (fixed seed —
    recombed, deterministic), spin the trails up on that timestep's
    field, and render hair-like strands colored by the scalar at each
    trail head. With ``bivariate=True`` (default) the strands are also
    brightness-encoded by wind/current speed on a reel-wide fixed
    scale. Reel-wide fixed vmin/vmax; the ocean mask (currents)
    or the Natural Earth land mask (wind) clips strands so geography
    emerges with no basemap drawn.
    """
    ae = ctx["ae"]
    np = _np()
    spec, field = ctx["spec"], ctx["field"]
    preset, cname = ctx["preset"], ctx["cmap"]
    angle = ctx["angle"]
    out = Path(ctx["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    strand_count = ctx["strand_count"]
    strand_lw = ctx["strand_linewidth"]

    advect, fields_mod = _flow()
    flow = _extract_flow(spec, field)
    var = spec.variable
    u, v, scalar = flow["u"], flow["v"], flow["scalar"]
    scalar_label, scalar_unit = flow["scalar_label"], flow["scalar_unit"]
    # The strand color: for currents _extract_flow's scalar is already
    # temperature; for wind the warming.watch convention colors the wind
    # strands by the 2 m air temperature, not by wind speed.
    scalar_name = "temperature" if var == "currents" else "air_temperature"
    if var == "wind":
        t2m = (field.get("air_temperature")
               if isinstance(field, dict)
               else getattr(field, "air_temperature", None))
        if t2m is None:
            raise ValueError(
                "preset 'dark_strands' on wind needs an 'air_temperature' "
                "scalar on the field (the strands are colored by it)")
        scalar = np.asarray(t2m, dtype=float)
        scalar_label, scalar_unit = "air temperature", (
            field.get("temperature_unit", "°F")
            if isinstance(field, dict)
            else getattr(field, "temperature_unit", "°F"))
    lats, lons = flow["lats"], flow["lons"]
    moments = _as_datetimes(flow["times"])
    dates = [m.date() for m in moments]
    from .render import _bucket_indices
    frame_idx = _bucket_indices(dates, spec.cadence)

    # Reel-wide fixed scales (never per-frame — that flickers).
    # robust_scale (default): survey-autopilot p2/p98 limits when the
    # peer is importable, legacy min/max otherwise (see
    # _resolve_scale_limits). Explicit spec.vmin/vmax always win.
    vmin, vmax, scale_info = _resolve_scale_limits(
        np, scalar, spec, bool(ctx.get("robust_scale", True)))
    if not vmax > vmin:
        vmax = vmin + 1.0
    if spec.vmin is None:
        vmin = round(vmin, 1)
    if spec.vmax is None:
        vmax = round(vmax, 1)
    if not vmax > vmin:
        vmax = vmin + 1.0

    # Bivariate strand encoding: strand COLOR stays the temperature
    # scalar (the warming.watch convention); strand BRIGHTNESS is the
    # wind/current speed on a REEL-WIDE fixed scale (never per-frame —
    # a per-frame normalization would flicker). _extract_flow already
    # carries speed = hypot(u, v) over the full (T, ny, nx) stack. The
    # scale top is the 99th percentile, not the raw max: a single
    # extreme cell (tropical cyclone, model gust front) must not crush
    # the whole reel's brightness into black. vmin is 0 (speed is
    # non-negative); values above p99 clip like a colorbar's top end.
    bivariate = bool(ctx.get("bivariate", True))
    speed_label = "wind speed" if var == "wind" else "current speed"
    speed_vmin = 0.0
    speed_vmax = _robust_vmax(np, flow["speed"])
    speed_vmax = round(float(speed_vmax), 2)
    if not speed_vmax > speed_vmin:
        speed_vmax = speed_vmin + 1.0

    # Strand clip masks (True keeps strands); row 0 = top for the
    # engine. Currents infer the ocean from NaNs; wind rasterizes the
    # Natural Earth land layer once per reel (the grid is frame-static)
    # so the continent emerges from the strands. A failed land fetch
    # falls back to unclipped strands — never a render failure.
    ocean_mask = None
    land_mask = None
    wind_mask = None
    landmask_requested = bool(ctx.get("landmask", True))
    landmask_status: Optional[str] = None
    landmask_prov: Dict[str, Any] = {}
    if spec.variable == "currents":
        water = ~(np.isnan(u[0]) | np.isnan(v[0]))
        if not np.any(water):
            raise ValueError(
                "preset 'dark_strands' on 'currents' found no ocean "
                "cells in the region — the strand mask is empty")
        ocean_mask = water[::-1, :]  # row 0 = top
        land_mask = (~water)[::-1, :]
    elif spec.variable == "wind" and landmask_requested:
        from .underlay import fetch_landmask
        lm = fetch_landmask(tuple(float(x) for x in spec.bbox),
                            lats, lons)
        landmask_status = lm["status"]
        landmask_prov = lm.get("provenance", {})
        if lm["status"] == "ok" and lm["mask"] is not None:
            wind_mask = np.asarray(lm["mask"], dtype=bool)
            land_mask = wind_mask  # subtle_land basemap can use it too

    _, _, coastline_segs, underlay_status = ctx["underlay_parts"]
    basemap_style = ctx["basemap_style"]
    subtitle = ctx["subtitle"]
    rw, rh = ctx["render_size"]
    bbox = tuple(float(x) for x in spec.bbox)
    if bivariate:
        ctx["encoding"] = (f"COLOR = {scalar_label.upper()}, "
                           f"BRIGHTNESS = {speed_label.upper()}")
    else:
        ctx["encoding"] = f"COLOR = {scalar_label.upper()}"
    beats = _normalize_headline_beats(
        ctx.get("headline_beats"), len(frame_idx),
        [moments[i] for i in frame_idx])
    ctx["headline_beats_normalized"] = beats

    # Furniture placed once per reel (static boxes; contents vary).
    ctx["furniture"] = furn = _Furniture(ctx)

    frames: List[str] = []
    timestamps: List[str] = []
    for n, i in enumerate(frame_idx):
        moment = moments[i]
        vf = fields_mod.VectorField(
            np.asarray(u[i], dtype=float),
            np.asarray(v[i], dtype=float),
            np.asarray(lats, dtype=float),
            np.asarray(lons, dtype=float),
            scalar=np.asarray(scalar[i], dtype=float),
            scalar_name=scalar_name,
        )
        cfg = advect.AdvectionConfig(
            dt_seconds=_STRAND_DT_SECONDS,
            trail_length=_STRAND_TRAIL_LENGTH,
            seed=_STRAND_SEED,
        )
        ps = advect.ParticleSet(strand_count, vf, cfg, seed=_STRAND_SEED)
        for _ in range(_STRAND_SPINUP_STEPS):
            ps.step(vf)
        segs, valid = ps.trail_segments()  # (n, L-1, 2, 2) lon/lat
        trails: List[Any] = []
        head_lons: List[float] = []
        head_lats: List[float] = []
        for s, ok_row in zip(segs, valid):
            # (L-1) segments -> one L-point polyline: first segment's
            # start, then each segment's end. Dead links become NaN
            # vertices, which the engine splits into sub-trails.
            pts = []
            for k, seg in enumerate(s):
                ok = bool(ok_row[k])
                if k == 0:
                    pts.append(_lonlat_to_frac(
                        float(seg[0][0]), float(seg[0][1]), bbox)
                        if ok else (float("nan"), float("nan")))
                pts.append(_lonlat_to_frac(
                    float(seg[1][0]), float(seg[1][1]), bbox)
                    if ok else (float("nan"), float("nan")))
            trails.append(np.array(pts, dtype=float))
            head_lons.append(float(s[-1][1][0]))
            head_lats.append(float(s[-1][1][1]))
        head_vals = vf.sample_scalar(np.asarray(head_lons),
                                     np.asarray(head_lats))
        # Bivariate channel: speed at the same trail heads, on the
        # reel-wide fixed scale -> per-trail (n,) brightness in [0, 1].
        # NaN samples (head off-grid) become gain 0, i.e. those strands
        # fade out. bivariate=False passes NO brightness kwarg at all:
        # the engine then renders the exact old flat look.
        strand_kwargs: Dict[str, Any] = {}
        if bivariate:
            vf_speed = fields_mod.VectorField(
                np.asarray(u[i], dtype=float),
                np.asarray(v[i], dtype=float),
                np.asarray(lats, dtype=float),
                np.asarray(lons, dtype=float),
                scalar=np.asarray(flow["speed"][i], dtype=float),
                scalar_name="speed",
            )
            speed_head = vf_speed.sample_scalar(np.asarray(head_lons),
                                                np.asarray(head_lats))
            gain = (speed_head - speed_vmin) / (speed_vmax - speed_vmin)
            gain = np.where(np.isfinite(gain), gain, 0.0)
            strand_kwargs["brightness"] = np.clip(gain, 0.0, 1.0)
        rgba = ae.render_strands(
            trails, head_vals,
            vmin=vmin, vmax=vmax, cmap=cname,
            width_px=1080, height_px=1920,
            linewidth=strand_lw,
            mask=ocean_mask if ocean_mask is not None else wind_mask,
            mask_feather=3.0,
            **strand_kwargs,
        )

        def draw_map(ax, _rgba=rgba):
            ax.imshow(_rgba, extent=[0, 1, 0, 1], origin="upper",
                      transform=ax.transAxes, aspect="auto", zorder=2)
            _draw_basemap_frac(ax, ae, coastline_segs, spec.bbox,
                               basemap_style, land_mask=land_mask)

        def draw_furniture(ax, _moment=moment,
                           _bt=_beat_text_for_frame(beats, n, spec.title)):
            furn.draw_title(ax, _bt)
            furn.draw_subtitle(ax)
            furn.draw_timeline(ax, moments[frame_idx[0]],
                               moments[frame_idx[-1]], _moment)
            furn.draw_gradient_bar(ax, vmin, vmax, scalar_label,
                                   scalar_unit)
            if bivariate:
                # The bar shows the color channel (temperature); this
                # line names the brightness channel and its reel-wide
                # fixed m/s scale so the second encoding is inspectable.
                furn.draw_brightness_note(
                    ax, f"BRIGHTNESS = {speed_label.upper()} "
                        f"({speed_vmin:g}\u2013{speed_vmax:g} M/S)")
            furn.draw_north_arrow(ax)
            furn.draw_encoding(ax)
            ctx["draw_furniture_common"](ax)

        _write_frame(ctx, n, draw_map, draw_furniture)
        frames.append(str((out / f"frame_{n + 1:04d}.png").resolve()))
        timestamps.append(moment.isoformat())

    if ocean_mask is not None:
        mask_label: Optional[str] = "ocean"
    elif wind_mask is not None:
        mask_label = "land"
    elif (spec.variable == "wind" and landmask_requested
          and landmask_status == "unavailable"):
        mask_label = "land (unavailable — unclipped)"
    else:
        mask_label = None
    render_info = {
        "vmin": vmin, "vmax": vmax,
        "scalar": f"{scalar_label} ({scalar_unit})",
        "scalar_name": scalar_name,
        "bivariate": bivariate,
        **_scale_manifest_keys(scale_info),
        # Reel-wide fixed speed scale for the brightness channel (m/s):
        # 99th percentile, not the raw max (see _robust_vmax).
        "speed_vmin": speed_vmin,
        "speed_vmax": speed_vmax,
        "speed_vmax_percentile": 99.0,
        "strand_count": strand_count,
        "strand_linewidth": strand_lw,
        "strand_seed": _STRAND_SEED,
        "mask": mask_label,
        "landmask": {
            "requested": landmask_requested,
            "status": landmask_status,  # "ok" | "unavailable" | None
            "scale": landmask_prov.get("scale"),
            "n_polygons": landmask_prov.get("n_polygons"),
        },
        "underlay": underlay_status,
    }
    return frames, timestamps, render_info


# ---------------------------------------------------------------------------
# dark_glow: additive event glow (earthquakes / storm-tracks)
# ---------------------------------------------------------------------------

def _aware_utc(moment: _dt.datetime) -> _dt.datetime:
    """Coerce a datetime to offset-aware UTC (naive -> assumed UTC)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc)


def _extract_events(spec, field) -> Dict[str, Any]:
    """Return per-event (moment, lon, lat, value) lists for dark_glow.

    * ``earthquakes``: cumulative epicenters; value = magnitude (linear —
      brightness scales directly with magnitude, stated in the encoding
      line). Events without a magnitude get the catalog minimum (never
      invented).
    * ``storm-tracks``: cumulative track fixes; value = max sustained
      wind in kt (NaN winds are skipped — never guessed).
    """
    np = _np()
    var = spec.variable
    if var == "earthquakes":
        from .render import _normalize_quake_field
        events, _ = _normalize_quake_field(field)
        mags = [e["magnitude"] for e in events
                if e["magnitude"] is not None]
        m_min = min(mags) if mags else 0.0
        moments, lons, lats, values = [], [], [], []
        for e in events:
            if e["lat"] != e["lat"] or e["lon"] != e["lon"]:
                continue  # no location: never plotted
            m = e["magnitude"]
            moments.append(e["time"])
            lons.append(e["lon"])
            lats.append(e["lat"])
            values.append(m if m is not None else m_min)
        count_label = "earthquakes"
        value_label = "magnitude"
    elif var == "storm-tracks":
        from .render import _normalize_storm_field, _storm_time
        tracks = _normalize_storm_field(field)
        moments, lons, lats, values = [], [], [], []
        for tr in tracks:
            for t, la, lo, w in zip(tr["times"], tr["lats"],
                                    tr["lons"], tr["winds"]):
                if la != la or lo != lo:
                    continue
                if w != w:  # NaN wind: no value to encode
                    continue
                moments.append(t)
                lons.append(lo)
                lats.append(la)
                values.append(float(w))
        count_label = "storms"
        value_label = "max sustained wind (kt)"
    else:  # pragma: no cover - guarded by the preset/variable check
        raise ValueError(
            f"preset 'dark_glow' needs event data; variable {var!r} has none")
    order = sorted(range(len(moments)), key=lambda k: moments[k])
    return {
        "moments": [_aware_utc(moments[k]) for k in order],
        "lons": np.array([lons[k] for k in order]),
        "lats": np.array([lats[k] for k in order]),
        "values": np.array([values[k] for k in order]),
        "count_label": count_label,
        "value_label": value_label,
    }


def _splat_disks(np, xs, ys, values, shape, radius_px: float = 9.0) -> Any:
    """Splat sparse events as uniform disks onto a grid (for glow_from_grid).

    The engine's blur attenuates single-pixel spikes to invisibility, so
    sparse events (quake epicenters, storm fixes) are splatted as small
    disks first: after the engine's gaussian passes, each disk reads as
    one soft glow dot whose peak brightness scales with its value.
    Overlapping disks sum linearly (honest accumulation).
    """
    h, w = int(shape[0]), int(shape[1])
    grid = np.zeros((h, w), dtype=np.float64)
    xs = np.asarray(xs, dtype=float).ravel()
    ys = np.asarray(ys, dtype=float).ravel()
    values = np.asarray(values, dtype=float).ravel()
    if xs.size == 0:
        return grid
    r = int(max(1, round(radius_px)))
    r2 = float(radius_px) ** 2
    for x, y, val in zip(xs, ys, values):
        if not (val > 0):
            continue
        ix, iy = int(round(x)), int(round(y))
        x0, x1 = max(0, ix - r), min(w, ix + r + 1)
        y0, y1 = max(0, iy - r), min(h, iy + r + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        mask = (xx - x) ** 2 + (yy - y) ** 2 <= r2
        grid[y0:y1, x0:x1][mask] += val
    return grid


def _render_dark_glow(ctx: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Render cumulative event-glow frames.

    Events are splatted as disks and rendered with the engine's
    ``glow_from_grid`` (see :func:`_splat_disks` for why). Brightness
    encodes magnitude (quakes) or max sustained wind (storms) — linear,
    documented in the encoding line.
    """
    ae = ctx["ae"]
    np = _np()
    spec = ctx["spec"]
    preset, cname = ctx["preset"], ctx["cmap"]
    angle = ctx["angle"]
    out = Path(ctx["out_dir"])
    out.mkdir(parents=True, exist_ok=True)

    ev = _extract_events(spec, ctx["field"])
    if spec.variable == "earthquakes":
        from .render import _quake_frame_dates
        frame_dates = _quake_frame_dates(spec)
    else:
        from .render import _storm_frame_dates
        frame_dates = _storm_frame_dates(spec)
    frame_moments = [_dt.datetime(d.year, d.month, d.day,
                                  tzinfo=_dt.timezone.utc)
                     for d in frame_dates]

    # Reel-wide fixed value scale (brightness never flickers).
    vmax = float(np.max(ev["values"])) if ev["values"].size else 1.0
    if not vmax > 0:
        vmax = 1.0
    vmin = 0.0
    ctx["encoding"] = f"BRIGHTNESS = {ev['value_label'].upper()}"

    _, _, coastline_segs, underlay_status = ctx["underlay_parts"]
    basemap_style = ctx["basemap_style"]
    rw, rh = ctx["render_size"]
    lon0, lat0, lon1, lat1 = (float(v) for v in spec.bbox)
    beats = _normalize_headline_beats(
        ctx.get("headline_beats"), len(frame_moments), frame_moments)
    ctx["headline_beats_normalized"] = beats
    # Furniture placed once per reel (static boxes; dial/counter contents
    # vary per frame).
    ctx["furniture"] = furn = _Furniture(ctx)

    # Event lon/lat -> pixel coordinates on the (possibly enlarged) map canvas.
    fx_all = (ev["lons"] - lon0) / (lon1 - lon0)
    fy_all = (ev["lats"] - lat0) / (lat1 - lat0)
    xs_all = fx_all * rw
    ys_all = (1.0 - fy_all) * rh

    frames: List[str] = []
    timestamps: List[str] = []
    for n, now in enumerate(frame_moments):
        # Cumulative display: every event with time <= frame date.
        shown = np.fromiter((m <= now for m in ev["moments"]),
                            dtype=bool, count=len(ev["moments"]))
        grid = _splat_disks(np, xs_all[shown], ys_all[shown],
                            ev["values"][shown], (rh, rw))
        glow = ae.glow_from_grid(
            grid, vmin=vmin, vmax=vmax, cmap=cname)

        def draw_map(ax, _glow=glow):
            ax.imshow(_glow, extent=[0, 1, 0, 1], origin="upper",
                      transform=ax.transAxes, aspect="auto", zorder=2)
            _draw_basemap_frac(ax, ae, coastline_segs, spec.bbox,
                               basemap_style)

        count = int(shown.sum())

        def draw_furniture(ax, _count=count, _now=now,
                           _bt=_beat_text_for_frame(beats, n, spec.title)):
            furn.draw_title(ax, _bt)
            furn.draw_subtitle(ax)
            furn.draw_counter(ax, _count, ev["count_label"])
            furn.draw_dial(ax, _now)
            furn.draw_north_arrow(ax)
            furn.draw_encoding(ax)
            ctx["draw_furniture_common"](ax)

        _write_frame(ctx, n, draw_map, draw_furniture)
        frames.append(str((out / f"frame_{n + 1:04d}.png").resolve()))
        timestamps.append(now.isoformat())

    render_info = {
        "vmin": vmin, "vmax": vmax,
        "encoding": (f"cumulative {ev['count_label']}; brightness = "
                     f"{ev['value_label']}"),
        "underlay": underlay_status,
    }
    return frames, timestamps, render_info


# ---------------------------------------------------------------------------
# paper_prism: 3D prism extrusion (any continuous gridded variable)
# ---------------------------------------------------------------------------

def _downsample_grid(np, grid: Any, max_ny: int, max_nx: int) -> Any:
    """NaN-aware block-mean downsampling to at most (max_ny, max_nx)."""
    grid = np.asarray(grid, dtype=float)
    ny, nx = grid.shape
    fy = max(1, int(np.ceil(ny / max_ny)))
    fx = max(1, int(np.ceil(nx / max_nx)))
    if fy == 1 and fx == 1:
        return grid
    ny2, nx2 = (ny // fy) * fy, (nx // fx) * fx
    trimmed = grid[:ny2, :nx2]
    blocks = trimmed.reshape(ny2 // fy, fy, nx2 // fx, fx)
    with np.errstate(invalid="ignore"):
        means = np.nanmean(blocks, axis=(1, 3))
    # Cells whose whole block was NaN stay NaN (never filled).
    all_nan = np.all(np.isnan(blocks), axis=(1, 3))
    means[all_nan] = np.nan
    return means


def _render_paper_prism(ctx: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Render prism-extrusion frames (height = value)."""
    ae = ctx["ae"]
    np = _np()
    spec = ctx["spec"]
    preset, cname = ctx["preset"], ctx["cmap"]
    angle = ctx["angle"]
    out = Path(ctx["out_dir"])
    out.mkdir(parents=True, exist_ok=True)

    from .render import _bucket_indices, _normalize_field
    times, lats, lons, values = _normalize_field(ctx["field"])
    moments = _as_datetimes(times)
    dates = [m.date() for m in moments]
    frame_idx = _bucket_indices(dates, spec.cadence)

    # Reel-wide fixed range. Precipitation is naturally zero-based;
    # other variables keep their data floor (never silently clipped).
    dmin, dmax = _nanminmax(np, values)
    if spec.vmin is not None and spec.vmax is not None:
        vmin, vmax = float(spec.vmin), float(spec.vmax)
    else:
        vmin = float(spec.vmin) if spec.vmin is not None else min(0.0, dmin)
        vmax = float(spec.vmax) if spec.vmax is not None else dmax
    if not vmax > vmin:
        vmax = vmin + 1.0
    # Clean legend labels for data-derived scales (explicit spec
    # values pass through untouched).
    if spec.vmin is None:
        vmin = round(vmin, 1)
    if spec.vmax is None:
        vmax = round(vmax, 1)
    if not vmax > vmin:
        vmax = vmin + 1.0

    small = _downsample_grid(np, values[0], _PRISM_MAX_NY, _PRISM_MAX_NX)
    grid_ny, grid_nx = small.shape

    lay = preset.legend_layout
    rw, rh = ctx["render_size"]
    unit = ctx.get("unit") or ""
    var_label = _variable_label(spec.variable)
    beats = _normalize_headline_beats(
        ctx.get("headline_beats"), len(frame_idx),
        [moments[i] for i in frame_idx])
    ctx["headline_beats_normalized"] = beats
    # Furniture placed once per reel (static boxes; dial contents vary).
    ctx["furniture"] = furn = _Furniture(ctx)

    frames: List[str] = []
    timestamps: List[str] = []
    for n, i in enumerate(frame_idx):
        moment = moments[i]
        grid = _downsample_grid(np, values[i], _PRISM_MAX_NY, _PRISM_MAX_NX)
        # A smaller footprint than the engine default leaves paper margins
        # for the title block and the vertical scale bar (the default
        # 0.94 footprint runs the prisms under both).
        rgba = ae.prism_frame(grid, vmin=vmin, vmax=vmax, cmap=cname,
                             width_px=rw, height_px=rh,
                             footprint_frac=0.74, max_height_frac=0.30)

        def draw_map(ax, _rgba=rgba):
            ax.imshow(_rgba, extent=[0, 1, 0, 1], origin="upper",
                      transform=ax.transAxes, aspect="auto", zorder=1)

        def draw_furniture(ax, _moment=moment,
                           _bt=_beat_text_for_frame(beats, n, spec.title)):
            furn.draw_title(ax, _bt)
            furn.draw_subtitle(ax)
            furn.draw_scale_bar(ax, vmin, vmax, var_label, unit)
            furn.draw_dial(ax, _moment)
            furn.draw_north_arrow(ax)
            furn.draw_encoding(ax)
            ctx["draw_furniture_common"](ax)

        _write_frame(ctx, n, draw_map, draw_furniture)
        frames.append(str((out / f"frame_{n + 1:04d}.png").resolve()))
        timestamps.append(moment.isoformat())

    render_info = {
        "vmin": vmin, "vmax": vmax,
        "prism_grid": [grid_ny, grid_nx],
        "underlay": ctx["underlay_parts"][3],
    }
    return frames, timestamps, render_info


# ---------------------------------------------------------------------------
# surface: colored scalar surface over shadowed land (any gridded variable)
# ---------------------------------------------------------------------------

def _render_surface(ctx: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Render surface frames via ``aesthetics.render_surface``.

    Each frame's grid is oriented north-up (row 0 = top) and rendered
    through the peer at the reel-wide FIXED vmin/vmax (never per-frame
    — that flickers). Frames follow the existing preset mechanism:
    ``_bucket_indices`` picks one timestep per cadence bucket, so a
    frame is an observed timestep grid, never an invented intermediate
    grid; only the *counter* interpolates (in value space; for log
    scale that interpolation is still on the stat, which is computed
    from the raw values — see ``_counter_stats``). When the peer's
    ``render_surface`` is unavailable the grid falls back to a plain
    colormapped rendering (no contours, no shadow) and the manifest
    records ``peer_fallback: true``.
    """
    ae = ctx["ae"]
    np = _np()
    spec = ctx["spec"]
    cname = ctx["cmap"]
    out = Path(ctx["out_dir"])
    out.mkdir(parents=True, exist_ok=True)

    from .render import _bucket_indices, _normalize_field, _VARIABLE_UNITS
    try:
        from .render import _field_units as _fu
        field_unit = _fu(ctx["field"])
    except Exception:
        field_unit = ""
    times, lats, lons, values = _normalize_field(ctx["field"])
    values = np.asarray(values, dtype=float)
    moments = _as_datetimes(times)
    dates = [m.date() for m in moments]
    frame_idx = _bucket_indices(dates, spec.cadence)
    frame_moments = [moments[i] for i in frame_idx]

    scale = ctx.get("surface_scale", "linear")
    # Reel-wide fixed scale (never per-frame). Explicit spec ends win.
    if scale == "log":
        finite = values[np.isfinite(values)]
        positive = finite[finite > 0]
        data_lo = float(np.min(positive)) if positive.size else 1.0
        data_hi = float(np.max(positive)) if positive.size else 10.0
    else:
        data_lo, data_hi = _nanminmax(np, values)
    vmin = float(spec.vmin) if spec.vmin is not None else float(data_lo)
    vmax = float(spec.vmax) if spec.vmax is not None else float(data_hi)
    if scale == "log" and vmin <= 0:
        raise ValueError(
            f"surface_scale='log' needs a positive vmin, got {vmin!r} "
            f"(from spec.vmin or the positive data minimum)")
    if not vmax > vmin:
        vmax = vmin * 10.0 if scale == "log" else vmin + 1.0

    var_label = _variable_label(spec.variable)
    unit = field_unit or _VARIABLE_UNITS.get(spec.variable, "")
    ctx["encoding"] = f"COLOR = {var_label.upper()}"

    # Land mask (optional): Natural Earth raster, row 0 = north, from
    # the same fetch dark_strands uses. Unavailable -> None (the
    # peer then shadows the finite-cell footprint instead).
    land_mask = None
    landmask_status: Optional[str] = None
    landmask_requested = bool(ctx.get("landmask", True))
    if landmask_requested:
        try:
            from .underlay import fetch_landmask
            lm = fetch_landmask(tuple(float(x) for x in spec.bbox),
                                lats, lons)
            landmask_status = lm.get("status")
            if lm.get("status") == "ok" and lm.get("mask") is not None:
                cand = np.asarray(lm["mask"], dtype=bool)
                if cand.shape == values.shape[1:]:
                    land_mask = cand
        except Exception:
            landmask_status = "unavailable"
            land_mask = None

    surface_fn = _render_surface_fn(ae)
    peer_fallback = surface_fn is None

    # Counter stats per timestep (NaN-aware), computed once per reel.
    counter_spec = ctx.get("counter")
    counter_applied = counter_spec is not None
    stat_series: List[float] = []
    if counter_applied:
        stat_series = _counter_stats(np, values, counter_spec["stat"])

    beats = _normalize_headline_beats(
        ctx.get("headline_beats"), len(frame_idx), frame_moments)
    ctx["headline_beats_normalized"] = beats

    _, _, coastline_segs, underlay_status = ctx["underlay_parts"]
    basemap_style = ctx["basemap_style"]
    rw, rh = ctx["render_size"]
    ctx["furniture"] = furn = _Furniture(ctx)

    contour_levels = ctx.get("surface_contour_levels")
    frames: List[str] = []
    timestamps: List[str] = []
    for n, i in enumerate(frame_idx):
        moment = moments[i]
        grid = _orient_north_up(np, values[i], lats)
        if not peer_fallback:
            rgba = surface_fn(
                grid, width_px=rw, height_px=rh, cmap=cname,
                vmin=vmin, vmax=vmax, scale=scale,
                smoothing=float(ctx.get("surface_smoothing", 0.0)),
                contours=bool(ctx.get("surface_contours", True)),
                contour_levels=contour_levels,
                shadow=bool(ctx.get("surface_shadow", True)),
                land_mask=land_mask)
        else:
            rgba = _fallback_surface_rgba(
                np, grid, cname, vmin, vmax, scale, rw, rh)

        def draw_map(ax, _rgba=rgba):
            ax.imshow(_rgba, extent=[0, 1, 0, 1], origin="upper",
                      transform=ax.transAxes, aspect="auto", zorder=2)
            _draw_basemap_frac(ax, ae, coastline_segs, spec.bbox,
                               basemap_style, land_mask=land_mask)

        # Counter: interpolated stat at this frame's time (an estimate
        # between timesteps, not a measurement).
        _cval = None
        _ctext = ""
        if counter_applied:
            _cval = _interpolate_counter_stat(stat_series, moments, moment)
            _formatted = _format_counter_value(_cval)
            _unit = counter_spec.get("unit") or ""
            _ctext = f"{_formatted} {_unit}".strip()
        _readout = _readout_label(moment)
        _label = (counter_spec.get("label") if counter_applied else "") or ""
        # Display label beneath the stat: explicit label, else the stat
        # name; the unit rides on the stat line itself.
        _sublabel = _label or (counter_spec["stat"].upper()
                               if counter_applied else "")
        _beat_text = _beat_text_for_frame(beats, n, spec.title)

        def draw_furniture(ax, _moment=moment, _ctext=_ctext,
                           _readout=_readout, _sublabel=_sublabel,
                           _beat_text=_beat_text,
                           _has_counter=counter_applied):
            furn.draw_title(ax, _beat_text)
            furn.draw_subtitle(ax)
            furn.draw_timeline(ax, frame_moments[0], frame_moments[-1],
                               _moment)
            furn.draw_surface_gradient_bar(ax, vmin, vmax, var_label,
                                           unit, scale=scale)
            if _has_counter:
                furn.draw_surface_counter(ax, _readout, _ctext,
                                          label=_sublabel)
            furn.draw_north_arrow(ax)
            furn.draw_encoding(ax)
            ctx["draw_furniture_common"](ax)

        _write_frame(ctx, n, draw_map, draw_furniture)
        frames.append(str((out / f"frame_{n + 1:04d}.png").resolve()))
        timestamps.append(moment.isoformat())

    finite_stats = [s for s in stat_series if s == s]
    if counter_applied:
        counter_manifest: Dict[str, Any] = {
            "spec": counter_spec,
            "applied": True,
            "stat_first": (float(finite_stats[0]) if finite_stats else None),
            "stat_last": (float(finite_stats[-1]) if finite_stats else None),
            "series": [(float(s) if s == s else None) for s in stat_series],
            "note": ("between observed timesteps the counter shows "
                     "linearly interpolated estimates, not measurements"),
        }
    else:
        counter_manifest = {
            "spec": None, "applied": False,
            "stat_first": None, "stat_last": None, "series": [],
        }
    render_info = {
        "vmin": vmin, "vmax": vmax,
        "scalar": f"{var_label} ({unit})" if unit else var_label,
        "surface": {
            "cmap": cname,
            "scale": scale,
            "vmin": vmin, "vmax": vmax,
            "contours": bool(ctx.get("surface_contours", True)),
            "contour_levels": (
                list(contour_levels) if contour_levels is not None
                else "auto"),
            "shadow": bool(ctx.get("surface_shadow", True)),
            "smoothing": float(ctx.get("surface_smoothing", 0.0)),
            "peer_fallback": bool(peer_fallback),
            "land_mask": bool(land_mask is not None),
        },
        "landmask": {
            "requested": landmask_requested,
            "status": landmask_status,
        },
        "counter": counter_manifest,
        "headline_beats": beats,
        "underlay": underlay_status,
    }
    return frames, timestamps, render_info


# ---------------------------------------------------------------------------
# Frame writer (rotation-aware) + main entry point
# ---------------------------------------------------------------------------

def _write_frame(ctx: Dict[str, Any], n: int,
                 draw_map, draw_furniture) -> None:
    """Render one frame: north-up map, optional rotation, then furniture.

    The engine rule — furniture AFTER rotation — is enforced here:
    titles, legends, and the watermark are drawn on the final
    1080x1920 canvas, never on the pre-rotation map canvas.
    """
    ae = ctx["ae"]
    preset = ctx["preset"]
    angle = ctx["angle"]
    out = Path(ctx["out_dir"])
    rw, rh = ctx["render_size"]

    fig, ax = ae.new_canvas(rw, rh, background=preset.bg_rgb)
    try:
        draw_map(ax)
        if not angle:
            draw_furniture(ax)
            fig.savefig(out / f"frame_{n + 1:04d}.png",
                        facecolor=fig.get_facecolor())
            return
        map_rgba = ae.fig_to_rgba(fig)
    finally:
        ae.close(fig)
    final = ae.rotate_frame_fill(map_rgba, angle, (_CANVAS_W, _CANVAS_H))
    fig2, ax2 = ae.new_canvas(_CANVAS_W, _CANVAS_H,
                              background=preset.bg_rgb)
    try:
        ax2.imshow(final, extent=[0, 1, 0, 1], origin="upper",
                   transform=ax2.transAxes, aspect="auto", zorder=1)
        draw_furniture(ax2)
        fig2.savefig(out / f"frame_{n + 1:04d}.png",
                     facecolor=fig2.get_facecolor())
    finally:
        ae.close(fig2)


def render_preset_viz(
    spec,
    field,
    out_dir: str = "frames",
    *,
    preset: str,
    rotation: Any = None,
    watermark: Optional[str] = None,
    subtitle: Optional[str] = None,
    encoding_line: bool = True,
    underlay: bool = True,
    cmap: Optional[str] = None,
    style: Optional[str] = None,
    place_labels: Any = True,
    max_labels: int = 8,
    min_population: int = 0,
    basemap: Optional[str] = None,
    strand_count: Union[int, str] = "auto",
    strand_linewidth: float = _DEFAULT_STRAND_LINEWIDTH,
    landmask: bool = True,
    bivariate: bool = True,
    robust_scale: bool = True,
    salience_labels: bool = True,
    surface_contours: bool = True,
    surface_contour_levels: Any = None,
    surface_shadow: bool = True,
    surface_smoothing: float = 0.0,
    surface_scale: str = "linear",
    counter: Any = None,
    headline_beats: Any = None,
    # Set by render_viz when it pre-resolves strand_count="auto" to an
    # int (see _resolve_strand_count); direct callers leave it None and
    # the resolution happens here. Internal plumbing, not a user knob.
    strand_count_method: Optional[str] = None,
) -> Tuple[List[str], str]:
    """Render a VizSpec through a survey-aesthetics preset.

    Args:
        spec: a :class:`viz.spec.VizSpec`.
        field: the fetched field (same duck-typed shapes :func:`viz.render.render_viz`
            accepts).
        out_dir: where PNG frames + ``manifest.json`` are written.
        preset: one of :data:`AESTHETIC_PRESETS`.
        rotation: ``None`` (north-up), ``"auto"`` (optimal rotation for
            the region bbox), or degrees counter-clockwise.
        watermark: brand handle burned into the furniture (``None`` =
            no watermark furniture at all — the default).
        subtitle: explicit editorial subtitle; ``None`` uses the auto
            time-window label (e.g. "16–23 September 2026").
        encoding_line: draw the preset's honesty line
            (e.g. "BRIGHTNESS = SPEED"). Default True.
        underlay: fetch coastlines for the map layer (default True;
            falls back to none when unavailable — never a crash).
        cmap: override the preset's curated colormap (validated; the
            preset default is kept when None).
        style: recorded in the manifest; the preset's background wins.
        place_labels: ``True`` (default) auto-fetches place labels for
            the reel's north-up bbox from the survey-gazetteer peer —
            ONCE per reel, reused across frames; ``False`` draws no
            labels (the peer is never imported); an explicit list of
            label dicts (``{"x": lon, "y": lat, "text": str,
            "priority": int}``) is used verbatim and wins over the
            automatic set. Labels are drawn with the rest of the
            furniture on the final canvas, upright, after rotation;
            final on-canvas decluttering stays in the engine's
            ``place_labels``.
        max_labels: cap for automatic labels (default 8).
        min_population: minimum place population for automatic labels
            (default 0).
        basemap: basemap style for the preset path — one of
            ``"void_black"`` (hairline coastlines on the black void),
            ``"no_basemap"`` (nothing drawn; geography emerges from the
            data mask), ``"subtle_land"`` (faint landmass under
            hairlines; needs a landmask, otherwise coastlines only).
            ``None`` (default) keeps the preset's bundled style.
            Unknown names fail fast.
        strand_count: particle count per frame for the ``dark_strands``
            preset — ``"auto"`` (default) asks the survey-autopilot
            peer for an area-proportional count for the region bbox
            (falls back to the old 3000 default when the peer is
            absent); a positive int is used verbatim. Preset-path only.
        strand_linewidth: strand width in points for ``dark_strands``
            (default 1.4; 1–2 reads hair-like at 1080x1920).
            Preset-path only.
        landmask: clip ``dark_strands`` wind strands to the Natural
            Earth land polygons (default True — the continent emerges
            from the strands, the warming.watch look). The mask is
            rasterized once per reel and cached in-process; when the
            land layer is unavailable the render falls back to
            unclipped strands (recorded in the manifest) rather than
            failing. ``currents`` always uses its NaN-inferred ocean
            mask. Pass ``False`` to disable clipping.
        bivariate: ``dark_strands`` strand encoding (default True):
            COLOR = temperature, BRIGHTNESS = wind/current speed on a
            reel-wide fixed m/s scale (recorded in the manifest as
            ``speed_vmin``/``speed_vmax``). ``False`` restores the
            single-variable flat look (no brightness channel, old
            encoding line). Preset-path only.
        robust_scale: ``dark_flow``/``dark_strands`` hue (temperature)
            color limits (default True): the survey-autopilot robust
            p2/p98 limits instead of the raw data min/max — a single
            outlier cell no longer washes out the whole reel's colors.
            Explicit ``spec.vmin``/``spec.vmax`` always win. When the
            peer is absent, or with ``robust_scale=False``, the legacy
            min/max scale is used. The manifest records
            ``scale_method`` (``"autopilot-p2-p98"`` /
            ``"minmax-fallback"`` / ``"minmax"``) plus
            ``scale_percentiles`` when the peer was used. The
            bivariate p99 brightness channel is untouched — this only
            affects the hue scale. Preset-path only.
        salience_labels: automatic place labels on the flow presets
            (default True): when the flow field carries speed AND
            temperature grids, candidate labels are ranked by the
            survey-autopilot salience map — built from the TIME-MEAN
            speed/temperature grids, so the reel's typical hotspot
            outranks a single-frame flare — BEFORE the ``max_labels``
            cut, so salience decides which labels survive. When the
            peer is absent, or labels are explicit/off, the gazetteer
            order is kept verbatim. The manifest records
            ``place_labels.label_ranking`` (``"autopilot-salience"`` /
            ``"legacy"``). Preset-path only.
        surface_contours / surface_contour_levels / surface_shadow /
        surface_smoothing / surface_scale: ``surface`` preset only —
            contours (default True) at auto or explicit levels, drop
            shadow (default True), Gaussian smoothing sigma in output
            pixels (default 0.0), and ``"linear"``/``"log"`` scale
            (log is for density-like data spanning orders of
            magnitude; non-positive cells are no-data). Unknown scales
            raise ``ValueError``.
        counter: ``surface`` preset only — ``None`` (default) or
            ``{"stat": "sum"|"mean"|"max", "unit": str, "label": str}``.
            The stat is computed per timestep over finite cells only;
            the value shown at each frame is linearly interpolated
            between the bracketing timesteps' stats (clamped at the
            ends) — between observed timesteps it is an *estimate*,
            not a measurement. Passed with any other preset it is
            recorded in the manifest as requested-but-not-applied and
            no counter is drawn.
        headline_beats: ``None`` (default) or a list of ``(when, text)``
            pairs, where ``when`` is a float fraction in [0, 1] of the
            reel or an ISO-8601 timestamp string resolved against the
            field's frame times. The title swaps with a clean cut at
            each beat (the default title shows until the first beat;
            beats are sorted by frame and a later-listed beat wins a
            shared frame). Any preset. Plain data only — viz never
            imports survey-narrate; callers may draft beats with
            ``narrate.headline_beats`` and pass the list in.

    Returns ``(frames, manifest_path)`` like :func:`viz.render.render_viz`.
    """
    ae = _engine()
    if preset not in AESTHETIC_PRESETS:
        raise ValueError(
            f"unknown preset {preset!r}; choose from {list(AESTHETIC_PRESETS)}")
    allowed = PRESET_VARIABLES[preset]
    if allowed is not None and spec.variable not in allowed:
        raise ValueError(
            f"preset {preset!r} renders {', '.join(allowed)}; "
            f"variable {spec.variable!r} has no compatible data "
            f"(no vector field for dark_flow, no events for dark_glow)")
    # --- surface / counter / headline-beats validation (fail fast) ---
    if surface_scale not in ("linear", "log"):
        raise ValueError(
            f"surface_scale: expected 'linear' or 'log', got "
            f"{surface_scale!r}")
    try:
        surface_smoothing = float(surface_smoothing)
    except (TypeError, ValueError):
        raise ValueError(
            f"surface_smoothing: expected a non-negative number, got "
            f"{surface_smoothing!r}") from None
    if surface_smoothing < 0:
        raise ValueError(
            f"surface_smoothing: expected a non-negative number, got "
            f"{surface_smoothing!r}")
    if surface_contour_levels is not None:
        try:
            surface_contour_levels = [float(v) for v in
                                      surface_contour_levels]
        except (TypeError, ValueError):
            raise ValueError(
                f"surface_contour_levels: expected None or a sequence "
                f"of numbers, got {surface_contour_levels!r}") from None
    counter_spec = _validate_counter(counter)
    # Raw beats are validated here (text/fraction/timestamp syntax);
    # frame normalization happens in each renderer, which knows the
    # frame times. Stored as the caller's plain data.
    _validate_headline_beats(headline_beats)

    angle = _resolve_rotation(rotation, spec.bbox)
    preset_obj = (_surface_preset_obj(ae) if preset == "surface"
                  else ae.get_preset(preset))
    # The dark_strands preset's honesty line names air temperature (the
    # warming.watch wind look); on currents the strands show water
    # temperature — say so. With bivariate=True the strands are also
    # brightness-encoded by speed, which the line states too.
    encoding = preset_obj.encoding
    if preset == "dark_strands":
        speed_name = ("WIND SPEED" if spec.variable == "wind"
                      else "CURRENT SPEED")
        if spec.variable == "currents":
            encoding = "COLOR = WATER TEMPERATURE"
        elif spec.variable == "wind":
            encoding = "COLOR = AIR TEMPERATURE"
        if bivariate:
            encoding += f", BRIGHTNESS = {speed_name}"

    from .render import _fetch_underlay_once, _require_plotting, _validate_cmap
    plt, _, np = _require_plotting()
    cname = _validate_cmap(cmap, plt) or preset_obj.cmap
    underlay_parts = _fetch_underlay_once(spec, plt, np, bool(underlay))

    # Basemap style: explicit choice wins; otherwise the preset default.
    if basemap is None:
        basemap_style = preset_obj.basemap
        if basemap_style is None:
            try:
                basemap_style = ae.get_basemap("void_black")
            except Exception:
                basemap_style = None
        basemap_name = (basemap_style.name if basemap_style is not None
                        else "void_black")
    else:
        try:
            basemap_style = ae.get_basemap(basemap)
        except KeyError as exc:
            raise ValueError(
                f"unknown basemap {basemap!r}; choose from "
                f"{ae.list_basemaps()} or None for the preset default"
            ) from exc
        basemap_name = basemap_style.name
    if isinstance(strand_count, str):
        # render_viz resolves "auto" before dispatching; a direct
        # caller may still pass it — resolve here identically.
        strand_count, strand_count_method = _resolve_strand_count(
            strand_count, spec)
    elif strand_count_method is None:
        strand_count_method = "explicit"
    if (not isinstance(strand_count, int)
            or isinstance(strand_count, bool) or strand_count <= 0):
        raise ValueError(
            f"strand_count must be 'auto' or a positive int, got "
            f"{strand_count!r}")
    if not strand_linewidth > 0:
        raise ValueError(
            f"strand_linewidth must be positive, got {strand_linewidth!r}")

    if angle:
        render_size = ae.rotated_render_size(angle, (_CANVAS_W, _CANVAS_H))
    else:
        render_size = (_CANVAS_W, _CANVAS_H)

    subtitle_text = subtitle.strip() if isinstance(subtitle, str) and subtitle.strip() \
        else _window_subtitle(spec)

    # Place labels: resolved ONCE per reel from the north-up bbox, then
    # projected onto the final furniture-canvas fractions (upright after
    # rotation). Reel-static, so the same list rides every frame.
    #
    # Salience ranking (survey-autopilot, optional): on the flow
    # presets with automatic labels, fetch a candidate pool instead of
    # the final cut, then rank candidates by the salience map BEFORE
    # the max_labels cut — salience decides which labels survive.
    # Without the peer (or without speed+temperature grids, or with
    # explicit/off labels) the gazetteer order is kept verbatim.
    want_salience = (
        salience_labels and place_labels is True
        and preset in _SALIENCE_PRESETS
        and isinstance(max_labels, int) and not isinstance(max_labels, bool)
        and max_labels > 0 and _autopilot() is not None)
    label_dicts, label_mode = _resolve_place_labels(
        place_labels, spec, max_labels, min_population,
        candidate_pool=_salience_pool(max_labels) if want_salience else None)
    label_ranking = "legacy"
    if want_salience and label_mode == "auto" and label_dicts:
        try:
            salience_inputs = _salience_speed_temp(spec, field)
            ap = _autopilot()
            if ap is not None and salience_inputs is not None:
                # The salience map is built from the TIME-MEAN speed
                # and temperature grids (see _time_mean): the reel's
                # typical hotspot outranks a single-frame flare.
                speed, temp, slats, slons = salience_inputs
                sal_map = ap.salience.salience_map(
                    _time_mean(np, speed), _time_mean(np, temp))
                candidates = [{"name": str(lb["text"]),
                               "lon": float(lb["x"]),
                               "lat": float(lb["y"]),
                               "_orig": lb}
                              for lb in label_dicts]
                ranked = ap.salience.rank_label_positions(
                    candidates, sal_map,
                    np.asarray(slats, dtype=float),
                    np.asarray(slons, dtype=float))
                label_dicts = [r["_orig"] for r in ranked[:max_labels]]
                label_ranking = "autopilot-salience"
        except Exception:
            # The optional peer misbehaved — degrade to the gazetteer
            # order (the pool cut to max_labels) rather than failing a
            # render.
            label_dicts = label_dicts[:max_labels]
            label_ranking = "legacy"
    canvas_labels = _project_labels_to_final(
        label_dicts, spec.bbox, angle, render_size)

    # Shared furniture tail: watermark block (opt-in) + encoding honesty line.
    # Each preset renderer may override ctx["encoding"] (dark_glow does).
    data_source = _data_source_label(spec, field)

    def draw_furniture_common(ax):
        enc = ctx.get("encoding") or ""
        # The standalone honesty line is placed by the furniture layout
        # (ctx["furniture"].draw_encoding); only the watermark block keeps
        # its fixed corner position.
        if watermark:
            import datetime as _dtd
            ae.frame_furniture(
                ax, brand=watermark, holder=watermark,
                year=_dtd.date.today().year,
                data_source=data_source,
                encoding=enc if encoding_line else None,
                color=preset_obj.text_color)
        if ctx["place_canvas_labels"]:
            # Geographic labels, upright on the final canvas; they avoid
            # the placed furniture rects (dial, legends), then the
            # engine's greedy decluttering drops what cannot fit.
            furniture = ctx.get("furniture")
            obstacles = (furniture.obstacle_rects()
                         if furniture is not None else [])
            try:
                ae.place_labels(ax, ctx["place_canvas_labels"],
                                color=preset_obj.text_color,
                                dot_color=preset_obj.text_color,
                                obstacles=obstacles)
            except TypeError:
                # Older engine without the obstacles kwarg.
                ae.place_labels(ax, ctx["place_canvas_labels"],
                                color=preset_obj.text_color,
                                dot_color=preset_obj.text_color)

    ctx: Dict[str, Any] = {
        "ae": ae, "spec": spec, "field": field, "preset": preset_obj,
        "cmap": cname, "angle": angle, "out_dir": out_dir,
        "render_size": render_size, "subtitle": subtitle_text,
        "underlay_parts": underlay_parts,
        "draw_furniture_common": draw_furniture_common,
        "encoding": encoding,
        "encoding_line": encoding_line,
        "watermark": watermark,
        "basemap_style": basemap_style,
        "basemap_name": basemap_name,
        "strand_count": strand_count,
        "strand_linewidth": strand_linewidth,
        "landmask": landmask,
        "bivariate": bivariate,
        "robust_scale": robust_scale,
        "surface_contours": surface_contours,
        "surface_contour_levels": surface_contour_levels,
        "surface_shadow": surface_shadow,
        "surface_smoothing": surface_smoothing,
        "surface_scale": surface_scale,
        "counter": counter_spec,
        "counter_requested": counter,
        "headline_beats": headline_beats,
        "place_canvas_labels": canvas_labels,
    }

    if preset == "dark_flow":
        frames, timestamps, info = _render_dark_flow(ctx)
    elif preset == "dark_glow":
        frames, timestamps, info = _render_dark_glow(ctx)
    elif preset == "dark_strands":
        frames, timestamps, info = _render_dark_strands(ctx)
    elif preset == "surface":
        frames, timestamps, info = _render_surface(ctx)
    else:
        frames, timestamps, info = _render_paper_prism(ctx)

    from .render import SCHEMA_ID
    manifest = {
        "schema": SCHEMA_ID,
        "spec": spec.to_dict(),
        "frames": frames,
        "timestamps": timestamps,
        "render": {
            "layout": "reel-vertical",
            "style": style or spec.style,
            "preset": preset,
            "preset_cmap": preset_obj.cmap,
            "cmap": cname,
            "cmap_requested": cmap,
            "cmap_overridden": cmap is not None,
            "rotation_requested": rotation,
            "rotation_deg": angle,
            "watermark": watermark,
            "subtitle": subtitle_text,
            "encoding": encoding,
            "encoding_line": encoding_line,
            "basemap": basemap_name,
            "strand_count": strand_count,
            "strand_count_method": strand_count_method,
            "strand_linewidth": strand_linewidth,
            "bivariate": bivariate,
            "furniture": {
                kind: {"rect": list(p.rect), "placed": bool(p.placed),
                       "scale": float(p.scale)}
                for kind, p in (ctx["furniture"].placed
                                if "furniture" in ctx else {}).items()
            },
            "n_frames": len(frames),
            "place_labels": {
                "mode": label_mode,  # "auto" | "explicit" | "off"
                "label_ranking": label_ranking,  # "autopilot-salience" | "legacy"
                "max_labels": max_labels,
                "min_population": min_population,
                "kinds": list(_LABEL_KINDS),
                "gazetteer": _gazetteer_version(),
                "n_labels": len(label_dicts),
                # lon/lat label dicts (JSON-serializable): exactly what
                # the survey-cache frame-batch fingerprint hashes.
                "labels": label_dicts,
            },
            "engine": (f"survey-viz {_viz_version()} + "
                       f"survey-aesthetics {_aesthetics_version()}"),
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            # surface / counter / headline beats (v0.28.0). The
            # surface renderer overrides all three via ``info``; other
            # presets keep these honest defaults (counter requested on
            # a non-surface preset is recorded as not applied).
            "surface": (info.get("surface") if preset == "surface"
                        else None),
            "counter": (
                info.get("counter") or {
                    "spec": counter_spec,
                    "applied": False,
                    "stat_first": None,
                    "stat_last": None,
                    "note": ("requested but not applied — counter is "
                             "surface-preset only"
                             if counter_spec is not None else
                             "not requested"),
                }),
            "headline_beats": ctx.get("headline_beats_normalized", []),
            **info,
        },
    }
    manifest_path = Path(out_dir) / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return frames, str(manifest_path.resolve())
