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


def render_viz(
    spec,
    field,
    series=None,
    out_dir="frames",
    layout: str = "reel-vertical",
    style: Optional[str] = None,
) -> Tuple[List[str], str]:
    """Render a VizSpec into PNG frames + a frame manifest.

    Returns ``(frames_list, manifest_path)``. ``frames_list`` holds absolute
    file paths in render order; the manifest is directly consumable by
    survey-animate's ``--frames-dir`` / manifest path.
    """
    if layout != "reel-vertical":
        raise ValueError(f"Unknown layout: {layout!r} (only 'reel-vertical' in v0.1.0)")
    style = style or spec.style
    if style not in _STYLE:
        raise ValueError(f"Unknown style: {style!r} (expected one of {sorted(_STYLE)})")

    plt, mdates, np = _require_plotting()

    times, lats, lons, values = _normalize_field(field)
    series_norm = _normalize_series(series)
    frame_idx = _bucket_indices(times, spec.cadence)
    overlay_arrays = _overlay_grids(field, tuple(getattr(spec, "overlays", ())), len(times))

    # Fixed color scale: from the spec when given, else the full data range once.
    vmin = spec.vmin if spec.vmin is not None else float(np.nanmin(values))
    vmax = spec.vmax if spec.vmax is not None else float(np.nanmax(values))
    if not vmin < vmax:
        vmax = vmin + 1.0  # degenerate constant field: avoid a zero-range cmap

    st = _STYLE[style]
    unit = _VARIABLE_UNITS.get(spec.variable, "")
    region = get_region(spec.region_key)
    region_name = region["name"] if region else spec.region_key

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    frames: List[str] = []
    timestamps: List[str] = []
    lon_min, lat_min, lon_max, lat_max = spec.bbox

    for n, i in enumerate(frame_idx):
        frame_date = times[i]
        grid = values[i]

        fig = plt.figure(figsize=(10.8, 19.2), dpi=100)
        fig.patch.set_facecolor(st["face"])

        # Title block -----------------------------------------------------
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

        # Map panel --------------------------------------------------------
        ax_m = fig.add_axes([0.04, 0.36, 0.92, 0.52])
        ax_m.set_facecolor(st["axes"])
        mesh = ax_m.pcolormesh(
            lons, lats, grid, vmin=vmin, vmax=vmax, cmap=st["cmap"], shading="auto"
        )
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
        ax_f = fig.add_axes([0.0, 0.0, 1.0, 0.06])
        ax_f.axis("off")
        ax_f.set_facecolor(st["face"])
        footer_var = spec.variable
        ov = list(getattr(spec, "overlays", ()))
        if ov:
            footer_var += " + " + " + ".join(f"{o} contours" for o in ov)
        ax_f.text(
            0.5,
            0.5,
            f"{footer_var} · {spec.start.isoformat()} → {spec.end.isoformat()}",
            ha="center",
            va="center",
            fontsize=16,
            color=st["muted"],
        )

        frame_path = out / f"frame_{n + 1:04d}.png"
        fig.savefig(frame_path, facecolor=fig.get_facecolor())
        plt.close(fig)
        frames.append(str(frame_path.resolve()))
        timestamps.append(frame_date.isoformat())

    manifest = {
        "schema": SCHEMA_ID,
        "spec": spec.to_dict(),
        "frames": frames,
        "timestamps": timestamps,
        "render": {
            "layout": layout,
            "style": style,
            "cmap": st["cmap"],
            "vmin": vmin,
            "vmax": vmax,
            "n_frames": len(frames),
            "engine": f"survey-viz {__version__}",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        },
    }
    manifest_path = out / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return frames, str(manifest_path.resolve())
