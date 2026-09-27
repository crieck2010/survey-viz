"""Deterministic, offline, stdlib-only description parser.

:func:`parse_description` turns plain English like
``"Lake Superior surface temperature over the past 5 years"``
into a :class:`viz.spec.VizSpec`. No AI, no network: regex + gazetteer
lookup only. The full grammar is documented in ``docs/PARSER.md``.

Raises :class:`UnparseableDescription` when no region matches, when time
phrases conflict, or when an explicit year range is invalid.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import List, Optional, Tuple

from .gazetteer import find_region
from .spec import VizSpec

__all__ = ["UnparseableDescription", "parse_description"]


class UnparseableDescription(ValueError):
    """Raised when a description cannot be deterministically parsed."""


# --- variable keywords -------------------------------------------------------
# Each group maps to a VizSpec.variable value. When keywords from several
# groups appear, the group whose keyword occurs EARLIEST in the text wins
# (documented in docs/PARSER.md). No keyword at all -> "sst" (documented default).
#
# "burn-scar" is listed FIRST so it wins ties against "fire": "burn scar"
# matches both ``burns?`` (fire) and ``burn[ -]?scars?`` (burn-scar) at
# the same position, and the scar reading is the honest one — it has no
# fetch adapter yet (see viz.sources.default_source).
_VARIABLE_PATTERNS = {
    "burn-scar": [
        r"burn[ -]?scars?",
        r"burned[ -]?areas?",
        r"burn[ -]?severity",
    ],
    # "land-ice" is listed BEFORE "sea-ice" so it wins ties the same way
    # "burn-scar" wins ties against "fire": "ice sheet" matches both
    # ``\bice\b`` (sea-ice) and ``ice\s*sheets?`` (land-ice) at the same
    # position, and the land-ice reading is the honest one — glaciers,
    # ice sheets, and icebergs are a different physical product with no
    # fetch adapter (see viz.sources.default_source).
    "land-ice": [
        r"\bglaciers?\b",
        r"\bice\s*sheets?\b",
        r"\bicebergs?\b",
        r"\bland\s*ice\b",
    ],
    "sea-ice": [
        r"sea[ -]?ice",
        r"\bpack\s*ice\b",
        r"\bice\s*concentrations?\b",
        r"\bice\s*covers?\b",
        r"\bice\s*extents?\b",
        r"\bice\b",
    ],
    "fire": [
        r"wildfires?",
        r"\bfires?\b",
        r"\bburning\b",
        r"\bburns?\b",
    ],
    "currents": [
        r"currents?",
        r"flows?",
        r"streams?",
        r"velocity",
        r"circulation",
        r"drift",
        r"\bedd(?:y|ies)\b",
    ],
    "ocean-color": [
        # Canonical since v0.13.0 (was "chlorophyll"). Generic "green
        # ocean" wording routes HERE — never to a terrestrial water
        # product (documented in docs/OCEANCOLOR.md).
        r"ocean\s*colou?r",
        r"green\s*oceans?",
        r"chlorophyll",
        r"\bchl\b",
        r"algae",
        r"algal",
        r"blooms?",
        r"phytoplankton",
    ],
    "sst": [
        r"\bsst\b",
        r"temperatures?",
        r"thermal",
        r"warmth",
        r"\bwarm\b",
        r"\bcold\b",
        r"degrees?",
        r"celsius",
        r"°c",
        r"deg\s*c",
    ],
    # --- ERA5 variables (survey-currents v0.4.0+, source "era5") ---
    "wind": [
        r"\bwinds?\b",
        r"\bwindy\b",
        r"\bgales?\b",
        r"\bgusts?\b",
    ],
    "msl": [
        r"\bpressures?\b",
        r"\bisobars?\b",
        r"sea[\s-]?level pressures?",
    ],
    "t2m": [
        r"\bair\s+temperatures?\b",
        r"\batmospheric\s+temperatures?\b",
        r"\bheat\s*waves?\b",
        # Bare "heat" means air heat (ERA5 2-m temperature), not water
        # temperature — the documented disambiguation rule (v0.3.0).
        # "warm"/"warmth"/"cold"/"thermal" stay SST (backward compatible).
        r"\bheat\b",
    ],
    "tp": [
        r"\brain\b",
        r"\brainfall\b",
        r"\bprecipitation\b",
        r"\bdeluges?\b",
        r"\bdownpours?\b",
    ],
    # "power-outage" is listed BEFORE "night-lights" so it wins ties the
    # same way "burn-scar" wins ties against "fire": a blackout is
    # temporal change detection across two or more epochs, and the
    # change-detection reading is the honest one — it has no fetch
    # adapter, only a refusal (see viz.sources.default_source).
    "power-outage": [
        r"\bpower[ -]?outages?\b",
        r"\bblackouts?\b",
    ],
    "night-lights": [
        r"\bblack marble\b",
        r"\bvnp46a2?\b",
        r"night[ -]?lights?",
        r"city[ -]?lights?",
        r"urban[ -]?lights?",
        r"light[ -]?pollution",
        r"\belectrification\b",
        r"\bgrowth\s+of\s+cit(?:y|ies)\b",
        # Bare "development" almost always means urban development in
        # this context; caveat: it wins the earliest-wins contest
        # against later variable keywords (documented in docs/PARSER.md).
        r"\bdevelopment\b",
    ],
    # "country-borders" is listed AFTER "night-lights" so the power-outage
    # safety rule above keeps working; it has no fetch adapter — Natural
    # Earth vectors are a cartographic underlay, not a data variable —
    # so "country borders" alone is an honest refusal
    # (see viz.sources.default_source).
    "country-borders": [
        r"\bcountr(?:y|ies)[ -]?(?:borders?|boundaries|outlines?)\b",
        r"\bnational[ -]?borders?\b",
        r"\bpolitical[ -]?boundar(?:y|ies)\b",
    ],
    # --- GEBCO variables (survey-currents v0.10.0+, source "gebco") ---
    # "bathymetry" is listed before "elevation" so "seafloor depth"
    # reads as bathymetry; both route globally to GEBCO 2024.
    "bathymetry": [
        r"\bbathymetr(?:y|ic)\b",
        r"\bseafloors?\b",
        r"\bsea[ -]?floors?\b",
        # Bare "depth"/"depths" but not "in-depth" (lookbehind excludes
        # the hyphenated intensifier).
        r"(?<![\w-])depths?\b",
        r"\btrenches?\b",
        r"\babyss(?:es|al)?\b",
        r"\bhadal\b",
    ],
    "elevation": [
        r"\belevations?\b",
        r"\bterrains?\b",
        r"\btopograph(?:y|ic)\b",
        r"\bmountains?\b",
    ],
    # --- GRACE variables (survey-currents v0.12.0+, source "grace") ---
    # "water-storage" routes to CSR GRACE/GRACE-FO RL06.3 terrestrial
    # water storage anomalies. "sea-level" stays refusal-only: "sea
    # level" (without "pressure" — the negative lookahead keeps "sea
    # level pressure" on "msl") is satellite altimetry, a different
    # observable from GRACE TWS. "streamflow" now routes to USGS
    # streamgages (survey-currents v0.13.0+, source "usgs"); the
    # explicit _parse_streamflow_request check below guarantees the
    # streamflow intent wins before the earliest-wins race, because the
    # broad "currents" patterns (bare "streams?", "flows?") would
    # otherwise misread "river flow" as ocean currents.
    "water-storage": [
        r"\bwater[ -]?storages?\b",
        r"\bgroundwaters?\b",
        r"\baquifers?\b",
        r"\bterrestrial\s+waters?\b",
        r"\btotal\s+water\s+storages?\b",
        r"\bgrace[\s-]?fo\b",
        r"\bgrace\b",
        r"\btws\b",
        r"\bdroughts?\b",
    ],
    "sea-level": [
        # Not "sea level pressure" — that stays "msl" (the lookahead
        # fails on "pressure", so the msl pattern wins).
        r"sea[\s-]?levels?\b(?!\s*pressures?\b)",
        r"\bsea\s*level\s*rises?\b",
    ],
    "streamflow": [
        r"\bstreamflows?\b",
        r"\briver\s*discharges?\b",
        r"\bstream\s*discharges?\b",
        # "river flow" is streamflow intent, NOT ocean currents: the
        # pre-race _parse_streamflow_request guarantees this wins over
        # the bare "flows?" currents pattern.
        r"\briver\s*flows?\b",
        r"\bgages?\b",
        r"\bgauges?\b",
        r"\bstreamgages?\b",
        r"\bstream\s+gauges?\b",
    ],
    # --- earthquakes (survey-currents v0.15.0+, source "comcat") ---
    # "earthquakes" is listed last in the variable groups and ALSO
    # pre-checked by _parse_earthquake_request before the earliest-wins
    # race: the "bathymetry" group has a bare "depth" pattern
    # (r"(?<![\w-])depths?\b") that would otherwise win on phrasing
    # like "The depth of the earthquake..." — the pre-check guarantees
    # earthquake intent wins (see docs/PARSER.md). "seismic hazard"
    # wording routes here too — the catalog is observed events, NOT a
    # forecast hazard model, and the renderer records that honesty
    # note in every manifest (see docs/EARTHQUAKES.md).
    "earthquakes": [
        r"\bearthquakes?\b",
        r"\bseismic\b",
        r"\bquakes?\b",
        r"\btremors?\b",
        r"\bmagnitudes?\b",
        r"\bforeshocks?\b",
        r"\baftershocks?\b",
        r"\bseismic\s*hazards?\b",
    ],
}

# Storm keywords: they name the ("wind", overlays=["msl"]) combination
# (wind base map + pressure isobars), and race in the same earliest-wins
# contest as the variable groups above — EXCEPT when the description
# shows track/identity/history/ranking intent (a named storm, "track"
# wording, or "strongest ..." wording). That intent is detected first
# by _parse_storm_request and routes to the "storm-tracks" variable
# (NOAA IBTrACS best tracks) instead — see docs/STORMS.md. Generic
# "hurricane conditions" wording keeps the ERA5 wind + msl combination.
_STORM_PATTERNS = [
    r"\bstorms?\b",
    r"\bcyclones?\b",
    r"\bhurricanes?\b",
    r"\btyphoons?\b",
]

# Words that can follow a storm keyword positionally but are never storm
# names ("hurricane season", "Hurricane Track", ...). Checked
# case-insensitively; the name candidate must ALSO be capitalized (storm
# names are proper nouns — "Hurricane pressure" and "the hurricane
# nears" are not names).
_NON_STORM_NAMES = frozenset({
    "season", "seasons", "seasonal", "track", "tracks", "path", "paths",
    "conditions", "condition", "activity", "outlook", "forecast",
    "forecasts", "watch", "watches", "warning", "warnings", "center",
    "update", "updates", "preparedness", "awareness", "landfall",
    "landfalls", "formation", "pressure", "pressures", "wind", "winds",
    "rain", "rainfall", "surge", "damage",
})

#: "Hurricane Katrina" / "tropical storm Milton" / "Typhoon Haiyan's".
#: The storm keyword matches case-insensitively; the name candidate
#: must start with an uppercase letter (checked in code, since
#: re.IGNORECASE would make [A-Z] match lowercase too).
_NAMED_STORM = re.compile(
    r"\b(?:hurricanes?|tropical\s+storms?|typhoons?|cyclones?|"
    r"tropical\s+depressions?)\s+([A-Za-z]+)(?:'s)?\b", re.IGNORECASE)

#: Track/identity intent: the description is about the track geometry,
#: not the ambient storm environment.
_STORM_TRACK_INTENT = [
    r"\btracks?\b",
    r"\bpaths?\b",
    r"\btrajector(?:y|ies)\b",
]

#: Ranking intent: "strongest hurricanes of the 2024 season" etc.
_STORM_RANK_INTENT = [
    r"\bstrongest\b",
    r"\bmost\s+intense\b",
    r"\bmost\s+powerful\b",
]

#: Optional ERA5 ambient-field context on a storm-tracks request
#: ("Hurricane Milton's track with the wind field"): the overlay name
#: is fetched from ERA5 by the pipeline and drawn as contours under
#: the tracks. At most one (MAX_OVERLAYS == 1).
_STORM_CONTEXT_PATTERNS = {
    "wind": [r"\bwind\s*fields?\b", r"\bwith\s+winds?\b"],
    "msl": [r"\bpressure\s*fields?\b", r"\bwith\s+pressures?\b"],
}

#: Optional precipitation context on a "water-storage" request
#: ("Groundwater decline vs rainfall in California"): the overlay name
#: is fetched by the pipeline (ERA5 or IMERG tp) and drawn as contours
#: under the GRACE anomaly map. At most one (MAX_OVERLAYS == 1). When
#: the context cannot be fetched the pipeline degrades to an "absent"
#: manifest status instead of failing — never silently dropped.
_WATER_CONTEXT_PATTERNS = {
    "tp": [r"\bvs\.?\s+(?:rain|rainfall|precipitation)\b",
           r"\bwith\s+(?:rain|rainfall|precipitation)\b",
           r"\bcompared\s+to\s+(?:rain|rainfall|precipitation)\b",
           r"\bagainst\s+(?:rain|rainfall|precipitation)\b"],
}

# --- ocean-color context (survey-currents v0.14.0+, source "oceancolor")
# "Phytoplankton bloom with ocean currents" -> ocean-color base +
# "currents" context; "bloom conditions" / "chlorophyll vs temperature"
# -> ocean-color base + "sst" context. Context is fetched alongside the
# base variable (see VizSpec.context) — it is NOT drawn as contours on
# the chlorophyll map. At most MAX_CONTEXTS entries (spec.py).
_OCEANCOLOR_CURRENTS_PATTERNS = [
    r"\bcurrents?\b",
    r"\bcirculation\b",
    r"\bdrift\b",
    r"\bedd(?:y|ies)\b",
]
_OCEANCOLOR_SST_PATTERNS = [
    r"\bbloom\s+conditions\b",
    r"\bconditions?\b.{0,20}\bblooms?\b",
    r"\bvs\.?\s+(?:sea\s+)?surface\s+temperatures?\b",
    r"\bvs\.?\s+temperatures?\b",
    r"\btemperatures?\s+vs\.?\b",
    r"\bsea\s+surface\s+temperatures?\b",
    r"\bsst\b",
]

#: Terrestrial water-quality wording with NO ocean-color adapter behind
#: it (nitrate, turbidity, "water quality", dissolved oxygen, ...).
#: When these match and no variable keyword won the race, the request
#: is refused honestly instead of defaulting to SST (documented in
#: docs/OCEANCOLOR.md). Chlorophyll / algae / bloom wording is NOT in
#: this list — that is the ocean-color variable.
_TERRESTRIAL_WQ_PATTERNS = [
    r"\bwater\s+quality\b",
    r"\bnitrates?\b",
    r"\bnitrites?\b",
    r"\bphosphates?\b",
    r"\bphosphorus\b",
    r"\bturbidity\b",
    r"\bturbid\b",
    r"\bdissolved\s+oxygen\b",
    r"\bdo\s+levels?\b",
    r"\bpollutants?\b",
    r"\bcontamination\b",
    r"\be\.?\s?coli\b",
    r"\bph\s+levels?\b",
    r"\bacidity\b",
]

# --- streamflow intent (survey-currents v0.13.0+, source "usgs") ------------
# Explicit streamflow phrases: "streamflow", "river discharge", "stream
# discharge", "river flow", "gage"/"gages", "streamgage"/"streamgages".
# These are checked BEFORE the earliest-wins race (and before storm
# intent) because the broad "currents" patterns (bare "streams?",
# "flows?", "currents?") would otherwise misread "river flow" or
# "stream discharge" as ocean currents. A bare "flow"/"flows" with no
# river/stream/gage context is NOT streamflow intent — ocean/current
# "flow" requests keep routing to "currents".
_STREAMFLOW_PATTERNS = [
    r"\bstreamflows?\b",
    r"\briver\s*discharges?\b",
    r"\bstream\s*discharges?\b",
    r"\briver\s*flows?\b",
    r"\bgages?\b",
    r"\bgauges?\b",
    r"\bstreamgages?\b",
    r"\bstream\s+gauges?\b",
]

#: Flood wording that, TOGETHER WITH rainfall wording, is a streamflow
#: request: "Flooding after heavy rainfall in Ohio" -> streamflow
#: primary + "tp" precipitation overlay. Flood wording ALONE (no
#: rainfall phrasing) is not streamflow intent — a flood-inundation
#: map is survey-flood's imagery domain, not streamgage discharge.
_FLOOD_PATTERNS = [
    r"\bflood(?:s|ed|ing)?\b",
]
_RAINFALL_PATTERNS = [
    r"\brainfalls?\b",
    r"\bprecipitations?\b",
    r"\bheavy\s+rains?\b",
    r"\bdownpours?\b",
    r"\bdeluges?\b",
    r"\brain\b",
]


def _parse_streamflow_request(text: str) -> Optional[Tuple[str, Tuple[str, ...]]]:
    """Detect streamflow / river-discharge intent.

    Returns ``("streamflow", overlays)`` when the description names
    streamflow explicitly (streamflow, river/stream discharge, river
    flow, gage/gages, streamgage(s)) or pairs flood wording with
    rainfall wording ("flooding after heavy rainfall"), else ``None``.
    The overlay is ``("tp",)`` when rainfall wording is present —
    precipitation is the best-effort context the pipeline fetches
    alongside the gages; it degrades gracefully when unavailable.
    """
    lowered = text.lower()
    explicit = any(re.search(p, lowered) for p in _STREAMFLOW_PATTERNS)
    flood = any(re.search(p, lowered) for p in _FLOOD_PATTERNS)
    rain = any(re.search(p, lowered) for p in _RAINFALL_PATTERNS)
    if explicit or (flood and rain):
        return "streamflow", (("tp",) if rain else ())
    return None


# --- earthquake intent (survey-currents v0.15.0+, source "comcat") ----------
# Explicit earthquake/seismic wording: "earthquake", "quake", "seismic",
# "tremor", "magnitude", "foreshock", "aftershock", "seismic hazard".
# These are checked BEFORE the earliest-wins race (and before storm
# intent) because the "bathymetry" group has a bare "depth"/"depths"
# pattern that would otherwise win on phrasing like "The depth of the
# earthquake...". A quake depth is hypocentral depth, not seafloor
# depth — the pre-check guarantees earthquake intent wins.
_EARTHQUAKE_PATTERNS = [
    r"\bearthquakes?\b",
    r"\bseismic\b",
    r"\bquakes?\b",
    r"\btremors?\b",
    r"\bmagnitudes?\b",
    r"\bforeshocks?\b",
    r"\baftershocks?\b",
    r"\bseismic\s*hazards?\b",
]


def _parse_earthquake_request(text: str) -> Optional[str]:
    """Detect earthquake / seismic-event intent.

    Returns ``"earthquakes"`` when the description names earthquakes
    explicitly (earthquake, quake, seismic, tremor, magnitude,
    foreshock, aftershock, seismic hazard), else ``None``. Checked
    before the earliest-wins race so the "bathymetry" bare-"depth"
    pattern cannot steal "the depth of the earthquake".
    """
    lowered = text.lower()
    if any(re.search(p, lowered) for p in _EARTHQUAKE_PATTERNS):
        return "earthquakes"
    return None

_VARIABLE_LABELS = {
    "sst": "Surface Water Temperature",
    "currents": "Surface Currents",
    "ocean-color": "Chlorophyll-a (Ocean Color)",
    "chlorophyll": "Chlorophyll-a",
    "wind": "10-m Wind",
    "msl": "Sea-Level Pressure",
    "t2m": "2-m Air Temperature",
    "tp": "Precipitation",
    "fire": "Active Fires",
    "burn-scar": "Burn Scar",
    "sea-ice": "Sea Ice",
    "land-ice": "Land Ice",
    "night-lights": "Night Lights",
    "power-outage": "Power Outage",
    "bathymetry": "Bathymetry",
    "elevation": "Elevation",
    "country-borders": "Country Borders",
    "storm-tracks": "Storm Tracks",
    "water-storage": "Terrestrial Water Storage",
    "streamflow": "Streamflow",
    "sea-level": "Sea Level",
    "earthquakes": "Earthquakes",
}

# --- time phrases ------------------------------------------------------------
_PAST_YEARS = re.compile(r"\bpast\s+(\d+)\s+years?\b", re.IGNORECASE)
_LAST_N_YEARS = re.compile(r"\blast\s+(\d+)\s+years?\b", re.IGNORECASE)
_PAST_MONTHS = re.compile(r"\bpast\s+(\d+)\s+months?\b", re.IGNORECASE)
_LAST_SUMMER = re.compile(r"\blast\s+summer\b", re.IGNORECASE)
_THIS_YEAR = re.compile(r"\bthis\s+year\b", re.IGNORECASE)
_EXPLICIT_RANGE = re.compile(r"\b(\d{4})\s*(?:to|through|-|–|—)\s*(\d{4})\b")
_SINCE_YEAR = re.compile(r"\bsince\s+(\d{4})\b", re.IGNORECASE)

_EXAMPLES = [
    '"Lake Superior surface temperature over the past 5 years"',
    '"Gulf of Mexico currents, 2015 to 2020"',
    '"Chlorophyll in Puget Sound last summer"',
]


def _subtract_years(day: _dt.date, n: int) -> _dt.date:
    try:
        return day.replace(year=day.year - n)
    except ValueError:
        # Feb 29 -> Feb 28 on non-leap years.
        return day.replace(year=day.year - n, day=28)


def _subtract_months(day: _dt.date, n: int) -> _dt.date:
    month = day.month - n
    year = day.year
    while month <= 0:
        month += 12
        year -= 1
    # Clamp day to the target month length.
    import calendar as _cal

    last = _cal.monthrange(year, month)[1]
    return _dt.date(year, month, min(day.day, last))


def _last_summer_window(today: _dt.date) -> Tuple[_dt.date, _dt.date]:
    """Most recent fully-completed Jun-Aug window before ``today``."""
    if _dt.date(today.year, 8, 31) < today:
        year = today.year
    else:
        year = today.year - 1
    return _dt.date(year, 6, 1), _dt.date(year, 8, 31)


def _parse_time(text: str, today: _dt.date) -> Tuple[Optional[_dt.date], Optional[_dt.date], List[str]]:
    """Return (start, end, matched_phrases). None/None when no time phrase found."""
    matched: List[str] = []
    candidates: List[Tuple[_dt.date, _dt.date]] = []

    def add(phrase: str, start: _dt.date, end: _dt.date) -> None:
        matched.append(phrase)
        candidates.append((start, end))

    m = _PAST_YEARS.search(text)
    if m:
        n = int(m.group(1))
        add(f"past {n} year(s)", _subtract_years(today, n), today)
    m = _LAST_N_YEARS.search(text)
    if m and not _LAST_SUMMER.search(text):
        n = int(m.group(1))
        add(f"last {n} year(s)", _subtract_years(today, n), today)
    m = _PAST_MONTHS.search(text)
    if m:
        n = int(m.group(1))
        add(f"past {n} month(s)", _subtract_months(today, n), today)
    if _LAST_SUMMER.search(text):
        s, e = _last_summer_window(today)
        add("last summer", s, e)
    if _THIS_YEAR.search(text):
        add("this year", _dt.date(today.year, 1, 1), today)
    m = _EXPLICIT_RANGE.search(text)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        if y1 > y2:
            raise UnparseableDescription(
                f"Invalid year range {y1}–{y2}: the start year must not be after the end year.\n"
                f"Try one of these examples:\n" + "\n".join(f"  - {e}" for e in _EXAMPLES)
            )
        add(f"{y1} to {y2}", _dt.date(y1, 1, 1), _dt.date(y2, 12, 31))
    m = _SINCE_YEAR.search(text)
    if m:
        y = int(m.group(1))
        add(f"since {y}", _dt.date(y, 1, 1), today)

    if not matched:
        return None, None, []
    if len(matched) > 1:
        raise UnparseableDescription(
            f"Conflicting time phrases in {text!r}: {', '.join(matched)}.\n"
            "Use exactly one time phrase.\n"
            f"Try one of these examples:\n" + "\n".join(f"  - {e}" for e in _EXAMPLES)
        )
    (start, end) = candidates[0]
    return start, end, matched


def _parse_storm_request(text: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """Detect storm track/identity/ranking intent.

    Returns ``(storm_name, storm_rank, storm_top_n)`` when the
    description asks for IBTrACS track geometry rather than the ERA5
    ambient storm environment, else ``None``:

    * a named storm ("Hurricane Katrina", "Tropical Storm Milton") ->
      ``(name, "", None)`` — the name is the storm identity, so the
      track reading always wins;
    * track wording ("track", "tracks", "path", "trajectory") together
      with a storm word ("storm", "hurricane", "cyclone", "typhoon") ->
      ``("", "", None)``;
    * ranking wording ("strongest", "most intense", "most powerful")
      with a storm word -> ``("", "strongest", top_n)``, where
      ``top_n`` comes from "top N" phrasing (``None`` = default 5).

    Generic "hurricane conditions" wording matches none of these and
    falls through to the ordinary earliest-wins race (ERA5 wind +
    msl). A bare name with no storm word ("Katrina's track") is not
    detected — the parser has no storm-name gazetteer, so a storm word
    is required.
    """
    lowered = text.lower()
    if not any(re.search(p, lowered) for p in _STORM_PATTERNS):
        return None
    name = ""
    m = _NAMED_STORM.search(text)
    if m:
        candidate = m.group(1)
        # Storm names are proper nouns: the candidate must be
        # capitalized ("Hurricane Katrina" yes; "Hurricane pressure"
        # and "the hurricane nears" no). An all-lowercase name is not
        # detected — track wording still routes to "storm-tracks", just
        # without the name pin (documented in docs/STORMS.md).
        if (candidate[:1].isupper()
                and candidate.lower() not in _NON_STORM_NAMES
                and len(candidate) > 2):
            name = candidate.lower()
    track_intent = any(re.search(p, lowered) for p in _STORM_TRACK_INTENT)
    rank_intent = any(re.search(p, lowered) for p in _STORM_RANK_INTENT)
    top_n: Optional[int] = None
    m = re.search(r"\btop\s+(\d+)\b", lowered)
    if m:
        top_n = int(m.group(1))
    if name or track_intent or rank_intent:
        return name, ("strongest" if rank_intent else ""), top_n
    return None


def _parse_variable(text: str) -> Tuple[str, Tuple[str, ...], bool,
                                        str, str, Optional[int],
                                        Tuple[str, ...]]:
    """Return (variable, overlays, defaulted, storm_name, storm_rank,
    storm_top_n, context).

    Storm track/identity/ranking intent (see :func:`_parse_storm_request`)
    is detected FIRST: a named storm, track wording, or "strongest ..."
    wording with a storm word routes to ``"storm-tracks"`` (NOAA IBTrACS
    best tracks). Otherwise the earliest keyword in the text wins;
    storm keywords ("storm", "cyclone", "hurricane", "typhoon") compete
    in the same race and map to the ("wind", overlays=("msl",))
    combination — so generic "hurricane conditions" wording keeps the
    ERA5 wind + msl overlay. Safety rule (v0.8.0): "power-outage"
    wording anywhere in the text overrides a "night-lights" win —
    outage mapping is temporal change detection and a single-epoch
    Black Marble map would be a lie. No keyword ->
    ("sst", (), True, "", "", None, ()).

    Streamflow intent (see :func:`_parse_streamflow_request`) is
    detected BEFORE storm intent: explicit streamflow phrasing
    ("streamflow", "river discharge", "river flow", "gages",
    "streamgages") and flood-plus-rainfall wording ("flooding after
    heavy rainfall") route to ``"streamflow"`` (USGS Water Services
    daily values, source "usgs") — the broad "currents" patterns (bare
    "streams?"/"flows?") would otherwise misread them as ocean
    currents. Rainfall wording on a streamflow win adds the "tp"
    precipitation overlay as best-effort context.

    Earthquake intent (see :func:`_parse_earthquake_request`) is
    detected right after streamflow and BEFORE storm intent:
    explicit earthquake/seismic wording ("earthquake", "quake",
    "seismic", "tremor", "magnitude", "foreshock", "aftershock",
    "seismic hazard") routes to ``"earthquakes"`` (USGS Earthquake
    Catalog, source "comcat") — the "bathymetry" group's bare
    "depth"/"depths" pattern would otherwise win on phrasing like
    "The depth of the earthquake...". Storm intent requires storm
    words ("storm", "hurricane", "cyclone", "typhoon"), so no
    earthquake/storm interaction exists — the earthquake win is
    unconditional.

    On a ``"storm-tracks"`` win, "with the wind field" / "with the
    pressure field" wording adds the corresponding ERA5 contour overlay
    (the pipeline fetches it as context; the renderer degrades
    gracefully when it is absent).

    On a ``"water-storage"`` win, "vs rainfall" / "with rainfall"
    wording adds a precipitation contour overlay (``"tp"``) — the GRACE
    anomaly map stays the base map and precipitation is context.
    On a ``"streamflow"`` win, rainfall wording ("after heavy
    rainfall", "with rainfall", ...) adds the same ``"tp"`` overlay —
    the gage markers stay the base layer and precipitation is context.

    On an ``"ocean-color"`` win (v0.13.0), currents wording ("with
    ocean currents", "circulation", "drift", "eddy/eddies") adds the
    ``"currents"`` context and SST wording ("bloom conditions",
    "chlorophyll vs temperature", "sea surface temperature", "sst")
    adds the ``"sst"`` context — fetched alongside the chlorophyll-a
    base map, recorded in provenance, never drawn as contours on it
    (see docs/OCEANCOLOR.md).
    """
    lowered = text.lower()
    # Streamflow intent wins before everything else: explicit
    # streamflow phrasing (or flood + rainfall wording) is
    # unambiguous, and the broad "currents" patterns would otherwise
    # misread "river flow"/"stream discharge" as ocean currents.
    streamflow_req = _parse_streamflow_request(text)
    if streamflow_req is not None:
        return (streamflow_req[0], streamflow_req[1], False,
                "", "", None, ())
    # Earthquake intent wins before the earliest-wins race (and
    # before storm intent): explicit earthquake/seismic wording is
    # unambiguous, and the "bathymetry" bare-"depth" pattern would
    # otherwise win on phrasing like "The depth of the earthquake...".
    if _parse_earthquake_request(text) is not None:
        return "earthquakes", (), False, "", "", None, ()
    storm_req = _parse_storm_request(text)
    if storm_req is not None:
        storm_name, storm_rank, storm_top_n = storm_req
        overlays: Tuple[str, ...] = ()
        for ov_name, patterns in _STORM_CONTEXT_PATTERNS.items():
            if any(re.search(p, lowered) for p in patterns):
                overlays = (ov_name,)
                break
        return "storm-tracks", overlays, False, storm_name, storm_rank, storm_top_n, ()
    best: Optional[Tuple[int, str, Tuple[str, ...]]] = None
    for variable, patterns in _VARIABLE_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, lowered)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), variable, ())
    for pattern in _STORM_PATTERNS:
        m = re.search(pattern, lowered)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), "wind", ("msl",))
    if best is None:
        # Documented default rule: no variable keyword -> sst.
        return "sst", (), True, "", "", None, ()
    variable = best[1]
    if variable == "night-lights" and any(
            re.search(p, lowered)
            for p in _VARIABLE_PATTERNS["power-outage"]):
        # Safety rule (v0.8.0): blackout / power-outage wording anywhere
        # overrides a night-lights win — outage mapping is temporal
        # change detection, and a single daily Black Marble map would be
        # a lie. Documented in docs/PARSER.md.
        return "power-outage", (), False, "", "", None, ()
    if variable == "water-storage":
        # "groundwater decline vs rainfall": keep GRACE as the base map
        # and add precipitation as a contour overlay (the pipeline
        # fetches it as context; it degrades to an "absent" manifest
        # status when unavailable). Documented in docs/PARSER.md.
        for ov_name, patterns in _WATER_CONTEXT_PATTERNS.items():
            if any(re.search(p, lowered) for p in patterns):
                return "water-storage", (ov_name,), False, "", "", None, ()
    context: Tuple[str, ...] = ()
    if variable == "ocean-color":
        ctx: List[str] = []
        if any(re.search(p, lowered) for p in _OCEANCOLOR_CURRENTS_PATTERNS):
            ctx.append("currents")
        if any(re.search(p, lowered) for p in _OCEANCOLOR_SST_PATTERNS):
            ctx.append("sst")
        context = tuple(ctx)
    return variable, best[2], False, "", "", None, context


# --- source-quality keywords ---------------------------------------------------
# SST-only: asking for high resolution / ultra / coastal detail pins
# VizSpec.source = "mur" (NASA JPL MUR v4.1, ~1 km). Currents: the same
# high-resolution / ultra / 1-km wording pins VizSpec.source =
# "cmems-currents" (CMEMS global ocean physics, 1/12° — coarsest true fit
# for a high-resolution current request). Anything else leaves source
# empty so viz.sources.resolve_source applies the regional default
# (Great Lakes SST -> GLSEA, other SST -> OISST, non-Great-Lakes
# currents -> OSCAR). Documented in docs/PARSER.md.
_SOURCE_QUALITY_PATTERNS = [
    r"high[ -]?resolution",
    r"\bultra\b",
    r"coastal detail",
    r"\b1\s?km\b",
    r"kilometre",
    r"kilometer",
]


# --- precipitation source signals (variable == "tp" only) -------------------
# Explicit product names win; otherwise recent/observed/event wording pins
# "imerg" (NASA GPM IMERG V07 — satellite-observed, half-hourly,
# 2000–present) and long-record/trend wording pins "era5" (Copernicus
# ERA5 reanalysis, 1940–present). Anything else leaves the source empty
# so viz.sources.resolve_source applies the regional default (tp ->
# era5). Documented in docs/PARSER.md. All patterns use word boundaries
# (or anchored prefixes like "since 19") and are checked in the order
# listed by _first_match.
_TP_IMERG_PATTERNS = [
    r"\brecent\b",
    r"\blast week\b",
    r"\bevents?\b",
    r"\bstorms?\b",
    r"\bhurricanes?\b",
    r"\btyphoons?\b",
    r"\bcyclones?\b",
    r"\bobserved\b",
    r"\bsatellite\b",
    r"high[ -]?resolution",
    r"\bultra\b",
]
_TP_ERA5_PATTERNS = [
    r"\btrend\b",
    r"\bclimatolog\w*\b",
    r"\bsince 19",
    r"\bsince 20",
    r"\bdecades?\b",
    r"long[ -]?term",
]


def _first_match(lowered: str, patterns) -> Optional[str]:
    """Return the first matching pattern string, or None."""
    for p in patterns:
        if re.search(p, lowered):
            return p
    return None


def _parse_source(text: str, variable: str) -> Tuple[str, str]:
    """Return ``(pinned source, reason)`` or ``("", "")`` for the default.

    * ``"sst"`` + high-resolution / ultra / coastal-detail wording pins
      ``"mur"`` (NASA JPL MUR v4.1, ~1 km).
    * ``"currents"`` + the same wording pins ``"cmems-currents"`` (CMEMS
      global ocean physics, 1/12°).
    * ``"tp"`` (precipitation): an explicit ``IMERG`` / ``ERA5`` pins
      that product; otherwise recent/observed/event wording
      (``recent``, ``last week``, ``event``, ``storm``, ``hurricane``,
      ``observed``, ``satellite``, ``high resolution``, ``ultra``) pins
      ``"imerg"`` (NASA GPM IMERG V07 — satellite-observed,
      half-hourly, 2000–present), while long-record wording (``trend``,
      ``climatology``, ``since 19…`` / ``since 20…``, ``decades``,
      ``long-term``) pins ``"era5"`` (Copernicus ERA5 reanalysis,
      1940–present). Anything else leaves the source empty so
      ``viz.sources.resolve_source`` applies the regional default
      (``tp`` -> ``"era5"``).
    * ``"night-lights"`` always pins ``"blackmarble"`` (NASA Black
      Marble VNP46A2 V002 daily, gap-filled lunar BRDF-adjusted DNB
      radiance, 2012-01-19–present) with an inspectable
      ``source_reason``.
    * ``"bathymetry"`` / ``"elevation"`` always pin ``"gebco"`` (GEBCO
      2024 global topography/bathymetry, 15 arc-second) with an
      inspectable ``source_reason`` — in every region.
    * ``"storm-tracks"`` always pins ``"ibtracs"`` (NOAA IBTrACS
      v04r01 tropical-cyclone best tracks) with an inspectable
      ``source_reason`` — in every region.
    * ``"water-storage"`` always pins ``"grace"`` (CSR GRACE/GRACE-FO
      RL06.3 terrestrial water storage anomalies: monthly, cm of
      liquid-water-equivalent thickness, land-only, 2002–present)
      with an inspectable ``source_reason`` — in every region.
    * ``"streamflow"`` always pins ``"usgs"`` (USGS Water Services
      NWIS streamgage daily values: daily MEAN discharge/gage height,
      keyless HTTPS, US-only) with an inspectable ``source_reason``
      — in every region; bboxes outside USGS coverage yield an honest
      empty field, rendered as an explicit empty-frame message.
    * ``"ocean-color"`` always pins ``"oceancolor"`` (NOAA CoastWatch
      ERDDAP: MODIS Aqua R2022 Level-3 chlorophyll-a, monthly, ~4 km,
      2002-present, keyless) with an inspectable ``source_reason``
      — in every region; the global L3 grid covers the Great Lakes
      geometrically, but inland/coastal retrievals are indicative
      only (case-2 waters: land adjacency, bottom reflectance, CDOM,
      suspended sediment — see docs/OCEANCOLOR.md).
    * ``"earthquakes"`` always pins ``"comcat"`` (USGS Earthquake
      Catalog FDSN event service: keyless, global, GeoJSON) with an
      inspectable ``source_reason`` — in every region; the catalog is
      observed events, not a forecast hazard model (see
      docs/EARTHQUAKES.md). "earthquakes and topography" wording keeps
      the pin and notes in ``source_reason`` that topographic context
      comes from the default GEBCO underlay (no separate fetch).

    The reason is always populated when a source is pinned (used for
    ``VizSpec.source_reason``).
    """
    lowered = text.lower()
    if variable == "sst":
        hit = _first_match(lowered, _SOURCE_QUALITY_PATTERNS)
        if hit:
            return ("mur",
                    "high-resolution SST request "
                    "-> NASA JPL MUR v4.1 (~1 km)")
        return "", ""
    if variable == "currents":
        hit = _first_match(lowered, _SOURCE_QUALITY_PATTERNS)
        if hit:
            return ("cmems-currents",
                    "high-resolution currents request "
                    "-> CMEMS global ocean physics (1/12°)")
        return "", ""
    if variable == "tp":
        if re.search(r"\bimerg\b", lowered):
            return "imerg", "explicit IMERG request"
        if re.search(r"\bera5\b", lowered):
            return "era5", "explicit ERA5 request"
        hit = _first_match(lowered, _TP_IMERG_PATTERNS)
        if hit:
            return ("imerg",
                    "recent/observed precipitation wording "
                    "-> NASA GPM IMERG V07")
        hit = _first_match(lowered, _TP_ERA5_PATTERNS)
        if hit:
            return ("era5",
                    "long-record precipitation wording "
                    "-> Copernicus ERA5 reanalysis (1940-present)")
        return "", ""
    if variable == "night-lights":
        return ("blackmarble",
                "night-lights observations -> "
                "NASA Black Marble VNP46A2 daily corrected radiance")
    if variable in ("bathymetry", "elevation"):
        return ("gebco",
                f"{variable} description -> "
                "GEBCO 2024 global topography/bathymetry (15 arc-second)")
    if variable == "storm-tracks":
        return ("ibtracs",
                "storm track/identity request -> "
                "NOAA IBTrACS v04r01 best tracks")
    if variable == "water-storage":
        return ("grace",
                "water storage / groundwater description -> "
                "CSR GRACE/GRACE-FO RL06.3 terrestrial water storage "
                "anomalies (monthly, cm LWE, land-only)")
    if variable == "streamflow":
        return ("usgs",
                "streamflow / river-discharge description -> "
                "USGS Water Services (NWIS) streamgage daily values "
                "(daily MEAN discharge, keyless, US-only)")
    if variable == "ocean-color":
        return ("oceancolor",
                "chlorophyll-a / ocean color description -> "
                "NOAA CoastWatch ERDDAP (MODIS Aqua R2022, "
                "monthly L3 chlorophyll-a, ~4 km, 2002-present, keyless)")
    if variable == "earthquakes":
        reason = ("earthquake / seismic description -> "
                  "USGS Earthquake Catalog (ComCat), keyless, global")
        # "earthquakes and topography" wording keeps variable=
        # "earthquakes" (no separate fetch): the topographic context
        # is the default GEBCO underlay beneath every quake render,
        # and the reason says so inspectably.
        if _first_match(lowered, _VARIABLE_PATTERNS["elevation"]):
            reason += ("; topographic context comes from the GEBCO "
                       "underlay beneath the quake markers (no separate "
                       "fetch)")
        return "comcat", reason
    return "", ""


def _derive_title(region_name: str, variable: str, start: _dt.date, end: _dt.date) -> str:
    label = _VARIABLE_LABELS.get(variable, variable)
    if start.year == end.year:
        years = f"{start.year}"
    else:
        years = f"{start.year}\u2013{end.year}"  # en dash
    return f"{region_name} \u2014 {label}, {years}"  # em dash


def _failure_message(
    text: str,
    region: Optional[dict],
    variable: str,
    variable_defaulted: bool,
    time_phrases: List[str],
    default_time: bool,
) -> str:
    understood = []
    understood.append(f"region={region['name']}" if region else "region=<none>")
    understood.append(f"variable={variable}" + (" (defaulted)" if variable_defaulted else ""))
    if time_phrases:
        understood.append(f"time={time_phrases[0]}")
    elif default_time:
        understood.append("time=<defaulted to past 1 year>")
    else:
        understood.append("time=<none>")
    failed = []
    if region is None:
        failed.append("no known region matched")
    lines = [
        f"Could not parse description: {text!r}",
        "Understood: " + "; ".join(understood) + ".",
        "Failed: " + ("; ".join(failed) if failed else "unknown") + ".",
        "",
        "Examples that DO parse:",
    ]
    lines.extend(f"  - {e}" for e in _EXAMPLES)
    lines.append(
        "Note: a future app layer may add an LLM upgrade path for free-form "
        "descriptions; this parser is intentionally deterministic and offline."
    )
    return "\n".join(lines)


def parse_description(
    text: str,
    title: Optional[str] = None,
    today: Optional[_dt.date] = None,
) -> VizSpec:
    """Parse a plain-English description into a :class:`VizSpec`.

    Args:
        text: e.g. ``"Lake Superior surface temperature over the past 5 years"``.
        title: optional explicit title override.
        today: reference date for relative time phrases (defaults to today;
            exposed for deterministic testing).
    """
    if not isinstance(text, str) or not text.strip():
        raise UnparseableDescription(
            "Empty description.\n"
            "Examples that DO parse:\n" + "\n".join(f"  - {e}" for e in _EXAMPLES)
        )
    today = today or _dt.date.today()
    normalized = " ".join(text.split())

    region = find_region(normalized)
    (variable, overlays, variable_defaulted,
     storm_name, storm_rank, storm_top_n, context) = _parse_variable(normalized)
    start, end, time_phrases = _parse_time(normalized, today)

    if region is None:
        raise UnparseableDescription(
            _failure_message(normalized, region, variable, variable_defaulted, time_phrases, False)
        )

    # Honest refusal (v0.13.0): terrestrial water-quality wording
    # (nitrate, turbidity, "water quality", ...) with no variable
    # keyword is refused instead of defaulting to SST — there is no
    # water-quality adapter, and answering with a temperature map would
    # be a lie. Chlorophyll / algae / bloom wording is the ocean-color
    # variable, not this guard (documented in docs/OCEANCOLOR.md).
    if variable_defaulted and any(
            re.search(p, normalized.lower())
            for p in _TERRESTRIAL_WQ_PATTERNS):
        raise UnparseableDescription(
            "No water-quality adapter: this request asks about a "
            "terrestrial water-quality parameter (nitrate, turbidity, "
            "\"water quality\", ...) for which no fetch adapter exists. "
            "The only water-quality-adjacent product is ocean color "
            "(chlorophyll-a from satellite ocean color) — ask about "
            "\"chlorophyll\", \"algae bloom\", or \"phytoplankton\" "
            "instead.\n"
            "Examples that DO parse:\n" + "\n".join(f"  - {e}" for e in _EXAMPLES)
        )

    default_time = False
    if start is None or end is None:
        # Documented default rule: no time phrase -> past 1 year.
        start, end = _subtract_years(today, 1), today
        default_time = True

    spec_title = title or _derive_title(region["name"], variable, start, end)
    # Sea ice and night lights change fast like fire: one frame per day,
    # not one representative day per month (v0.6.0 for sea ice; v0.8.0
    # for night lights — the Black Marble source is daily). Bathymetry
    # and elevation are static: yearly cadence, and a single frame when
    # no explicit time phrase was given (v0.9.0 — GEBCO is a static
    # compilation, not a time series). Storm tracks are fix-level time
    # series: daily cadence, one frame per day, tracks drawn
    # cumulatively (v0.10.0). Water storage is a monthly product: GRACE
    # always renders one frame per month (v0.11.0). Streamflow is a
    # daily product: USGS daily values render one frame per day
    # (v0.12.0). Earthquakes are event-level: one cumulative frame per
    # day (each frame shows all events with time <= that frame date,
    # v0.14.0). Ocean color is a monthly product by default (the most
    # cloud-complete composite); explicit "daily" wording requests the
    # daily L3 product instead (v0.13.0).
    if variable in ("bathymetry", "elevation"):
        cadence = "yearly"
        if default_time:
            start = end = today
    elif variable == "water-storage":
        cadence = "monthly"
    elif variable == "ocean-color" and re.search(
            r"\bdaily\b", normalized.lower()):
        cadence = "daily"
    else:
        cadence = "daily" if variable in ("fire", "sea-ice", "night-lights",
                                          "storm-tracks", "streamflow",
                                          "earthquakes") else "monthly"
    source, source_reason = _parse_source(normalized, variable)
    return VizSpec(
        title=spec_title,
        region_key=region["key"],
        bbox=region["bbox"],
        variable=variable,
        start=start,
        end=end,
        cadence=cadence,
        layout="reel-vertical",
        style="reel-dark",
        source=source,
        source_reason=source_reason,
        overlays=overlays,
        context=context,
        storm_name=storm_name,
        storm_rank=storm_rank,
        storm_top_n=storm_top_n,
    )
