# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
