"""Optional universal basemap underlay: GEBCO tint/hillshade + coastlines.

The underlay is drawn *beneath* the variable map in
:func:`viz.render.render_viz` — wherever the variable field is NaN
(e.g. land under an SST field), the terrain tint and coastlines show
through. For the ``"bathymetry"`` / ``"elevation"`` variables the
GEBCO tint is skipped (it *is* the variable) while coastlines are
still drawn.

Design rules:

* **Lazy peer loading** — ``currents.basemaps`` (survey-currents
  >= 0.10.0) is imported inside :func:`fetch_underlay`, never at
  module import, so ``viz`` imports fine without the peer.
* **Never raises** — every failure (missing peer, no network,
  corrupt cache, …) is reported as
  ``{"status": "unavailable", "reason": ...}``; the renderer falls
  back to the plain background.
* **Cached** — the peer already caches GEBCO tiles / Natural Earth
  zips on disk with SHA-256 sidecars; this module adds a small
  in-process cache of the assembled underlay keyed by
  ``(bbox, resolution, scale, include_topo)`` so multi-frame renders
  fetch once.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

#: Underlay result schema version (recorded in the frame manifest).
UNDERLAY_SCHEMA = "survey-viz.underlay/1.0"

#: Coarsest grid we will build for the tint: beyond this many cells per
#: axis the resolution is doubled until it fits.
_MAX_UNDERLAY_CELLS = 720

#: First render in a new region downloads GEBCO tile entries
#: (~500 MB per 90° tile, cached afterwards) — the peer reports that
#: honestly; we never hide it.
_UNDERLAY_TOPO_RES = 0.25

_cache: Dict[Tuple[Any, ...], Dict[str, Any]] = {}


def hillshade(elev: np.ndarray, res_deg: float,
              azimuth_deg: float = 315.0,
              altitude_deg: float = 45.0) -> np.ndarray:
    """Simple shaded relief in [0, 1] from a 2-D elevation grid.

    ``elev`` rows run north -> south (the renderer convention);
    NaN stays NaN so the caller can mask it.
    """
    elev = np.asarray(elev, dtype=float)
    # metres per degree, latitude-corrected for the zonal gradient.
    with np.errstate(invalid="ignore", divide="ignore"):
        dz_dy, dz_dx = np.gradient(elev, res_deg)
    # Scale-free shading: use the gradient direction only, which keeps
    # abyssal plains and mountain fronts comparable.
    az = np.radians(360.0 - azimuth_deg + 90.0)
    alt = np.radians(altitude_deg)
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = np.pi / 2.0 - np.arctan(
            np.hypot(dz_dx, dz_dy) / 1000.0)
        aspect = np.arctan2(-dz_dx, dz_dy)
        shaded = (np.sin(alt) * np.sin(slope)
                  + np.cos(alt) * np.cos(slope) * np.cos(az - aspect))
    out = np.clip((shaded + 1.0) / 2.0, 0.0, 1.0)
    out[np.isnan(elev)] = np.nan
    return out


def _underlay_resolution(bbox: Tuple[float, float, float, float]) -> float:
    """Pick a topo resolution whose grid fits in ``_MAX_UNDERLAY_CELLS``."""
    lon_min, lat_min, lon_max, lat_max = bbox
    res = _UNDERLAY_TOPO_RES
    while ((lon_max - lon_min) / res > _MAX_UNDERLAY_CELLS
           or (lat_max - lat_min) / res > _MAX_UNDERLAY_CELLS):
        res *= 2.0
    return res


def _coastline_scale(bbox: Tuple[float, float, float, float]) -> str:
    lon_min, _lat_min, lon_max, _lat_max = bbox
    return "110m" if (lon_max - lon_min) > 60.0 else "50m"


def _fc_to_segments(fc: Dict[str, Any]) -> List[Dict[str, List[float]]]:
    """GeoJSON LineString/MultiLineString -> [{lons: [...], lats: [...]}]."""
    segments: List[Dict[str, List[float]]] = []
    for feat in fc.get("features", []):
        geom = (feat or {}).get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        lines = []
        if gtype == "LineString":
            lines = [coords]
        elif gtype == "MultiLineString":
            lines = list(coords)
        for line in lines:
            lons = [float(p[0]) for p in line]
            lats = [float(p[1]) for p in line]
            if lons:
                segments.append({"lons": lons, "lats": lats})
    return segments


def _unavailable(reason: str) -> Dict[str, Any]:
    return {
        "schema": UNDERLAY_SCHEMA,
        "status": "unavailable",
        "reason": str(reason)[:300],
        "topo": None,
        "coastlines": [],
        "n_coastline_segments": 0,
        "resolution": None,
        "coastline_scale": None,
        "provenance": {},
    }


def fetch_underlay(bbox: Tuple[float, float, float, float],
                   include_topo: bool = True,
                   timeout: float = 600.0,
                   use_cache: bool = True) -> Dict[str, Any]:
    """Fetch the basemap underlay for ``bbox`` (never raises).

    Returns a dict with ``status`` ``"ok"`` or ``"unavailable"``. On
    ``"ok"``, ``"topo"`` holds ``{"lats", "lons", "values", "resolution",
    "source"}`` (omitted when ``include_topo`` is False — used for the
    bathymetry/elevation variables where GEBCO *is* the variable) and
    ``"coastlines"`` holds Natural Earth coastline segments clipped to
    the bbox.
    """
    lon_min, lat_min, lon_max, lat_max = (float(bbox[0]), float(bbox[1]),
                                          float(bbox[2]), float(bbox[3]))
    res = _underlay_resolution((lon_min, lat_min, lon_max, lat_max))
    scale = _coastline_scale((lon_min, lat_min, lon_max, lat_max))
    key = (round(lon_min, 3), round(lat_min, 3), round(lon_max, 3),
           round(lat_max, 3), res, scale, bool(include_topo))
    if use_cache and key in _cache:
        return _cache[key]

    try:
        from currents.basemaps import (fetch_gebco, fetch_naturalearth)
    except ImportError as exc:
        return _unavailable(
            "survey-currents>=0.10.0 (currents.basemaps) is not installed; "
            f"underlay disabled ({exc})")

    try:
        topo: Optional[Dict[str, Any]] = None
        gebco_prov: Dict[str, Any] = {}
        if include_topo:
            field = fetch_gebco((lon_min, lat_min, lon_max, lat_max),
                                resolution=res, timeout=timeout)
            topo = {
                "lats": [float(v) for v in field.lats],
                "lons": [float(v) for v in field.lons],
                "values": np.asarray(field.elevation,
                                      dtype=float).tolist(),
                "resolution": float(field.resolution),
                "source": field.source,
            }
            gebco_prov = dict(field.provenance)

        collections = fetch_naturalearth(
            (lon_min, lat_min, lon_max, lat_max),
            scale=scale, layers=("coastline",), timeout=timeout)
        coastlines = _fc_to_segments(collections.get("coastline", {}))

        result = {
            "schema": UNDERLAY_SCHEMA,
            "status": "ok",
            "reason": "",
            "topo": topo,
            "coastlines": coastlines,
            "n_coastline_segments": len(coastlines),
            "resolution": res,
            "coastline_scale": scale,
            "provenance": {"gebco": gebco_prov,
                           "naturalearth_scale": scale},
        }
    except Exception as exc:  # never crash the render
        return _unavailable(f"{type(exc).__name__}: {exc}")

    if use_cache:
        _cache[key] = result
    return result


def clear_cache() -> None:
    """Drop the in-process underlay cache (tests)."""
    _cache.clear()
