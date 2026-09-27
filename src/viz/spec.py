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
                   "wind", "msl", "t2m", "tp",
                   "fire", "burn-scar",
                   "sea-ice", "land-ice",
                   "night-lights", "power-outage",
                   "bathymetry", "elevation",
                   "country-borders",
                   "storm-tracks",
                   # --- GRACE (survey-currents v0.12.0+, source "grace") ---
                   "water-storage",
                   # Refusal-only variables (documented, no fetch adapter):
                   # "streamflow" (no river-discharge adapter yet —
                   # streamgages are item 11 of the remote-sensing
                   # program) and "sea-level" (satellite altimetry, a
                   # different observable from GRACE TWS).
                   "streamflow", "sea-level")
KNOWN_CADENCES = ("daily", "monthly", "yearly")
KNOWN_LAYOUTS = ("reel-vertical",)
KNOWN_STYLES = ("reel-dark", "light")
#: Fetch adapters (see :mod:`viz.sources`). "" means "not pinned —
#: resolve the regional default at fetch time" (v0.1.0 specs have no
#: source and keep working unchanged).
KNOWN_SOURCES = ("glsea", "oisst", "mur", "era5", "oscar", "cmems-currents",
                 "firms", "nsidc", "imerg", "blackmarble", "gebco", "ibtracs",
                 "grace")
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
            ``"glsea"``, ``"oisst"``, ``"mur"``, ``"era5"``, ``"imerg"``,
            etc. Empty means :func:`viz.sources.resolve_source` picks the
            regional default at fetch time (Great Lakes SST -> GLSEA,
            other SST -> OISST, wind/msl/t2m/tp -> ERA5, tp may be pinned
            to IMERG — see below). The deterministic parser sets
            ``"mur"`` when the description asks for high resolution /
            ultra / coastal detail (SST only), ``"cmems-currents"`` for
            the same wording on currents, and ``"imerg"`` / ``"era5"``
            for precipitation (``"tp"``) requests that name the product
            explicitly or signal recent/observed vs long-record intent.
        source_reason: human-readable reason the parser pinned
            ``source`` (``""`` when the parser did not pin — the
            regional default applies at fetch time). Recorded by the
            parser whenever it pins a source; old spec dicts without
            this key load with ``""``.
        overlays: optional contour overlays drawn over the base variable
            map, e.g. ``("msl",)`` for isobars over a wind field (the
            "storm" combination). Max ``MAX_OVERLAYS`` entries, each in
            ``KNOWN_OVERLAYS`` and different from ``variable``.
        underlay: optional basemap underlay drawn beneath the variable
            map (GEBCO tint/hillshade + Natural Earth coastlines,
            ``True`` by default). The underlay is fetched lazily from
            the survey-currents peer (``currents.basemaps``,
            survey-currents>=0.10.0) and cached; when the peer or the
            data is unavailable the renderer falls back to the plain
            background and records the underlay status in the frame
            manifest — never a crash, never silent wrongness. Set to
            ``False`` for the plain background always. For the
            ``"bathymetry"``/``"elevation"`` variables the GEBCO tint is
            skipped (it *is* the variable) but coastlines are still
            drawn. For ``"storm-tracks"`` the tint/hillshade draws under
            the tracks and coastlines draw above the tint but below the
            tracks.
        storm_name: named-storm selection for ``"storm-tracks"`` specs
            (e.g. ``"katrina"`` — case-insensitive; ``""`` means all
            storms in the bbox/time window). Set by the parser from
            descriptions like "Hurricane Katrina's track".
        storm_rank: intensity-ranking mode for ``"storm-tracks"`` specs:
            ``""`` (default, no ranking) or ``"strongest"`` (the parser
            sets this for "strongest hurricanes" wording; the fetch
            layer keeps the ``storm_top_n`` most intense storms by
            lifetime max sustained wind — see ``docs/STORMS.md``).
        storm_top_n: how many storms ``storm_rank="strongest"`` keeps
            (``None`` = the parser default, 5).
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
    source_reason: str = ""
    overlays: Tuple[str, ...] = ()
    underlay: bool = True
    storm_name: str = ""
    storm_rank: str = ""
    storm_top_n: Optional[int] = None

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
        if not isinstance(self.source_reason, str):
            raise TypeError(
                "VizSpec.source_reason: expected str, got "
                f"{type(self.source_reason).__name__}")
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
        if not isinstance(self.underlay, bool):
            raise TypeError(
                "VizSpec.underlay: expected bool, got "
                f"{type(self.underlay).__name__}")
        self.storm_name = str(self.storm_name or "").strip()
        self.storm_rank = str(self.storm_rank or "").strip().lower()
        if self.storm_rank not in ("", "strongest"):
            raise ValueError(
                f"VizSpec.storm_rank: {self.storm_rank!r} not in "
                "('', 'strongest')")
        if self.storm_top_n is not None:
            if not isinstance(self.storm_top_n, int) or self.storm_top_n < 1:
                raise ValueError(
                    "VizSpec.storm_top_n: expected a positive int, got "
                    f"{self.storm_top_n!r}")

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
            "source_reason": self.source_reason,
            "overlays": list(self.overlays),
            "underlay": self.underlay,
            "storm_name": self.storm_name,
            "storm_rank": self.storm_rank,
            "storm_top_n": self.storm_top_n,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VizSpec":
        """Rebuild from :meth:`to_dict` output (dates may be ISO strings).

        v0.2.0 dicts without ``"overlays"`` load unchanged (default ``()``);
        dicts without ``"source_reason"`` (pre-v0.7.0) load with ``""``;
        dicts without ``"underlay"`` (pre-v0.9.0) load with ``True``;
        dicts without ``"storm_name"`` / ``"storm_rank"`` /
        ``"storm_top_n"`` (pre-v0.10.0) load with ``""`` / ``""`` /
        ``None``.
        """
        if not isinstance(data, dict):
            raise TypeError(f"VizSpec.from_dict: expected dict, got {type(data).__name__}")
        kwargs = dict(data)
        return cls(**kwargs)
