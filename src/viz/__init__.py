"""survey-viz: plain-English descriptions -> vertical map frame sequences.

Three stages, zero AI, zero network in the engine:

1. :mod:`viz.parser` — deterministic offline description -> :class:`VizSpec`
2. :mod:`viz.spec` — the validated spec dataclass (the interop contract)
3. :mod:`viz.render` — spec + duck-typed field/series -> PNG frames + manifest

Peers (survey-currents, survey-animate) are optional and duck-typed —
never hard imports, never hard dependencies.
"""

from __future__ import annotations

# Single source of truth for the package version: the installed
# distribution metadata (written from pyproject.toml at install time).
# This never goes stale the way a hand-edited string does.
try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("survey-viz")
except Exception:  # pragma: no cover - editable/uninstalled checkout
    try:
        __version__ = version("survey_viz")
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"

from .gazetteer import find_region, get_region, is_fetchable, load_regions
from .parser import UnparseableDescription, parse_description
from .render import CURATED_CMAPS, SCHEMA_ID, render_viz
from .sources import (KNOWN_SOURCES, SOURCE_LABELS, default_source,
                      explain_source, fetch_for_source,
                      is_fetchable as is_source_fetchable, resolve_source)
from .spec import VizSpec

__all__ = [
    "__version__",
    "CURATED_CMAPS",
    "SCHEMA_ID",
    "VizSpec",
    "UnparseableDescription",
    "parse_description",
    "render_viz",
    "find_region",
    "get_region",
    "is_fetchable",
    "load_regions",
    "KNOWN_SOURCES",
    "SOURCE_LABELS",
    "default_source",
    "explain_source",
    "resolve_source",
    "fetch_for_source",
    "is_source_fetchable",
]
