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
    "chlorophyll": [
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
}

# Storm keywords: they name the ("wind", overlays=["msl"]) combination
# (wind base map + pressure isobars), and race in the same earliest-wins
# contest as the variable groups above.
_STORM_PATTERNS = [
    r"\bstorms?\b",
    r"\bcyclones?\b",
    r"\bhurricanes?\b",
    r"\btyphoons?\b",
]

_VARIABLE_LABELS = {
    "sst": "Surface Water Temperature",
    "currents": "Surface Currents",
    "chlorophyll": "Chlorophyll-a",
    "wind": "10-m Wind",
    "msl": "Sea-Level Pressure",
    "t2m": "2-m Air Temperature",
    "tp": "Precipitation",
    "fire": "Active Fires",
    "burn-scar": "Burn Scar",
    "sea-ice": "Sea Ice",
    "land-ice": "Land Ice",
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


def _parse_variable(text: str) -> Tuple[str, Tuple[str, ...], bool]:
    """Return (variable, overlays, defaulted).

    Earliest keyword in the text wins; storm keywords ("storm",
    "cyclone", "hurricane", "typhoon") compete in the same race and
    map to the ("wind", overlays=("msl",)) combination. No keyword ->
    ("sst", (), True).
    """
    lowered = text.lower()
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
        return "sst", (), True
    return best[1], best[2], False


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
    variable, overlays, variable_defaulted = _parse_variable(normalized)
    start, end, time_phrases = _parse_time(normalized, today)

    if region is None:
        raise UnparseableDescription(
            _failure_message(normalized, region, variable, variable_defaulted, time_phrases, False)
        )

    default_time = False
    if start is None or end is None:
        # Documented default rule: no time phrase -> past 1 year.
        start, end = _subtract_years(today, 1), today
        default_time = True

    spec_title = title or _derive_title(region["name"], variable, start, end)
    # Sea ice changes fast like fire: one frame per day, not one
    # representative day per month (v0.6.0; the NSIDC source is daily).
    cadence = "daily" if variable in ("fire", "sea-ice") else "monthly"
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
    )
