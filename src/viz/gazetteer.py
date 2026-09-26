"""Built-in YAML gazetteer of named regions.

The canonical file is ``data/regions.yaml`` at the repo root (symlinked to
``src/viz/data/regions.yaml``, which is what ships inside the wheel). This
module resolves the file in the following order:

1. ``SURVEY_VIZ_REGIONS`` environment variable, if set;
2. ``<repo-root>/data/regions.yaml`` (src-layout checkout);
3. ``src/viz/data/regions.yaml`` inside the installed package.

Only the 5 Great Lakes are *fetchable* via GLSEA; ocean-basin regions
fetch via OISST v2.1 / MUR v4.1 (SST) and any region can fetch the ERA5
atmosphere variables (``wind``/``msl``/``t2m``/``tp``) — see
:func:`is_fetchable` and the ``notes`` field on each entry.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml

# The 5 Great Lakes: the only regions with a fetch adapter today.
FETCHABLE_KEYS = (
    "lake-superior",
    "lake-michigan",
    "lake-huron",
    "lake-erie",
    "lake-ontario",
)

_NO_ADAPTER_NOTE = "no adapter yet"


def _candidate_paths() -> List[Path]:
    here = Path(__file__).resolve()
    candidates: List[Path] = []
    env = os.environ.get("SURVEY_VIZ_REGIONS")
    if env:
        candidates.append(Path(env))
    # src-layout checkout: <repo-root>/data/regions.yaml
    candidates.append(here.parent.parent.parent / "data" / "regions.yaml")
    # inside the installed wheel: src/viz/data/regions.yaml
    candidates.append(here.parent / "data" / "regions.yaml")
    return candidates


def regions_path() -> Path:
    for path in _candidate_paths():
        if path.is_file():
            return path
    searched = ", ".join(str(p) for p in _candidate_paths())
    raise FileNotFoundError(
        "survey-viz gazetteer: regions.yaml not found. "
        f"Searched: {searched}. Set SURVEY_VIZ_REGIONS to override."
    )


def load_regions(path: Optional[Path] = None) -> List[Dict]:
    """Load the gazetteer; each entry has key/name/aliases/bbox/notes."""
    with open(path or regions_path(), "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    regions = data.get("regions", [])
    out = []
    for entry in regions:
        out.append(
            {
                "key": entry["key"],
                "name": entry["name"],
                "aliases": list(entry.get("aliases") or []),
                "bbox": tuple(float(x) for x in entry["bbox"]),
                "notes": entry.get("notes", ""),
            }
        )
    return out


def get_region(key: str) -> Optional[Dict]:
    """Return the gazetteer entry for ``key``, or None."""
    for entry in load_regions():
        if entry["key"] == key:
            return entry
    return None


def is_fetchable(key: str) -> bool:
    """True only for regions with a data-fetch adapter today (the 5 Great Lakes)."""
    return key in FETCHABLE_KEYS


def _normalize(text: str) -> str:
    # Periods are dropped so "st. lawrence" matches "st lawrence".
    return " ".join(text.lower().replace(".", "").split())


def find_region(text: str) -> Optional[Dict]:
    """Match the longest gazetteer display name/alias found in ``text``.

    Case-insensitive; longest match wins (so "Lake Superior" beats the bare
    alias "superior" when both appear). Returns the full entry, or None.
    """
    normalized = _normalize(text)
    candidates = []
    for entry in load_regions():
        names = [entry["name"]] + entry["aliases"]
        for name in names:
            needle = _normalize(name)
            if needle and needle in normalized:
                candidates.append((len(needle), entry))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]
