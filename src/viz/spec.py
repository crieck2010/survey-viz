"""VizSpec: the declarative spec for one map-visualization request.

A VizSpec is the contract between the deterministic description parser
(:mod:`viz.parser`), the renderer (:mod:`viz.render`), and downstream peers
(survey-currents feeds it, survey-animate encodes its frames). It is plain
data: JSON-serializable via :meth:`to_dict` / :meth:`from_dict`, validated
on construction.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

KNOWN_VARIABLES = ("sst", "currents", "chlorophyll",
                   "wind", "msl", "t2m", "tp")
KNOWN_CADENCES = ("daily", "monthly", "yearly")
KNOWN_LAYOUTS = ("reel-vertical",)
KNOWN_STYLES = ("reel-dark", "light")
#: Fetch adapters (see :mod:`viz.sources`). "" means "not pinned —
#: resolve the regional default at fetch time" (v0.1.0 specs have no
#: source and keep working unchanged).
KNOWN_SOURCES = ("glsea", "oisst", "mur", "era5", "oscar", "cmems-currents")
#: Variables that may appear in ``VizSpec.overlays`` (drawn as contour
#: overlays over the base variable, e.g. isobars over a wind map).
KNOWN_OVERLAYS = ("wind", "msl", "t2m", "tp")
#: Hard cap: at most this many overlays on one spec (v0.3.0 supports one).
MAX_OVERLAYS = 1


def _coerce_date(value: Any, field: str) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(f"VizSpec.{field}: not an ISO date: {value!r}") from exc
    raise TypeError(f"VizSpec.{field}: expected date or ISO string, got {type(value).__name__}")


def _validate_bbox(bbox: Any) -> Tuple[float, float, float, float]:
    try:
        parts = tuple(float(x) for x in bbox)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"VizSpec.bbox: expected 4 numbers, got {bbox!r}") from exc
    if len(parts) != 4:
        raise ValueError(f"VizSpec.bbox: expected 4 numbers, got {len(parts)}")
    lon_min, lat_min, lon_max, lat_max = parts
    if not lon_min < lon_max:
        raise ValueError(f"VizSpec.bbox: lon_min must be < lon_max, got {bbox!r}")
    if not lat_min < lat_max:
        raise ValueError(f"VizSpec.bbox: lat_min must be < lat_max, got {bbox!r}")
    if not (-180.0 <= lon_min <= 180.0 and -180.0 <= lon_max <= 180.0):
        raise ValueError(f"VizSpec.bbox: longitudes must be within [-180, 180], got {bbox!r}")
    if not (-90.0 <= lat_min <= 90.0 and -90.0 <= lat_max <= 90.0):
        raise ValueError(f"VizSpec.bbox: latitudes must be within [-90, 90], got {bbox!r}")
    return parts


@dataclass
class VizSpec:
    """One visualization request.

    Fields:
        title: human-readable title, burned into the frame header.
        region_key: slug into the gazetteer (``data/regions.yaml``).
        bbox: (lon_min, lat_min, lon_max, lat_max) in decimal degrees.
        variable: e.g. ``"sst"``, ``"currents"``, ``"chlorophyll"``.
        start/end: inclusive date range of the visualization.
        cadence: one of ``"daily"``, ``"monthly"``, ``"yearly"``.
        layout: ``"reel-vertical"`` (1080x1920). Only layout implemented in v0.1.0.
        style: ``"reel-dark"`` (default) or ``"light"``.
        vmin/vmax: fixed colormap scale. If None, the renderer computes them
            from the full data range once, so the colormap never flickers
            between frames.
        source: SST fetch adapter pin — ``""`` (default, not pinned),
            ``"glsea"``, ``"oisst"``, ``"mur"``, or ``"era5"``. Empty means
            :func:`viz.sources.resolve_source` picks the regional default
            at fetch time (Great Lakes SST -> GLSEA, other SST -> OISST,
            wind/msl/t2m/tp -> ERA5). The deterministic parser sets
            ``"mur"`` when the description asks for high resolution /
            ultra / coastal detail (SST only).
        overlays: optional contour overlays drawn over the base variable
            map, e.g. ``("msl",)`` for isobars over a wind field (the
            "storm" combination). Max ``MAX_OVERLAYS`` entries, each in
            ``KNOWN_OVERLAYS`` and different from ``variable``.
    """

    title: str
    region_key: str
    bbox: Tuple[float, float, float, float]
    variable: str
    start: _dt.date
    end: _dt.date
    cadence: str
    layout: str = "reel-vertical"
    style: str = "reel-dark"
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    source: str = ""
    overlays: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("VizSpec.title: must be a non-empty string")
        if not isinstance(self.region_key, str) or not self.region_key.strip():
            raise ValueError("VizSpec.region_key: must be a non-empty string")
        self.bbox = _validate_bbox(self.bbox)
        if not isinstance(self.variable, str) or not self.variable.strip():
            raise ValueError("VizSpec.variable: must be a non-empty string")
        self.start = _coerce_date(self.start, "start")
        self.end = _coerce_date(self.end, "end")
        if self.start > self.end:
            raise ValueError(
                f"VizSpec: start ({self.start}) must not be after end ({self.end})"
            )
        if self.cadence not in KNOWN_CADENCES:
            raise ValueError(
                f"VizSpec.cadence: {self.cadence!r} not in {KNOWN_CADENCES}"
            )
        if self.layout not in KNOWN_LAYOUTS:
            raise ValueError(
                f"VizSpec.layout: {self.layout!r} not in {KNOWN_LAYOUTS}"
            )
        if self.style not in KNOWN_STYLES:
            raise ValueError(
                f"VizSpec.style: {self.style!r} not in {KNOWN_STYLES}"
            )
        if self.vmin is not None and self.vmax is not None and not self.vmin < self.vmax:
            raise ValueError(
                f"VizSpec: vmin ({self.vmin}) must be < vmax ({self.vmax})"
            )
        if not isinstance(self.source, str):
            raise TypeError(
                f"VizSpec.source: expected str, got {type(self.source).__name__}")
        self.source = self.source.strip().lower()
        if self.source and self.source not in KNOWN_SOURCES:
            raise ValueError(
                f"VizSpec.source: {self.source!r} not in {KNOWN_SOURCES} "
                "(or empty for the regional default)")
        overlays = tuple(str(o).strip().lower() for o in self.overlays or ())
        for ov in overlays:
            if ov not in KNOWN_OVERLAYS:
                raise ValueError(
                    f"VizSpec.overlays: {ov!r} not in {KNOWN_OVERLAYS}")
            if ov == self.variable:
                raise ValueError(
                    f"VizSpec.overlays: {ov!r} duplicates the base variable")
        if len(overlays) > MAX_OVERLAYS:
            raise ValueError(
                f"VizSpec.overlays: at most {MAX_OVERLAYS} overlay(s), "
                f"got {len(overlays)}")
        # Canonicalize (tuple of str) so == / to_dict are stable.
        self.overlays = overlays

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable dict."""
        return {
            "title": self.title,
            "region_key": self.region_key,
            "bbox": list(self.bbox),
            "variable": self.variable,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "cadence": self.cadence,
            "layout": self.layout,
            "style": self.style,
            "vmin": self.vmin,
            "vmax": self.vmax,
            "source": self.source,
            "overlays": list(self.overlays),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VizSpec":
        """Rebuild from :meth:`to_dict` output (dates may be ISO strings).

        v0.2.0 dicts without ``"overlays"`` load unchanged (default ``()``).
        """
        if not isinstance(data, dict):
            raise TypeError(f"VizSpec.from_dict: expected dict, got {type(data).__name__}")
        kwargs = dict(data)
        return cls(**kwargs)
