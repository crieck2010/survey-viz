"""Source routing: VizSpec -> survey-currents SST fetch adapter.

A VizSpec names *what* to visualize (region, variable, time); this module
decides *which adapter* fetches the data. Peers stay optional: nothing
here imports survey-currents at module import time — the fetch callables
are imported lazily by :func:`fetch_for_source`, and only when a fetch is
actually resolved.

Routing rules (documented in docs/PARSER.md):

* ``spec.source`` set explicitly (``"glsea"`` / ``"oisst"`` / ``"mur"`` /
  ``"era5"`` / ``"oscar"`` / ``"cmems-currents"``) always wins. The
  deterministic parser pins ``"mur"`` when the description asks for
  high resolution / ultra / coastal detail (SST only), and
  ``"cmems-currents"`` when it asks for high resolution / ultra / 1 km
  (currents only).
* Otherwise the regional default applies: SST over one of the 5 Great
  Lakes -> ``"glsea"``; SST anywhere else -> ``"oisst"``;
  ``wind``/``msl``/``t2m``/``tp`` anywhere -> ``"era5"``; ``currents``
  anywhere except the 5 Great Lakes -> ``"oscar"`` (Great Lakes
  ``currents`` stays an honest refusal — no lake-scale current adapter
  exists); ``fire`` anywhere -> ``"firms"`` (the FIRMS area API is
  global).
* ``"chlorophyll"`` and ``"burn-scar"`` have no fetch adapter -> ``""``
  (callers turn this into the honest "no adapter" refusal they already
  produce; burn-scar / burn-severity mapping belongs to survey-burn's
  future imagery adapter, not FIRMS).

Note: the ``"era5"`` adapter is ``currents.era5.fetch_era5``, whose first
argument is the variable list — ``fetch_for_source("era5")`` returns the
raw callable and callers (reel-studio) must supply the variables from
the spec (``[spec.variable] + spec.overlays``).
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from .spec import KNOWN_SOURCES

__all__ = [
    "KNOWN_SOURCES",
    "SOURCE_LABELS",
    "ERA5_VARIABLES",
    "default_source",
    "resolve_source",
    "fetch_for_source",
    "is_fetchable",
]

#: Human-readable labels for fetch-plan messages.
SOURCE_LABELS: Dict[str, str] = {
    "glsea": "NOAA GLSEA",
    "oisst": "NOAA OISST v2.1",
    "mur": "NASA JPL MUR v4.1",
    "era5": "Copernicus ERA5 (CDS)",
    "oscar": "NASA PODAAC OSCAR v2.0",
    "cmems-currents": "CMEMS Global Ocean Physics (daily)",
    "firms": "NASA FIRMS",
}

#: source -> (module, attribute) inside the survey-currents peer,
#: imported lazily by fetch_for_source.
_SOURCE_ADAPTERS: Dict[str, tuple] = {
    "glsea": ("currents.glsea", "fetch_glsea_sst"),
    "oisst": ("currents.sst_global", "fetch_oisst"),
    "mur": ("currents.sst_global", "fetch_mur"),
    "era5": ("currents.era5", "fetch_era5"),
    "oscar": ("currents.currents_global", "fetch_oscar"),
    "cmems-currents": ("currents.currents_global", "fetch_cmems_currents"),
    "firms": ("currents.fires", "fetch_firms"),
}

#: Minimum survey-currents version providing each adapter (used for the
#: honest upgrade message in fetch_for_source).
_SOURCE_MIN_VERSIONS: Dict[str, str] = {
    "glsea": "0.2.0",
    "oisst": "0.3.0",
    "mur": "0.3.0",
    "era5": "0.4.0",
    "oscar": "0.5.0",
    "cmems-currents": "0.5.0",
    "firms": "0.6.0",
}

#: Variables whose regional default source is ERA5 (any region).
ERA5_VARIABLES = ("wind", "msl", "t2m", "tp")


def _great_lakes_keys() -> frozenset:
    from .gazetteer import FETCHABLE_KEYS
    return frozenset(FETCHABLE_KEYS)


def default_source(variable: str, region_key: str) -> str:
    """Regional default adapter for ``(variable, region_key)``.

    Returns ``"glsea"`` for SST over the 5 Great Lakes, ``"oisst"`` for
    SST anywhere else, ``"era5"`` for the ERA5 variables
    (``wind``/``msl``/``t2m``/``tp``) in any region, ``"oscar"`` for
    ``currents`` in any region except the 5 Great Lakes (Great Lakes
    currents stay an honest refusal), ``"firms"`` for ``fire`` in any
    region (the FIRMS area API is global), and ``""`` when no adapter
    exists (``chlorophyll``, ``burn-scar`` — burned-area / burn-severity
    mapping is survey-burn's future domain, not FIRMS').
    """
    variable = str(variable)
    if variable == "sst":
        if str(region_key) in _great_lakes_keys():
            return "glsea"
        return "oisst"
    if variable in ERA5_VARIABLES:
        return "era5"
    if variable == "currents":
        if str(region_key) in _great_lakes_keys():
            return ""
        return "oscar"
    if variable == "fire":
        return "firms"
    return ""


def resolve_source(spec: Any) -> str:
    """Effective fetch-adapter name for ``spec`` (``""`` when none).

    An explicit :attr:`VizSpec.source` always wins; otherwise the
    regional default from :func:`default_source` applies. ``spec`` is
    duck-typed (``source``/``variable``/``region_key`` attributes) so
    callers can pass test doubles.
    """
    explicit = str(getattr(spec, "source", "") or "").strip().lower()
    if explicit:
        if explicit not in KNOWN_SOURCES:
            raise ValueError(
                f"unknown source {explicit!r}; expected one of {KNOWN_SOURCES}")
        return explicit
    return default_source(getattr(spec, "variable", ""),
                          getattr(spec, "region_key", ""))


def fetch_for_source(source: str) -> Callable:
    """Lazily import and return the fetch callable for ``source``.

    Raises:
        ValueError: unknown source name.
        ImportError: survey-currents (or the needed submodule) is not
            installed — the message names the ``pip install`` fix.
    """
    if source not in _SOURCE_ADAPTERS:
        raise ValueError(
            f"unknown source {source!r}; expected one of {tuple(_SOURCE_ADAPTERS)}")
    module_name, attr = _SOURCE_ADAPTERS[source]
    min_version = _SOURCE_MIN_VERSIONS.get(source, "0.3.0")
    try:
        import importlib
        module = importlib.import_module(module_name)
        return getattr(module, attr)
    except ImportError as exc:
        raise ImportError(
            "resolving source "
            f"'{source}' needs survey-currents>={min_version} ({module_name}.{attr}), "
            "but it is not installed or is too old.\nInstall it with:\n\n"
            "    pip install git+https://github.com/crieck2010/survey-currents.git"
        ) from exc


def is_fetchable(spec: Any) -> bool:
    """True when :func:`resolve_source` finds an adapter for ``spec``.

    Spec-aware (unlike ``viz.gazetteer.is_fetchable``, which is
    region-key-only and kept for backward compatibility).
    """
    return bool(resolve_source(spec))
