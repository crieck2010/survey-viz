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

KNOWN_VARIABLES = ("sst", "currents",
                   # --- ocean color (survey-currents v0.14.0+, source
                   # "oceancolor"): "ocean-color" is canonical since
                   # v0.13.0; "chlorophyll" is the legacy alias and is
                   # canonicalized to "ocean-color" on spec construction.
                   "ocean-color", "chlorophyll",
                   "wind", "msl", "t2m", "tp",
                   "fire", "burn-scar",
                   "sea-ice", "land-ice",
                   "night-lights", "power-outage",
                   "bathymetry", "elevation",
                   "country-borders",
                   "storm-tracks",
                   # --- GRACE (survey-currents v0.12.0+, source "grace") ---
                   "water-storage",
                   # --- USGS (survey-currents v0.13.0+, source "usgs") ---
                   "streamflow",
                   # --- earthquakes (survey-currents v0.15.0+, source
                   # "comcat"): USGS Earthquake Catalog (ComCat),
                   # rendered as point events, not a scalar grid.
                   "earthquakes",
                   # Refusal-only variables (documented, no fetch adapter):
                   # "sea-level" (satellite altimetry, a different
                   # observable from GRACE TWS).
                   "sea-level")
#: Legacy variable names canonicalized in ``VizSpec.__post_init__``
#: (old serialized specs keep loading; the parser only emits the
#: canonical names).
_VARIABLE_ALIASES = {"chlorophyll": "ocean-color"}
KNOWN_CADENCES = ("daily", "monthly", "yearly")
KNOWN_LAYOUTS = ("reel-vertical",)
KNOWN_STYLES = ("reel-dark", "light")
#: Fetch adapters (see :mod:`viz.sources`). "" means "not pinned —
#: resolve the regional default at fetch time" (v0.1.0 specs have no
#: source and keep working unchanged).
KNOWN_SOURCES = ("glsea", "oisst", "mur", "era5", "oscar", "cmems-currents",
                 "firms", "nsidc", "imerg", "blackmarble", "gebco", "ibtracs",
                 "grace", "usgs", "oceancolor", "comcat", "gfs-wind")
#: Variables that may appear in ``VizSpec.overlays`` (drawn as contour
#: overlays over the base variable, e.g. isobars over a wind map).
KNOWN_OVERLAYS = ("wind", "msl", "t2m", "tp")
#: Hard cap: at most this many overlays on one spec (v0.3.0 supports one).
MAX_OVERLAYS = 1
#: Variables that may appear in ``VizSpec.context`` (companion data the
#: pipeline fetches alongside the base variable — e.g. ``"currents"``
#: next to an ``"ocean-color"`` bloom map, or ``"sst"`` next to it for
#: "bloom conditions". Unlike ``overlays`` these are NOT drawn as
#: contours on the base map: they are fetched, recorded in provenance,
#: named in the frame footer, and handed to downstream peers
#: (survey-flow renders current vectors; survey-animate can encode
#: companion reels). v0.13.0 supports the ocean-color combinations.
KNOWN_CONTEXTS = ("currents", "sst")
#: Hard cap: at most this many context variables on one spec.
MAX_CONTEXTS = 2


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
        variable: e.g. ``"sst"``, ``"currents"``, ``"ocean-color"``.
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
        context: optional companion variables the pipeline fetches
            alongside the base variable, e.g. ``("currents",)`` on an
            ``"ocean-color"`` spec for "phytoplankton bloom with ocean
            currents", or ``("sst",)`` for "bloom conditions"
            (chlorophyll vs temperature). Max ``MAX_CONTEXTS`` entries,
            each in ``KNOWN_CONTEXTS`` and different from ``variable``.
            Context is NOT drawn on the base map — it is fetched,
            recorded in provenance, named in the frame footer, and
            handed to downstream peers (see docs/OCEANCOLOR.md).
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
        underlay_topo: coastlines-only underlay switch (``True`` by
            default). When ``underlay`` is ``True`` and this is ``False``,
            the renderer skips the GEBCO tint/hillshade download (which
            OOM-kills small VMs at global extents) and draws only the
            Natural Earth coastline segments — the peer's
            ``fetch_underlay(include_topo=False)`` path, tiny downloads,
            no heavy rasters. Ignored when ``underlay`` is ``False``.
            Recorded in the frame manifest via the underlay status.
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
        caption: optional custom footer caption, prepended to the frame
            footer ahead of the variable/honesty text (``None`` = default
            footer). ``""`` is treated as ``None``.
        timescale_reason: human-readable reason the parser chose the
            time window via the **survey-timescales** peer
            (survey-timescales v0.1.0+) — e.g. ``"aligned to California
            fire season (May–Oct)"``. ``""`` when the window came from
            an explicit user time phrase or the peer was unavailable
            (then the documented past-1-year default applied). Recorded
            in the frame manifest via ``to_dict`` so the window decision
            is part of run provenance — never metadata-only.
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
    context: Tuple[str, ...] = ()
    underlay: bool = True
    underlay_topo: bool = True
    storm_name: str = ""
    storm_rank: str = ""
    storm_top_n: Optional[int] = None
    caption: Optional[str] = None
    #: Viewer-facing provenance for derived products (survey-derive
    #: v0.1.0+), e.g. ``"anomaly vs 1991–2020 climatology"``. Appended
    #: to the frame footer so the baseline an anomaly is computed
    #: against is part of the image itself — never metadata-only.
    derived_note: Optional[str] = None
    #: Provenance for the time-window decision (survey-timescales
    #: v0.1.0+): the engine's ``reason`` string when the peer suggested
    #: the window, ``""`` when the window came from an explicit user
    #: time phrase or the peer was unavailable. Recorded in the frame
    #: manifest via :meth:`to_dict`.
    timescale_reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("VizSpec.title: must be a non-empty string")
        if not isinstance(self.region_key, str) or not self.region_key.strip():
            raise ValueError("VizSpec.region_key: must be a non-empty string")
        self.bbox = _validate_bbox(self.bbox)
        if not isinstance(self.variable, str) or not self.variable.strip():
            raise ValueError("VizSpec.variable: must be a non-empty string")
        # Legacy alias: pre-v0.13.0 "chlorophyll" specs load as the
        # canonical "ocean-color" (same observable, new adapter).
        self.variable = _VARIABLE_ALIASES.get(
            self.variable.strip().lower(), self.variable.strip())
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
        if not isinstance(self.timescale_reason, str):
            raise TypeError(
                "VizSpec.timescale_reason: expected str, got "
                f"{type(self.timescale_reason).__name__}")
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
        context = tuple(str(c).strip().lower() for c in self.context or ())
        for cx in context:
            if cx not in KNOWN_CONTEXTS:
                raise ValueError(
                    f"VizSpec.context: {cx!r} not in {KNOWN_CONTEXTS}")
            if cx == self.variable:
                raise ValueError(
                    f"VizSpec.context: {cx!r} duplicates the base variable")
        if len(context) > MAX_CONTEXTS:
            raise ValueError(
                f"VizSpec.context: at most {MAX_CONTEXTS} context variable(s), "
                f"got {len(context)}")
        self.context = context
        if not isinstance(self.underlay, bool):
            raise TypeError(
                "VizSpec.underlay: expected bool, got "
                f"{type(self.underlay).__name__}")
        if not isinstance(self.underlay_topo, bool):
            raise TypeError(
                "VizSpec.underlay_topo: expected bool, got "
                f"{type(self.underlay_topo).__name__}")
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
        if self.caption is not None:
            if not isinstance(self.caption, str):
                raise TypeError(
                    "VizSpec.caption: expected str or None, got "
                    f"{type(self.caption).__name__}")
            self.caption = self.caption.strip() or None

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
            "context": list(self.context),
            "underlay": self.underlay,
            "underlay_topo": self.underlay_topo,            "storm_name": self.storm_name,
            "storm_rank": self.storm_rank,
            "storm_top_n": self.storm_top_n,
            "caption": self.caption,
            "derived_note": self.derived_note,
            "timescale_reason": self.timescale_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VizSpec":
        """Rebuild from :meth:`to_dict` output (dates may be ISO strings).

        v0.2.0 dicts without ``"overlays"`` load unchanged (default ``()``);
        dicts without ``"source_reason"`` (pre-v0.7.0) load with ``""``;
        dicts without ``"underlay"`` (pre-v0.9.0) load with ``True``;
        dicts without ``"storm_name"`` / ``"storm_rank"`` /
        ``"storm_top_n"`` (pre-v0.10.0) load with ``""`` / ``""`` /
        ``None``; dicts without ``"context"`` (pre-v0.13.0) load with
        ``()``; ``"variable": "chlorophyll"`` (pre-v0.13.0) loads as
        the canonical ``"ocean-color"``; dicts without ``"caption"``
        (pre-v0.15.0) load with ``None`` (default footer); dicts without
        ``"derived_note"`` (pre-v0.19.0) load with ``None``; dicts without
        ``"underlay_topo"`` (pre-v0.20.0) load with ``True`` (dataclass
        default); dicts without ``"timescale_reason"`` (pre-v0.21.0)
        load with ``""`` (dataclass default).
        """
        if not isinstance(data, dict):
            raise TypeError(f"VizSpec.from_dict: expected dict, got {type(data).__name__}")
        kwargs = dict(data)
        return cls(**kwargs)
