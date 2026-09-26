"""Source routing: VizSpec -> survey-currents SST fetch adapter.

A VizSpec names *what* to visualize (region, variable, time); this module
decides *which adapter* fetches the data. Peers stay optional: nothing
here imports survey-currents at module import time — the fetch callables
are imported lazily by :func:`fetch_for_source`, and only when a fetch is
actually resolved.

Routing rules (documented in docs/PARSER.md):

* ``spec.source`` set explicitly (``"glsea"`` / ``"oisst"`` / ``"mur"``)
  always wins. The deterministic parser pins ``"mur"`` when the
  description asks for high resolution / ultra / coastal detail.
* Otherwise the regional default applies: SST over one of the 5 Great
  Lakes -> ``"glsea"``; SST anywhere else -> ``"oisst"``.
* Non-SST variables have no fetch adapter yet -> ``""`` (callers turn
  this into the honest "no adapter" refusal they already produce).
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from .spec import KNOWN_SOURCES

__all__ = [
    "KNOWN_SOURCES",
    "SOURCE_LABELS",
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
}

#: source -> (module, attribute) inside the survey-currents peer,
#: imported lazily by fetch_for_source.
_SOURCE_ADAPTERS: Dict[str, tuple] = {
    "glsea": ("currents.glsea", "fetch_glsea_sst"),
    "oisst": ("currents.sst_global", "fetch_oisst"),
    "mur": ("currents.sst_global", "fetch_mur"),
}


def _great_lakes_keys() -> frozenset:
    from .gazetteer import FETCHABLE_KEYS
    return frozenset(FETCHABLE_KEYS)


def default_source(variable: str, region_key: str) -> str:
    """Regional default adapter for ``(variable, region_key)``.

    Returns ``"glsea"`` for SST over the 5 Great Lakes, ``"oisst"`` for
    SST anywhere else, and ``""`` when no adapter exists (non-SST
    variables).
    """
    if str(variable) == "sst":
        if str(region_key) in _great_lakes_keys():
            return "glsea"
        return "oisst"
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
    try:
        import importlib
        module = importlib.import_module(module_name)
        return getattr(module, attr)
    except ImportError as exc:
        raise ImportError(
            "resolving source "
            f"'{source}' needs survey-currents>=0.3.0 ({module_name}.{attr}), "
            "but it is not installed or is too old.\nInstall it with:\n\n"
            "    pip install git+https://github.com/crieck2010/survey-currents.git"
        ) from exc


def is_fetchable(spec: Any) -> bool:
    """True when :func:`resolve_source` finds an adapter for ``spec``.

    Spec-aware (unlike ``viz.gazetteer.is_fetchable``, which is
    region-key-only and kept for backward compatibility).
    """
    return bool(resolve_source(spec))
