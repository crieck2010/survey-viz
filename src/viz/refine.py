"""viz.refine: plain-language refinements to an existing :class:`VizSpec`.

Where :func:`viz.parser.parse_description` turns a *new* description into
a spec from scratch, :func:`refine_spec` edits an *existing* spec from a
short follow-up instruction such as::

    "zoom in on Florida and use a warmer colormap"
    "title it 'Gulf Heat' and run it from 2015 to 2020"
    "switch to light mode"

The instruction is parsed with the same deterministic machinery as
descriptions (region gazetteer, variable / time / source detectors),
and **only the fields the instruction actually mentions are changed** —
everything else in the spec is preserved. The result reports every
applied change (old value -> new value, with the reason) and notes
anything the instruction asked for that could not be understood, so a
UI can show the user exactly what happened instead of silently
guessing.

Supported refinement intents (documented, tested in
``tests/test_refine.py``):

* title: ``title it "..."``, ``call it ...``, ``change the title to ...``,
  ``rename to ...``
* caption: ``add a caption saying ...``, ``set the caption to ...``,
  ``remove/clear the caption``
* colormap: ``use the <name> colormap``, ``change the colormap to <name>``
  (validated against :data:`viz.render.CURATED_CMAPS`; unknown names are
  reported, not guessed), ``warmer colors`` -> ``"inferno"``,
  ``cooler colors`` -> ``"cool"``
* style: ``dark mode`` / ``light mode``
* basemap underlay: ``turn off/disable/remove the basemap``,
  ``turn on/enable/add the basemap``
* region: ``zoom in on <place>`` / ``focus on <place>`` / ``show <place>``
  (gazetteer lookup); bare ``zoom in`` / ``zoom out`` tightens or
  widens the current bbox around its center
* time: any time phrase the description parser understands
  (``2015 to 2020``, ``last 5 years``, ``last summer``, ...)
* variable: any variable phrase the description parser understands
  (``show sea ice instead``, ``switch to precipitation``) — the
  cadence is recomputed with the same rules as a fresh parse, and
  overlays / context / storm fields follow the variable
* source: explicit product pins (``use the MUR product``)

Two deliberate behaviors worth knowing:

1. **Automatic titles follow the spec.** If the spec's title is exactly
   what the parser would have derived for its region/variable/dates
   (i.e. nobody customized it), a region/variable/time change
   re-derives the title. A customized title is never touched unless
   the instruction mentions the title.
2. **The colormap is not a spec field.** :class:`VizSpec` carries no
   colormap (rendering takes it as a ``render_viz(..., cmap=...)``
   argument), so :class:`RefineResult` carries the requested ``cmap``
   separately for the caller to pass through.

The module is pure: no I/O, no network, no global state — safe to call
from threads, batch jobs, or a future server endpoint.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .gazetteer import find_region, get_region
# Package-internal parser machinery, shared (not duplicated) with
# parse_description so refinements behave exactly like fresh parses.
from .parser import (_cadence_for, _derive_title, _parse_source,
                     _parse_time, _parse_variable, UnparseableDescription)
from .render import CURATED_CMAPS
from .spec import VizSpec

__all__ = ["SpecChange", "RefineResult", "refine_spec",
           "SUPPORTED_REFINEMENTS"]

#: Human-readable list of what refine_spec understands (used in the
#: "nothing matched" error so the user learns the vocabulary).
SUPPORTED_REFINEMENTS = (
    'title: title it "..." / call it ... / change the title to ...',
    "caption: add a caption saying ... / remove the caption",
    "colormap: use the <name> colormap / warmer colors / cooler colors",
    "style: dark mode / light mode",
    "basemap: turn off the basemap / turn on the basemap",
    "region: zoom in on <place> / focus on <place> / zoom in / zoom out",
    "time: 2015 to 2020 / last 5 years / last summer / ...",
    "variable: show sea ice instead / switch to precipitation",
    "source: use the MUR product",
)

# Curated-spelling lookup for case-insensitive colormap names.
_CMAP_CANONICAL = {name.lower(): name for name in CURATED_CMAPS}
#: Mood words mapped to curated colormaps (documented heuristic).
_MOOD_CMAPS = {"warmer": "inferno", "cooler": "cool"}

_TITLE_PATTERNS = (
    re.compile(r"\btitle\s+(?:it|this)\s+[\u201c\"'](.+?)[\u201d\"']"),
    re.compile(r"\b(?:change|set)\s+the\s+title\s+to\s+[\u201c\"']?(.+?)"
               r"[\u201d\"']?\s*$"),
    re.compile(r"\bcall\s+it\s+[\u201c\"']?(.+?)[\u201d\"']?\s*$"),
    re.compile(r"\brename\s+(?:it\s+)?to\s+[\u201c\"']?(.+?)[\u201d\"']?\s*$"),
)
_CAPTION_SET_PATTERNS = (
    re.compile(r"\badd\s+a\s+caption\s+(?:saying|that\s+says|:)?\s*"
               r"[\u201c\"']?(.+?)[\u201d\"']?\s*$"),
    re.compile(r"\bset\s+(?:the\s+)?caption\s+to\s+"
               r"[\u201c\"']?(.+?)[\u201d\"']?\s*$"),
)
_CAPTION_CLEAR = re.compile(r"\b(?:remove|clear)\s+(?:the\s+)?caption\b")
_CMAP_PATTERNS = (
    re.compile(r"\buse\s+(?:the\s+)?([\w-]+)\s+colou?r\s*map\b"),
    re.compile(r"\b(?:change|switch|set)\s+(?:the\s+)?colou?r\s*map\s+to\s+"
               r"([\w-]+)\b"),
)
_MOOD_CMAP = re.compile(r"\b(warmer|cooler)\s+colou?rs?(\s*map)?\b")
_DARK_MODE = re.compile(r"\bdark\s+mode\b")
_LIGHT_MODE = re.compile(r"\blight\s+mode\b")
_UNDERLAY_OFF = re.compile(
    r"\b(?:turn\s+(?:the\s+)?basemap\s+off|"
    r"(?:turn\s+off|disable|remove|hide)\s+(?:the\s+)?basemap)\b")
_UNDERLAY_ON = re.compile(
    r"\b(?:turn\s+(?:the\s+)?basemap\s+(?:back\s+)?on|"
    r"(?:turn\s+on|enable|add|show)\s+(?:the\s+)?basemap)\b")
_REGION_VERBS = re.compile(
    r"\b(?:zoom\s+in\s+on|zoom\s+on|focus\s+(?:in\s+)?on|center\s+(?:in\s+)?on|"
    r"show|switch\s+to|change\s+(?:the\s+)?(?:region|area)\s+to|"
    r"pan\s+to)\b")
_ZOOM_IN_BARE = re.compile(r"\bzoom\s+in\b")
_ZOOM_OUT_BARE = re.compile(r"\bzoom\s+out\b")


@dataclass
class SpecChange:
    """One field changed by :func:`refine_spec`."""
    field: str
    old: Any
    new: Any
    reason: str


@dataclass
class RefineResult:
    """The outcome of :func:`refine_spec`.

    Attributes:
        spec: the refined :class:`VizSpec` (a new object; the input is
            never mutated).
        cmap: requested colormap override (``None`` = the instruction
            did not mention one — pass through whatever the caller
            was already using).
        applied: the changes made, in instruction order.
        unparsed: notes about parts of the instruction that could not
            be understood or applied (never silently ignored).
    """
    spec: VizSpec
    cmap: Optional[str]
    applied: List[SpecChange] = field(default_factory=list)
    unparsed: List[str] = field(default_factory=list)


def _clean_capture(text: str) -> str:
    return text.strip().strip("\"'\u201c\u201d").strip()


def _detect_title(normalized: str) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(title, raw_span)``; ``raw_span`` is the matched text."""
    for pattern in _TITLE_PATTERNS:
        match = pattern.search(normalized)
        if match:
            title = _clean_capture(match.group(1))
            if title:
                return title, match.group(0)
    return None, None


def _detect_caption(normalized: str) -> Tuple[Optional[str], bool, Optional[str]]:
    """Return ``(caption_text_or_None, clear_requested, raw_span)``."""
    match = _CAPTION_CLEAR.search(normalized)
    if match:
        return None, True, match.group(0)
    for pattern in _CAPTION_SET_PATTERNS:
        match = pattern.search(normalized)
        if match:
            caption = _clean_capture(match.group(1))
            if caption:
                return caption, False, match.group(0)
    return None, False, None


def _detect_cmap(normalized: str) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(canonical_cmap_or_None, error_note_or_None)``."""
    # Mood words first: "use a warmer colormap" must not be read as a
    # colormap literally named "warmer".
    match = _MOOD_CMAP.search(normalized)
    if match:
        return _MOOD_CMAPS[match.group(1).lower()], None
    for pattern in _CMAP_PATTERNS:
        match = pattern.search(normalized)
        if match:
            name = match.group(1)
            canonical = _CMAP_CANONICAL.get(name.lower())
            if canonical is None:
                return None, (
                    f"unknown colormap {name!r} — not one of the "
                    f"{len(CURATED_CMAPS)} curated choices; "
                    "the colormap was left unchanged")
            return canonical, None
    return None, None


#: Explicit product names a refinement can pin, mapped to
#: ``(source, applicable variables)``. ``_parse_source`` (shared with
#: the description parser) only pins on *quality* wording
#: ("high-resolution", "ultra"); naming the product outright
#: ("use the MUR product") is a refinement-level intent handled here.
#: A name that does not fit the spec's variable is reported, not
#: applied.
_EXPLICIT_PRODUCTS = {
    "mur": ("mur", ("sst",)),
    "oisst": ("oisst", ("sst",)),
    "glsea": ("glsea", ("sst",)),
    "era5": ("era5", ("wind", "msl", "t2m", "tp")),
    "imerg": ("imerg", ("tp",)),
    "gpm": ("imerg", ("tp",)),
    "cmems": ("cmems-currents", ("currents",)),
    "oscar": ("oscar", ("currents",)),
    "firms": ("firms", ("fire", "burn-scar")),
    "nsidc": ("nsidc", ("sea-ice", "land-ice")),
    "black marble": ("blackmarble", ("night-lights", "power-outage")),
    "grace": ("grace", ("water-storage",)),
    "ibtracs": ("ibtracs", ("storm-tracks",)),
    "usgs": ("usgs", ("streamflow",)),
    "comcat": ("comcat", ("earthquakes",)),
}


def _pin_explicit_product(scan: str, values: Dict[str, Any],
                          set_field, unparsed: List[str]) -> None:
    lowered = scan.lower()
    variable = values["variable"]
    for name, (source, variables) in _EXPLICIT_PRODUCTS.items():
        if re.search(r"\b" + re.escape(name) + r"\b", lowered):
            if variable in variables:
                set_field("source", source,
                          f"instruction named the {name.upper()} product")
                values["source_reason"] = (
                    f"refinement named the {name.upper()} product")
            else:
                unparsed.append(
                    f"{name.upper()} does not serve the {variable!r} "
                    f"variable — the source was left unchanged")
            return


def _zoom_bbox(bbox: Tuple[float, float, float, float],
               factor: float) -> Tuple[float, float, float, float]:
    """Scale ``bbox`` about its center by ``factor`` (<1 zooms in)."""
    lon_min, lat_min, lon_max, lat_max = bbox
    clon, clat = (lon_min + lon_max) / 2.0, (lat_min + lat_max) / 2.0
    dlon, dlat = (lon_max - lon_min) * factor / 2.0, (lat_max - lat_min) * factor / 2.0
    return (max(-180.0, clon - dlon), max(-90.0, clat - dlat),
            min(180.0, clon + dlon), min(90.0, clat + dlat))


def refine_spec(spec: VizSpec, instruction: str,
                today: Optional[_dt.date] = None) -> RefineResult:
    """Refine ``spec`` according to a plain-language ``instruction``.

    Only fields the instruction mentions are changed; everything else
    is preserved. Raises :class:`UnparseableDescription` when the
    instruction matches no known refinement intent at all.

    Args:
        spec: the spec to refine (never mutated — a new spec is built).
        instruction: e.g. ``"zoom in on Florida and use inferno"``.
        today: reference date for relative time phrases (defaults to
            today; exposed for deterministic testing).
    """
    if not isinstance(instruction, str) or not instruction.strip():
        raise UnparseableDescription(
            "Empty refinement instruction.\n"
            "Refinements I understand:\n"
            + "\n".join(f"  - {s}" for s in SUPPORTED_REFINEMENTS))
    today = today or _dt.date.today()
    normalized = " ".join(instruction.split())
    lowered = normalized.lower()

    # Original values, for change records and auto-title detection.
    orig = {"title": spec.title, "region_key": spec.region_key,
            "bbox": spec.bbox, "variable": spec.variable,
            "start": spec.start, "end": spec.end,
            "style": spec.style, "underlay": spec.underlay,
            "caption": spec.caption, "source": spec.source,
            "source_reason": spec.source_reason,
            "overlays": spec.overlays, "context": spec.context,
            "cadence": spec.cadence}

    values: Dict[str, Any] = dict(spec.to_dict())
    # to_dict serializes dates/bbox/overlays; rebuild native forms.
    values["start"] = spec.start
    values["end"] = spec.end
    values["bbox"] = spec.bbox
    values["overlays"] = spec.overlays
    values["context"] = spec.context

    applied: List[SpecChange] = []
    unparsed: List[str] = []
    cmap: Optional[str] = None
    title_explicit = False

    def set_field(field_name: str, new_value: Any, reason: str) -> None:
        old_value = values[field_name]
        if old_value != new_value:
            values[field_name] = new_value
            applied.append(SpecChange(field_name, old_value, new_value,
                                      reason))

    # --- title ---
    title, title_span = _detect_title(normalized)
    if title is not None:
        title_explicit = True
        set_field("title", title, f"instruction set the title to {title!r}")

    # --- caption ---
    caption, clear_caption, caption_span = _detect_caption(normalized)
    if clear_caption:
        set_field("caption", None, "instruction cleared the caption")
    elif caption is not None:
        set_field("caption", caption,
                  f"instruction set the caption to {caption!r}")

    # The remaining detectors scan the instruction with the literal
    # title/caption text removed: quoted frame text is content to burn
    # into the image, not further instruction ("title it 'Ocean
    # Currents'" must not switch the variable to currents).
    scan = normalized
    for span in (title_span, caption_span):
        if span:
            scan = scan.replace(span, " ")
    scan_lower = scan.lower()

    # --- colormap (not a spec field; carried on the result) ---
    new_cmap, cmap_error = _detect_cmap(lowered)
    if cmap_error is not None:
        unparsed.append(cmap_error)
    elif new_cmap is not None:
        # refine_spec does not know the caller's current colormap, so
        # the "old" side names the standing default.
        applied.append(SpecChange("colormap", "(previous setting)",
                                  new_cmap,
                                  "instruction requested the "
                                  f"{new_cmap!r} colormap"))
        cmap = new_cmap

    # --- style ---
    if _DARK_MODE.search(scan_lower):
        set_field("style", "reel-dark", "instruction requested dark mode")
    elif _LIGHT_MODE.search(scan_lower):
        set_field("style", "light", "instruction requested light mode")

    # --- basemap underlay ---
    if _UNDERLAY_OFF.search(scan_lower):
        set_field("underlay", False, "instruction turned the basemap off")
    elif _UNDERLAY_ON.search(scan_lower):
        set_field("underlay", True, "instruction turned the basemap on")

    # --- region ---
    region = find_region(scan)
    if region is not None and _REGION_VERBS.search(scan_lower):
        set_field("region_key", region["key"],
                  f"instruction refocused on {region['name']!r}")
        set_field("bbox", tuple(region["bbox"]),
                  f"instruction refocused on {region['name']!r}")
    elif region is None:
        if _ZOOM_IN_BARE.search(scan_lower):
            set_field("bbox", _zoom_bbox(values["bbox"], 0.5),
                      "instruction zoomed in (bbox halved about its center)")
        elif _ZOOM_OUT_BARE.search(scan_lower):
            set_field("bbox", _zoom_bbox(values["bbox"], 2.0),
                      "instruction zoomed out (bbox doubled about its "
                      "center)")

    # --- time ---
    try:
        start, end, time_phrases = _parse_time(scan, today)
    except UnparseableDescription as exc:
        unparsed.append(str(exc).split("\n")[0])
        start, end, time_phrases = None, None, []
    time_mentioned = bool(time_phrases)
    if time_mentioned:
        set_field("start", start, f"instruction set the time window "
                                  f"({time_phrases[0]})")
        set_field("end", end, f"instruction set the time window "
                              f"({time_phrases[0]})")

    # --- variable (plus overlays / context / storm fields / cadence) ---
    try:
        (variable, overlays, variable_defaulted, storm_name, storm_rank,
         storm_top_n, context) = _parse_variable(scan)
    except UnparseableDescription as exc:
        unparsed.append(str(exc).split("\n")[0])
        variable, variable_defaulted = orig["variable"], True
        overlays, context = orig["overlays"], orig["context"]
        storm_name, storm_rank, storm_top_n = "", "", None
    if not variable_defaulted:
        set_field("variable", variable,
                  f"instruction switched the variable to {variable!r}")
        set_field("overlays", overlays,
                  f"overlays follow the {variable!r} variable")
        set_field("context", context,
                  f"context follows the {variable!r} variable")
        if variable == "storm-tracks":
            values["storm_name"] = storm_name
            values["storm_rank"] = storm_rank
            values["storm_top_n"] = storm_top_n
            if storm_name or storm_rank:
                applied.append(SpecChange(
                    "storm selection", "", storm_name or storm_rank,
                    "instruction named a storm selection"))
        # Cadence follows the same rules as a fresh parse.
        new_start, new_end, new_cadence = _cadence_for(
            variable, scan, not time_mentioned,
            values["start"], values["end"], today)
        set_field("cadence", new_cadence,
                  f"cadence recomputed for the {variable!r} variable")
        if (new_start, new_end) != (values["start"], values["end"]):
            values["start"], values["end"] = new_start, new_end
            applied.append(SpecChange(
                "time window", (orig["start"], orig["end"]),
                (new_start, new_end),
                f"static {variable!r} data renders a single frame"))

    # --- source pin ---
    source, source_reason = _parse_source(scan, values["variable"])
    if source:
        set_field("source", source,
                  f"instruction pinned the {source!r} product")
        values["source_reason"] = source_reason
    else:
        _pin_explicit_product(scan, values, set_field, unparsed)

    # --- automatic title follows region/variable/time changes ---
    geo_changed = any(c.field in ("region_key", "variable", "start", "end")
                      for c in applied)
    if geo_changed and not title_explicit:
        old_region = get_region(orig["region_key"])
        if old_region is not None and orig["title"] == _derive_title(
                old_region["name"], orig["variable"],
                orig["start"], orig["end"]):
            new_region = get_region(values["region_key"])
            if new_region is not None:
                new_title = _derive_title(new_region["name"],
                                          values["variable"],
                                          values["start"], values["end"])
                set_field("title", new_title,
                          "title was automatic, so it followed the "
                          "region/variable/time change")
        elif old_region is not None:
            unparsed.append(
                "the title was left as-is (it looks customized) — say "
                "'title it ...' to retitle")

    if not applied and not unparsed:
        raise UnparseableDescription(
            f"Could not understand {instruction!r} as a refinement.\n"
            "Refinements I understand:\n"
            + "\n".join(f"  - {s}" for s in SUPPORTED_REFINEMENTS))

    new_spec = VizSpec(**values)
    return RefineResult(spec=new_spec, cmap=cmap, applied=applied,
                        unparsed=unparsed)
