# survey-viz

Turn a plain-English description of a map visualization into a vertical
(1080×1920) social-media-ready frame sequence.

Three stages, **zero AI, zero network** in the engine:

1. **Parse** — deterministic offline description → spec (`viz parse`)
2. **Spec** — `VizSpec`: the validated contract between parser, renderer, and peers
3. **Render** — spec + data → PNG frames + `manifest.json` (`viz demo`, `viz render`)

Peers are optional and duck-typed: `survey-currents` feeds it field data,
`survey-animate` encodes its frames into MP4/GIF/WebM reels. Never hard
imports, never hard dependencies.

## Install

```bash
pip install survey-viz            # parser + spec + gazetteer (stdlib + pyyaml)
pip install "survey-viz[render]"  # + numpy/matplotlib for rendering
```

Or from source:

```bash
git clone https://github.com/crieck2010/survey-viz
cd survey-viz
pip install -e ".[render,test]"
```

## Quickstart

```bash
# Parse a description into a VizSpec (prints JSON)
viz parse "Lake Superior surface temperature over the past 5 years"

# Render a small synthetic vertical reel, fully offline
viz demo --out-dir viz-demo-frames

# Render from your own data
viz parse "Gulf of Mexico currents, 2015 to 2020" > spec.json
viz render --spec spec.json --field-npz field.npz --series-csv series.csv --out-dir frames
# then: survey-animate --frames-dir frames   (encodes the reel)
```

`field.npz` must contain arrays `times` (ISO date strings), `lats`, `lons`,
and `values` shaped `(ntime, nlat, nlon)`. `series.csv` has `date,value`
columns (optional — the chart panel shows a placeholder note without it).

## API overview

```python
from viz import parse_description, render_viz, VizSpec

spec = parse_description("Chlorophyll in Puget Sound last summer")
print(spec.title)   # "Puget Sound — Chlorophyll-a, 2026"
print(spec.bbox)    # (-123.5, 47.0, -122.0, 48.8)

# field: duck-typed — dict, numpy bundle, or a GlseaField-shaped object
# (has .times/.lats/.lons + 3D grid access). Never imports survey-currents.
frames, manifest = render_viz(spec, field, series, out_dir="frames")
```

## Parser rules (summary)

- **Region**: gazetteer display names/aliases, case-insensitive, longest match wins.
  40 regions: the 5 Great Lakes + 21 coastal regions/bays/seas + 8 ocean
  basins + the Gulf Stream + 5 fire regions (`data/regions.yaml`).
- **Variable**: `temperature/sst/thermal/warmth/cold` → `sst`;
  `current/flow/stream/velocity/eddy/eddies` → `currents`; `chlorophyll/chl/algae/bloom` →
  `chlorophyll`; **wind(s)/windy/gale(s)** → `wind`; **pressure/isobar(s)** →
  `msl`; **air temperature/heatwave/heat** → `t2m`;
  **rain/rainfall/precipitation/deluge** → `tp`;
  **fire(s)/wildfire(s)/burning/burn(s)** → `fire` (NASA FIRMS active-fire
  detections); **burn scar(s)/burned area(s)/burn severity** →
  `burn-scar` (parses, but has no fetch adapter — honest refusal naming
  survey-burn as the future home); **sea ice/pack ice/ice
  concentration/ice cover/ice extent/ice** → `sea-ice` (NSIDC G02135
  daily sea-ice concentration, polar regions only);
  **glacier(s)/ice sheet(s)/iceberg(s)/land ice** → `land-ice` (parses,
  but has no fetch adapter — honest refusal; land ice is a different
  physical product from sea ice). **night light(s)/city light(s)/urban
  light(s)/light pollution/electrification/development/growth of
  cities/Black Marble** → `night-lights` (NASA Black Marble VNP46A2
  V002 daily DNB radiance, 2012–present, free Earthdata Login —
  `survey-currents` v0.9.0+); **power outage(s)/blackout(s)** →
  `power-outage` (parses, but has no fetch adapter — honest refusal:
  outage mapping is temporal change detection, not a single-epoch
  lights map; blackout wording anywhere overrides a `night-lights`
  win so it is never silently misrendered). **bathymetry / seafloor /
  depth / trench / abyss** → `bathymetry`, **elevation / terrain /
  topography / mountain(s)** → `elevation` (both route globally to
  GEBCO 2024 — `survey-currents` v0.10.0+ — with an inspectable
  `source_reason`; static variables, so cadence is `yearly` and a
  bare request renders one frame). **country borders / national
  borders** → `country-borders` (parses, but has no fetch adapter —
  honest refusal: Natural Earth country vectors are a cartographic
  *underlay*, not a data variable; see docs/BASEMAPS.md). Earliest keyword in the
  text wins (`burn-scar` wins ties against `fire`, `land-ice` wins ties
  against `sea-ice`); **no keyword → `sst`**.
  **Storms** (`storm/cyclone/hurricane/typhoon`) → `wind` + pressure-isobar overlay —
  **except** when the description shows track/identity/ranking intent: a named
  storm (`Hurricane Katrina`), track wording (`track`/`path`/`trajectory`), or
  ranking wording (`strongest`/`most intense`/`most powerful`) routes to the new
  `storm-tracks` variable (NOAA IBTrACS v04r01 best tracks — `survey-currents`
  v0.11.0+), with the name in `VizSpec.storm_name`, ranking in
  `VizSpec.storm_rank`/`storm_top_n`, and an inspectable `source_reason`.
  `storm-tracks` renders as cumulative track polylines colored by Saffir-Simpson
  category over the basemap underlay; `… with the wind field` adds an ERA5 wind
  contour context. See [docs/STORMS.md](docs/STORMS.md).
- **Source pinning** (new in v0.2.0): `high resolution` / `ultra` /
  `coastal detail` / `1 km` + SST → `source="mur"` (NASA JPL MUR v4.1,
  ~1 km). Otherwise `source` stays empty and
  `viz.sources.resolve_source` picks the regional default: Great Lakes
  SST → `glsea`, other SST → `oisst`, `wind`/`msl`/`t2m`/`tp` anywhere →
  `era5` (Copernicus ERA5 reanalysis, needs a free CDS account —
  `survey-currents` v0.4.0+), non-Great-Lakes `currents` → `oscar`
  (`survey-currents` v0.5.0+), `fire` anywhere → `firms` (NASA FIRMS
  active fires, needs a free FIRMS MAP_KEY — `survey-currents` v0.6.0+),
  `sea-ice` in polar regions → `nsidc` (NSIDC G02135 v4.0, keyless —
  `survey-currents` v0.7.0+), `tp` with recent/observed/event wording →
  `imerg` (NASA GPM IMERG V07 half-hourly precipitation, 2000–present,
  needs a free Earthdata Login — `survey-currents` v0.8.0+), `tp` with
  long-record wording → `era5` (see docs/PARSER.md §6a for the
  observed-vs-reanalysis rule). `night-lights` always pins
  `source="blackmarble"` (NASA Black Marble VNP46A2 V002 daily
  gap-filled lunar BRDF-adjusted DNB radiance, 2012-01-19–present —
  `survey-currents` v0.9.0+) with an inspectable
  `VizSpec.source_reason`. `bathymetry`/`elevation` always pin
  `source="gebco"` (GEBCO 2024 global topography/bathymetry, 15
  arc-second — `survey-currents` v0.10.0+) with an inspectable
  `source_reason`. `storm-tracks` always pins `source="ibtracs"` (NOAA
  IBTrACS v04r01 tropical-cyclone best tracks, 1980–present, keyless —
  `survey-currents` v0.11.0+) with an inspectable `source_reason`
  (see docs/STORMS.md). `water-storage` always pins `source="grace"`
  (CSR GRACE/GRACE-FO RL06.3 terrestrial water storage anomalies —
  monthly, cm LWE, land-only, 2002–present, keyless —
  `survey-currents` v0.12.0+) with an inspectable `source_reason`
  (see docs/WATER.md); "sea level" (without "pressure") and "river
  discharge" are honest no-adapter refusals, never GRACE maps.
  Whenever the parser pins a source it
  records `VizSpec.source_reason`, and
  `viz.sources.explain_source(spec)` explains any spec's routing
  (pinned, default, or refused).
- **Basemap underlay** (new in v0.9.0): every render draws a GEBCO
  tint/hillshade + Natural Earth coastlines beneath the variable map
  (skipped tint for `bathymetry`/`elevation`, where GEBCO *is* the
  variable). Lazy peer import, cached, and failure-tolerant — a
  missing peer or failed download falls back to the plain background
  and is recorded in `manifest["render"]["underlay"]`, never a crash.
  `VizSpec.underlay` (default `True`) or `render_viz(...,
  underlay=False)` controls it. See [docs/BASEMAPS.md](docs/BASEMAPS.md).
- **Time**: `past N years`, `last N years`, `past N months`, `last summer`
  (most recent fully-completed Jun–Aug), `this year`, `2015 to 2020` /
  `2015-2020`, `since 2018`. **No time phrase → past 1 year.**
- Anything else raises `UnparseableDescription` with a helpful message,
  2–3 working examples, and a note about the future LLM upgrade path.

Full grammar: [docs/PARSER.md](docs/PARSER.md).

## Honest limitations

- **Fetchable**: the 5 Great Lakes via GLSEA; other SST via OISST (default)
  or MUR (high-resolution requests); `wind`/`msl`/`t2m` via ERA5 in
  any region (needs a free CDS account); `tp` via ERA5 by default, or
  via IMERG for recent/observed/event wording (needs a free Earthdata
  Login); `currents` via OSCAR (needs a
  free Earthdata Login) except the Great Lakes; `fire` via FIRMS in any
  region (needs a free FIRMS MAP_KEY); `sea-ice` via NSIDC G02135 v4.0 in
  the polar regions (`arctic-ocean`, `southern-ocean` — keyless, no
  account); `night-lights` via Black Marble VNP46A2 V002 daily DNB
  radiance in any region (needs a free Earthdata Login) — all through
  `survey-currents`
  (v0.10.0+ for GEBCO, v0.9.0+ for Black Marble, v0.8.0+ for IMERG,
  v0.7.0+ for NSIDC). `bathymetry`/`elevation` via GEBCO 2024 in any
  region (static compilation — keyless download, cached locally).
  `chlorophyll`, `burn-scar`, `land-ice`, `power-outage`, and
  `country-borders` still
  have no fetch adapter: they parse but raise a clear "no adapter yet"
  error (burn-scar's message points at survey-burn as the future
  adapter; land-ice's message explains glaciers / ice sheets / icebergs
  need a different product; power-outage's message explains outage
  mapping is change detection, not a single-epoch map;
  country-borders' message explains Natural Earth vectors are an
  underlay, not a variable). See
  `viz.sources.resolve_source()` and the `notes` field in
  `data/regions.yaml`.
- The map panel is a plain `pcolormesh` over the region bbox (no cartopy
  dependency, fully offline) with an optional GEBCO tint/hillshade +
  Natural Earth coastline underlay (v0.9.0+, on by default). Overlay
  contours (e.g. isobars) are matplotlib contour lines drawn over the
  same panel.
- Research/offline tool: synthetic demo data is clearly synthetic.

## Docs

- [docs/PARSER.md](docs/PARSER.md) — the full deterministic grammar
- [docs/BASEMAPS.md](docs/BASEMAPS.md) — the GEBCO + Natural Earth
  basemap underlay, GEBCO-as-variable routing, and the
  country-borders refusal
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module map and design decisions
- [docs/INTEROP.md](docs/INTEROP.md) — how survey-currents / survey-animate /
  a future app plug in
- [CHANGELOG.md](CHANGELOG.md)

## License

MIT — see [LICENSE](LICENSE).
