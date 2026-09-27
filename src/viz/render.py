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

Layout ``"reel-vertical"`` (1080x1920): title block, map panel (pcolormesh,
fixed vmin/vmax so the colormap never flickers, burned-in timestamp),
time-series panel with a playhead line + dot synced to the frame date.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
}

#: Colormaps per variable for the main map panel. Variables not listed
#: fall back to the style default.
_VARIABLE_CMAPS = {
    "bathymetry": "Blues_r",   # deep = dark blue
    "elevation": "terrain",
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
                    values = np.asarray(arr, dtype=float)
                    break
        if values is None:
            for method in _GRID_METHODS:
                if callable(getattr(field, method, None)):
                    fn = getattr(field, method)
                    values = np.stack([np.asarray(fn(i), dtype=float) for i in range(len(times))])
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


def _draw_title_block(fig, spec, st, region_name: str) -> None:
    """Shared title block (map frames and storm-track frames)."""
    ax_t = fig.add_axes([0.0, 0.90, 1.0, 0.10])
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


def _draw_footer(fig, spec, st, footer_var: str) -> None:
    """Shared footer (map frames and storm-track frames)."""
    ax_f = fig.add_axes([0.0, 0.0, 1.0, 0.06])
    ax_f.axis("off")
    ax_f.set_facecolor(st["face"])
    ax_f.text(
        0.5,
        0.5,
        f"{footer_var} · {spec.start.isoformat()} → {spec.end.isoformat()}",
        ha="center",
        va="center",
        fontsize=16,
        color=st["muted"],
    )


def _draw_gap_panel(ax_m, st) -> None:
    """Mark a GRACE gap-month frame as NO DATA (never interpolated)."""
    ax_m.text(
        0.5,
        0.55,
        "NO GRACE OBSERVATION",
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
        "no GRACE/GRACE-FO solution this month",
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
        include_topo=spec.variable not in ("bathymetry", "elevation"))
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


def render_viz(
    spec,
    field,
    series=None,
    out_dir="frames",
    layout: str = "reel-vertical",
    style: Optional[str] = None,
    underlay: Optional[bool] = None,
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
    """
    if layout != "reel-vertical":
        raise ValueError(f"Unknown layout: {layout!r} (only 'reel-vertical' in v0.1.0)")
    style = style or spec.style
    if style not in _STYLE:
        raise ValueError(f"Unknown style: {style!r} (expected one of {sorted(_STYLE)})")

    if spec.variable == "storm-tracks":
        return _render_storm_tracks(
            spec, field, series, out_dir, layout, style,
            underlay if underlay is not None else bool(spec.underlay))

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
    else:
        vmin = spec.vmin if spec.vmin is not None else float(np.nanmin(values))
        vmax = spec.vmax if spec.vmax is not None else float(np.nanmax(values))
        if not vmin < vmax:
            vmax = vmin + 1.0  # degenerate constant field: avoid a zero-range cmap

    st = _STYLE[style]
    var_cmap = _VARIABLE_CMAPS.get(spec.variable, st["cmap"])
    unit = _VARIABLE_UNITS.get(spec.variable, "")
    region = get_region(spec.region_key)
    region_name = region["name"] if region else spec.region_key

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    frames: List[str] = []
    timestamps: List[str] = []
    gap_flags: List[bool] = []
    lon_min, lat_min, lon_max, lat_max = spec.bbox

    for n, i in enumerate(frame_idx):
        frame_date = times[i]
        grid = values[i]

        fig = plt.figure(figsize=(10.8, 19.2), dpi=100)
        fig.patch.set_facecolor(st["face"])

        # Title block -----------------------------------------------------
        _draw_title_block(fig, spec, st, region_name)

        # Map panel --------------------------------------------------------
        ax_m = fig.add_axes([0.04, 0.36, 0.92, 0.52])
        ax_m.set_facecolor(st["axes"])
        # Basemap underlay (beneath the variable): GEBCO tint/hillshade
        # shows wherever the variable field is NaN; coastlines draw over
        # the variable for cartographic context.
        if underlay_rgba is not None:
            ax_m.imshow(underlay_rgba, extent=underlay_extent,
                        origin="upper", aspect="auto", zorder=1)
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
        # visibly marked instead of rendering as an empty map.
        is_gap_frame = spec.variable == "water-storage" and bool(
            np.all(np.isnan(grid)))
        if is_gap_frame:
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
        ax_s = fig.add_axes([0.08, 0.08, 0.84, 0.24])
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

        # Footer ------------------------------------------------------------
        footer_var = spec.variable
        ov = list(getattr(spec, "overlays", ()))
        if ov:
            footer_var += " + " + " + ".join(f"{o} contours" for o in ov)
        if spec.variable == "water-storage":
            # The anomaly baseline is part of the data's identity:
            # every value is vs the 2004–2009 time-mean removed by CSR.
            footer_var += " · anomaly vs 2004–2009 mean"
            n_gaps = sum(gap_flags)
            if n_gaps:
                footer_var += f" · {n_gaps} month{'s' if n_gaps != 1 else ''} with no GRACE data"
        _draw_footer(fig, spec, st, footer_var)

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
            "gap_months": [ts for ts, gap in zip(timestamps, gap_flags)
                           if gap] or None,
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
                         want_underlay: bool) -> Tuple[List[str], str]:
    """Render IBTrACS storm tracks: cumulative track frames + manifest."""
    plt, mdates, np = _require_plotting()
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

    for n, frame_date in enumerate(frame_dates):
        fig = plt.figure(figsize=(10.8, 19.2), dpi=100)
        fig.patch.set_facecolor(st["face"])
        _draw_title_block(fig, spec, st, region_name)

        # Map panel ------------------------------------------------------
        ax_m = fig.add_axes([0.04, 0.36, 0.92, 0.52])
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
        ax_s = fig.add_axes([0.08, 0.08, 0.84, 0.24])
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
        _draw_footer(fig, spec, st, footer_var)

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
