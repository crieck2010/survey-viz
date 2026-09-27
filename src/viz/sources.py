"""Source routing: VizSpec -> survey-currents SST fetch adapter.

A VizSpec names *what* to visualize (region, variable, time); this module
decides *which adapter* fetches the data. Peers stay optional: nothing
here imports survey-currents at module import time — the fetch callables
are imported lazily by :func:`fetch_for_source`, and only when a fetch is
actually resolved.

Routing rules (documented in docs/PARSER.md):

* ``spec.source`` set explicitly (``"glsea"`` / ``"oisst"`` / ``"mur"`` /
  ``"era5"`` / ``"imerg"`` / ``"oscar"`` / ``"cmems-currents"``)
  always wins. The deterministic parser pins ``"mur"`` when the
  description asks for high resolution / ultra / coastal detail (SST
  only), ``"cmems-currents"`` when it asks for high resolution /
  ultra / 1 km (currents only), and ``"imerg"`` / ``"era5"`` for
  precipitation (``"tp"``) requests as described above.
* Otherwise the regional default applies: SST over one of the 5 Great
  Lakes -> ``"glsea"``; SST anywhere else -> ``"oisst"``;
  ``wind``/``msl``/``t2m``/``tp`` anywhere -> ``"era5"``; ``currents``
  anywhere except the 5 Great Lakes -> ``"oscar"`` (Great Lakes
  ``currents`` stays an honest refusal — no lake-scale current adapter
  exists); ``fire`` anywhere -> ``"firms"`` (the FIRMS area API is
  global); ``sea-ice`` in the polar regions (``arctic-ocean``,
  ``southern-ocean``) -> ``"nsidc"`` (NSIDC G02135; sea ice elsewhere
  stays an honest refusal — the product has no mid-latitude ice domain).
* ``"tp"`` may be *pinned* by the deterministic parser to ``"imerg"``
  (NASA GPM IMERG V07) for explicit requests and recent / observed /
  event wording (``recent``, ``last week``, ``event``, ``storm``,
  ``hurricane``, ``observed``, ``satellite``, ``high resolution``,
  ``ultra``), or to ``"era5"`` for explicit requests and long-record
  wording (``trend``, ``climatology``, ``since 19…`` / ``since 20…``,
  ``decades``, ``long-term``). Unpinned ``"tp"`` keeps the regional
  default ``"era5"`` — the observed-vs-reanalysis distinction is
  documented in docs/PARSER.md.
* ``"night-lights"`` anywhere -> ``"blackmarble"`` (NASA Black Marble
  VNP46A2 V002 daily, pinned by the parser with an inspectable
  ``source_reason``). ``"power-outage"`` (blackout / power-outage
  wording) has no adapter -> ``""``: outage mapping is temporal
  change detection, and a single-epoch lights map would be a lie.
* ``"storm-tracks"`` anywhere -> ``"ibtracs"`` (NOAA IBTrACS v04r01
  best tracks, pinned by the parser with an inspectable
  ``source_reason`` — the archive is global, so no regional
  restriction applies).
* ``"water-storage"`` anywhere -> ``"grace"`` (CSR GRACE/GRACE-FO
  RL06.3 terrestrial water storage anomalies, monthly, cm LWE,
  land-only — the archive is global, so no regional restriction
  applies).
* ``"streamflow"`` anywhere -> ``"usgs"`` (USGS Water Services NWIS
  streamgage daily values, daily MEAN discharge/gage height — the
  network is US-only, so bboxes outside USGS coverage yield an honest
  empty field; the gage renderer turns that into an explicit
  empty-frame message rather than fabricated data).
* ``"sea-level"`` (without "pressure") has no fetch adapter ->
  ``""``: sea level is satellite altimetry, a different observable
  from GRACE terrestrial water storage, so it is refused rather than
  answered with a GRACE map.
* ``"chlorophyll"``, ``"burn-scar"``, ``"land-ice"``, and
  ``"power-outage"`` have no fetch adapter -> ``""`` (callers turn this
  into the honest "no adapter" refusal they already produce;
  burn-scar / burn-severity mapping belongs to survey-burn's future
  imagery adapter, not FIRMS; glaciers / ice sheets / icebergs are a
  different physical product from sea-ice concentration, not NSIDC).

Note: the ``"era5"`` adapter is ``currents.era5.fetch_era5``, whose first
argument is the variable list — ``fetch_for_source("era5")`` returns the
raw callable and callers (reel-studio) must supply the variables from
the spec (``[spec.variable] + spec.overlays``).
Note: the ``"nsidc"`` adapter is ``currents.sea_ice.fetch_nsidc_sic`` —
``fetch_for_source("nsidc")`` returns the raw callable and callers pass
``(bbox, start, end, stride_days=...)`` positionally, like the
global-SST adapters (hemisphere is auto-picked from the bbox).
Note: the ``"imerg"`` adapter is ``currents.imerg.fetch_imerg`` —
``fetch_for_source("imerg")`` returns the raw callable and callers pass
``(bbox, start, end, accumulate=..., run=..., stride_days=...)``;
reel-studio uses the ``fetch_imerg`` defaults (``accumulate="daily"``,
``run="late"``).
Note: the ``"blackmarble"`` adapter is
``currents.blackmarble.fetch_blackmarble`` —
``fetch_for_source("blackmarble")`` returns the raw callable and
callers pass ``(bbox, start, end, product="daily", stride_days=...,
resolution=...)`` positionally.
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
    "explain_source",
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
    "nsidc": "NSIDC Sea Ice Index (G02135 v4.0)",
    "imerg": "NASA GPM IMERG V07",
    "blackmarble": "NASA Black Marble VNP46A2",
    "gebco": "GEBCO 2024",
    "ibtracs": "NOAA IBTrACS v04r01",
    "grace": "CSR GRACE/GRACE-FO RL06.3",
    "usgs": "USGS Water Services (NWIS)",
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
    "nsidc": ("currents.sea_ice", "fetch_nsidc_sic"),
    "imerg": ("currents.imerg", "fetch_imerg"),
    "blackmarble": ("currents.blackmarble", "fetch_blackmarble"),
    "gebco": ("currents.basemaps", "fetch_gebco"),
    "ibtracs": ("currents.storms", "fetch_ibtracs"),
    "grace": ("currents.grace", "fetch_grace"),
    "usgs": ("currents.streamgages", "fetch_usgs"),
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
    "nsidc": "0.7.0",
    "imerg": "0.8.0",
    "blackmarble": "0.9.0",
    "gebco": "0.10.0",
    "ibtracs": "0.11.0",
    "grace": "0.12.0",
    "usgs": "0.13.0",
}

#: Variables whose regional default source is ERA5 (any region).
ERA5_VARIABLES = ("wind", "msl", "t2m", "tp")

#: Region keys whose ice domain the NSIDC Sea Ice Index covers. ``sea-ice``
#: is only routed to NSIDC for these polar regions — the G02135 grids
#: cover north of 30.98°N / south of 39.23°S, and fetching mid-latitude
#: ocean "sea ice" would return an all-NaN field.
POLAR_REGION_KEYS = frozenset({"arctic-ocean", "southern-ocean"})


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
    region (the FIRMS area API is global), ``"nsidc"`` for ``sea-ice``
    in the polar regions only (mid-latitude sea ice has no ice domain
    in the product), ``"grace"`` for ``"water-storage"`` in any region
    (the CSR mascon archive is global), and ``""`` when no adapter
    exists (``chlorophyll``, ``burn-scar`` — burned-area / burn-severity
    mapping is survey-burn's future domain, not FIRMS; ``land-ice`` —
    glaciers / ice sheets / icebergs are a different physical product
    from sea-ice concentration).
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
    if variable == "sea-ice":
        # NSIDC G02135 (survey-currents >= 0.7.0) is only honest for the
        # polar regions — mid-latitude "sea ice" has no ice domain in the
        # product and would return an all-NaN field.
        if str(region_key) in POLAR_REGION_KEYS:
            return "nsidc"
        return ""
    # "land-ice" (glaciers / ice sheets / icebergs) has no adapter: land
    # ice is a different physical product from sea-ice concentration,
    # and routing it to NSIDC would be a lie.
    if variable == "night-lights":
        return "blackmarble"
    # "bathymetry" / "elevation" route globally to GEBCO 2024 — the grid
    # is global, so there is no regional restriction.
    if variable in ("bathymetry", "elevation"):
        return "gebco"
    # "storm-tracks" route globally to IBTrACS — the archive is global,
    # so there is no regional restriction.
    if variable == "storm-tracks":
        return "ibtracs"
    # "water-storage" routes globally to GRACE — the CSR RL06.3 mascon
    # archive is global and land-only (oceans masked), so there is no
    # regional restriction. Precipitation wording NEVER routes to
    # GRACE: "rainfall"/"precipitation" stay on "tp" (IMERG/ERA5) via
    # the parser's variable race.
    if variable == "water-storage":
        return "grace"
    # "streamflow" (river discharge) routes globally to USGS Water
    # Services — the NWIS network is US-only, but the adapter answers
    # bboxes outside USGS coverage with an honest empty field (never
    # fabricated data), and the gage renderer turns that into an
    # explicit empty-frame message. "sea-level" (without "pressure")
    # has no adapter: sea level is satellite altimetry, a different
    # observable from GRACE terrestrial water storage — refusing keeps
    # the parser from answering it with a GRACE map.
    if variable == "streamflow":
        return "usgs"
    # "power-outage" (blackout / power-outage wording) has no adapter:
    # outage mapping is temporal change detection across two or more
    # epochs, and a single daily Black Marble map cannot show it — so
    # it is refused rather than misrendered.
    # "country-borders" has no adapter either: Natural Earth vectors
    # are a cartographic underlay, not a data variable — "country
    # borders" alone is refused rather than rendered as an empty map.
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


def _explain_refusal(variable: str, region_key: str) -> str:
    """Honest no-adapter message for a (variable, region)."""
    if variable == "sea-ice":
        return ("no source: the NSIDC Sea Ice Index (G02135) covers only "
                "the polar regions — 'sea-ice' over "
                f"{region_key!r} has no ice domain in the product and would "
                "return an all-NaN field.")
    if variable == "land-ice":
        return ("no source: 'land-ice' (glaciers / ice sheets / icebergs) "
                "is a different physical product from sea-ice concentration; "
                "no land-ice adapter exists.")
    if variable == "currents":
        return ("no source: no lake-scale current adapter exists — "
                f"'currents' over {region_key!r} is refused rather than "
                "fetched from a global model that cannot resolve the lakes.")
    if variable == "chlorophyll":
        return "no source: no chlorophyll-a adapter exists yet."
    if variable == "burn-scar":
        return ("no source: burned-area / burn-severity mapping belongs to "
                "survey-burn's future imagery adapter, not the FIRMS "
                "active-fire detections.")
    if variable == "power-outage":
        return ("no source: 'power-outage' (blackout / power-outage mapping) "
                "is temporal change detection across two or more epochs — "
                "a single daily Black Marble night-lights map cannot show "
                "it, so it is refused rather than misrendered.")
    if variable == "country-borders":
        return ("no source: 'country-borders' is cartographic context, not "
                "a data variable — Natural Earth country vectors are drawn "
                "as a map underlay (see docs/BASEMAPS.md), never as the "
                "visualization itself, so 'country borders' alone is "
                "refused rather than rendered as an empty map.")
    if variable == "streamflow":
        return ("no source: no river-discharge / streamflow adapter exists "
                "yet — USGS streamgages are item 11 of the remote-sensing "
                "program (see docs/ROADMAP in earthwatch-suite), so "
                "'streamflow' is refused rather than misrendered.")
    if variable == "sea-level":
        return ("no source: 'sea-level' is satellite altimetry — a "
                "different observable from GRACE terrestrial water "
                "storage — and no sea-level adapter exists, so it is "
                "refused rather than answered with a GRACE map.")
    return f"no source: no fetch adapter exists for variable {variable!r}."


def explain_source(spec: Any) -> str:
    """Human-readable explanation of which adapter ``spec`` resolves to.

    Covers every spec: pinned sources (the parser's
    ``spec.source_reason`` is quoted when present), regional defaults,
    and honest refusals (no adapter). ``spec`` is duck-typed like
    :func:`resolve_source`.
    """
    variable = str(getattr(spec, "variable", "") or "")
    region_key = str(getattr(spec, "region_key", "") or "")
    source = resolve_source(spec)
    if not source:
        return _explain_refusal(variable, region_key)
    label = SOURCE_LABELS.get(source, source)
    reason = str(getattr(spec, "source_reason", "") or "").strip()
    if reason:
        return f"source {source!r} ({label}): {reason}."
    default_why = {
        "glsea": f"SST over the Great Lakes ({region_key})",
        "oisst": f"SST outside the Great Lakes ({region_key})",
        "era5": f"{variable!r} is an ERA5 reanalysis variable",
        "oscar": f"surface currents outside the Great Lakes ({region_key})",
        "firms": "active-fire detections are global in the FIRMS area API",
        "nsidc": f"sea ice over the polar region {region_key}",
        "imerg": "precipitation with no long-record wording",
        "blackmarble": ("night-lights observations -> NASA Black Marble "
                        "VNP46A2 daily corrected radiance"),
        "gebco": "bathymetry/elevation -> GEBCO 2024 global topography "
                 "(15 arc-second, static compilation)",
        "ibtracs": "storm tracks/identity -> NOAA IBTrACS v04r01 best "
                   "tracks (global archive)",
        "grace": "water storage / groundwater -> CSR GRACE/GRACE-FO "
                 "RL06.3 terrestrial water storage anomalies (monthly, "
                 "cm LWE, land-only)",
        "usgs": "streamflow / river discharge -> USGS Water Services "
                "(NWIS) streamgage daily values (keyless, US-only; "
                "empty field outside USGS coverage)",
    }.get(source, f"variable {variable!r}")
    return (f"source {source!r} ({label}): regional default — {default_why}; "
            "the parser did not pin a source.")


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
