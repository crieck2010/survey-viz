"""Renderer: VizSpec + duck-typed field/series -> PNG frames + manifest.

``render_viz`` is the only entry point. ``matplotlib`` (and ``numpy``) are
**lazy imports**: the engine imports fine without them, and ``render_viz``
raises an informative error if they are missing.

Inputs are duck-typed on purpose — peers are optional, never hard imports:

* ``field``: any object with ``.times``, ``.lats``, ``.lons`` plus grid
  access — either a 3D array attribute (``values``/``data``/``sst``/``grids``)
  shaped ``(ntime, nlat, nlon)``, a per-index method
  (``grid``/``frame``/``at``/``get_frame``), or a survey-currents
  ``CurrentField`` with 3D ``u``/``v`` (rendered as scalar current speed
  ``sqrt(u^2+v^2)`` through the scalar path — no quiver/streamline
  rendering here). Plain dicts with
  ``times``/``lats``/``lons``/``values`` keys also work. Never imports
  survey-currents; a ``GlseaField``-shaped object just works.
* ``series``: any object with ``.dates``/``.values`` (or
  ``.timestamps``/``.values``), a dict with ``dates``/``values``, or None.
  None still renders — the chart panel shows a placeholder note.

When ``spec.variable == "storm-tracks"`` a dedicated track renderer
runs instead of the scalar path: an IBTrACS ``StormField`` (or its
``to_dict()`` form) is drawn as cumulative track polylines colored by
Saffir-Simpson category, with genesis markers + name labels, an
optional ERA5 contour context, and a category legend. See
``docs/STORMS.md``.

When ``spec.variable == "streamflow"`` a dedicated gage renderer runs:
a USGS ``GageField`` (or its ``to_dict()`` form) is drawn as gage
markers over the underlay, colored by the current discharge
percentile category (USGS WaterWatch-style classes), with a
hydrograph panel (selected gage via ``spec.gage_site``, else the
documented regional-median rule) and optional precipitation contour
context. Empty gage sets render an explicit empty-frame message —
never fabricated data. See ``docs/STREAMFLOW.md``.

When ``spec.variable == "earthquakes"`` a dedicated quake renderer
runs: a USGS ``QuakeField`` (or its ``to_dict()`` form) is drawn as
event markers over the underlay, sized by magnitude (documented power
law) and colored by hypocentral-depth bin, with a daily event-count
time-series panel and a largest-events readout. Frames are
CUMULATIVE: each frame shows all events with time <= that frame date.
Empty event sets render an explicit empty-frame message — never
fabricated data. Every manifest records that the catalog is observed
events, not a forecast hazard model. See ``docs/EARTHQUAKES.md``.

Layout ``"reel-vertical"`` (1080x1920): title block, map panel (pcolormesh,
fixed vmin/vmax so the colormap never flickers, burned-in timestamp),
time-series panel with a playhead line + dot synced to the frame date.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from . import __version__
from .gazetteer import get_region

SCHEMA_ID = "survey-viz.frame-manifest/1.0"
MAX_FRAMES = 240  # hard cap; longer runs are evenly subsampled

_STYLE = {
    "reel-dark": {
        "face": "#0b0e14",
        "axes": "#11151d",
        "text": "#f2f4f8",
        "muted": "#9aa3b2",
        "cmap": "turbo",
        "series": "#4cc9f0",
    },
    "light": {
        "face": "#ffffff",
        "axes": "#f4f5f7",
        "text": "#1a1d23",
        "muted": "#6b7280",
        "cmap": "viridis",
        "series": "#1d4ed8",
    },
}

_VARIABLE_UNITS = {
    "sst": "°C",
    "currents": "m/s",
    # Ocean color (survey-currents v0.14.0+, source "oceancolor"):
    # chlorophyll-a in mg/m³, rendered on a log scale ("chlorophyll"
    # is the legacy variable alias — same unit, same rendering).
    "ocean-color": "mg/m³",
    "chlorophyll": "mg/m³",
    # ERA5 variables (survey-currents v0.4.0+, source "era5").
    "wind": "m/s",
    "msl": "hPa",
    "t2m": "°C",
    "tp": "mm",
    # FIRMS active fires (survey-currents v0.6.0+, source "firms"):
    # daily detection counts per grid cell ("burn-scar" has no adapter
    # and is never rendered — the unit is registered only so a spec
    # carrying it degrades gracefully in messages).
    "fire": "detections",
    "burn-scar": "",
    # NASA Black Marble night lights (survey-currents v0.9.0+, source
    # "blackmarble"): daily DNB radiance ("power-outage" has no adapter
    # and is never rendered — the unit is registered only so a spec
    # carrying it degrades gracefully in messages).
    "night-lights": "nW/cm²/sr",
    "power-outage": "",
    # GEBCO 2024 topography/bathymetry (survey-currents v0.10.0+,
    # source "gebco"): metres, positive up ("country-borders" has no
    # adapter and is never rendered).
    "bathymetry": "m",
    "elevation": "m",
    "country-borders": "",
    # IBTrACS storm tracks (survey-currents v0.11.0+, source
    # "ibtracs"): max sustained wind in knots drives the
    # Saffir-Simpson coloring.
    "storm-tracks": "kt",
    # CSR GRACE/GRACE-FO RL06.3 terrestrial water storage
    # (survey-currents v0.12.0+, source "grace"): anomalies in cm of
    # liquid-water-equivalent thickness vs the 2004–2009 time-mean
    # (land-only; ocean cells are NaN).
    "water-storage": "cm",
    # USGS Water Services (NWIS) streamgage daily values
    # (survey-currents v0.13.0+, source "usgs"): marker unit follows
    # the field's unit system (the gage renderer reads it from the
    # field dict) — native ft³/s for discharge (00060), m³/s after
    # to_si(). Registered here so messages degrade gracefully.
    "streamflow": "ft³/s",
    # USGS Earthquake Catalog (ComCat) quake events
    # (survey-currents v0.15.0+, source "comcat"): markers are sized by
    # magnitude (unit "M" — moment/ML/etc. per event mag_type).
    "earthquakes": "M",
}

#: Colormaps per variable for the main map panel. Variables not listed
#: fall back to the style default.
_VARIABLE_CMAPS = {
    "bathymetry": "Blues_r",   # deep = dark blue
    "elevation": "terrain",
    # Chlorophyll-a: viridis on a LogNorm scale (see the ocean-color
    # branch in render_viz) — readable on both dark and light styles,
    # and perceptually uniform across the log decades.
    "ocean-color": "viridis",
    "chlorophyll": "viridis",  # legacy alias, same rendering
    # Diverging anomaly colormap for GRACE TWS: brown = drier than the
    # 2004–2009 mean, blue = wetter; the scale is always centered on
    # zero (see the vmin/vmax block in render_viz).
    "water-storage": "BrBG",
}

#: Coastline color per style for the basemap underlay.
_UNDERLAY_COAST_COLOR = {
    "reel-dark": "#9fd8ff",
    "light": "#2f5d8a",
}

#: Curated matplotlib colormaps for UI dropdowns (e.g. reel-studio's
#: Aesthetics picker). ``render_viz`` accepts ANY registered matplotlib
#: colormap as ``cmap`` — this list is just the recommended subset.
CURATED_CMAPS = (
    # Perceptually uniform sequential
    "viridis", "plasma", "inferno", "magma", "cividis",
    # Sequential
    "Blues", "Greens", "Oranges", "Purples", "Reds",
    "YlOrRd", "YlGnBu", "hot", "cool",
    "spring", "summer", "autumn", "winter",
    # Diverging
    "coolwarm", "RdBu_r", "RdYlBu_r", "BrBG", "PiYG", "seismic",
    # Miscellaneous
    "turbo", "jet", "nipy_spectral", "terrain", "ocean", "gist_earth",
)


def _validate_cmap(cmap: Optional[str], plt) -> Optional[str]:
    """Validate a user-supplied colormap name against matplotlib's registry.

    Returns the (stripped) name, or ``None`` when ``cmap`` is ``None``.
    Raises :class:`ValueError` naming the curated recommendations when the
    name is not a registered colormap. ``plt`` is the lazily imported
    matplotlib pyplot module (keeps the engine import-light).
    """
    if cmap is None:
        return None
    if not isinstance(cmap, str) or not cmap.strip():
        raise ValueError(
            f"render_viz: cmap must be a registered matplotlib colormap "
            f"name or None, got {cmap!r}")
    name = cmap.strip()
    if name not in plt.colormaps():
        raise ValueError(
            f"render_viz: unknown colormap {name!r}. "
            f"Recommended: {', '.join(CURATED_CMAPS)} "
            f"(any other registered matplotlib colormap also works)")
    return name

#: Contour defaults per overlay variable: contour every ``step`` units,
#: labelled in ``unit``.
_OVERLAY_DEFAULTS = {
    "wind": {"step": 5.0, "unit": "m/s"},
    "msl": {"step": 4.0, "unit": "hPa"},   # isobars
    "t2m": {"step": 2.0, "unit": "°C"},
    "tp": {"step": 5.0, "unit": "mm"},
}


def _require_plotting():
    """Lazy import so the engine imports without matplotlib/numpy."""
    try:
        import matplotlib
    except ImportError as exc:
        raise RuntimeError(
            "render_viz requires matplotlib, which is not installed. "
            "Install the render extra: pip install 'survey-viz[render]'."
        ) from exc
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "render_viz requires numpy, which is not installed. "
            "Install the render extra: pip install 'survey-viz[render]'."
        ) from exc
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    return plt, mdates, np


def _coerce_date(value: Any) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        return _dt.date.fromisoformat(value.strip())
    # numpy datetime64
    try:
        import numpy as np  # local; only reached for datetime64 inputs

        if isinstance(value, np.datetime64):
            return _dt.date(1970, 1, 1) + _dt.timedelta(
                days=int(value.astype("datetime64[D]").astype(int))
            )
    except ImportError:
        pass
    raise TypeError(f"Cannot coerce {value!r} to a date")


_GRID_ATTRS = ("values", "data", "sst", "grids", "grid")
_GRID_METHODS = ("grid", "frame", "at", "get_frame")

#: Suffix marking a derived anomaly product (survey-derive v0.1.0+).
_DERIVED_SUFFIX = "-anomaly"


def _derived_base(variable: str) -> str:
    """Base variable for a possibly-derived ``"<base>-anomaly"`` name."""
    if variable.endswith(_DERIVED_SUFFIX) and len(variable) > len(_DERIVED_SUFFIX):
        return variable[: -len(_DERIVED_SUFFIX)]
    return variable


def _field_units(field: Any) -> str:
    """Display units carried by the field dict/object itself."""
    if isinstance(field, dict):
        return str(field.get("units") or "")
    return str(getattr(field, "units", "") or "")


def _normalize_field(field: Any) -> Tuple[List[_dt.date], Any, Any, Any]:
    """Return (times, lats, lons, values[T,H,W]) as numpy arrays."""
    _, _, np = _require_plotting()
    if isinstance(field, dict):
        times = [_coerce_date(t) for t in field["times"]]
        lats = np.asarray(field["lats"], dtype=float)
        lons = np.asarray(field["lons"], dtype=float)
        values = np.asarray(field["values"], dtype=float)
    else:
        times = [_coerce_date(t) for t in field.times]
        lats = np.asarray(field.lats, dtype=float)
        lons = np.asarray(field.lons, dtype=float)
        values = None
        # CurrentField (survey-currents): render the scalar current speed
        # sqrt(u^2 + v^2) through the existing scalar path. No
        # quiver/streamline/particle rendering here — that is the
        # survey-flow renderer's job. Masked (land/missing) cells become
        # NaN so they stay out of the color scale and the map.
        if hasattr(field, "u") and hasattr(field, "v"):
            u = np.asanyarray(field.u, dtype=float)
            v = np.asanyarray(field.v, dtype=float)
            if u.ndim == 3 and v.ndim == 3:
                mask = np.ma.getmaskarray(u) | np.ma.getmaskarray(v)
                speed = np.sqrt(np.ma.getdata(u) ** 2 + np.ma.getdata(v) ** 2)
                values = np.where(mask, np.nan, speed)
        for attr in _GRID_ATTRS:
            if values is not None:
                break
            if hasattr(field, attr):
                arr = getattr(field, attr)
                if arr is not None and np.ndim(arr) == 3:
                    # Masked cells (land / cloud / missing) become NaN so
                    # they stay out of the color scale and the map —
                    # np.asarray alone would silently DROP the mask and
                    # render masked cells as data (never acceptable for
                    # cloud gaps in ocean color).
                    values = np.ma.filled(np.ma.asanyarray(arr, dtype=float),
                                          np.nan)
                    break
        if values is None:
            for method in _GRID_METHODS:
                if callable(getattr(field, method, None)):
                    fn = getattr(field, method)
                    values = np.stack([np.ma.filled(
                        np.ma.asanyarray(fn(i), dtype=float), np.nan)
                        for i in range(len(times))])
                    break
        if values is None:
            raise TypeError(
                "field has .times/.lats/.lons but no 3D grid data: expected a "
                f"(ntime, nlat, nlon) attribute among {_GRID_ATTRS} or a per-index "
                f"method among {_GRID_METHODS}."
            )
    if not (values.ndim == 3 and values.shape[0] == len(times)):
        raise ValueError(
            f"field grid shape {values.shape} inconsistent with {len(times)} times"
        )
    return times, lats, lons, values


def _overlay_grids(field: Any, overlays: Tuple[str, ...],
                   n_times: int) -> Dict[str, Any]:
    """Return {overlay_name: (nt,ny,nx) array} for the requested overlays.

    Reads ``field.overlay_grids`` (an ``Era5Field``-shaped dict attribute)
    or the ``"overlay_grids"`` key of a dict field. Raises ``ValueError``
    when the spec requests an overlay the field cannot provide — the
    caller never silently drops a requested overlay.
    """
    _, _, np = _require_plotting()
    if not overlays:
        return {}
    if isinstance(field, dict):
        src = field.get("overlay_grids") or {}
    else:
        src = getattr(field, "overlay_grids", None) or {}
    out: Dict[str, Any] = {}
    for name in overlays:
        arr = src.get(name)
        if arr is None:
            raise ValueError(
                f"spec requests overlay {name!r} but the field provides no "
                f"matching grid (field.overlay_grids keys: {sorted(src)})")
        arr = np.asarray(arr, dtype=float)
        if arr.ndim != 3 or arr.shape[0] != n_times:
            raise ValueError(
                f"overlay {name!r} grid shape {arr.shape} inconsistent with "
                f"{n_times} frame times")
        out[name] = arr
    return out


def _normalize_series(series: Any) -> Optional[Tuple[List[_dt.date], Any]]:
    if series is None:
        return None
    _, _, np = _require_plotting()
    if isinstance(series, dict):
        dates = [_coerce_date(d) for d in series["dates"]]
        vals = np.asarray(series["values"], dtype=float)
    else:
        raw_dates = getattr(series, "dates", getattr(series, "timestamps", None))
        if raw_dates is None:
            raise TypeError(
                "series needs .dates (or .timestamps) and .values attributes, "
                "or be a dict with 'dates'/'values' keys, or None."
            )
        dates = [_coerce_date(d) for d in raw_dates]
        vals = np.asarray(series.values, dtype=float)
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    return [dates[i] for i in order], vals[order]


def _subsample(idxs: List[int]) -> List[int]:
    if len(idxs) > MAX_FRAMES:
        step = len(idxs) / MAX_FRAMES
        return [idxs[int(i * step)] for i in range(MAX_FRAMES)]
    return idxs


def _bucket_indices(times: List[_dt.date], cadence: str) -> List[int]:
    """Pick one representative timestep per cadence bucket (closest to mid-bucket)."""
    if cadence == "daily":
        return _subsample(list(range(len(times))))
    if cadence == "monthly":
        key = lambda d: (d.year, d.month)
        target_day = 15
    elif cadence == "yearly":
        key = lambda d: (d.year,)
        target_day = None
    else:
        raise ValueError(f"Unknown cadence: {cadence!r}")

    buckets: Dict[Any, List[int]] = {}
    for i, t in enumerate(times):
        buckets.setdefault(key(t), []).append(i)

    chosen = []
    for _k, idxs in sorted(buckets.items()):
        if cadence == "monthly":
            best = min(idxs, key=lambda i: abs((times[i].day - target_day)))
        else:  # yearly: closest to Jul 1
            best = min(idxs, key=lambda i: abs((times[i].month - 7) * 31 + (times[i].day - 1)))
        chosen.append(best)
    chosen.sort()
    return _subsample(chosen)


def _wrap_title(title: str, width: int = 34) -> str:
    words, lines, current = title.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def _draw_title_block(fig, spec, st, region_name: str, rect=None) -> None:
    """Shared title block (map frames and storm-track frames)."""
    ax_t = fig.add_axes(rect if rect is not None else [0.0, 0.90, 1.0, 0.10])
    ax_t.axis("off")
    ax_t.set_facecolor(st["face"])
    ax_t.text(
        0.5,
        0.62,
        _wrap_title(spec.title),
        ha="center",
        va="center",
        fontsize=34,
        weight="bold",
        color=st["text"],
        linespacing=1.25,
    )
    ax_t.text(
        0.5,
        0.18,
        f"{region_name}  ·  {spec.cadence}  ·  survey-viz",
        ha="center",
        va="center",
        fontsize=20,
        color=st["muted"],
    )


def _draw_caption(fig, st, text: str, fontsize: int = 22, rect=None) -> None:
    """Lower-third story caption, drawn above the footer strip.

    A rounded, semi-transparent chip centered on the figure — visible
    over any map without obscuring the data panel.
    """
    ax_c = fig.add_axes(rect if rect is not None else [0.0, 0.065, 1.0, 0.075])
    ax_c.axis("off")
    ax_c.set_facecolor(st["face"])
    ax_c.text(
        0.5,
        0.5,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        color=st["text"],
        bbox=dict(boxstyle="round,pad=0.45", facecolor=st["axes"],
                  edgecolor="none", alpha=0.88),
    )


def _draw_footer(fig, spec, st, footer_var: str, fontsize: int = 16, rect=None) -> None:
    """Shared footer (map frames and storm-track frames).

    ``fontsize`` is 16 everywhere except the earthquakes footer,
    whose prescribed wording ("N events · largest M{x} · observed
    events — not a forecast") needs a smaller size to fit the
    1080 px figure width.

    When ``spec.caption`` is set, it is prepended to the footer —
    ``"<caption> · <footer_var> · <start> → <end>"`` — so per-renderer
    honesty wording (e.g. the quake catalog's "observed events — not a
    forecast") is never erased by a custom caption.
    """
    ax_f = fig.add_axes(rect if rect is not None else [0.0, 0.0, 1.0, 0.06])
    ax_f.axis("off")
    ax_f.set_facecolor(st["face"])
    caption = getattr(spec, "caption", None)
    if caption:
        footer_text = (f"{caption} · {footer_var} · "
                       f"{spec.start.isoformat()} → {spec.end.isoformat()}")
    else:
        footer_text = (f"{footer_var} · {spec.start.isoformat()} → "
                       f"{spec.end.isoformat()}")
    ax_f.text(
        0.5,
        0.5,
        footer_text,
        ha="center",
        va="center",
        fontsize=fontsize,
        color=st["muted"],
    )


def _draw_gap_panel(ax_m, st, title="NO GRACE OBSERVATION",
                    subtitle="no GRACE/GRACE-FO solution this month") -> None:
    """Mark an all-NaN frame as NO DATA (never interpolated)."""
    ax_m.text(
        0.5,
        0.55,
        title,
        transform=ax_m.transAxes,
        ha="center",
        va="center",
        fontsize=30,
        weight="bold",
        color=st["muted"],
        zorder=6,
        bbox=dict(boxstyle="round,pad=0.6", fc=st["face"],
                  ec=st["muted"], alpha=0.9),
    )
    ax_m.text(
        0.5,
        0.44,
        subtitle,
        transform=ax_m.transAxes,
        ha="center",
        va="center",
        fontsize=18,
        color=st["muted"],
        zorder=6,
        bbox=dict(boxstyle="round,pad=0.4", fc=st["face"],
                  ec=st["muted"], alpha=0.9),
    )


def _fetch_underlay_once(spec, plt, np, want: bool):
    """Fetch the basemap underlay once per render.

    Returns ``(rgba, extent, coastlines, status)``: ``rgba``/``extent``
    are the GEBCO tint/hillshade image (or ``None`` when unavailable or
    when the variable *is* the topography), ``coastlines`` the Natural
    Earth segment list, ``status`` the manifest record. Never raises
    for a missing peer — the caller falls back to the plain
    background.
    """
    status: Dict[str, Any] = {"status": "disabled", "reason": "",
                               "topo": False, "coastline_segments": 0,
                               "resolution": None,
                               "coastline_scale": None}
    if not want:
        return None, None, [], status
    from .underlay import fetch_underlay, hillshade
    info = fetch_underlay(
        tuple(float(v) for v in spec.bbox),
        include_topo=(spec.variable not in ("bathymetry", "elevation"))
                     and getattr(spec, "underlay_topo", True))
    status = {
        "status": info["status"],
        "reason": info["reason"],
        "topo": info["topo"] is not None,
        "coastline_segments": info["n_coastline_segments"],
        "resolution": info["resolution"],
        "coastline_scale": info["coastline_scale"],
    }
    if info["status"] != "ok":
        return None, None, [], status
    coastlines = info["coastlines"]
    topo = info["topo"]
    if topo is None:
        return None, None, coastlines, status
    tvals = np.array(topo["values"], dtype=float)
    tlats = np.array(topo["lats"], dtype=float)
    tlons = np.array(topo["lons"], dtype=float)
    tres = float(topo["resolution"])
    lo, hi = float(np.nanmin(tvals)), float(np.nanmax(tvals))
    if not lo < hi:
        hi = lo + 1.0
    rgba = plt.get_cmap("terrain")(
        np.clip((tvals - lo) / (hi - lo), 0.0, 1.0))
    shade = np.nan_to_num(hillshade(tvals, tres), nan=0.75)
    rgba[..., :3] *= (0.55 + 0.45 * shade)[..., None]
    rgba[..., 3] = 0.9
    rgba[np.isnan(tvals), 3] = 0.0
    extent = [float(tlons[0] - tres / 2),
              float(tlons[-1] + tres / 2),
              float(tlats[-1] - tres / 2),
              float(tlats[0] + tres / 2)]
    return rgba, extent, coastlines, status


# ---------------------------------------------------------------------------
# Platform canvas support (survey-layout interop)
#
# ``render_viz(..., canvas=...)`` accepts a plain dict — the shape built
# by ``layout.to_viz_canvas`` — so this module never imports the
# survey-layout peer (peers stay optional). When ``canvas`` is None the
# historical hardcoded 1080x1920 layout is used, pixel-identical to
# <= 0.17.
# ---------------------------------------------------------------------------

#: Legacy region rects (figure fractions), reproduced exactly from the
#: hardcoded layout of survey-viz <= 0.17.
_LEGACY_RECTS = {
    "title": [0.0, 0.90, 1.0, 0.10],
    "map": [0.04, 0.36, 0.92, 0.52],
    "chart": [0.08, 0.08, 0.84, 0.24],
    "caption": [0.0, 0.065, 1.0, 0.075],
    "footer": [0.0, 0.0, 1.0, 0.06],
}

#: Legacy rects for the earthquake renderer (map + largest-events
#: ranking panel + daily-count chart).
_QUAKE_LEGACY_RECTS = {
    "title": [0.0, 0.90, 1.0, 0.10],
    "map": [0.04, 0.52, 0.92, 0.36],
    "ranking": [0.04, 0.30, 0.92, 0.20],
    "chart": [0.08, 0.08, 0.84, 0.18],
    "caption": [0.0, 0.065, 1.0, 0.075],
    "footer": [0.0, 0.0, 1.0, 0.06],
}


def _resolve_canvas(canvas, flavor="standard"):
    """Resolve a ``canvas`` dict into ``(width, height, rects, info)``.

    ``rects`` maps region name -> ``[left, bottom, width, height]``
    figure fractions. ``info`` is the JSON-able provenance record (or
    None for the legacy layout). ``flavor`` is ``"standard"`` or
    ``"quake"`` (the earthquake renderer has an extra ranking panel).

    Fail-fast: a malformed canvas raises ValueError before any frame
    renders, so a typo can't silently misplace a title under a
    platform's status bar.
    """
    base = _QUAKE_LEGACY_RECTS if flavor == "quake" else _LEGACY_RECTS
    if canvas is None:
        return 1080, 1920, {k: list(v) for k, v in base.items()}, None
    if not isinstance(canvas, dict):
        raise ValueError(
            f"canvas must be a dict, got {type(canvas).__name__}")
    try:
        width = int(canvas["width"])
        height = int(canvas["height"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "canvas needs integer 'width' and 'height' (px), got "
            f"{canvas!r}") from None
    if width <= 0 or height <= 0:
        raise ValueError(
            f"canvas width/height must be positive, got {(width, height)}")
    regions = canvas.get("regions") or {}
    if not isinstance(regions, dict):
        raise ValueError(
            f"canvas['regions'] must be a dict, got {regions!r}")
    rects = {}
    for name, default in base.items():
        raw = regions.get(name, default)
        try:
            rect = [float(v) for v in raw]
        except (TypeError, ValueError):
            raise ValueError(
                f"canvas region {name!r} must be [l, b, w, h], got "
                f"{raw!r}") from None
        if len(rect) != 4 or not all(0.0 <= v <= 1.0 for v in rect):
            raise ValueError(
                f"canvas region {name!r} must be [l, b, w, h] fractions, "
                f"got {raw!r}")
        rects[name] = rect
    if flavor == "quake" and "ranking" not in regions:
        # Fill the band between the chart top and the map bottom at the
        # map's x-span (legacy: [0.04, 0.30, 0.92, 0.20]).
        ml, mb, mw, mh = rects["map"]
        _cl, cb, _cw, chh = rects["chart"]
        top = cb + chh
        rects["ranking"] = [ml, top, mw, max(0.0, mb - top)]
    info = {
        "platform": canvas.get("platform"),
        "width": width,
        "height": height,
        "regions": sorted(rects),
    }
    return width, height, rects, info


def render_viz(
    spec,
    field,
    series=None,
    out_dir="frames",
    layout: str = "reel-vertical",
    style: Optional[str] = None,
    underlay: Optional[bool] = None,
    cmap: Optional[str] = None,
    story_captions: bool = False,
    canvas=None,
    preset: Optional[str] = None,
    rotation: Any = None,
    watermark: Optional[str] = None,
    subtitle: Optional[str] = None,
    encoding_line: bool = True,
    place_labels: Any = True,
    max_labels: int = 8,
    min_population: int = 0,
    basemap: Optional[str] = None,
    strand_count: Union[int, str] = "auto",
    strand_linewidth: float = 1.4,
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
) -> Tuple[List[str], str]:
    """Render a VizSpec into PNG frames + a frame manifest.

    Returns ``(frames_list, manifest_path)``. ``frames_list`` holds absolute
    file paths in render order; the manifest is directly consumable by
    survey-animate's ``--frames-dir`` / manifest path.

    ``underlay`` controls the optional basemap underlay (GEBCO
    tint/hillshade + Natural Earth coastlines, drawn beneath the
    variable map wherever the variable is NaN): ``None`` (default)
    follows ``spec.underlay``; ``True``/``False`` override it. The
    underlay is fetched once per render (lazy peer import, cached);
    when the peer or the data is unavailable the renderer falls back
    to the plain background and records the underlay status in the
    manifest — never a crash, never silent wrongness.

    ``cmap`` overrides the data colormap with any registered matplotlib
    colormap name (see :data:`CURATED_CMAPS` for the recommended
    subset). It applies ONLY to continuous data maps (the main map
    renderer: SST, ocean color, ERA5, fires, night lights, topography,
    water storage, ...). The categorical renderers — storm tracks
    (Saffir-Simpson colors), streamgages (WaterWatch percentile
    classes), earthquakes (hypocentral-depth bins) — keep their fixed
    scientific encodings; a ``cmap`` passed for those variables is
    validated (typos still fail fast) but recorded in the manifest as
    requested-not-applied, never silently recoloring data whose colors
    carry meaning. ``None`` (default) keeps each variable's default.

    ``story_captions`` burns data-driven headline captions into the
    frames (see :mod:`viz.insights`): a peak caption on the
    highest-mean frame and, when the trend reaches 5% of the frames'
    mean range, a trend caption on the closing frames. Only the
    continuous-data main path renders them; the categorical renderers
    record the request as not-applied in the manifest (their colors
    carry meaning, and per-frame scalar stats don't apply).

    ``canvas`` is an optional platform-canvas dict — the shape built by
    ``survey_layout.to_viz_canvas`` (``{"platform", "width", "height",
    "regions", "unsafe"}``). It sets the frame size and overrides the
    title/map/chart/caption/footer region rects so platform chrome
    (TikTok's status bar, the action rail, the bottom caption zone)
    never covers content. ``None`` (default) keeps the historical
    1080x1920 layout, pixel-identical to <= 0.17. Malformed canvases
    fail fast before any frame renders.

    ``preset`` selects the mapped.earth aesthetic path (see
    :mod:`viz.aesthetic_render` and the survey-aesthetics peer):
    ``"dark_flow"`` (LIC streaks for ``currents``/``wind``),
    ``"dark_glow"`` (event glow for ``earthquakes``/``storm-tracks``),
    or ``"paper_prism"`` (3D extrusion for gridded variables).
    ``None`` (default) keeps the legacy renderer, byte-identical to
    <= 0.21. The preset-path options ``rotation`` (``None``/``"auto"``/
    degrees), ``watermark`` (brand handle, ``None`` = off),
    ``subtitle`` (explicit editorial subtitle), ``encoding_line``
    (the preset's honesty line, default True), ``place_labels``,
    ``max_labels``, ``min_population``, ``bivariate`` (dark_strands
    brightness-by-speed encoding, default True) require ``preset`` —
    they fail fast without it. ``story_captions`` and ``canvas`` are legacy-
    path features and fail fast with a preset (the preset has its own
    editorial system and layout grammar).

    ``place_labels`` controls geographic place labels on the preset
    path (see :mod:`viz.aesthetic_render` and the survey-gazetteer
    peer): ``True`` (default) auto-fetches up to ``max_labels`` places
    (population >= ``min_population``) for the reel's north-up bbox,
    once per reel; ``False`` draws no labels; an explicit list of
    label dicts (``{"x": lon, "y": lat, "text": str,
    "priority": int}``) is used verbatim and wins over the automatic
    set. Labels are drawn upright with the rest of the furniture after
    rotation, and are recorded (JSON-serializable) in the frame
    manifest for the survey-cache fingerprint.

    ``strand_count`` is ``"auto"`` (default) or a positive int:
    ``"auto"`` asks the survey-autopilot peer for an area-proportional
    particle count for the region bbox (falling back to the old 3000
    default when the peer is absent); an int is used verbatim.
    Resolved to an int before the preset path dispatches. Preset-path
    only.

    ``robust_scale`` (default True) makes the ``dark_flow`` /
    ``dark_strands`` hue (temperature) color limits the survey-autopilot
    robust p2/p98 limits instead of the raw data min/max — a single
    outlier cell no longer washes out the whole reel's colors.
    Explicit ``spec.vmin``/``spec.vmax`` always win. When the peer is
    absent, or with ``robust_scale=False``, the legacy min/max scale is
    used; the manifest records ``scale_method``
    (``"autopilot-p2-p98"`` / ``"minmax-fallback"`` / ``"minmax"``)
    plus ``scale_percentiles`` when the peer was used. The bivariate
    p99 brightness channel is untouched. Preset-path only.

    ``salience_labels`` (default True) ranks automatic place labels on
    the flow presets by the survey-autopilot salience map — built from
    the TIME-MEAN speed/temperature grids, so the reel's typical
    hotspot outranks a single-frame flare — BEFORE the ``max_labels``
    cut, so salience decides which labels survive. Requires labels in
    ``"auto"`` mode and a flow field carrying speed AND temperature
    grids; when the peer is absent, or labels are explicit/off, the
    gazetteer order is kept verbatim. The manifest records
    ``place_labels.label_ranking`` (``"autopilot-salience"`` /
    ``"legacy"``). Preset-path only.

    ``surface_*`` options configure the ``surface`` preset (v0.28.0):
    a colored scalar surface over a shadowed land/footprint silhouette
    via survey-aesthetics ``render_surface`` (default cmap
    ``teal_pink``; ``surface_scale="log"`` for density-like data,
    ``surface_contours``/``surface_shadow`` on by default,
    ``surface_smoothing`` Gaussian sigma in output pixels). Reel-wide
    fixed vmin/vmax — never per-frame. When ``render_surface`` is
    unavailable (peer < 0.4.0) the preset falls back to a plain
    colormapped grid and records ``peer_fallback`` in the manifest.

    ``counter`` (surface preset only) is ``None`` or
    ``{"stat": "sum"|"mean"|"max", "unit": str, "label": str}``: a
    per-timestep NaN-aware stat, linearly interpolated between
    bracketing timesteps at each frame (clamped at the ends) and drawn
    under a big date readout. Between observed timesteps the counter
    shows linearly interpolated *estimates*, not measurements. On any
    other preset it is recorded as requested-but-not-applied.

    ``headline_beats`` is ``None`` or a list of ``(when, text)`` pairs
    (``when`` = fraction in [0, 1] of the reel, or an ISO-8601
    timestamp resolved against the frame times): the title swaps with
    a clean cut at each beat on any preset. Plain data — viz never
    imports survey-narrate (reel-studio may draft beats with it).
    """
    if layout != "reel-vertical":
        raise ValueError(f"Unknown layout: {layout!r} (only 'reel-vertical' in v0.1.0)")
    # Fail fast on a bad canvas before any rendering work.
    _resolve_canvas(canvas)
    # Aesthetic preset path (survey-aesthetics engine): routes before
    # the legacy dispatch. The lazy import keeps the peer optional —
    # the legacy path never touches it (no circular import either).
    preset_requested = (
        preset is not None or rotation is not None
        or watermark is not None or subtitle is not None
        or encoding_line is not True or basemap is not None
        or strand_count != "auto" or strand_linewidth != 1.4
        # An explicit label list, or non-default label tuning, only has
        # meaning on the preset path. (place_labels=False and the
        # default True are no-ops on the legacy renderer.)
        or (isinstance(place_labels, (list, tuple)) and len(place_labels) > 0)
        or max_labels != 8 or min_population != 0 or bivariate is not True
        or robust_scale is not True or salience_labels is not True
        or surface_contours is not True or surface_contour_levels is not None
        or surface_shadow is not True or surface_smoothing != 0.0
        or surface_scale != "linear" or counter is not None
        or headline_beats is not None)
    if preset_requested:
        if preset is None:
            raise ValueError(
                "rotation/watermark/subtitle/encoding_line/basemap/"
                "strand_count/strand_linewidth/place_labels/"
                "max_labels/min_population/bivariate/robust_scale/"
                "salience_labels/surface_contours/surface_contour_levels/"
                "surface_shadow/surface_smoothing/surface_scale/counter/"
                "headline_beats need "
                "preset='dark_flow'/'dark_glow'/'paper_prism'/'dark_strands'/"
                "'surface' "
                "— they are preset-path options with no meaning on the "
                "legacy renderer")
        if story_captions:
            raise ValueError(
                "story_captions is a legacy-path feature; the preset path "
                "has its own editorial system")
        if canvas is not None:
            raise ValueError(
                "canvas (platform safe zones) is a legacy-path feature; "
                "the preset path uses its own layout grammar")
        from .aesthetic_render import render_preset_viz, _resolve_strand_count
        want_underlay = underlay if underlay is not None else bool(spec.underlay)
        # "auto" strand counts resolve to an int BEFORE dispatching;
        # downstream keeps a plain int (plus the method for the
        # manifest). An int passes through verbatim.
        strand_count, strand_count_method = _resolve_strand_count(
            strand_count, spec)
        return render_preset_viz(
            spec, field, out_dir, preset=preset, rotation=rotation,
            watermark=watermark, subtitle=subtitle,
            encoding_line=encoding_line, underlay=want_underlay,
            cmap=cmap, style=style, place_labels=place_labels,
            max_labels=max_labels, min_population=min_population,
            basemap=basemap, strand_count=strand_count,
            strand_count_method=strand_count_method,
            strand_linewidth=strand_linewidth, landmask=landmask,
            bivariate=bivariate, robust_scale=robust_scale,
            salience_labels=salience_labels,
            surface_contours=surface_contours,
            surface_contour_levels=surface_contour_levels,
            surface_shadow=surface_shadow,
            surface_smoothing=surface_smoothing,
            surface_scale=surface_scale,
            counter=counter,
            headline_beats=headline_beats)
    style = style or spec.style
    if style not in _STYLE:
        raise ValueError(f"Unknown style: {style!r} (expected one of {sorted(_STYLE)})")

    want_underlay = underlay if underlay is not None else bool(spec.underlay)

    if spec.variable == "storm-tracks":
        return _render_storm_tracks(
            spec, field, series, out_dir, layout, style,
            want_underlay, cmap=cmap, story_captions=story_captions,
            canvas=canvas)

    if spec.variable == "streamflow":
        return _render_gage_field(
            spec, field, series, out_dir, layout, style,
            want_underlay, cmap=cmap, story_captions=story_captions,
            canvas=canvas)

    if spec.variable == "earthquakes":
        return _render_quake_field(
            spec, field, series, out_dir, layout, style,
            want_underlay, cmap=cmap, story_captions=story_captions,
            canvas=canvas)

    plt, mdates, np = _require_plotting()

    times, lats, lons, values = _normalize_field(field)
    series_norm = _normalize_series(series)
    frame_idx = _bucket_indices(times, spec.cadence)
    overlay_arrays = _overlay_grids(field, tuple(getattr(spec, "overlays", ())), len(times))

    # Optional universal basemap underlay (fetched once, not per frame).
    want_underlay = bool(spec.underlay) if underlay is None else bool(underlay)
    underlay_rgba, underlay_extent, coastline_segs, underlay_status = \
        _fetch_underlay_once(spec, plt, np, want_underlay)

    # Fixed color scale: from the spec when given, else the full data range once.
    if spec.variable == "water-storage" and spec.vmin is None and spec.vmax is None:
        # GRACE anomaly scale is always zero-centered and symmetric:
        # brown = drier than the 2004–2009 mean, blue = wetter. The
        # bound is the largest absolute finite anomaly across all
        # frames (gap months are all-NaN and contribute nothing).
        finite = values[np.isfinite(values)]
        bound = float(np.max(np.abs(finite))) if finite.size else 1.0
        if not bound > 0.0:
            bound = 1.0
        vmin, vmax = -bound, bound
        norm = None
    elif spec.variable == "ocean-color":
        # Chlorophyll-a is lognormally distributed (0.01–100 mg/m³ is
        # the honest dynamic range): a LOGARITHMIC color scale is the
        # only rendering that shows both oligotrophic gyres and bloom
        # peaks in one map. LogNorm needs vmin > 0, so the floor is
        # the smallest positive finite value across all frames.
        from matplotlib.colors import LogNorm
        finite = values[np.isfinite(values)]
        positive = finite[finite > 0]
        vmin = (spec.vmin if spec.vmin is not None
                else (float(np.min(positive)) if positive.size else 0.01))
        vmax = (spec.vmax if spec.vmax is not None
                else (float(np.max(finite)) if finite.size else 1.0))
        if not vmin > 0.0:
            vmin = 0.01
        if not vmax > vmin:
            vmax = vmin * 10.0
        norm = LogNorm(vmin=vmin, vmax=vmax)
    else:
        vmin = spec.vmin if spec.vmin is not None else float(np.nanmin(values))
        vmax = spec.vmax if spec.vmax is not None else float(np.nanmax(values))
        if not vmin < vmax:
            vmax = vmin + 1.0  # degenerate constant field: avoid a zero-range cmap
        norm = None

    st = _STYLE[style]
    # Derived anomaly products (survey-derive): resolve the base
    # variable for registry lookups. Dispatch above already routed
    # the exact categorical names, so "-anomaly" names always land
    # here on the continuous path — and never in the ocean-color
    # LogNorm branch (signed anomalies must never be log-scaled).
    base_variable = _derived_base(spec.variable)
    is_derived = base_variable != spec.variable
    var_cmap = _validate_cmap(cmap, plt) or _VARIABLE_CMAPS.get(
        base_variable, st["cmap"])
    if is_derived:
        # The derive engine sets the field's own units ("°C" for
        # anomalies, "σ" for standardized, "%" for percent-of-normal);
        # the registry only knows the base variable's unit, which
        # would mislabel standardized anomalies.
        unit = _field_units(field) or _VARIABLE_UNITS.get(base_variable, "")
    else:
        unit = _VARIABLE_UNITS.get(spec.variable, "")
    region = get_region(spec.region_key)
    region_name = region["name"] if region else spec.region_key

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Data-driven story captions (main continuous path only): computed
    # once from the full field, then burned per-frame in the loop.
    story_caps: List[Dict[str, Any]] = []
    if story_captions:
        from .insights import frame_stats, suggest_captions
        _stats = frame_stats(values, frame_idx, times)
        story_caps = [
            c.to_dict()
            for c in suggest_captions(_stats, spec.variable, unit)
        ]

    frames: List[str] = []
    timestamps: List[str] = []
    gap_flags: List[bool] = []
    lon_min, lat_min, lon_max, lat_max = spec.bbox

    fig_w, fig_h, rects, canvas_info = _resolve_canvas(canvas)

    for n, i in enumerate(frame_idx):
        frame_date = times[i]
        grid = values[i]

        fig = plt.figure(figsize=(fig_w / 100, fig_h / 100), dpi=100)
        fig.patch.set_facecolor(st["face"])

        # Title block -----------------------------------------------------
        _draw_title_block(fig, spec, st, region_name, rects["title"])

        # Map panel --------------------------------------------------------
        ax_m = fig.add_axes(rects["map"])
        ax_m.set_facecolor(st["axes"])
        # Basemap underlay (beneath the variable): GEBCO tint/hillshade
        # shows wherever the variable field is NaN; coastlines draw over
        # the variable for cartographic context.
        if underlay_rgba is not None:
            ax_m.imshow(underlay_rgba, extent=underlay_extent,
                        origin="upper", aspect="auto", zorder=1)
        if norm is not None:
            mesh = ax_m.pcolormesh(
                lons, lats, grid, norm=norm, cmap=var_cmap, shading="auto"
            )
        else:
            mesh = ax_m.pcolormesh(
                lons, lats, grid, vmin=vmin, vmax=vmax, cmap=var_cmap, shading="auto"
            )
        if coastline_segs:
            coast_color = _UNDERLAY_COAST_COLOR[style]
            for seg in coastline_segs:
                ax_m.plot(seg["lons"], seg["lats"], color=coast_color,
                          lw=1.2, zorder=5, solid_capstyle="round")
        # GRACE gap months (e.g. the 2017–2018 inter-mission gap) are
        # all-NaN frames: they are never interpolated, and the frame is
        # visibly marked instead of rendering as an empty map. Ocean
        # color frames that are entirely cloud-covered (all-NaN) get the
        # same treatment — partially cloudy frames keep their NaN
        # cells, which show the GEBCO/Natural Earth underlay beneath as
        # honest "no observation" regions (never filled).
        is_gap_frame = (
            spec.variable == "water-storage" and bool(np.all(np.isnan(grid)))
        ) or (
            spec.variable == "ocean-color" and bool(np.all(np.isnan(grid)))
        )
        if is_gap_frame:
            if spec.variable == "ocean-color":
                _draw_gap_panel(ax_m, st,
                                title="NO OCEAN COLOR OBSERVATION",
                                subtitle="fully cloud-covered this period")
            else:
                _draw_gap_panel(ax_m, st)
        ax_m.set_xlim(lon_min, lon_max)
        ax_m.set_ylim(lat_min, lat_max)
        ax_m.tick_params(colors=st["muted"], labelsize=14)
        for spine in ax_m.spines.values():
            spine.set_color(st["muted"])
        cbar = fig.colorbar(mesh, ax=ax_m, orientation="horizontal", pad=0.04, shrink=0.9)
        cbar.set_label(unit, color=st["muted"], fontsize=16)
        cbar.ax.tick_params(colors=st["muted"], labelsize=14)
        # Burned-in timestamp.
        ax_m.text(
            0.97,
            0.03,
            frame_date.isoformat(),
            transform=ax_m.transAxes,
            ha="right",
            va="bottom",
            fontsize=22,
            weight="bold",
            color="white",
            bbox=dict(boxstyle="round,pad=0.4", fc="black", ec="none", alpha=0.55),
        )
        # Contour overlays (e.g. isobars over a wind map): levels recomputed
        # per frame from that frame's data range so contours always fit.
        for ov_name, ov_arr in overlay_arrays.items():
            cfg = _OVERLAY_DEFAULTS.get(ov_name, {"step": 1.0, "unit": ""})
            step = cfg["step"]
            ov_grid = ov_arr[i]
            if np.all(np.isnan(ov_grid)):
                continue
            lo = float(np.nanmin(ov_grid))
            hi = float(np.nanmax(ov_grid))
            levels = np.arange(np.floor(lo / step) * step,
                               np.ceil(hi / step) * step + step / 2,
                               step)
            if len(levels) < 2:
                continue
            cs = ax_m.contour(
                lons, lats, ov_grid, levels=levels,
                colors="white", linewidths=1.4, alpha=0.85,
            )
            ax_m.clabel(cs, fmt=f"%g {cfg['unit']}".strip(),
                        fontsize=12, colors="white")

        # Time-series panel -------------------------------------------------
        ax_s = fig.add_axes(rects["chart"])
        ax_s.set_facecolor(st["axes"])
        ax_s.tick_params(colors=st["muted"], labelsize=14)
        for spine in ax_s.spines.values():
            spine.set_color(st["muted"])
        ax_s.set_title("Time series", color=st["text"], fontsize=20, loc="left", pad=8)
        if series_norm is None:
            ax_s.text(
                0.5,
                0.5,
                "No time series provided\n(chart panel placeholder)",
                transform=ax_s.transAxes,
                ha="center",
                va="center",
                fontsize=20,
                color=st["muted"],
                linespacing=1.6,
            )
            ax_s.set_xlim(0, 1)
            ax_s.set_ylim(0, 1)
        else:
            s_dates, s_vals = series_norm
            ax_s.plot(mdates.date2num(s_dates), s_vals, color=st["series"], lw=3)
            ax_s.axvline(mdates.date2num(frame_date), color=st["text"], lw=2, ls="--", alpha=0.9)
            # Playhead dot: nearest series value at/below the frame date.
            at = [j for j, d in enumerate(s_dates) if d <= frame_date]
            if at:
                j = at[-1]
                ax_s.scatter(
                    [mdates.date2num(s_dates[j])],
                    [s_vals[j]],
                    s=90,
                    color=st["series"],
                    edgecolors=st["text"],
                    zorder=5,
                )
            ax_s.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax_s.set_ylabel(unit, color=st["muted"], fontsize=16)

        # Story caption (data-driven headline, when requested) ------------
        if story_caps and not is_gap_frame:
            cap = next(
                (c for c in story_caps
                 if c["frame_start"] <= n <= c["frame_end"]),
                None,
            )
            if cap is not None:
                _draw_caption(fig, st, cap["text"], rect=rects["caption"])

        # Footer ------------------------------------------------------------
        footer_var = base_variable if is_derived else spec.variable
        ov = list(getattr(spec, "overlays", ()))
        if ov:
            footer_var += " + " + " + ".join(f"{o} contours" for o in ov)
        ctx = list(getattr(spec, "context", ()))
        if ctx:
            footer_var += " + " + " + ".join(f"{c} context" for c in ctx)
        if spec.variable == "water-storage":
            # The anomaly baseline is part of the data's identity:
            # every value is vs the 2004–2009 time-mean removed by CSR.
            footer_var += " · anomaly vs 2004–2009 mean"
            n_gaps = sum(gap_flags)
            if n_gaps:
                footer_var += f" · {n_gaps} month{'s' if n_gaps != 1 else ''} with no GRACE data"
        if spec.variable == "ocean-color":
            # The log scale is part of the map's identity (chlorophyll
            # is lognormal); cloud gaps are honest "no observation".
            footer_var += " · log scale"
            n_gaps = sum(gap_flags)
            if n_gaps:
                footer_var += (f" · {n_gaps} frame{'s' if n_gaps != 1 else ''} "
                               "with no ocean-color observation (cloud)")
        if is_derived and getattr(spec, "derived_note", None):
            # The anomaly baseline is part of the data's identity:
            # "+2°C" is meaningless without "vs 1991–2020".
            footer_var += f" · {spec.derived_note}"
        _draw_footer(fig, spec, st, footer_var, rect=rects["footer"])

        frame_path = out / f"frame_{n + 1:04d}.png"
        fig.savefig(frame_path, facecolor=fig.get_facecolor())
        plt.close(fig)
        frames.append(str(frame_path.resolve()))
        timestamps.append(frame_date.isoformat())
        gap_flags.append(is_gap_frame)

    manifest = {
        "schema": SCHEMA_ID,
        "spec": spec.to_dict(),
        "frames": frames,
        "timestamps": timestamps,
        "render": {
            "layout": layout,
            "style": style,
            "cmap": var_cmap,
            "cmap_requested": cmap,
            "cmap_overridden": cmap is not None,
            "story_captions": story_caps,
            "canvas": canvas_info,
            "vmin": vmin,
            "vmax": vmax,
            "n_frames": len(frames),
            "engine": f"survey-viz {__version__}",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "underlay": underlay_status,
            # GRACE provenance: the anomaly baseline is part of the
            # data's identity, and gap months are listed explicitly
            # (they render as NO GRACE OBSERVATION panels).
            "anomaly_baseline": (
                "2004–2009 time-mean removed (CSR RL06.3 "
                "time_mean_removed 2004.000–2009.999)"
                if spec.variable == "water-storage" else None),
            # Derived-product provenance (survey-derive): which
            # anomaly family was rendered and against what baseline.
            "derived": (
                {"variable": spec.variable,
                 "note": getattr(spec, "derived_note", None)}
                if is_derived else None),
            "gap_months": [ts for ts, gap in zip(timestamps, gap_flags)
                           if gap] or None,
            # Ocean-color provenance: the log scale is part of the
            # map's identity (chlorophyll is lognormally distributed),
            # all-NaN frames are listed explicitly (they render as NO
            # OCEAN COLOR OBSERVATION panels), and partial cloud gaps
            # are honest NaN — the underlay shows through them.
            "log_scale": spec.variable == "ocean-color" or None,
            "gap_frames": ([ts for ts, gap in zip(timestamps, gap_flags)
                            if gap]
                           if spec.variable == "ocean-color" else None) or None,
            "context": list(getattr(spec, "context", ())) or None,
        },
    }
    manifest_path = out / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return frames, str(manifest_path.resolve())


# ---------------------------------------------------------------------------
# Storm-track renderer (variable == "storm-tracks", source "ibtracs")
# ---------------------------------------------------------------------------
# Track geometry is not a scalar grid, so it gets a dedicated path
# rather than going through the pcolormesh renderer above. The field is
# duck-typed: either a survey-currents ``StormField`` (``.tracks`` with
# ``.times``/``.lats``/``.lons``/``.winds``/``.name``/``.sid``) or the
# dict from ``StormField.to_dict()`` (``"storm_tracks"`` key). Optional
# ERA5 context grids (e.g. wind contours) ride in
# ``field["overlay_grids"][name]`` as ``{"times", "lats", "lons",
# "grid"}`` dicts; when a requested overlay is absent the renderer
# degrades gracefully and records it in the manifest — never a crash.

#: Saffir-Simpson category colors. Mirrors
#: ``currents.storms.SSHS_COLORS`` (survey-currents >= 0.11.0); kept as a
#: local copy because the renderer never hard-imports the peer — the
#: mapping itself is documented in docs/STORMS.md.
_SSHS_COLORS = {
    "TD": "#5ebaff",    # tropical depression
    "TS": "#00e676",    # tropical storm
    "C1": "#ffea00",    # category 1
    "C2": "#ff9100",    # category 2
    "C3": "#ff3d00",    # category 3
    "C4": "#d500f9",    # category 4
    "C5": "#ff1744",    # category 5
    "unknown": "#9aa3b2",
}

#: Legend order for the category chips.
_SSHS_ORDER = ("TD", "TS", "C1", "C2", "C3", "C4", "C5", "unknown")

_SSHS_LABELS = {
    "TD": "Tropical depression (<34 kt)",
    "TS": "Tropical storm (34–63 kt)",
    "C1": "Category 1 (64–82 kt)",
    "C2": "Category 2 (83–95 kt)",
    "C3": "Category 3 (96–112 kt)",
    "C4": "Category 4 (113–136 kt)",
    "C5": "Category 5 (≥137 kt)",
    "unknown": "Unknown intensity",
}


def _sshs_category(wind_kt: float) -> str:
    """Saffir-Simpson code for ``wind_kt`` (mirrors currents.storms).

    Boundaries (kt): TD <34, TS 34–63, C1 64–82, C2 83–95, C3 96–112,
    C4 113–136, C5 ≥137. Formally based on 1-minute sustained winds;
    IBTrACS USA-agency values are the most defensible input — see
    docs/STORMS.md.
    """
    try:
        w = float(wind_kt)
    except (TypeError, ValueError):
        return "unknown"
    if not w == w or w < 0:
        return "unknown"
    if w < 34:
        return "TD"
    if w < 64:
        return "TS"
    if w < 83:
        return "C1"
    if w < 96:
        return "C2"
    if w < 113:
        return "C3"
    if w < 137:
        return "C4"
    return "C5"


def _storm_time(value: Any) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day,
                            tzinfo=_dt.timezone.utc)
    return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _normalize_storm_field(field: Any) -> List[Dict[str, Any]]:
    """Return track dicts: name/sid/times/lats/lons/winds (numpy arrays)."""
    _, _, np = _require_plotting()
    if isinstance(field, dict):
        raw_tracks = field.get("storm_tracks", [])
    else:
        raw_tracks = getattr(field, "tracks", [])
    tracks: List[Dict[str, Any]] = []
    for raw in raw_tracks:
        if isinstance(raw, dict):
            times = [_storm_time(t) for t in raw.get("times", [])]
            lats = np.asarray(raw.get("lats", []), dtype=float)
            lons = np.asarray(raw.get("lons", []), dtype=float)
            winds = np.asarray(
                [np.nan if w is None else w for w in raw.get("winds", [])],
                dtype=float)
            name = str(raw.get("name", "") or "")
            sid = str(raw.get("sid", "") or "")
        else:
            times = [_storm_time(t) for t in (raw.times or [])]
            lats = np.asarray(raw.lats, dtype=float)
            lons = np.asarray(raw.lons, dtype=float)
            winds = np.asarray(raw.winds, dtype=float)
            name = str(getattr(raw, "name", "") or "")
            sid = str(getattr(raw, "sid", "") or "")
        n = len(times)
        if n == 0:
            continue
        tracks.append({
            "name": name, "sid": sid, "times": times,
            "lats": np.asarray(lats, dtype=float).reshape(n),
            "lons": np.asarray(lons, dtype=float).reshape(n),
            "winds": np.asarray(winds, dtype=float).reshape(n),
        })
    return tracks


def _normalize_storm_overlays(field: Any) -> Dict[str, Dict[str, Any]]:
    """Return {overlay name: {times, lats, lons, grid}} for ERA5 context.

    Accepts the ``"overlay_grids"`` dict on a dict field or the
    ``.overlay_grids`` attribute on an object field. Each entry is
    either a ``{"times", "lats", "lons", "grid"}`` dict (the pipeline
    contract) or a plain (nt, ny, nx) array drawn against
    ``"overlay_lats"``/``"overlay_lons"`` carried alongside.
    """
    _, _, np = _require_plotting()
    if isinstance(field, dict):
        src = field.get("overlay_grids") or {}
        ov_lats = field.get("overlay_lats")
        ov_lons = field.get("overlay_lons")
    else:
        src = getattr(field, "overlay_grids", None) or {}
        ov_lats = getattr(field, "overlay_lats", None)
        ov_lons = getattr(field, "overlay_lons", None)
    out: Dict[str, Dict[str, Any]] = {}
    for name, entry in dict(src).items():
        if isinstance(entry, dict) and "grid" in entry:
            grid = np.asarray(entry["grid"], dtype=float)
            lats = np.asarray(entry.get("lats", []), dtype=float)
            lons = np.asarray(entry.get("lons", []), dtype=float)
            times = [_storm_time(t).date()
                     for t in entry.get("times", range(grid.shape[0]))]
        else:
            grid = np.asarray(entry, dtype=float)
            if ov_lats is None or ov_lons is None:
                raise TypeError(
                    f"storm overlay {name!r}: plain grid arrays need "
                    "'overlay_lats'/'overlay_lons' alongside; use the "
                    "{'times','lats','lons','grid'} dict form instead")
            lats = np.asarray(ov_lats, dtype=float)
            lons = np.asarray(ov_lons, dtype=float)
            times = [_storm_time(t).date()
                     for t in range(grid.shape[0])]
        if grid.ndim != 3:
            raise ValueError(
                f"storm overlay {name!r}: expected a (ntime, nlat, nlon) "
                f"grid, got shape {grid.shape}")
        out[str(name)] = {"times": times, "lats": lats, "lons": lons,
                          "grid": grid}
    return out


def _storm_frame_dates(spec) -> List[_dt.date]:
    """Frame dates for the storm renderer (cumulative display)."""
    start = _coerce_date(spec.start)
    end = _coerce_date(spec.end)
    dates: List[_dt.date] = []
    if spec.cadence == "monthly":
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            import calendar as _cal
            last = _cal.monthrange(y, m)[1]
            dates.append(_dt.date(y, m, min(15, last)))
            m += 1
            if m > 12:
                m, y = 1, y + 1
    elif spec.cadence == "yearly":
        for y in range(start.year, end.year + 1):
            dates.append(_dt.date(y, 7, 1))
    else:  # daily
        d = start
        while d <= end:
            dates.append(d)
            d += _dt.timedelta(days=1)
    dates = [d for d in dates if start <= d <= end]
    if not dates:
        dates = [end]
    return _subsample(dates)


def _storm_runs(track: Dict[str, Any], cutoff: _dt.date) -> List[Tuple[str, list]]:
    """Contiguous same-category polyline runs with fixes on/before ``cutoff``.

    Returns ``[(sshs_code, [(lon, lat), ...]), ...]``. Segments whose
    endpoints jump more than 180° in longitude (dateline crossings)
    break the run — the wrap line is never drawn.
    """
    times, lats, lons, winds = (track["times"], track["lats"],
                               track["lons"], track["winds"])
    runs: List[Tuple[str, list]] = []
    cur: Optional[List] = None  # [code, [(lon, lat), ...]]
    for i in range(len(times) - 1):
        if times[i + 1].date() > cutoff:
            break
        la0, lo0, la1, lo1 = lats[i], lons[i], lats[i + 1], lons[i + 1]
        if not (la0 == la0 and lo0 == lo0 and la1 == la1 and lo1 == lo1):
            if cur is not None:
                runs.append((cur[0], cur[1]))
                cur = None
            continue
        if abs(lo1 - lo0) > 180.0:
            # Dateline crossing: never draw the false wrap line.
            if cur is not None:
                runs.append((cur[0], cur[1]))
                cur = None
            continue
        w0 = winds[i] if winds[i] == winds[i] else float("nan")
        w1 = winds[i + 1] if winds[i + 1] == winds[i + 1] else float("nan")
        seg_w = max(w0, w1) if (w0 == w0 or w1 == w1) else float("nan")
        if not seg_w == seg_w:
            code = "unknown"
        else:
            code = _sshs_category(seg_w)
        pt0, pt1 = (float(lo0), float(la0)), (float(lo1), float(la1))
        if cur is not None and cur[0] == code and cur[1][-1] == pt0:
            cur[1].append(pt1)
        else:
            if cur is not None:
                runs.append((cur[0], cur[1]))
            cur = [code, [pt0, pt1]]
    if cur is not None:
        runs.append((cur[0], cur[1]))
    return runs


def _render_storm_tracks(spec, field, series, out_dir: str,
                         layout: str, style: str,
                         want_underlay: bool,
                         cmap: Optional[str] = None,
                         story_captions: bool = False,
                         canvas=None,
                         ) -> Tuple[List[str], str]:
    """Render IBTrACS storm tracks: cumulative track frames + manifest."""
    plt, mdates, np = _require_plotting()
    # Categorical Saffir-Simpson encoding: a user cmap is validated
    # (typos fail fast) but never applied — recorded in the manifest.
    _validate_cmap(cmap, plt)
    from matplotlib.lines import Line2D

    tracks = _normalize_storm_field(field)
    overlays = _normalize_storm_overlays(field)
    frame_dates = _storm_frame_dates(spec)
    requested = [str(o) for o in (getattr(spec, "overlays", None) or ())]
    era5_context = {name: ("ok" if name in overlays else "absent")
                    for name in requested}

    underlay_rgba, underlay_extent, coastline_segs, underlay_status = \
        _fetch_underlay_once(spec, plt, np, want_underlay)

    st = _STYLE[style]
    unit = _VARIABLE_UNITS.get(spec.variable, "")
    region = get_region(spec.region_key)
    region_name = region["name"] if region else spec.region_key
    lon_min, lat_min, lon_max, lat_max = spec.bbox

    # Overall wind range (for the manifest; tracks are categorical).
    all_winds = np.concatenate([t["winds"] for t in tracks]) \
        if tracks else np.array([])
    valid = all_winds[all_winds == all_winds]
    wind_range = (float(np.min(valid)), float(np.max(valid))) \
        if valid.size else (None, None)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames: List[str] = []
    timestamps: List[str] = []

    fig_w, fig_h, rects, canvas_info = _resolve_canvas(canvas)

    for n, frame_date in enumerate(frame_dates):
        fig = plt.figure(figsize=(fig_w / 100, fig_h / 100), dpi=100)
        fig.patch.set_facecolor(st["face"])
        _draw_title_block(fig, spec, st, region_name, rects["title"])

        # Map panel ------------------------------------------------------
        ax_m = fig.add_axes(rects["map"])
        ax_m.set_facecolor(st["axes"])
        if underlay_rgba is not None:
            ax_m.imshow(underlay_rgba, extent=underlay_extent,
                        origin="upper", aspect="auto", zorder=1)
        # Optional ERA5 context contours (e.g. wind) under the tracks.
        for ov_name in requested:
            ov = overlays.get(ov_name)
            if ov is None:
                continue
            cfg = _OVERLAY_DEFAULTS.get(ov_name, {"step": 1.0, "unit": ""})
            step = cfg["step"]
            # Nearest overlay timestep at/below the frame date.
            idx = max([j for j, d in enumerate(ov["times"])
                       if d <= frame_date] or [0])
            ov_grid = ov["grid"][idx]
            if np.all(np.isnan(ov_grid)):
                continue
            lo = float(np.nanmin(ov_grid))
            hi = float(np.nanmax(ov_grid))
            levels = np.arange(np.floor(lo / step) * step,
                               np.ceil(hi / step) * step + step / 2,
                               step)
            if len(levels) < 2:
                continue
            cs = ax_m.contour(
                ov["lons"], ov["lats"], ov_grid, levels=levels,
                colors="white", linewidths=1.2, alpha=0.7, zorder=4)
            ax_m.clabel(cs, fmt=f"%g {cfg['unit']}".strip(),
                        fontsize=12, colors="white")

        # Tracks (above terrain tint and coastlines).
        shown_cats: List[str] = []
        peak_wind = float("nan")
        any_shown = False
        for track in tracks:
            runs = _storm_runs(track, frame_date)
            if not runs:
                continue
            any_shown = True
            shown = [t for t in track["times"] if t.date() <= frame_date]
            # Genesis marker + name label at the first shown fix.
            g0 = next(i for i, t in enumerate(track["times"])
                      if t.date() <= frame_date)
            ax_m.scatter([track["lons"][g0]], [track["lats"][g0]],
                         s=110, c="white", edgecolors="black",
                         linewidths=1.5, zorder=8)
            label = track["name"] or track["sid"] or "unnamed"
            ax_m.text(track["lons"][g0], track["lats"][g0], f"  {label}",
                      fontsize=15, weight="bold", color="white", zorder=9,
                      bbox=dict(boxstyle="round,pad=0.35", fc="black",
                                ec="none", alpha=0.6))
            for code, pts in runs:
                if code not in shown_cats:
                    shown_cats.append(code)
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                ax_m.plot(xs, ys, color=_SSHS_COLORS[code], lw=3.0,
                          zorder=6, solid_capstyle="round", solid_joinstyle="round")
            tw = track["winds"][:len(shown)]
            tw = tw[tw == tw]
            if tw.size:
                peak_wind = max(peak_wind if peak_wind == peak_wind else -1.0,
                                float(np.max(tw)))
        if coastline_segs:
            coast_color = _UNDERLAY_COAST_COLOR[style]
            for seg in coastline_segs:
                ax_m.plot(seg["lons"], seg["lats"], color=coast_color,
                          lw=1.2, zorder=5, solid_capstyle="round")
        if not any_shown:
            ax_m.text(0.5, 0.5, "No storm fixes in this window",
                      transform=ax_m.transAxes, ha="center", va="center",
                      fontsize=22, color=st["muted"])
        ax_m.set_xlim(lon_min, lon_max)
        ax_m.set_ylim(lat_min, lat_max)
        ax_m.tick_params(colors=st["muted"], labelsize=14)
        for spine in ax_m.spines.values():
            spine.set_color(st["muted"])
        # Category legend (only categories actually shown).
        if shown_cats:
            handles = [Line2D([0], [0], color=_SSHS_COLORS[c], lw=4)
                       for c in _SSHS_ORDER if c in shown_cats]
            labels = [_SSHS_LABELS[c] for c in _SSHS_ORDER if c in shown_cats]
            leg = ax_m.legend(handles, labels, loc="lower left",
                              fontsize=13, framealpha=0.75,
                              facecolor=st["face"], edgecolor=st["muted"])
            for txt in leg.get_texts():
                txt.set_color(st["text"])
        # Burned-in timestamp + peak-wind readout.
        ax_m.text(
            0.97, 0.03, frame_date.isoformat(), transform=ax_m.transAxes,
            ha="right", va="bottom", fontsize=22, weight="bold",
            color="white",
            bbox=dict(boxstyle="round,pad=0.4", fc="black", ec="none", alpha=0.55))
        if peak_wind == peak_wind:
            ax_m.text(
                0.03, 0.97,
                f"Peak {peak_wind:.0f} {unit} ({_SSHS_LABELS[_sshs_category(peak_wind)]})",
                transform=ax_m.transAxes, ha="left", va="top",
                fontsize=18, weight="bold", color="white",
                bbox=dict(boxstyle="round,pad=0.4", fc="black",
                          ec="none", alpha=0.55))

        # Time-series panel -------------------------------------------------
        ax_s = fig.add_axes(rects["chart"])
        ax_s.set_facecolor(st["axes"])
        ax_s.tick_params(colors=st["muted"], labelsize=14)
        for spine in ax_s.spines.values():
            spine.set_color(st["muted"])
        ax_s.set_title("Time series", color=st["text"], fontsize=20,
                       loc="left", pad=8)
        series_norm = _normalize_series(series)
        if series_norm is None:
            ax_s.text(
                0.5, 0.5,
                "No time series provided\n(chart panel placeholder)",
                transform=ax_s.transAxes, ha="center", va="center",
                fontsize=20, color=st["muted"], linespacing=1.6)
            ax_s.set_xlim(0, 1)
            ax_s.set_ylim(0, 1)
        else:
            s_dates, s_vals = series_norm
            ax_s.plot(mdates.date2num(s_dates), s_vals,
                      color=st["series"], lw=3)
            ax_s.axvline(mdates.date2num(frame_date), color=st["text"],
                         lw=2, ls="--", alpha=0.9)
            at = [j for j, d in enumerate(s_dates) if d <= frame_date]
            if at:
                j = at[-1]
                ax_s.scatter([mdates.date2num(s_dates[j])], [s_vals[j]],
                             s=90, color=st["series"],
                             edgecolors=st["text"], zorder=5)
            ax_s.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax_s.set_ylabel(unit, color=st["muted"], fontsize=16)

        # Footer ------------------------------------------------------------
        footer_var = spec.variable
        if requested:
            drawn = [o for o in requested if o in overlays]
            missing = [o for o in requested if o not in overlays]
            if drawn:
                footer_var += " + " + " + ".join(f"{o} contours" for o in drawn)
            if missing:
                footer_var += f" ({', '.join(missing)} context unavailable)"
        _draw_footer(fig, spec, st, footer_var, rect=rects["footer"])

        frame_path = out / f"frame_{n + 1:04d}.png"
        fig.savefig(frame_path, facecolor=fig.get_facecolor())
        plt.close(fig)
        frames.append(str(frame_path.resolve()))
        timestamps.append(frame_date.isoformat())

    storm_meta = []
    for t in tracks:
        tw = t["winds"][t["winds"] == t["winds"]]
        mw = float(np.max(tw)) if tw.size else float("nan")
        storm_meta.append({
            "name": t["name"], "sid": t["sid"],
            "n_fixes": len(t["times"]),
            "max_wind_kt": mw,
            "max_category": _sshs_category(mw),
        })

    manifest = {
        "schema": SCHEMA_ID,
        "spec": spec.to_dict(),
        "frames": frames,
        "timestamps": timestamps,
        "render": {
            "layout": layout,
            "style": style,
            "cmap": "sshs-categorical",
            "cmap_requested": cmap,
            "story_captions": [],
            "canvas": canvas_info,
            "story_captions_note": (
                "requested but not applied — categorical variables keep "
                "fixed scientific encodings"
                if story_captions else None),
            "vmin": wind_range[0],
            "vmax": wind_range[1],
            "n_frames": len(frames),
            "engine": f"survey-viz {__version__}",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "underlay": underlay_status,
            "storms": storm_meta,
            "era5_context": era5_context,
        },
    }
    manifest_path = out / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return frames, str(manifest_path.resolve())


# ---------------------------------------------------------------------------
# Gage renderer (variable == "streamflow", source "usgs")
# ---------------------------------------------------------------------------
# Gages are point/time-series data, not a scalar grid, so they get a
# dedicated path modeled on _render_storm_tracks: markers over the
# GEBCO/Natural Earth underlay, colored by the current discharge
# percentile category (USGS WaterWatch-style classes), with a
# hydrograph panel below and an optional "tp" precipitation contour
# overlay as best-effort context. The field arrives as
# GageField.to_dict() (key "gage_records"), passed through directly —
# never converted through a scalar grid. An empty record list renders
# an explicit empty-frame message (never fabricated data); the
# manifest carries the empty status and the USGS provenance.

#: Primary parameter for marker coloring / hydrographs (discharge).
_GAGE_PRIMARY = "00060"

#: Percentile classes (lower, upper, label, color) — USGS
#: WaterWatch-style: much-below / below / normal / above /
#: much-above normal. A gage with no value at the frame date is drawn
#: in _GAGE_NODATA_COLOR ("Not ranked").
_GAGE_CATEGORIES = (
    (0.0, 10.0, "Much below normal", "#d73027"),
    (10.0, 25.0, "Below normal", "#fc8d59"),
    (25.0, 75.0, "Normal", "#1a9850"),
    (75.0, 90.0, "Above normal", "#91bfdb"),
    (90.0, 100.0001, "Much above normal", "#4575b4"),
)
_GAGE_NODATA_COLOR = "#9e9e9e"
_GAGE_NODATA_LABEL = "Not ranked"


def _gage_unit_label(field: Dict[str, Any]) -> str:
    """Display unit for the gage field's primary parameter."""
    units = str(field.get("units", "native"))
    params = field.get("parameters") or [_GAGE_PRIMARY]
    primary = str(params[0])
    if primary == "00065":
        return "m" if units == "si" else "ft"
    return "m³/s" if units == "si" else "ft³/s"


def _normalize_gage_field(field: Any) -> List[Dict[str, Any]]:
    """Return gage dicts: site_no/site_name/lat/lon + per-param series.

    Accepts the ``"gage_records"`` list from ``GageField.to_dict()``
    (dates as ISO strings, missing values as null) or a live
    ``GageField`` object's ``.records``. Series become ``{"dates":
    [date, ...], "values": np.ndarray, "unit": str}`` with nulls as
    NaN — gaps stay gaps.
    """
    _, _, np = _require_plotting()
    if isinstance(field, dict):
        raw = field.get("gage_records", []) or []
    else:
        raw = getattr(field, "records", []) or []
    gages: List[Dict[str, Any]] = []
    for rec in raw:
        if isinstance(rec, dict):
            site_no = str(rec.get("site_no", ""))
            site_name = str(rec.get("site_name", "") or site_no)
            lat = rec.get("lat")
            lon = rec.get("lon")
            raw_series = rec.get("series", {}) or {}
        else:
            site_no = str(getattr(rec, "site_no", ""))
            site_name = str(getattr(rec, "site_name", "") or site_no)
            lat = getattr(rec, "lat", None)
            lon = getattr(rec, "lon", None)
            raw_series = getattr(rec, "series", {}) or {}
        series: Dict[str, Dict[str, Any]] = {}
        for code, s in raw_series.items():
            if isinstance(s, dict):
                dates = s.get("dates", [])
                vals = s.get("values", [])
            else:
                dates = getattr(s, "dates", [])
                vals = getattr(s, "values", [])
            dlist = [_coerce_date(d) for d in dates]
            vlist = [float("nan") if v is None else float(v) for v in vals]
            unit = s.get("unit") if isinstance(s, dict) \
                else getattr(s, "unit", "")
            series[str(code)] = {"dates": dlist,
                                 "values": np.asarray(vlist, dtype=float),
                                 "unit": unit}
        gages.append({"site_no": site_no, "site_name": site_name,
                      "lat": float(lat) if lat is not None else float("nan"),
                      "lon": float(lon) if lon is not None else float("nan"),
                      "drain_area_sqmi": (rec.get("drain_area_sqmi")
                                          if isinstance(rec, dict)
                                          else getattr(rec, "drain_area_sqmi",
                                                       None)),
                      "series": series})
    return gages


def _value_percentile(values: np.ndarray, value: float) -> Optional[float]:
    """Percentile of ``value`` within finite ``values`` (0–100).

    Same documented rule as
    ``GageRecord.percentile_of_record``: ``100 * (# below) / (n - 1)``;
    a single-value record ranks 50; no finite values -> None.
    """
    _, _, np = _require_plotting()
    finite = values[np.isfinite(values)]
    if finite.size == 0 or not value == value:
        return None
    if finite.size == 1:
        return 50.0
    return 100.0 * float(np.sum(finite < value)) / (finite.size - 1)


def _gage_category(percentile: Optional[float]) -> Tuple[str, str]:
    """(label, color) for a percentile, or the not-ranked style."""
    if percentile is None:
        return _GAGE_NODATA_LABEL, _GAGE_NODATA_COLOR
    for lo, hi, label, color in _GAGE_CATEGORIES:
        if lo <= percentile < hi:
            return label, color
    return _GAGE_NODATA_LABEL, _GAGE_NODATA_COLOR


def _gage_frame_dates(spec) -> List[_dt.date]:
    """Frame dates for the gage renderer (daily, capped at MAX_FRAMES)."""
    # Reuse the storm renderer's date logic: daily branch covers the
    # USGS daily cadence (cadence is pinned to "daily" by the parser).
    return _storm_frame_dates(spec)


def _regional_median_series(gages: List[Dict[str, Any]],
                            start: _dt.date, end: _dt.date,
                            parameter: str = _GAGE_PRIMARY
                            ) -> Tuple[List[_dt.date], np.ndarray]:
    """Daily median across gages (documented multi-gage hydrograph rule).

    For each date in [start, end], the median of the finite values
    across all gages carrying ``parameter``; dates with no valid value
    anywhere are NaN. Mirrors ``GageField.regional_median`` for the
    dict field form the renderer receives.
    """
    _, _, np = _require_plotting()
    ndays = (end - start).days + 1
    dates = [start + _dt.timedelta(days=k) for k in range(ndays)]
    grid = np.full((len(gages), ndays), np.nan)
    for i, g in enumerate(gages):
        s = g["series"].get(parameter)
        if s is None:
            continue
        vals = np.asarray(s["values"], dtype=float)
        for d, v in zip(s["dates"], vals):
            k = (d - start).days
            if 0 <= k < ndays and v == v:
                grid[i, k] = v
    with np.errstate(all="ignore"):
        med = np.nanmedian(grid, axis=0)
    return dates, med


def _draw_tp_overlay(ax_m, ov, frame_date) -> bool:
    """Draw white precipitation contour lines from an overlay dict.

    ``ov`` is a ``{"times", "lats", "lons", "grid"}`` dict (the
    pipeline attaches it as ``"overlay_grids"``). The nearest overlay
    timestep at/below the frame date is drawn. Returns True when
    contours were drawn.
    """
    _, _, np = _require_plotting()
    times = [str(t)[:10] for t in ov.get("times", [])]
    fdate = frame_date.isoformat() if hasattr(frame_date, "isoformat") \
        else str(frame_date)[:10]
    idx = max([j for j, d in enumerate(times) if d <= fdate] or [0])
    grid = np.asarray(ov["grid"][idx], dtype=float)
    if not np.any(np.isfinite(grid)):
        return False
    step = _OVERLAY_DEFAULTS.get("tp", {}).get("step", 5.0)
    lo = float(np.nanmin(grid))
    hi = float(np.nanmax(grid))
    levels = np.arange(np.floor(lo / step) * step,
                       np.ceil(hi / step) * step + step / 2, step)
    if len(levels) < 2:
        return False
    lats = np.asarray(ov["lats"], dtype=float)
    lons = np.asarray(ov["lons"], dtype=float)
    cs = ax_m.contour(lons, lats, grid, levels=levels, colors="white",
                      linewidths=1.2, alpha=0.7, zorder=4)
    ax_m.clabel(cs, fmt="%g mm", fontsize=12, colors="white")
    return True


def _render_gage_field(spec, field, series, out_dir: str,
                       layout: str, style: str,
                       want_underlay: bool,
                       cmap: Optional[str] = None,
                       story_captions: bool = False,
                       canvas=None,
                       ) -> Tuple[List[str], str]:
    """Render USGS streamgage daily values: gage markers + hydrograph.

    Map panel: one marker per gage at its (lon, lat), colored by the
    current value's percentile category within its own record (USGS
    WaterWatch-style classes); gages with no value at the frame date
    draw gray ("Not ranked"). Hydrograph panel: the series for
    ``spec.gage_site`` when it names a known site, else the
    documented regional-median rule (daily median across gages).
    Optional ``"tp"`` precipitation context draws as white contours
    (the pipeline attaches it as ``"overlay_grids"``; a requested but
    unfetched overlay is recorded "absent", never silently dropped).
    No gages -> an explicit empty-frame message and an ``"empty"``
    manifest status — never fabricated data.
    """
    plt, mdates, np = _require_plotting()
    # Categorical WaterWatch-percentile encoding: a user cmap is
    # validated (typos fail fast) but never applied — recorded in the
    # manifest.
    _validate_cmap(cmap, plt)
    from matplotlib.lines import Line2D

    gages = _normalize_gage_field(field)
    overlays = _normalize_storm_overlays(field)  # same dict contract
    frame_dates = _gage_frame_dates(spec)
    requested = [str(o) for o in (getattr(spec, "overlays", None) or ())]
    tp_context = {name: ("ok" if name in overlays else "absent")
                  for name in requested}
    field_dict = field if isinstance(field, dict) else {}
    unit = _gage_unit_label(field_dict)

    underlay_rgba, underlay_extent, coastline_segs, underlay_status = \
        _fetch_underlay_once(spec, plt, np, want_underlay)

    st = _STYLE[style]
    region = get_region(spec.region_key)
    region_name = region["name"] if region else spec.region_key
    lon_min, lat_min, lon_max, lat_max = spec.bbox

    # Hydrograph source: a selected gage when spec.gage_site names one,
    # else the documented regional-median rule.
    gage_site = str(getattr(spec, "gage_site", "") or "").strip()
    selected = next((g for g in gages if g["site_no"] == gage_site), None)
    start = _coerce_date(spec.start)
    end = _coerce_date(spec.end)
    if selected is not None:
        s = selected["series"].get(_GAGE_PRIMARY, {})
        hydro_dates = list(s.get("dates", []))
        hydro_vals = np.asarray(s.get("values", []), dtype=float)
        hydro_title = (f"Hydrograph — {selected['site_name']} "
                       f"({selected['site_no']})")
        hydro_rule = f"site:{selected['site_no']}"
    else:
        hydro_dates, hydro_vals = _regional_median_series(
            gages, start, end, _GAGE_PRIMARY)
        n_hg = len(gages)
        hydro_title = (f"Regional median discharge — {n_hg} "
                       f"gage{'s' if n_hg != 1 else ''}")
        hydro_rule = "regional-median"
    hydro_vals = np.asarray(hydro_vals, dtype=float)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames: List[str] = []
    timestamps: List[str] = []
    empty = not gages

    fig_w, fig_h, rects, canvas_info = _resolve_canvas(canvas)

    for n, frame_date in enumerate(frame_dates):
        fig = plt.figure(figsize=(fig_w / 100, fig_h / 100), dpi=100)
        fig.patch.set_facecolor(st["face"])
        _draw_title_block(fig, spec, st, region_name, rects["title"])

        # Map panel ------------------------------------------------------
        ax_m = fig.add_axes(rects["map"])
        ax_m.set_facecolor(st["axes"])
        if underlay_rgba is not None:
            ax_m.imshow(underlay_rgba, extent=underlay_extent,
                        origin="upper", aspect="auto", zorder=1)
        # Optional precipitation context contours (IMERG/ERA5 tp) under
        # the markers.
        for ov_name in requested:
            ov = overlays.get(ov_name)
            if ov is None:
                continue
            cfg = _OVERLAY_DEFAULTS.get(ov_name, {"step": 1.0, "unit": ""})
            step = cfg["step"]
            idx = max([j for j, d in enumerate(ov["times"])
                       if d <= frame_date] or [0])
            ov_grid = ov["grid"][idx]
            if np.all(np.isnan(ov_grid)):
                continue
            lo = float(np.nanmin(ov_grid))
            hi = float(np.nanmax(ov_grid))
            levels = np.arange(np.floor(lo / step) * step,
                               np.ceil(hi / step) * step + step / 2,
                               step)
            if len(levels) < 2:
                continue
            cs = ax_m.contour(
                ov["lons"], ov["lats"], ov_grid, levels=levels,
                colors="white", linewidths=1.2, alpha=0.7, zorder=4)
            ax_m.clabel(cs, fmt=f"%g {cfg['unit']}".strip(),
                        fontsize=12, colors="white")

        shown_cats: List[Tuple[str, str]] = []
        if empty:
            reason = ""
            prov = field_dict.get("provenance", {}) or {}
            if prov.get("empty_reason"):
                reason = str(prov["empty_reason"])
            ax_m.text(
                0.5, 0.5,
                "No USGS streamgages with daily data\nin this window",
                transform=ax_m.transAxes, ha="center", va="center",
                fontsize=24, weight="bold", color=st["text"],
                linespacing=1.6)
            if reason:
                ax_m.text(0.5, 0.38, reason[:110],
                          transform=ax_m.transAxes, ha="center",
                          va="center", fontsize=13, color=st["muted"],
                          wrap=True)
        else:
            for g in gages:
                s = g["series"].get(_GAGE_PRIMARY)
                value = float("nan")
                if s is not None:
                    for d, v in zip(s["dates"], s["values"]):
                        if d == frame_date:
                            value = float(v)
                            break
                pct = (_value_percentile(s["values"], value)
                       if s is not None else None)
                label, color = _gage_category(pct)
                if (label, color) not in shown_cats:
                    shown_cats.append((label, color))
                if g["lat"] == g["lat"] and g["lon"] == g["lon"]:
                    ax_m.scatter([g["lon"]], [g["lat"]], s=170,
                                 c=color, edgecolors="black",
                                 linewidths=1.5, zorder=8)
            if selected is not None:
                ax_m.text(selected["lon"], selected["lat"],
                          f"  {selected['site_no']}",
                          fontsize=14, weight="bold", color="white",
                          zorder=9,
                          bbox=dict(boxstyle="round,pad=0.35", fc="black",
                                    ec="none", alpha=0.6))
        if coastline_segs:
            coast_color = _UNDERLAY_COAST_COLOR[style]
            for seg in coastline_segs:
                ax_m.plot(seg["lons"], seg["lats"], color=coast_color,
                          lw=1.2, zorder=5, solid_capstyle="round")
        ax_m.set_xlim(lon_min, lon_max)
        ax_m.set_ylim(lat_min, lat_max)
        ax_m.tick_params(colors=st["muted"], labelsize=14)
        for spine in ax_m.spines.values():
            spine.set_color(st["muted"])
        if shown_cats:
            handles = [Line2D([0], [0], marker="o", color="w",
                              markerfacecolor=color, markersize=12,
                              markeredgecolor="black")
                       for _, color in shown_cats]
            labels = [label for label, _ in shown_cats]
            leg = ax_m.legend(handles, labels, loc="lower left",
                              fontsize=13, framealpha=0.75,
                              facecolor=st["face"], edgecolor=st["muted"])
            for txt in leg.get_texts():
                txt.set_color(st["text"])
        # Burned-in timestamp.
        ax_m.text(
            0.97, 0.03, frame_date.isoformat(), transform=ax_m.transAxes,
            ha="right", va="bottom", fontsize=22, weight="bold",
            color="white",
            bbox=dict(boxstyle="round,pad=0.4", fc="black", ec="none",
                      alpha=0.55))

        # Hydrograph panel -------------------------------------------------
        ax_s = fig.add_axes(rects["chart"])
        ax_s.set_facecolor(st["axes"])
        ax_s.tick_params(colors=st["muted"], labelsize=14)
        for spine in ax_s.spines.values():
            spine.set_color(st["muted"])
        ax_s.set_title(hydro_title, color=st["text"], fontsize=20,
                       loc="left", pad=8)
        if hydro_dates and np.any(np.isfinite(hydro_vals)):
            ax_s.plot(mdates.date2num(hydro_dates), hydro_vals,
                      color=st["series"], lw=3)
            ax_s.axvline(mdates.date2num(frame_date), color=st["text"],
                         lw=2, ls="--", alpha=0.9)
            at = [j for j, d in enumerate(hydro_dates) if d <= frame_date]
            if at:
                j = at[-1]
                if hydro_vals[j] == hydro_vals[j]:
                    ax_s.scatter([mdates.date2num(hydro_dates[j])],
                                 [hydro_vals[j]], s=90,
                                 color=st["series"],
                                 edgecolors=st["text"], zorder=5)
            ax_s.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax_s.set_ylabel(f"discharge ({unit})", color=st["muted"],
                            fontsize=16)
        else:
            ax_s.text(0.5, 0.5, "No discharge values in this window",
                      transform=ax_s.transAxes, ha="center", va="center",
                      fontsize=20, color=st["muted"])
            ax_s.set_xlim(0, 1)
            ax_s.set_ylim(0, 1)

        # Footer ------------------------------------------------------------
        footer_var = spec.variable
        if requested:
            drawn = [o for o in requested if o in overlays]
            missing = [o for o in requested if o not in overlays]
            if drawn:
                footer_var += " + " + " + ".join(f"{o} contours"
                                                 for o in drawn)
            if missing:
                footer_var += (f" ({', '.join(missing)} context "
                               "unavailable)")
        if empty:
            footer_var += " · no gages in window"
        _draw_footer(fig, spec, st, footer_var, rect=rects["footer"])

        frame_path = out / f"frame_{n + 1:04d}.png"
        fig.savefig(frame_path, facecolor=fig.get_facecolor())
        plt.close(fig)
        frames.append(str(frame_path.resolve()))
        timestamps.append(frame_date.isoformat())

    gage_meta = []
    for g in gages:
        s = g["series"].get(_GAGE_PRIMARY, {})
        vals = np.asarray(s.get("values", []), dtype=float)
        n_valid = int(np.sum(np.isfinite(vals)))
        latest = float("nan")
        if n_valid:
            idx = np.flatnonzero(np.isfinite(vals))
            latest = float(vals[int(idx[-1])])
        pct = _value_percentile(vals, latest)
        label, _ = _gage_category(pct)
        gage_meta.append({
            "site_no": g["site_no"], "site_name": g["site_name"],
            "lat": g["lat"], "lon": g["lon"],
            "drain_area_sqmi": g.get("drain_area_sqmi"),
            "n_values": n_valid,
            "latest_value": (None if latest != latest else latest),
            "latest_percentile": pct,
            "category": label,
            "unit": unit,
        })

    manifest = {
        "schema": SCHEMA_ID,
        "spec": spec.to_dict(),
        "frames": frames,
        "timestamps": timestamps,
        "render": {
            "layout": layout,
            "style": style,
            "cmap": "gage-percentile",
            "cmap_requested": cmap,
            "story_captions": [],
            "canvas": canvas_info,
            "story_captions_note": (
                "requested but not applied — categorical variables keep "
                "fixed scientific encodings"
                if story_captions else None),
            "vmin": None,
            "vmax": None,
            "n_frames": len(frames),
            "engine": f"survey-viz {__version__}",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "underlay": underlay_status,
            "gages": gage_meta,
            "hydrograph": hydro_rule,
            "hydrograph_title": hydro_title,
            "precipitation_context": tp_context or None,
            "empty": empty,
            "usgs": dict(field_dict.get("provenance", {})),
        },
    }
    manifest_path = out / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return frames, str(manifest_path.resolve())

# Quake renderer (variable == "earthquakes", source "comcat")
# ---------------------------------------------------------------------------
# Quake events are point/time data, not a scalar grid, so they get a
# dedicated path modeled on _render_gage_field: markers over the
# GEBCO/Natural Earth underlay, sized by magnitude and colored by
# hypocentral-depth bin, with a daily event-count time-series panel
# below and a largest-events ranking readout. The field arrives as a
# survey-currents QuakeField (or its to_dict() form — key
# "quake_events"), passed through directly — never converted through
# a scalar grid. Frames are CUMULATIVE: frame n shows every event with
# time <= that frame date (a swarm unfolds over the reel, and a quake
# stays visible once it has happened). An empty event list renders an
# explicit empty-frame message (never fabricated markers); every
# manifest records the catalog honesty note: ComCat is observed
# events, NOT a forecast hazard model (see docs/EARTHQUAKES.md).

#: Hypocentral-depth bins for marker coloring (standard
#: seismological bins: shallow < 70 km, intermediate 70–300 km,
#: deep >= 300 km).
_QUAKE_DEPTH_BINS = (
    ("shallow", 0.0, 70.0, "Shallow (<70 km)", "#ffd166"),
    ("intermediate", 70.0, 300.0, "Intermediate (70–300 km)", "#f4845f"),
    ("deep", 300.0, float("inf"), "Deep (≥300 km)", "#e5383b"),
)
_QUAKE_DEPTH_UNKNOWN = ("unknown", "Unknown depth", "#9aa3b2")

#: Marker-sizing rule (documented, tested): marker AREA (points²) =
#: _QUAKE_AREA0 * 10 ** (_QUAKE_AREA_EXP * (M - _QUAKE_AREA_REF)).
#: With the defaults, each +1 magnitude unit multiplies the marker
#: area by 10**0.75 ≈ 5.62 — a damped visual proxy for seismic-moment
#: scaling (moment ∝ 10**(1.5*M) would let a great quake swallow the
#: map). Events with no reported magnitude draw at the fixed
#: _QUAKE_AREA_UNKNOWN size.
_QUAKE_AREA0 = 80.0
_QUAKE_AREA_REF = 4.0
_QUAKE_AREA_EXP = 0.75
_QUAKE_AREA_UNKNOWN = 40.0

#: Magnitudes shown as marker chips in the legend.
_QUAKE_LEGEND_MAGS = (4.0, 5.0, 6.0, 7.0)

#: Honesty note recorded in every earthquakes manifest: the USGS
#: Earthquake Catalog is a catalog of OBSERVED events, not a forecast
#: hazard model — so "seismic hazard" descriptions render the
#: observed catalog, never a hazard forecast.
_QUAKE_CATALOG_NOTE = (
    "USGS Earthquake Catalog (ComCat) is a catalog of observed events, "
    "not a forecast hazard model."
)


def _quake_depth_bin(depth_km: Any) -> Tuple[str, str, str]:
    """(code, label, color) for a hypocentral depth in km."""
    if depth_km is None:
        return _QUAKE_DEPTH_UNKNOWN
    try:
        d = float(depth_km)
    except (TypeError, ValueError):
        return _QUAKE_DEPTH_UNKNOWN
    if not d == d or d < 0:
        return _QUAKE_DEPTH_UNKNOWN
    for code, lo, hi, label, color in _QUAKE_DEPTH_BINS:
        if lo <= d < hi:
            return code, label, color
    return _QUAKE_DEPTH_UNKNOWN


def _quake_marker_area(magnitude: Any) -> float:
    """Marker area (points²) for a magnitude, per the documented rule.

    Area = ``_QUAKE_AREA0 * 10 ** (_QUAKE_AREA_EXP * (M - _QUAKE_AREA_REF))``;
    events with no/NaN magnitude draw at ``_QUAKE_AREA_UNKNOWN``.
    """
    if magnitude is None:
        return float(_QUAKE_AREA_UNKNOWN)
    try:
        m = float(magnitude)
    except (TypeError, ValueError):
        return float(_QUAKE_AREA_UNKNOWN)
    if not m == m:
        return float(_QUAKE_AREA_UNKNOWN)
    return float(_QUAKE_AREA0 * 10.0 ** (_QUAKE_AREA_EXP * (m - _QUAKE_AREA_REF)))


def _quake_time(value: Any) -> _dt.datetime:
    """Parse an event time (datetime/date/ISO string, 'Z' tolerated)."""
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day,
                            tzinfo=_dt.timezone.utc)
    return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _normalize_quake_field(field: Any) -> Tuple[List[Dict[str, Any]], int]:
    """Return (event dicts, n_skipped): parsed event rows with datetimes.

    Accepts the ``"quake_events"`` list from ``QuakeField.to_dict()``
    (times as ISO strings, missing depths/magnitudes as null) or a
    live ``QuakeField`` object's ``.events``. Events whose time cannot
    be parsed are skipped and counted (never guessed at a time).
    """
    if isinstance(field, dict):
        raw = field.get("quake_events", []) or []
    else:
        raw = getattr(field, "events", []) or []
    events: List[Dict[str, Any]] = []
    skipped = 0
    for raw_ev in raw:
        if isinstance(raw_ev, dict):
            event_id = str(raw_ev.get("event_id", "") or "")
            time_raw = raw_ev.get("time")
            lat = raw_ev.get("lat")
            lon = raw_ev.get("lon")
            depth_km = raw_ev.get("depth_km")
            magnitude = raw_ev.get("magnitude")
            mag_type = str(raw_ev.get("mag_type", "") or "")
            place = str(raw_ev.get("place", "") or "")
            event_type = str(raw_ev.get("event_type", "") or "")
        else:
            event_id = str(getattr(raw_ev, "event_id", "") or "")
            time_raw = getattr(raw_ev, "time", None)
            lat = getattr(raw_ev, "lat", None)
            lon = getattr(raw_ev, "lon", None)
            depth_km = getattr(raw_ev, "depth_km", None)
            magnitude = getattr(raw_ev, "magnitude", None)
            mag_type = str(getattr(raw_ev, "mag_type", "") or "")
            place = str(getattr(raw_ev, "place", "") or "")
            event_type = str(getattr(raw_ev, "event_type", "") or "")
        try:
            when = _quake_time(time_raw)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if depth_km is not None:
            try:
                depth_km = float(depth_km)
            except (TypeError, ValueError):
                depth_km = None
        if magnitude is not None:
            try:
                magnitude = float(magnitude)
            except (TypeError, ValueError):
                magnitude = None
        events.append({
            "event_id": event_id, "time": when,
            "lat": (float(lat) if lat is not None else float("nan")),
            "lon": (float(lon) if lon is not None else float("nan")),
            "depth_km": depth_km, "magnitude": magnitude,
            "mag_type": mag_type, "place": place,
            "event_type": event_type,
        })
    return events, skipped


def _quake_frame_dates(spec) -> List[_dt.date]:
    """Frame dates for the quake renderer (cumulative daily display)."""
    # Reuse the storm renderer's date logic: the daily branch covers
    # the ComCat daily cadence (cadence is pinned to "daily" by the
    # parser), capped at MAX_FRAMES by _subsample.
    return _storm_frame_dates(spec)


def _quake_daily_counts(events: List[Dict[str, Any]],
                        start: _dt.date, end: _dt.date
                        ) -> Tuple[List[_dt.date], List[int]]:
    """Daily event counts over [start, end] (documented cumulative rule).

    Events outside the spec window are ignored (never drawn, never
    counted) — the frame window is the spec's [start, end].
    """
    ndays = (end - start).days + 1
    dates = [start + _dt.timedelta(days=k) for k in range(ndays)]
    counts = [0] * ndays
    for ev in events:
        k = (ev["time"].date() - start).days
        if 0 <= k < ndays:
            counts[k] += 1
    return dates, counts


def _quake_largest(events: List[Dict[str, Any]], n: int = 5
                   ) -> List[Dict[str, Any]]:
    """Top-``n`` events by magnitude (magnitude None sorts last)."""
    return sorted(
        events,
        key=lambda e: (e["magnitude"] is None,
                       -(e["magnitude"] or 0.0)),
    )[:n]


def _render_quake_field(spec, field, series, out_dir: str,
                        layout: str, style: str,
                        want_underlay: bool,
                        cmap: Optional[str] = None,
                        story_captions: bool = False,
                        canvas=None,
                        ) -> Tuple[List[str], str]:
    """Render USGS Earthquake Catalog events: cumulative quake frames.

    Map panel: one marker per event at its (lon, lat), marker area per
    the documented magnitude power law, colored by hypocentral-depth
    bin (shallow < 70 km / intermediate 70–300 km / deep ≥ 300 km).
    Frames are cumulative: each frame shows every event with time <=
    that frame date. The lower panels hold the daily event-count
    time series (with a cumulative playhead) and the largest-events
    ranking readout (top 5 by magnitude, with place + depth). The
    GEBCO/Natural Earth underlay draws beneath (topographic context —
    quake depth is hypocentral, not seafloor); coastlines draw above
    the tint. An empty event list renders an explicit empty-frame
    message — never fabricated markers — and every manifest records
    the catalog honesty note (observed events, not a forecast hazard
    model).
    """
    plt, mdates, np = _require_plotting()
    # Categorical hypocentral-depth-bin encoding: a user cmap is
    # validated (typos fail fast) but never applied — recorded in the
    # manifest.
    _validate_cmap(cmap, plt)
    from matplotlib.lines import Line2D

    events, n_skipped = _normalize_quake_field(field)
    frame_dates = _quake_frame_dates(spec)
    field_dict = field if isinstance(field, dict) else {}
    prov = field_dict.get("provenance", {}) or {}
    min_magnitude = field_dict.get("min_magnitude",
                                   getattr(field, "min_magnitude", None))
    event_type = field_dict.get("event_type",
                                getattr(field, "event_type", None))

    underlay_rgba, underlay_extent, coastline_segs, underlay_status = \
        _fetch_underlay_once(spec, plt, np, want_underlay)

    st = _STYLE[style]
    region = get_region(spec.region_key)
    region_name = region["name"] if region else spec.region_key
    lon_min, lat_min, lon_max, lat_max = spec.bbox
    start = _coerce_date(spec.start)
    end = _coerce_date(spec.end)
    count_dates, count_vals = _quake_daily_counts(events, start, end)
    cum_vals = [sum(count_vals[:i + 1]) for i in range(len(count_vals))]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames: List[str] = []
    timestamps: List[str] = []
    empty = not events

    fig_w, fig_h, rects, canvas_info = _resolve_canvas(canvas, flavor="quake")

    for n, frame_date in enumerate(frame_dates):
        # Cumulative display: every event with time <= frame date.
        shown = [ev for ev in events if ev["time"].date() <= frame_date]
        # Largest markers first so small markers stay visible on top.
        shown.sort(key=lambda e: _quake_marker_area(e["magnitude"]),
                   reverse=True)
        largest = _quake_largest(shown, 5)
        cum_now = cum_vals[max(
            [i for i, d in enumerate(count_dates) if d <= frame_date]
            or [0])]

        fig = plt.figure(figsize=(fig_w / 100, fig_h / 100), dpi=100)
        fig.patch.set_facecolor(st["face"])
        _draw_title_block(fig, spec, st, region_name, rects["title"])

        # Map panel ------------------------------------------------------
        ax_m = fig.add_axes(rects["map"])
        ax_m.set_facecolor(st["axes"])
        if underlay_rgba is not None:
            ax_m.imshow(underlay_rgba, extent=underlay_extent,
                        origin="upper", aspect="auto", zorder=1)
        shown_bins: List[Tuple[str, str]] = []
        shown_bin_codes = set()
        if empty:
            reason = str(prov.get("empty_reason", "") or "")
            ax_m.text(
                0.5, 0.55,
                "No earthquakes in this\ncatalog window",
                transform=ax_m.transAxes, ha="center", va="center",
                fontsize=26, weight="bold", color=st["text"],
                linespacing=1.6)
            if reason:
                ax_m.text(0.5, 0.40, reason[:120],
                          transform=ax_m.transAxes, ha="center",
                          va="center", fontsize=13, color=st["muted"],
                          wrap=True)
        elif not shown:
            ax_m.text(
                0.5, 0.55, "No events yet in this window",
                transform=ax_m.transAxes, ha="center", va="center",
                fontsize=24, weight="bold", color=st["muted"])
        else:
            for ev in shown:
                if ev["lat"] != ev["lat"] or ev["lon"] != ev["lon"]:
                    continue  # no location: never plotted
                bin_code, bin_label, bin_color = _quake_depth_bin(ev["depth_km"])
                if bin_code not in shown_bin_codes:
                    shown_bin_codes.add(bin_code)
                    shown_bins.append((bin_label, bin_color))
                ax_m.scatter([ev["lon"]], [ev["lat"]],
                             s=_quake_marker_area(ev["magnitude"]),
                             c=bin_color, edgecolors="black",
                             linewidths=1.2, alpha=0.9, zorder=8)
        if coastline_segs:
            coast_color = _UNDERLAY_COAST_COLOR[style]
            for seg in coastline_segs:
                ax_m.plot(seg["lons"], seg["lats"], color=coast_color,
                          lw=1.2, zorder=5, solid_capstyle="round")
        ax_m.set_xlim(lon_min, lon_max)
        ax_m.set_ylim(lat_min, lat_max)
        ax_m.tick_params(colors=st["muted"], labelsize=14)
        for spine in ax_m.spines.values():
            spine.set_color(st["muted"])
        # Depth-bin legend (only bins actually shown) + magnitude
        # marker chips (M4–M7, sized by the documented rule).
        if shown_bins:
            handles = [Line2D([0], [0], marker="o", color="w",
                              markerfacecolor=color, markersize=11,
                              markeredgecolor="black")
                       for _, color in shown_bins]
            labels = [label for label, _ in shown_bins]
            mag_handles = [Line2D(
                [0], [0], marker="o", color="w",
                markerfacecolor="white", markersize=min(
                    26, max(6, (_quake_marker_area(m) ** 0.5) / 2.4)),
                markeredgecolor="black") for m in _QUAKE_LEGEND_MAGS]
            mag_labels = [f"M{m:.0f}" for m in _QUAKE_LEGEND_MAGS]
            handles.extend(mag_handles)
            labels.extend(mag_labels)
            leg = ax_m.legend(handles, labels, loc="lower left",
                              fontsize=12, framealpha=0.75,
                              facecolor=st["face"], edgecolor=st["muted"],
                              ncol=2)
            for txt in leg.get_texts():
                txt.set_color(st["text"])
        # Burned-in timestamp + cumulative count readout.
        ax_m.text(
            0.97, 0.03, frame_date.isoformat(), transform=ax_m.transAxes,
            ha="right", va="bottom", fontsize=22, weight="bold",
            color="white",
            bbox=dict(boxstyle="round,pad=0.4", fc="black", ec="none",
                      alpha=0.55))
        if not empty:
            ax_m.text(
                0.03, 0.97, f"{cum_now} event{'s' if cum_now != 1 else ''}",
                transform=ax_m.transAxes, ha="left", va="top",
                fontsize=18, weight="bold", color="white",
                bbox=dict(boxstyle="round,pad=0.4", fc="black",
                          ec="none", alpha=0.55))

        # Largest-events ranking panel ------------------------------------
        ax_r = fig.add_axes(rects["ranking"])
        ax_r.axis("off")
        ax_r.set_facecolor(st["face"])
        ax_r.text(0.0, 0.92, "Largest events", ha="left", va="top",
                  fontsize=22, weight="bold", color=st["text"],
                  transform=ax_r.transAxes)
        if largest:
            lines = []
            for ev in largest:
                mag = ev["magnitude"]
                mag_s = f"M{mag:.1f}" if mag is not None else "M?"
                depth = ev["depth_km"]
                depth_s = (f"{depth:.0f} km deep"
                           if depth is not None and depth == depth
                           else "depth unknown")
                place = (ev["place"] or ev["event_id"] or "—")[:44]
                et = f" [{ev['event_type']}]" if ev["event_type"] else ""
                lines.append(f"{mag_s} · {place} · {depth_s}{et}")
            ax_r.text(0.0, 0.78, "\n".join(lines), ha="left", va="top",
                      fontsize=16, color=st["text"], transform=ax_r.transAxes,
                      linespacing=1.9)
        else:
            ax_r.text(0.0, 0.78, "—", ha="left", va="top",
                      fontsize=16, color=st["muted"],
                      transform=ax_r.transAxes)

        # Daily-count time-series panel ------------------------------------
        ax_s = fig.add_axes(rects["chart"])
        ax_s.set_facecolor(st["axes"])
        ax_s.tick_params(colors=st["muted"], labelsize=14)
        for spine in ax_s.spines.values():
            spine.set_color(st["muted"])
        ax_s.set_title("Daily event counts", color=st["text"],
                       fontsize=20, loc="left", pad=8)
        if count_dates and any(c != 0 for c in count_vals):
            xs = mdates.date2num(count_dates)
            ax_s.bar(xs, count_vals, color=st["series"], width=1.0,
                     alpha=0.85)
            ax_s.axvline(mdates.date2num(frame_date), color=st["text"],
                         lw=2, ls="--", alpha=0.9)
            at = [j for j, d in enumerate(count_dates) if d <= frame_date]
            if at:
                j = at[-1]
                ax_s.scatter([mdates.date2num(count_dates[j])],
                             [cum_vals[j]], s=90,
                             color=st["series"],
                             edgecolors=st["text"], zorder=5)
            ax_s.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax_s.set_ylabel("events/day", color=st["muted"], fontsize=16)
        else:
            ax_s.text(0.5, 0.5, "No events in this window",
                      transform=ax_s.transAxes, ha="center", va="center",
                      fontsize=20, color=st["muted"])
            ax_s.set_xlim(0, 1)
            ax_s.set_ylim(0, 1)

        # Footer ------------------------------------------------------------
        footer_var = spec.variable
        if empty:
            footer_var += " · no events in window"
        else:
            top = largest[0] if largest else None
            top_s = (f"M{top['magnitude']:.1f}"
                     if top and top["magnitude"] is not None else "M?")
            footer_var += (f" · {len(events)} events · largest {top_s} · "
                           "observed events — not a forecast")
        # The prescribed footer wording is long; a smaller size keeps it
        # on the 1080 px figure (see _draw_footer's fontsize option).
        _draw_footer(fig, spec, st, footer_var, fontsize=13,
                     rect=rects["footer"])

        frame_path = out / f"frame_{n + 1:04d}.png"
        fig.savefig(frame_path, facecolor=fig.get_facecolor())
        plt.close(fig)
        frames.append(str(frame_path.resolve()))
        timestamps.append(frame_date.isoformat())

    largest_meta = []
    for ev in _quake_largest(events, 5):
        largest_meta.append({
            "event_id": ev["event_id"],
            "time": ev["time"].isoformat(),
            "lat": ev["lat"], "lon": ev["lon"],
            "depth_km": ev["depth_km"],
            "magnitude": ev["magnitude"],
            "mag_type": ev["mag_type"],
            "place": ev["place"],
            "event_type": ev["event_type"],
        })

    manifest = {
        "schema": SCHEMA_ID,
        "spec": spec.to_dict(),
        "frames": frames,
        "timestamps": timestamps,
        "render": {
            "layout": layout,
            "style": style,
            "cmap": "quake-magnitude",
            "cmap_requested": cmap,
            "story_captions": [],
            "story_captions_note": (
                "requested but not applied — categorical variables keep "
                "fixed scientific encodings"
                if story_captions else None),
            "vmin": None,
            "vmax": None,
            "n_frames": len(frames),
            "engine": f"survey-viz {__version__}",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "underlay": underlay_status,
            "cumulative": True,
            "n_events": len(events),
            "daily_counts": count_vals,
            "largest": largest_meta,
            "min_magnitude": min_magnitude,
            "event_type": event_type,
            "skipped_no_time": n_skipped,
            "empty": empty,
            "catalog_note": _QUAKE_CATALOG_NOTE,
            "comcat": dict(prov),
            "canvas": canvas_info,
        },
    }
    manifest_path = out / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return frames, str(manifest_path.resolve())
