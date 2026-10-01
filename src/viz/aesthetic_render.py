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
from typing import Any, Dict, List, Optional, Tuple

#: Preset names accepted by ``render_viz(preset=...)``.
AESTHETIC_PRESETS = ("dark_flow", "dark_glow", "paper_prism", "dark_strands")

#: Variables each preset can render. ``None`` means "any continuous
#: gridded variable" (the paper_prism case — height = value works for
#: any scalar field).
PRESET_VARIABLES: Dict[str, Optional[Tuple[str, ...]]] = {
    "dark_flow": ("currents", "wind"),
    "dark_glow": ("earthquakes", "storm-tracks"),
    "paper_prism": None,
    "dark_strands": ("currents", "wind"),
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
                          max_labels: int, min_population: int) -> Tuple[List[Dict[str, Any]], str]:
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
        max_labels=max_labels, min_population=min_population,
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
        if name in ("dark_flow", "dark_strands"):
            gx, gy, gw, gh = lay["gradient_bar"]
            specs.append(ae.box_spec(
                "gradient_bar", (gx, gy, gw, gh), priority=6,
                alternatives=[(0.60, 0.18, gw, gh),
                              (0.06, 0.08, 0.60, gh)]))
            lx, ly, lw, lh = lay["timeline"]
            specs.append(ae.box_spec(
                "timeline", (lx, ly, lw, lh), priority=6,
                alternatives=[(0.06, 0.05, lw, lh),
                              (0.62, 0.26, lw, lh)]))
        if name == "dark_glow":
            cx0, cy0 = lay["counter"][:2]
            specs.append(ae.box_spec(
                "counter", (cx0, cy0 - 0.05, 0.32, 0.115), priority=7,
                alternatives=[(0.62, 0.05, 0.32, 0.115)]))
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

    def draw_title(self, ax):
        ae = self._ctx["ae"]
        preset, spec = self._ctx["preset"], self._ctx["spec"]
        p = self.placed["title"]
        # Shrink-to-fit: the engine's placed scale, then the extra clamp
        # for titles longer than the engine's shrink floor can fit.
        scale = p.scale * _title_fit_scale(
            (p.rect[0], p.rect[1], p.rect[2] * p.scale, p.rect[3]))
        ae.draw_title(ax, spec.title, p.rect[0], p.rect[1] + p.rect[3],
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
        ae.counter(ax, r[0], r[1] + 0.05, value, label=label,
                   color=self._colors())

    def draw_gradient_bar(self, ax, vmin, vmax, label, unit):
        ae = self._ctx["ae"]
        r = self._rect("gradient_bar")
        if r is None:
            return
        ae.gradient_bar(ax, r, self._ctx["cmap"], vmin, vmax,
                        label=label, unit=unit, color=self._colors())

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
    if spec.vmin is not None and spec.vmax is not None:
        vmin, vmax = float(spec.vmin), float(spec.vmax)
    else:
        vmin, vmax = _nanminmax(np, scalar)
        if spec.vmin is not None:
            vmin = float(spec.vmin)
        if spec.vmax is not None:
            vmax = float(spec.vmax)
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
    speed_max = smax if smax > 0 else 1.0

    _, _, coastline_segs, underlay_status = ctx["underlay_parts"]
    basemap_style = ctx["basemap_style"]
    subtitle = ctx["subtitle"]
    rw, rh = ctx["render_size"]
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

        def draw_furniture(ax):
            furn.draw_title(ax)
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
    trail head. Reel-wide fixed vmin/vmax; the ocean mask (currents)
    clips strands so geography emerges with no basemap drawn.
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
    if spec.vmin is not None and spec.vmax is not None:
        vmin, vmax = float(spec.vmin), float(spec.vmax)
    else:
        vmin, vmax = _nanminmax(np, scalar)
        if spec.vmin is not None:
            vmin = float(spec.vmin)
        if spec.vmax is not None:
            vmax = float(spec.vmax)
    if not vmax > vmin:
        vmax = vmin + 1.0
    if spec.vmin is None:
        vmin = round(vmin, 1)
    if spec.vmax is None:
        vmax = round(vmax, 1)
    if not vmax > vmin:
        vmax = vmin + 1.0

    # Ocean mask for currents (True keeps strands); row 0 = top for the
    # engine. Wind has no raster landmask in the offline peer set, so it
    # renders unclipped (documented limit).
    ocean_mask = None
    land_mask = None
    if spec.variable == "currents":
        water = ~(np.isnan(u[0]) | np.isnan(v[0]))
        if not np.any(water):
            raise ValueError(
                "preset 'dark_strands' on 'currents' found no ocean "
                "cells in the region — the strand mask is empty")
        ocean_mask = water[::-1, :]  # row 0 = top
        land_mask = (~water)[::-1, :]

    _, _, coastline_segs, underlay_status = ctx["underlay_parts"]
    basemap_style = ctx["basemap_style"]
    subtitle = ctx["subtitle"]
    rw, rh = ctx["render_size"]
    bbox = tuple(float(x) for x in spec.bbox)
    ctx["encoding"] = f"COLOR = {scalar_label.upper()}"

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
        rgba = ae.render_strands(
            trails, head_vals,
            vmin=vmin, vmax=vmax, cmap=cname,
            width_px=1080, height_px=1920,
            linewidth=strand_lw,
            mask=ocean_mask, mask_feather=3.0,
        )

        def draw_map(ax, _rgba=rgba):
            ax.imshow(_rgba, extent=[0, 1, 0, 1], origin="upper",
                      transform=ax.transAxes, aspect="auto", zorder=2)
            _draw_basemap_frac(ax, ae, coastline_segs, spec.bbox,
                               basemap_style, land_mask=land_mask)

        def draw_furniture(ax, _moment=moment):
            furn.draw_title(ax)
            furn.draw_subtitle(ax)
            furn.draw_timeline(ax, moments[frame_idx[0]],
                               moments[frame_idx[-1]], _moment)
            furn.draw_gradient_bar(ax, vmin, vmax, scalar_label,
                                   scalar_unit)
            furn.draw_north_arrow(ax)
            furn.draw_encoding(ax)
            ctx["draw_furniture_common"](ax)

        _write_frame(ctx, n, draw_map, draw_furniture)
        frames.append(str((out / f"frame_{n + 1:04d}.png").resolve()))
        timestamps.append(moment.isoformat())

    render_info = {
        "vmin": vmin, "vmax": vmax,
        "scalar": f"{scalar_label} ({scalar_unit})",
        "scalar_name": scalar_name,
        "strand_count": strand_count,
        "strand_linewidth": strand_lw,
        "strand_seed": _STRAND_SEED,
        "mask": "ocean" if ocean_mask is not None else None,
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

        def draw_furniture(ax, _count=count, _now=now):
            furn.draw_title(ax)
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

        def draw_furniture(ax, _moment=moment):
            furn.draw_title(ax)
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
    strand_count: int = _DEFAULT_STRAND_COUNT,
    strand_linewidth: float = _DEFAULT_STRAND_LINEWIDTH,
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
            preset (default 3000; the engine's cost note: ~1.3 s/frame
            at 4000). Preset-path only.
        strand_linewidth: strand width in points for ``dark_strands``
            (default 1.4; 1–2 reads hair-like at 1080x1920).
            Preset-path only.

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

    angle = _resolve_rotation(rotation, spec.bbox)
    preset_obj = ae.get_preset(preset)
    # The dark_strands preset's honesty line names air temperature (the
    # warming.watch wind look); on currents the strands show water
    # temperature — say so.
    encoding = preset_obj.encoding
    if preset == "dark_strands":
        if spec.variable == "currents":
            encoding = "COLOR = WATER TEMPERATURE"
        elif spec.variable == "wind":
            encoding = "COLOR = AIR TEMPERATURE"

    from .render import _fetch_underlay_once, _require_plotting, _validate_cmap
    plt, _, np = _require_plotting()
    cname = _validate_cmap(cmap, plt) or preset_obj.cmap
    underlay_parts = _fetch_underlay_once(spec, plt, np, bool(underlay))

    # Basemap style: explicit choice wins; otherwise the preset default.
    if basemap is None:
        basemap_style = preset_obj.basemap
        basemap_name = preset_obj.basemap.name
    else:
        try:
            basemap_style = ae.get_basemap(basemap)
        except KeyError as exc:
            raise ValueError(
                f"unknown basemap {basemap!r}; choose from "
                f"{ae.list_basemaps()} or None for the preset default"
            ) from exc
        basemap_name = basemap_style.name
    if strand_count <= 0:
        raise ValueError(
            f"strand_count must be positive, got {strand_count!r}")
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
    label_dicts, label_mode = _resolve_place_labels(
        place_labels, spec, max_labels, min_population)
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
        "place_canvas_labels": canvas_labels,
    }

    if preset == "dark_flow":
        frames, timestamps, info = _render_dark_flow(ctx)
    elif preset == "dark_glow":
        frames, timestamps, info = _render_dark_glow(ctx)
    elif preset == "dark_strands":
        frames, timestamps, info = _render_dark_strands(ctx)
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
            "strand_linewidth": strand_linewidth,
            "furniture": {
                kind: {"rect": list(p.rect), "placed": bool(p.placed),
                       "scale": float(p.scale)}
                for kind, p in (ctx["furniture"].placed
                                if "furniture" in ctx else {}).items()
            },
            "n_frames": len(frames),
            "place_labels": {
                "mode": label_mode,  # "auto" | "explicit" | "off"
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
            **info,
        },
    }
    manifest_path = Path(out_dir) / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return frames, str(manifest_path.resolve())
