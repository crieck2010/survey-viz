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
AESTHETIC_PRESETS = ("dark_flow", "dark_glow", "paper_prism")

#: Variables each preset can render. ``None`` means "any continuous
#: gridded variable" (the paper_prism case — height = value works for
#: any scalar field).
PRESET_VARIABLES: Dict[str, Optional[Tuple[str, ...]]] = {
    "dark_flow": ("currents", "wind"),
    "dark_glow": ("earthquakes", "storm-tracks"),
    "paper_prism": None,
}

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

    Returns a namespace over the engine's public surface. Two functions
    the engine's own docs/API.md promises — ``draw_north_arrow`` and
    ``close`` — are missing from the v0.1.0 top-level namespace (they
    exist in ``aesthetics.typography`` / ``aesthetics.backgrounds``);
    they are resolved from the submodules here so call sites use one
    namespace. The engine release itself is never modified.
    """
    import types

    try:
        import aesthetics as ae
        from aesthetics.backgrounds import close as _close
        from aesthetics.typography import draw_north_arrow as _north_arrow
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


# Default place kinds for automatic labels (the gazetteer's own default).
_LABEL_KINDS = ("city", "town")


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
    lay = preset.legend_layout
    subtitle = ctx["subtitle"]
    rw, rh = ctx["render_size"]

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
            _draw_coastlines_frac(ax, coastline_segs, spec.bbox,
                                  color="#3a3f4a")

        def draw_furniture(ax):
            ae.draw_title(ax, spec.title, *lay["title"],
                          size=preset.title_size, color=preset.text_color)
            ae.draw_subtitle(ax, subtitle, *lay["subtitle"],
                             size=preset.subtitle_size,
                             color=preset.text_color)
            ae.timeline(ax, lay["timeline"], moments[frame_idx[0]],
                        moments[frame_idx[-1]], moment,
                        color=preset.text_color)
            ae.gradient_bar(ax, lay["gradient_bar"], cname, vmin, vmax,
                            label=flow["scalar_label"],
                            unit=flow["scalar_unit"],
                            color=preset.text_color)
            ae.draw_north_arrow(ax, *lay["north_arrow"], angle_deg=angle,
                                color=preset.text_color)
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
    lay = preset.legend_layout
    subtitle = ctx["subtitle"]
    rw, rh = ctx["render_size"]
    lon0, lat0, lon1, lat1 = (float(v) for v in spec.bbox)

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
            _draw_coastlines_frac(ax, coastline_segs, spec.bbox,
                                  color="#3a3f4a")

        count = int(shown.sum())

        def draw_furniture(ax, _count=count, _now=now):
            ae.draw_title(ax, spec.title, *lay["title"],
                          size=preset.title_size, color=preset.text_color)
            ae.draw_subtitle(ax, subtitle, *lay["subtitle"],
                             size=preset.subtitle_size,
                             color=preset.text_color)
            ae.counter(ax, *lay["counter"], _count,
                       label=ev["count_label"], color=preset.text_color)
            dd = lay["date_dial"]
            ae.date_dial(ax, dd[0], dd[1], dd[2], _now,
                         color=preset.text_color)
            if angle:
                ae.draw_north_arrow(ax, 0.06, 0.80, angle_deg=angle,
                                    color=preset.text_color)
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
    subtitle = ctx["subtitle"]
    rw, rh = ctx["render_size"]
    unit = ctx.get("unit") or ""

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
            ae.draw_title(ax, spec.title, *lay["title"],
                          size=preset.title_size, color=preset.text_color)
            ae.draw_subtitle(ax, subtitle, *lay["subtitle"],
                             size=preset.subtitle_size,
                             color=preset.text_color)
            # The preset's scale-bar slot (x=0.06) clips tick labels at
            # the frame edge; nudged right onto clear paper.
            ae.vertical_scale_bar(ax, 0.10, 0.30, 0.28, vmin, vmax,
                                  label=_variable_label(spec.variable),
                                  unit=unit, color=preset.text_color)
            dd = lay["date_dial"]
            ae.date_dial(ax, dd[0], dd[1], dd[2], _moment,
                         color=preset.text_color)
            if angle:
                ae.draw_north_arrow(ax, 0.06, 0.80, angle_deg=angle,
                                    color=preset.text_color)
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

    from .render import _fetch_underlay_once, _require_plotting, _validate_cmap
    plt, _, np = _require_plotting()
    cname = _validate_cmap(cmap, plt) or preset_obj.cmap
    underlay_parts = _fetch_underlay_once(spec, plt, np, bool(underlay))

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
        if encoding_line and enc and watermark is None:
            # Without the watermark block the honesty line stands alone.
            ae.encoding_statement(ax, 0.5, 0.075, enc,
                                  color=preset_obj.text_color)
        if watermark:
            import datetime as _dtd
            ae.frame_furniture(
                ax, brand=watermark, holder=watermark,
                year=_dtd.date.today().year,
                data_source=data_source,
                encoding=enc if encoding_line else None,
                color=preset_obj.text_color)
        if ctx["place_canvas_labels"]:
            # Geographic labels, upright on the final canvas; the
            # engine's greedy decluttering drops what cannot fit.
            ae.place_labels(ax, ctx["place_canvas_labels"],
                            color=preset_obj.text_color,
                            dot_color=preset_obj.text_color)

    ctx: Dict[str, Any] = {
        "ae": ae, "spec": spec, "field": field, "preset": preset_obj,
        "cmap": cname, "angle": angle, "out_dir": out_dir,
        "render_size": render_size, "subtitle": subtitle_text,
        "underlay_parts": underlay_parts,
        "draw_furniture_common": draw_furniture_common,
        "encoding": preset_obj.encoding,
        "place_canvas_labels": canvas_labels,
    }

    if preset == "dark_flow":
        frames, timestamps, info = _render_dark_flow(ctx)
    elif preset == "dark_glow":
        frames, timestamps, info = _render_dark_glow(ctx)
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
            "encoding_line": encoding_line,
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
