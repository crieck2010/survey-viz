"""survey-viz: plain-English descriptions -> vertical map frame sequences.

Three stages, zero AI, zero network in the engine:

1. :mod:`viz.parser` — deterministic offline description -> :class:`VizSpec`
2. :mod:`viz.spec` — the validated spec dataclass (the interop contract)
3. :mod:`viz.render` — spec + duck-typed field/series -> PNG frames + manifest

Peers (survey-currents, survey-animate) are optional and duck-typed —
never hard imports, never hard dependencies.
"""

__version__ = "0.6.1"

from .gazetteer import find_region, get_region, is_fetchable, load_regions
from .parser import UnparseableDescription, parse_description
from .render import SCHEMA_ID, render_viz
from .sources import (KNOWN_SOURCES, SOURCE_LABELS, default_source,
                      fetch_for_source, is_fetchable as is_source_fetchable,
                      resolve_source)
from .spec import VizSpec

__all__ = [
    "__version__",
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
    "resolve_source",
    "fetch_for_source",
    "is_source_fetchable",
]
