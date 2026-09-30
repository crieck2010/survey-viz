# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.21.0] - 2026-09-30

### Added
- Smart default time windows via the **survey-timescales** peer
  (survey-timescales v0.1.0+, `pip install survey-viz[timescales]`):
  when the description has no time phrase, `parse_description` now
  consults `suggest_window(variable, region=region_key, today=today)`
  instead of hard-coding the past-1-year default —
  - season language (`fire season`, `hurricane season`,
    `melt season`, `monsoon`/`dry`/`wet`/`rainy`/`ice`/`growing`
    season, ...) requests the engine's `intent="season"` window
    ("California fire season" -> this year's May–Oct; "Arctic melt
    season" -> Jun–Sep);
  - `tp` (precipitation) passes `source=` so the engine
    disambiguates ERA5 vs IMERG (the pinned source, else `era5`);
  - new `VizSpec.timescale_reason` records the engine's `reason`
    string (`""` when the peer was not consulted) and rides into the
    frame manifest via `to_dict()`/`from_dict()` — pre-v0.21.0 dicts
    load with `""`;
  - graceful degradation throughout: peer absent (`ImportError`),
    unknown variable (`UnknownVariableError`), or time-invariant
    underlay (`StaticVariableError` for `bathymetry`/`elevation`) all
    keep the old past-1-year default — the parser works exactly as
    before.
- **Explicit user dates always win** (engine precedence rule #1): the
  peer is never consulted when a time phrase was parsed — explicit
  ranges and relative phrases (`past 5 years`, `last summer`, ...)
  produce byte-identical specs to v0.20.0, with `timescale_reason`
  `""`.
- `docs/PARSER.md` (new "3a. Smart default windows" section) and
  `docs/INTEROP.md` (new "survey-timescales → survey-viz" section)
  document the integration.

## [0.20.0] - 2026-09-29

### Added
- Coastlines-only underlay mode: ``VizSpec.underlay_topo`` (default
  ``True``). When ``underlay`` is ``True`` and ``underlay_topo`` is
  ``False``, the renderer calls the peer's
  ``fetch_underlay(include_topo=False)`` — skipping the GEBCO
  tint/hillshade download (which OOM-kills small VMs at global
  extents) and drawing only the Natural Earth coastline segments
  (tiny downloads, no heavy rasters). Round-trips through
  ``to_dict``/``from_dict``; pre-v0.20.0 dicts load with ``True``.

## [0.19.0] - 2026-09-28

### Added
- Derived-product support for climatological anomaly maps
  (**survey-derive** v0.1.0+): variables named `"<base>-anomaly"`
  (e.g. `"sst-anomaly"`) render on the continuous path with
  anomaly-aware behavior —
  - `viz.insights.suggest_captions()` resolves the base variable for
    the support check, labels, and trend words, so captions read
    `"Peak sea surface temperature anomaly: …"` instead of silently
    dropping or mislabeling anomalies;
  - colormap registry lookups use the base variable;
  - the colorbar label prefers the field's own `"units"`
    (`"°C"` for anomalies, `"σ"` for standardized anomalies, `"%"`
    for percent-of-normal) over the base variable's registered unit;
  - new `VizSpec.derived_note` (e.g.
    `"anomaly vs 1991–2020 climatology"`) is appended to the frame
    footer and recorded in the manifest under `render.derived`, so
    the baseline an anomaly is computed against is part of the image
    itself — never metadata-only.
  - Anomaly variables never take the ocean-color `LogNorm` branch
    (signed anomalies must never be log-scaled). `spec.vmin`/`vmax`
    (existing) carry the shared symmetric color scale, so anomaly
    frames never autoscale per frame.
  - `VizSpec.from_dict()` loads dicts without `"derived_note"`
    (pre-v0.19.0) with `None`.

## [0.18.0] - 2026-09-28

### Added
- `render_viz(..., canvas=None)`: platform aspect-ratio and safe-zone
  canvases from the new **survey-layout** engine. A canvas is a plain
  dict — `{"platform", "flavor", "width", "height", "regions",
  "unsafe"}` — resolved by `_resolve_canvas()` into figure size and
  Matplotlib axes fractions for title/map/chart/caption/footer (plus
  `ranking` for the earthquake flavor). Malformed canvases raise
  `ValueError` before any frame is rendered. When `canvas=None`
  (default), the historical 1080×1920 layout is used unchanged.
  Canvas provenance is recorded in every manifest at
  `render.canvas`. survey-viz **never imports survey-layout**; the
  contract is duck-typed via `layout.to_viz_canvas()` /
  `layout.validate_canvas()`. See docs/INTEROP.md and the
  survey-layout repo (github.com/crieck2010/survey-layout).

## [0.17.0] - 2026-09-28

### Added
- `viz.insights`: data-driven story captions (stdlib + numpy, no
  matplotlib). `frame_stats()` reduces each rendered frame's grid to
  headline scalars (mean/max/min, finite-pixel count); `suggest_captions()`
  derives deterministic caption events — a peak caption ("Peak sea
  surface temperature: 28.4°C regional mean (Aug 2024)") and a trend
  caption ("Warming trend: +5.0°C across the reel"). Honesty rules:
  captions describe only the rendered region/time window (never global
  records); peak captions say "regional mean"; the peak is skipped when
  the data are flat or the peak is the last frame; a trend needs both a
  >= 5% effect size and a statistically significant slope (|t| > 2);
  all-NaN gap frames never carry captions; at most one peak + one trend,
  never overlapping (peak wins); categorical products (storm tracks,
  streamgages, earthquakes) keep their fixed scientific encodings and
  get no auto-captions. Exported as `Caption`, `frame_stats`,
  `suggest_captions` from the package root.
- `render_viz(..., story_captions=True)`: burns the caption events in as
  lower-third chips on the covered frames and records them in the
  manifest under `render.story_captions`. Categorical renderers accept
  the flag and record it as not-applied (fixed encodings preserved).
- `refine_spec()` camera-motion intents: plain-language instructions
  like "add a slow zoom in", "pan left during the video", "no camera
  motion", or "make the transitions smoother" are parsed into a partial
  motion-settings dict on `RefineResult.motion` (zoom in/out/off +
  speed, 8 pan directions + speed, smoothing on/off + step count) for
  the caller to merge into its encode-time motion settings — camera
  motion stays out of `VizSpec`, matching the render-time colormap
  model. A bare "zoom in" with no camera context keeps its v0.16.0
  geographic meaning (bbox halved); motion words that collide with
  variable keywords ("burns" → fire, "drift" → currents) are neutralized
  after motion detection so a camera instruction never hijacks the
  variable.

## [0.16.0] - 2026-09-27

### Added
- `viz.refine_spec(spec, instruction)`: plain-language refinements to
  an existing `VizSpec` ("zoom in on the Gulf of Mexico and use a
  warmer colormap"). Only the fields the instruction mentions change;
  everything else is preserved. Returns a `RefineResult` with the new
  spec, the requested colormap (a render-time argument, not a spec
  field), every applied change (old → new + reason), and notes for
  anything not understood — never silently guessed. Automatic titles
  follow region/variable/time changes; customized titles are never
  touched unless the instruction mentions the title. Raises
  `UnparseableDescription` when nothing matches, listing the supported
  refinement vocabulary. Pure function: no I/O, no network, no global
  state.
- `viz.suggest_aesthetic(url_or_bytes)`: copy the *color mood* of a
  reference reel URL or screenshot. Fetches the page's `og:image` /
  `twitter:image` thumbnail (the video itself is deliberately out of
  scope — it sits behind login walls), measures mean brightness and
  dominant hue, and suggests the `reel-dark` / `light` style plus a
  curated colormap, with the measured palette and explanatory notes.
  Near-grayscale thumbnails suggest no colormap (the variable default
  is kept, not guessed). 15 s timeout, 5 MB cap, http(s) only;
  analysis downsamples to 256 px. Raises `AestheticError` with the
  honest reason on any failure. Also exported: `analyze_image`,
  `fetch_image_bytes`, `AestheticProfile`, `AestheticError`.

### Changed
- `parser._cadence_for(...)`: the cadence rules extracted into one
  shared helper used by both `parse_description` and `viz.refine`, so
  a refinement that switches variables recomputes the cadence exactly
  like a fresh parse. Behavior unchanged (parser suite green).

## [0.15.0] - 2026-09-27

### Added
- `VizSpec.caption`: optional custom footer caption. It is *prepended*
  to the frame footer (`"<caption> · <variable/honesty text> ·
  <start> → <end>"`), so per-renderer honesty wording — e.g. the
  earthquake catalog's "observed events — not a forecast" — is never
  erased by a custom caption. Empty/whitespace-only strings normalize
  to `None`; `to_dict()`/`from_dict()` round-trip it, and pre-0.15.0
  dicts without the key load with `caption=None`.
- `render_viz(..., cmap=None)`: optional matplotlib colormap override
  for the data map. Applies only to continuous data maps (SST, ocean
  color, ERA5, fires, night lights, topography, water storage, ...);
  the categorical renderers (storm tracks, streamgages, earthquakes)
  keep their fixed scientific encodings — a `cmap` passed for those is
  validated (typos fail fast) but recorded in the manifest as
  requested-not-applied, never silently recoloring data whose colors
  carry meaning. Invalid names raise `ValueError` with curated
  recommendations.
- `viz.CURATED_CMAPS`: 30 recommended colormap names (all verified
  registered with matplotlib), exported for UI pickers.

### Changed
- `viz.__version__` now reads the installed distribution metadata
  (written from `pyproject.toml` at install time) instead of a
  hand-edited string, so it can never go stale again.
- Frame manifests now record `"cmap_requested"` (and
  `"cmap_overridden"` on the main renderer) alongside the effective
  `"cmap"`.

## [0.14.1] - 2026-09-27

### Added
- Gazetteer: `japan` and `alaska` seismic regions
  (`data/regions.yaml`; earthquake catalog fetchable via the ComCat
  adapter in survey-currents v0.15.0+), so descriptions like
  "Earthquakes and topography in Japan" and "Earthquake swarm over
  time in Alaska" parse end-to-end (previously the regions were
  unknown and parsing failed).

## [0.14.0] - 2026-09-27

### Added
- USGS Earthquake Catalog (`KNOWN_VARIABLES += ("earthquakes",)`,
  `KNOWN_SOURCES += ("comcat",)`, source label "USGS Earthquake
  Catalog (ComCat)"; minimum peer `survey-currents >= 0.15.0`):
  - `earthquakes` always pins `source="comcat"` (ComCat FDSN event
    service: keyless, global, GeoJSON) with an inspectable
    `source_reason`; cadence is always daily.
    Keywords: "earthquake(s)", "seismic", "quake(s)", "tremor(s)",
    "magnitude(s)", "foreshock(s)", "aftershock(s)", "seismic hazard"
    — checked *before* the earliest-wins race (right after the
    streamflow pre-check, before storm intent) so `bathymetry`'s bare
    "depth" never misreads "the depth of the earthquake" as seafloor
    depth.
  - "Seismic hazard" wording routes to `earthquakes` — the catalog is
    *observed events, not a forecast hazard model*; the honesty note
    is recorded in every render manifest and the frame footer reads
    "… observed events — not a forecast".
  - "earthquakes and topography" wording keeps
    `variable="earthquakes"` (no separate fetch): topographic context
    is the default GEBCO underlay beneath every quake render, noted
    inspectably in `source_reason`.
  - Dedicated point/time quake renderer (no scalar-grid conversion):
    CUMULATIVE daily frames (each frame shows all events with time <=
    that frame date), markers sized by magnitude via the documented
    power law `area = 80 × 10**(0.75·(M − 4))` (each +1 M unit ≈ 5.6×
    marker area) and colored by hypocentral-depth bin (shallow
    <70 km / intermediate 70–300 km / deep ≥300 km / unknown),
    magnitude marker chips M4–M7, depth-bin legend, largest-events
    readout (top 5 by magnitude with place + depth, event_type shown
    honestly), daily event-count time series with a cumulative
    playhead, GEBCO/Natural Earth underlay beneath, coastlines above.
    Empty windows render an explicit empty-frame message (never
    fabricated markers); the manifest records `n_events`,
    `daily_counts`, `largest`, `min_magnitude`, the catalog/hazard
    note, and the underlay status.
  - Docs: new docs/EARTHQUAKES.md (access truth: keyless ComCat FDSN
    endpoint, default 20000-event limit — global M4+/one week = 179
    events and global M0+/one week = 1906 events both fit in one
    response, verified live 2026-09-27; `limit`/`offset` paging; no
    rate-limit headers observed; magnitude-of-completeness caveat;
    non-tectonic `event_type`s shown honestly); docs/PARSER.md (§2
    routing table + rules, new §6f) and README updated.

## [0.13.0] - 2026-09-27

### Added
- Ocean color (`KNOWN_VARIABLES += ("ocean-color",)`, source
  `"oceancolor"`; minimum peer `survey-currents >= 0.14.0`):
  - The legacy `chlorophyll` variable name canonicalizes to
    `ocean-color` through `VizSpec` (old serialized specs without
    `context`, and old `variable="chlorophyll"` specs, stay loadable).
  - `VizSpec.context` (separate from contour overlays): `currents`
    and `sst` companion datasets (bloom + currents wording →
    `context=("currents",)`; chlorophyll-vs-temperature / SST wording →
    `context=("sst",)`; both may coexist).
  - Parser: ocean color / chlorophyll / chl / phytoplankton / algae /
    algal / bloom / green ocean → `ocean-color`; monthly default,
    explicit daily wording → daily; terrestrial nitrate / turbidity /
    dissolved oxygen / "water quality" requests are refused honestly
    (no adapter) rather than silently mapped to SST.
  - Source: NOAA CoastWatch ERDDAP MODIS Aqua R2022 Level-3
    chlorophyll-a (monthly default, ~4 km, 2002-present, keyless) with
    the required credentials-gated NASA OBPG fallback (free Earthdata
    Login) behind `source="auto"`; ESA OC-CCI v6.0 and VIIRS SNPP
    sensors selectable at fetch time. The parser pins this source
    with an inspectable `source_reason`.
  - Log-scaled renderer (`matplotlib.colors.LogNorm`, viridis,
    mg/m³): masked arrays convert to NaN without dropping masks;
    partial cloud gaps stay NaN over the basemap; fully cloudy frames
    render an explicit **NO OCEAN COLOR OBSERVATION** panel (never
    filled/interpolated). Manifest records `log_scale`,
    `gap_frames`, and `context`; the footer notes log scale, gap
    count, and context.
  - Docs: new docs/OCEANCOLOR.md (parsing, rendering, gaps, context,
    coastal/inland case-2 limitations); docs/PARSER.md (§2, source
    pinning) and README updated. Coastal/inland retrievals are
    indicative only — the global L3 grid covers the Great Lakes
    geometrically, but land adjacency / bottom reflectance / CDOM /
    suspended sediment make values non-quantitative there.

## [0.12.0] - 2026-09-27

### Added
- USGS streamgages (`KNOWN_SOURCES += ("usgs",)`, source label "USGS
  Water Services (NWIS)"):
  - `streamflow` always pins `source="usgs"` (USGS Water Services
    NWIS daily-mean streamgage values, `survey-currents` v0.13.0+)
    with an inspectable `source_reason`; cadence is always daily.
    Keywords: "streamflow", "river discharge", "stream discharge",
    "river flow", "gage(s)", "gauge(s)", "streamgage(s)", "stream
    gauge(s)" — checked *before* the earliest-wins race so the broad
    `currents` patterns (bare "stream"/"flow") never misread them as
    ocean currents (a bare ocean/current "flow" request still routes
    to `currents`).
  - Dedicated point/time-series gage renderer (no scalar-grid
    conversion): gage markers over the GEBCO/Natural Earth underlay,
    colored by the current discharge percentile category
    (USGS WaterWatch-style classes), plus a hydrograph panel —
    `spec.gage_site` (a NWIS site number) selects the gage, else the
    documented regional-median rule; missing daily values stay gaps.
  - "Flooding after heavy rainfall" routes to `streamflow` primary +
    a best-effort `tp` precipitation contour overlay (degrades to
    "absent", never silently dropped); flood wording alone is not
    streamflow (flood-inundation mapping is survey-flood's domain).
  - Empty gage sets render an explicit empty-frame message, never
    fabricated data; `GageField` is shared with survey-flood.
  - Docs: new docs/STREAMFLOW.md; updated docs/PARSER.md (§2, §6d,
    new §6e), docs/WATER.md, docs/INTEROP.md, README.

## [0.11.0] - 2026-09-27

### Added
- GRACE terrestrial water storage (`KNOWN_VARIABLES +=
  ("water-storage", "streamflow", "sea-level")`,
  `KNOWN_SOURCES += ("grace",)`):
  - `water-storage` always pins `source="grace"` (CSR GRACE/GRACE-FO
    RL06.3, `survey-currents` v0.12.0+) with an inspectable
    `source_reason`; cadence is always monthly. Keywords: "water
    storage", "groundwater", "aquifer", "terrestrial water", "total
    water storage", "GRACE", "GRACE-FO", "TWS", "drought" (earliest
    wins, as usual).
  - Combination requests: "groundwater decline vs rainfall" keeps
    GRACE as the base map and adds `overlays=("tp",)` as a
    precipitation contour overlay (the pipeline fetches it as
    context; it degrades to an "absent" manifest status when
    unavailable).
  - Precipitation never routes to GRACE: "rainfall"/"precipitation"
    keep their `tp` race (IMERG/ERA5); only a `water-storage` primary
    win can add `"tp"` as an overlay.
  - Honest refusals, never GRACE maps: `variable="streamflow"` ("river
    discharge" — no adapter yet; USGS streamgages are item 11 of the
    remote-sensing program) and `variable="sea-level"` (satellite
    altimetry, a different observable from GRACE TWS). "Sea level
    pressure" keeps its `msl` (ERA5) reading via a negative lookahead.
  - Rendering: zero-centered symmetric `BrBG` diverging scale (brown
    = drier than the 2004–2009 mean, blue = wetter; explicit
    `vmin`/`vmax` still win), units "cm", gap months render as
    **"NO GRACE OBSERVATION"** panels (never interpolated), footer
    and manifest carry the anomaly baseline and the gap-month list.
  - New docs: `docs/WATER.md` (routing, parser rules, rendering,
    pipeline contract, limitations); `docs/PARSER.md` §6d.
  - 22 new tests (parser routing + refusals, source registry, render
    scale/gap/manifest).

## [0.10.0] - 2026-09-27

### Added
- IBTrACS storm tracks (`KNOWN_VARIABLES += ("storm-tracks",)`,
  `KNOWN_SOURCES += ("ibtracs",)`):
  - Track/identity/ranking intent is detected **before** the
    earliest-wins keyword race (requires a storm word): a **named
    storm** ("Hurricane Katrina", "Tropical Storm Milton", "Typhoon
    Haiyan's" — the candidate must be capitalized and not a common
    noun like "season"/"track"/"conditions"/"pressure"), **track
    wording** ("track(s)"/"path(s)"/"trajectory(ies)"), or **ranking
    wording** ("strongest"/"most intense"/"most powerful", with optional
    "top N") → `variable="storm-tracks"`, `source="ibtracs"` pinned
    (NOAA IBTrACS v04r01 global tropical-cyclone best tracks,
    1980–present, keyless — `survey-currents` v0.11.0+), with an
    inspectable `VizSpec.source_reason`. Ranking is by lifetime
    maximum sustained wind (kt), documented in docs/STORMS.md.
  - Generic storm-environment wording keeps the **unchanged ERA5
    reading** ("Hurricane conditions …" → `wind` + `msl` overlay);
    "… with the wind field" / "… with the pressure field" on a
    track request adds an ERA5 `wind`/`msl` contour overlay
    (IBTrACS+ERA5 combination).
  - New `VizSpec` fields: `storm_name=""`, `storm_rank=""`/`"strongest"`,
    `storm_top_n=None`; `from_dict` stays backward compatible with
    pre-v0.10.0 dicts.
  - Dedicated track renderer (src/viz/render.py): cumulative daily
    track polylines colored by Saffir-Simpson category (TD/TS/C1–C5,
    winds in kt, USA-agency preference inherited from
    `survey-currents`), dateline crossings break the polyline (no
    false Pacific line), genesis markers + name labels, category
    legend, peak-wind readout; tracks render **above** the Item 8
    GEBCO/coastline underlay; optional ERA5 contour context beneath
    the tracks with graceful "absent" degradation recorded in
    `manifest["render"]["era5_context"]`.
  - Gazetteer: bare "atlantic"/"atlantic ocean" now alias
    `north-atlantic` (longest-match keeps "north atlantic" winning).
  - Docs: new docs/STORMS.md (routing table, parser rules, renderer,
    Saffir-Simpson colors, agency caveat, pipeline contract,
    limitations), docs/PARSER.md §6c, README variable/source rows.
  - 39 new tests (tests/test_storms.py).

## [0.9.0] - 2026-09-27

### Added
- GEBCO variables (`KNOWN_VARIABLES += ("bathymetry", "elevation")`,
  `KNOWN_SOURCES += ("gebco",)`):
  - `variable="bathymetry"` (keywords: `bathymetry`, `seafloor`,
    `depth(s)` — the hyphenated intensifier "in-depth" deliberately
    does *not* match — `trench(es)`, `abyss`, `hadal`) and
    `variable="elevation"` (keywords: `elevation(s)`, `terrain(s)`,
    `topography`, `mountain(s)`) always pin `source="gebco"` —
    GEBCO 2024 global topography/bathymetry, 15 arc-second — with an
    inspectable `VizSpec.source_reason` ("bathymetry/elevation
    description -> GEBCO 2024 global topography/bathymetry
    (15 arc-second)"). Regional default is `gebco` in any region;
    lazily imported from `currents.basemaps.fetch_gebco` (minimum
    survey-currents 0.10.0, honest upgrade message in
    `fetch_for_source`).
  - GEBCO is a static compilation, not a time series: cadence is
    `yearly`, and with no explicit time phrase the spec collapses to
    a single frame (`start == end == today`). Colormaps: `Blues_r`
    for bathymetry (deep = dark), `terrain` for elevation; units
    metres.
  - `variable="country-borders"` (keywords: `country borders /
    boundaries`, `national borders`, `political boundaries`) parses
    but has **no fetch adapter**: Natural Earth country vectors are
    cartographic context, not a data variable — "country borders"
    alone is refused honestly (the refusal message points at the
    coastline underlay) rather than rendered as an empty map.
- Universal basemap underlay (new `viz.underlay` module, on by
  default via `VizSpec.underlay=True`, overridable per render with
  `render_viz(..., underlay=True/False)`):
  - GEBCO tint + hillshade (0.25°, auto-coarsened for huge regions)
    drawn *beneath* the variable map wherever the variable is NaN,
    plus Natural Earth coastlines (`110m`/`50m` by region width)
    drawn *over* the variable for cartographic context. For
    `bathymetry`/`elevation` the tint is skipped (GEBCO *is* the
    variable) while coastlines are still drawn.
  - Lazy peer loading (`currents.basemaps`, survey-currents ≥
    0.10.0); disk cache via the peer (SHA-256 sidecars) plus a
    small in-process cache so multi-frame renders fetch once.
  - Never crashes, never silently wrong: every failure mode (peer
    missing, network down, corrupt cache) returns
    `status="unavailable"` with a reason; the renderer falls back to
    the plain background. `manifest["render"]["underlay"]` records
    `status`/`reason`/`topo`/`coastline_segments`/`resolution`/
    `coastline_scale` for every render.
  - `docs/BASEMAPS.md` documents the underlay contract, the
    country-borders refusal, and GEBCO-as-variable routing.
- 36 offline tests (`tests/test_basemaps.py`): parser terms and
  GEBCO pinning with `source_reason`, static single-frame cadence,
  country-borders honest refusal, adapter wiring, underlay ok /
  failure-fallback / disabled paths, tint-skip for bathymetry, and
  per-variable colormaps — the peer is monkeypatched, so no test
  touches the network.

## [0.8.0] - 2026-09-27

### Added
- NASA Black Marble night-lights routing (`KNOWN_VARIABLES +=
  ("night-lights", "power-outage")`, `KNOWN_SOURCES += ("blackmarble",)`):
  - `variable="night-lights"` (keywords: `night lights`, `city lights`,
    `urban lights`, `light pollution`, `electrification`, `development`,
    `growth of cities`, `Black Marble`, `VNP46A2`) always pins
    `source="blackmarble"` — NASA Black Marble VNP46A2 V002 daily
    gap-filled lunar BRDF-adjusted DNB radiance, 2012-01-19–present,
    free Earthdata Login — with an inspectable `VizSpec.source_reason`
    ("night-lights observations -> NASA Black Marble VNP46A2 daily
    corrected radiance"). Regional default is `blackmarble` in any
    region; daily cadence; lazily imported from
    `currents.blackmarble.fetch_blackmarble` (minimum survey-currents
    0.9.0, honest upgrade message in `fetch_for_source`).
  - `variable="power-outage"` (`power outage(s)`, `blackout(s)`) parses
    but has **no fetch adapter**: outage mapping is temporal change
    detection across two or more epochs, and a single daily Black
    Marble map cannot show it — `explain_source` returns an honest
    refusal. Safety rule: blackout wording anywhere in the text
    overrides a `night-lights` win (like `burn-scar` winning ties
    against `fire`), so it is never silently misrendered.
  - `_VARIABLE_UNITS["night-lights"] = "nW/cm²/sr"` (render labels);
    `SOURCE_LABELS["blackmarble"] = "NASA Black Marble VNP46A2"`.
- `docs/PARSER.md` §6b documents the night-lights pinning, the daily
  cadence, the bare-`development` earliest-wins caveat, and the
  power-outage refusal rule with examples.
- 29 new tests (`tests/test_blackmarble_routing.py`), fully offline.
  250 passed.

## [0.7.0] - 2026-09-27

### Added
- GPM IMERG precipitation source routing for the new survey-currents
  v0.8.0 adapter (`KNOWN_SOURCES += ("imerg",)`): `VizSpec.source` may
  now be `"imerg"` (NASA GPM IMERG V07 half-hourly precipitation,
  2000–present, free Earthdata Login — lazily imported from
  `currents.imerg.fetch_imerg`). Minimum survey-currents version for
  `imerg` is 0.8.0 (honest upgrade message in `fetch_for_source`).
- `VizSpec.source_reason: str = ""` — the parser records a short
  human-readable justification whenever it pins a source (e.g.
  `"explicit IMERG request"`, `"recent/observed precipitation wording
  -> NASA GPM IMERG V07"`); `to_dict`/`from_dict` round-trip it, and
  pre-v0.7.0 dicts without the key load with `""`.
- `viz.sources.explain_source(spec) -> str` — a one-line explanation of
  which adapter a spec resolves to, for every spec: pinned (quotes the
  parser's reason), regional default, and honest refusals (no adapter).
- Precipitation source selection (`variable == "tp"`): explicit `IMERG`
  / `ERA5` pin that product; recent/observed/event wording (`recent`,
  `last week`, `event`, `storm`, `hurricane`, `observed`, `satellite`,
  `high resolution`, `ultra`) pins `"imerg"`; long-record wording
  (`trend`, `climatology`, `since 19…`/`since 20…`, `decades`,
  `long-term`) pins `"era5"`; unpinned `tp` keeps the regional default
  `"era5"`. The observed-vs-reanalysis distinction is documented in
  `docs/PARSER.md` (§6a). Existing MUR / CMEMS-currents pinning is
  unchanged (the parser now records reasons for those pins too).

## [0.6.2] - 2026-09-26

### Fixed
- `tests/test_sources.py::test_package_exports` asserted "0.6.0" while
  the package was 0.6.1. Test-only change; no source changes since
  v0.6.0.

## [0.6.1] - 2026-09-26

### Fixed
- `tests/test_sources.py::test_package_exports` asserted the old
  `viz.__version__` ("0.5.0") — now asserts "0.6.1". Test-only change;
  no source changes since v0.6.0.

## [0.6.0] - 2026-09-26

### Added
- NSIDC sea-ice source routing for the new survey-currents v0.7.0
  adapter (`KNOWN_SOURCES += ("nsidc",)`, `KNOWN_VARIABLES +=
  ("sea-ice", "land-ice")`): `VizSpec.source` may now be `"nsidc"`
  (NSIDC G02135 v4.0 daily sea-ice concentration, 1978–present,
  keyless — lazily imported from `currents.sea_ice.fetch_nsidc_sic`).
  Minimum survey-currents version for `nsidc` is 0.7.0 (honest upgrade
  message when the peer is older).
- Default routing for `variable="sea-ice"`: **polar regions only**
  (`arctic-ocean`, `southern-ocean` → `"nsidc"`); anywhere else it
  resolves to `""` (honest refusal — the G02135 grids cover north of
  30.98°N / south of 39.23°S, so mid-latitude "sea ice" would return an
  all-NaN field).
- Parser: `sea ice` / `sea-ice` / `pack ice` / `ice concentration` /
  `ice cover` / `ice extent` / bare `ice` keywords; sea-ice specs default
  to `cadence="daily"` (ice moves fast; the NSIDC source is daily).
  New gazetteer aliases: `arctic` (arctic-ocean), `antarctic`
  (southern-ocean) — "Antarctic pack ice this year" parses to
  `sea-ice` / `southern-ocean` → `nsidc`.
- **Land-ice disambiguation (documented in `docs/PARSER.md` §2):**
  `glacier(s)` / `ice sheet(s)` / `iceberg(s)` / `land ice` parse to
  `variable="land-ice"` — which wins ties against `sea-ice` ("ice
  sheet" matches both groups at the same position) but has **no fetch
  adapter**: glaciers / ice sheets / icebergs are a different physical
  product from sea-ice concentration, and routing them to NSIDC would be
  a lie.

## [0.5.0] - 2026-09-26

### Added
- FIRMS active-fire source routing for the new survey-currents v0.6.0
  adapter (`KNOWN_SOURCES += ("firms",)`, `KNOWN_VARIABLES += ("fire",
  "burn-scar")`): `VizSpec.source` may now be `"firms"` (NASA FIRMS
  active-fire detections, 2000–present, free MAP_KEY, lazily imported
  from `currents.fires.fetch_firms`). Minimum survey-currents version
  for `firms` is 0.6.0 (the per-source minimum-version table in
  `viz.sources` now drives the honest upgrade message).
- Default routing for `variable="fire"`: **any** region resolves to
  `"firms"` (the FIRMS area API is global) — no region-key gating.
- Parser: `fire`/`fires`/`wildfire(s)`/`burning`/`burn(s)` keywords;
  fire specs default to `cadence="daily"` (fires are fast phenomena;
  monthly bucketing would show one day per month). New fire gazetteer
  regions (40 total): `california`, `pacific-northwest`, `amazon-basin`,
  `australia-southeast`, `boreal-canada`.
- **Burn-scar disambiguation (documented in `docs/PARSER.md` §2):**
  "burn scar(s)" / "burned area(s)" / "burn severity" parse to
  `variable="burn-scar"` — which wins ties against `fire` ("burn scar"
  matches both groups at the same position) but has **no fetch
  adapter**: FIRMS is active-fire *detections* only, and burned-area /
  burn-severity mapping is survey-burn's future imagery adapter.
  `default_source` returns `""` for it, so it becomes an honest
  no-adapter refusal naming survey-burn — never a silent misroute to
  fire detections (the `chlorophyll` precedent).
- Renderer: fires render through the existing scalar path with zero
  changes — `FireField.to_density_grid()` produces the plain
  `times`/`lats`/`lons`/`values` dict the renderer already consumes
  (daily fire-count grids, colorbar unit "detections"). Per-detection
  point markers (dots) are deliberately deferred to a later version:
  they need a per-frame points channel in the render dict form, and
  half-plumbing it now would complicate the density path.

### Tests
- 17 new tests (parser fire/burn-scar keywords, tie-break, daily
  cadence, new regions; sources fire/burn-scar routing, firms lazy
  import, min-version message). Region count updated to 40.

## [0.4.0] - 2026-09-26

### Added
- Global-currents source routing for the new survey-currents v0.5.0
  adapters (`KNOWN_SOURCES += ("oscar", "cmems-currents")`):
  `VizSpec.source` may now be `"oscar"` (NASA PODAAC OSCAR v2.0, daily
  surface currents 1993–present, free Earthdata Login, lazily imported
  from `currents.currents_global.fetch_oscar`) or `"cmems-currents"`
  (CMEMS `global-physics-daily` preset, 1/12°, free CMEMS account,
  lazily imported from
  `currents.currents_global.fetch_cmems_currents`).
  Minimum survey-currents version for both is 0.5.0.
- Default routing for `variable="currents"`: any region except the
  5 Great Lakes resolves to `"oscar"`; Great Lakes `currents` stays an
  honest refusal (no lake-scale current adapter exists).
- Parser: added `r"\bedd(?:y|ies)\b"` to the currents keywords; the
  high-resolution / ultra / 1-km wording now pins `source="cmems-currents"`
  for currents (SST pinning to `"mur"` unchanged).
- New `gulf-stream` gazetteer region (35 regions total; bbox approx
  `[-81, 25, -55, 43]`).
- Renderer: a survey-currents `CurrentField` (3D `u`/`v`) renders as the
  scalar current speed `sqrt(u²+v²)` through the existing scalar path —
  no quiver/streamline/particle rendering here; that is the survey-flow
  renderer's job. Masked (land) cells become NaN so they stay out of the
  color scale.
- **Deferred:** a Gulf Stream `currents + SST base` particle-flow overlay
  — current overlay plumbing is contour-only, so particle flow is not
  implemented in this release.

### Verified parser examples
- "North Atlantic currents over the past year" → `oscar` / `currents` /
  `north-atlantic`
- "Gulf Stream eddies last summer" → `oscar` / `currents` / `gulf-stream`
- "ultra high resolution currents in the Caribbean" →
  `cmems-currents` / `currents` / `caribbean-sea`

## [0.3.0] - 2026-09-26

### Added
- ERA5 atmosphere variables: `VizSpec.variable` now also accepts `"wind"`
  (10-m wind), `"msl"` (mean sea-level pressure), `"t2m"` (2-m air
  temperature), and `"tp"` (precipitation) — the short keys of
  `survey-currents` v0.4.0's new `currents.era5` adapter (Copernicus ERA5
  hourly reanalysis, 1940–present, 0.25°, free CDS account).
- `VizSpec.overlays`: optional contour overlays over the base-variable map
  (max 1 in v0.3.0; each must differ from the base variable). Backward
  compatible — v0.2.0 spec dicts without `"overlays"` deserialize unchanged.
- Parser: storm keywords (`storm(s)`, `cyclone(s)`, `hurricane(s)`,
  `typhoon(s)`) map to `wind` + `overlays=["msl"]` (wind map with pressure
  isobars) in the same earliest-wins race as other variable keywords;
  `wind(s)`/`windy`/`gale(s)`/`gust(s)` → `wind`;
  `pressure(s)`/`isobar(s)`/`sea-level pressure` → `msl`;
  `air temperature(s)`/`atmospheric temperature(s)`/`heatwave(s)`/`heat` →
  `t2m`; `rain`/`rainfall`/`precipitation`/`deluge(s)`/`downpour(s)` → `tp`.
- Renderer: draws contour overlays per frame (levels recomputed from each
  frame's data range; `msl` every 4 hPa, labeled), footer names the
  overlay; raises `ValueError` if the field cannot supply a requested
  overlay grid — never silently dropped. `Era5Field` duck-types in via
  its `.values` property + `.overlay_grids`.
- Source routing: `default_source` returns `"era5"` for the four ERA5
  variables in any region; new `"era5"` adapter in `fetch_for_source`
  (`survey-currents>=0.4.0`, `currents.era5.fetch_era5`).
- New gazetteer region `us-east-coast` ([-82, 25, -65, 45]); 34 regions
  total. Region `notes` updated: ERA5 atmosphere variables are fetchable
  in every region.

### Changed
- **Disambiguation:** bare `heat` now means air heat (`t2m`, ERA5), not
  water temperature. `warm`/`warmth`/`cold`/`thermal` stay SST (backward
  compatible). Documented in `docs/PARSER.md` §2.
- `docs/PARSER.md`: new §2a (overlays), updated variable table, storm rule,
  heat disambiguation, and ERA5 source-routing examples.

## [0.2.0] - 2026-09-26

### Added
- `VizSpec.source`: backward-compatible new field (`""` default = not
  pinned, `"glsea"` / `"oisst"` / `"mur"`). v0.1.0 spec dicts (no
  `source` key) still deserialize unchanged.
- `viz.sources` (`src/viz/sources.py`): source routing —
  `resolve_source(spec)` (explicit pin wins, else regional default:
  Great-Lakes SST → `glsea`, other SST → `oisst`, non-SST → `""`),
  `fetch_for_source(source)` (lazy survey-currents import, actionable
  `ImportError` when the peer is missing/too old), `is_fetchable(spec)`
  (spec-aware), `default_source()`, `SOURCE_LABELS`. Peers stay
  optional; survey-viz never imports survey-currents itself.
- Parser pins `source="mur"` on SST quality keywords (`high
  resolution`, `ultra`, `coastal detail`, `1 km`, kilometre/kilometer);
  ignored for non-SST variables.
- 8 new ocean-basin gazetteer regions: `global`, `north-atlantic`,
  `south-atlantic`, `north-pacific`, `south-pacific`, `indian-ocean`,
  `southern-ocean`, `arctic-ocean` (+ `caribbean`/`mediterranean`
  aliases already existed). Gazetteer now 33 regions; SST is fetchable
  in all of them via survey-currents v0.3.0 (OISST default, MUR on
  request).
- 34 new tests: routing, parser keywords, backward-compatible
  serialization, ocean-region parsing, lazy peer import.

## [0.1.0] - 2026-09-26

### Added
- `VizSpec` dataclass (`src/viz/spec.py`): title, region_key, bbox, variable,
  start/end, cadence, layout (`reel-vertical`), style (`reel-dark`/`light`),
  optional `vmin`/`vmax`; `to_dict`/`from_dict` + strict validation.
- Built-in YAML gazetteer (`data/regions.yaml` + `src/viz/gazetteer.py`):
  25 named regions — the 5 Great Lakes plus 20 coastal regions/bays/seas —
  each with key, display name, aliases, bbox, and a data-availability note.
  `is_fetchable()` reports only the 5 Great Lakes as fetchable today (GLSEA
  adapter in survey-currents); other regions parse fine but fetching raises a
  clear "no adapter yet" error.
- Deterministic offline description parser (`src/viz/parser.py`):
  `parse_description(text) -> VizSpec`, stdlib-only (regex + gazetteer).
  Region matching (case-insensitive, longest match wins), variable keywords
  (earliest-in-text wins; default `sst`), time phrases (`past N years`,
  `last summer`, `this year`, explicit ranges, `since YYYY`; default past
  1 year), derived titles. `UnparseableDescription` with helpful message,
  working examples, and LLM-upgrade-path note.
- Renderer (`src/viz/render.py`): `render_viz(spec, field, series, out_dir)`
  → `(frames, manifest_path)`. Duck-typed field/series inputs (dicts, numpy,
  or GlseaField-shaped objects; never imports survey-currents). 1080×1920
  `reel-vertical` layout: title block, pcolormesh map panel with fixed
  vmin/vmax (no colormap flicker) and burned-in timestamp, time-series panel
  with playhead line + dot. `reel-dark` default, `light` option. matplotlib
  and numpy are lazy imports. Writes `manifest.json` with schema
  `survey-viz.frame-manifest/1.0`, directly consumable by survey-animate.
- Thin CLI (`src/viz/cli.py`): `viz parse`, `viz demo` (synthetic reel,
  fully offline), `viz render --spec --field-npz [--series-csv]`.
- Docs: README, docs/PARSER.md (full grammar), docs/ARCHITECTURE.md,
  docs/INTEROP.md (survey-currents/survey-animate/future-app wiring).
- 65 pytest tests, fully offline (render tests skip if matplotlib missing).
