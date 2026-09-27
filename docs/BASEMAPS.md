# Basemaps — GEBCO + Natural Earth (v0.9.0)

`survey-viz` renders the requested **variable** as the map; a basemap
underlay adds cartographic context *beneath* it. Two basemap sources
are involved, both served by the `survey-currents` peer
(`currents.basemaps`, survey-currents ≥ 0.10.0):

| Source | What | Role |
|---|---|---|
| **GEBCO 2024** | Global topography/bathymetry, 15 arc-second | (a) *variable* for `bathymetry`/`elevation`; (b) tint + hillshade underlay under every other variable |
| **Natural Earth** | Coastlines (`110m`/`50m`), country polygons | Coastline underlay only — never a variable |

## The universal underlay

Every render draws, beneath the variable map:

1. **GEBCO tint + hillshade** — terrain colors modulated by shaded
   relief, at 0.25° (coarsened automatically for very large regions).
   It shows wherever the variable field is NaN (e.g. land under an
   SST field).
2. **Natural Earth coastlines** — clipped to the bbox (`110m` for
   regions wider than 60°, else `50m`), drawn *over* the variable in
   a style-aware color.

The underlay is **optional but on by default**: `VizSpec.underlay`
(defaults `True`), overridable per render with
`render_viz(..., underlay=True/False)`.

For the `bathymetry` / `elevation` variables the GEBCO tint is
**skipped** — GEBCO *is* the variable there — but coastlines are
still drawn (recorded as `underlay.topo: false` in the manifest).

### Failure semantics (never a crash, never silent wrongness)

`viz.underlay.fetch_underlay` **never raises**. It lazily imports the
peer and returns a status dict:

- `status: "ok"` — `topo` (lats/lons/values/resolution/source),
  `coastlines` (segments), `n_coastline_segments`, `resolution`,
  `coastline_scale`, `provenance`.
- `status: "unavailable"` — with a human-readable `reason`
  (peer not installed, network down, corrupt cache, …). The renderer
  falls back to the plain axes background.
- `status: "disabled"` — underlay turned off.

The frame manifest records the outcome under
`manifest["render"]["underlay"]`:

```json
"underlay": {
  "status": "ok",
  "reason": "",
  "topo": true,
  "coastline_segments": 12,
  "resolution": 0.25,
  "coastline_scale": "110m"
}
```

The peer caches GEBCO tile entries and Natural Earth zips on disk
(SHA-256 sidecars, verified on every hit); `viz.underlay` adds a
small in-process cache so multi-frame renders fetch once. The first
render in a new region downloads the intersecting GEBCO tile entries
(~500 MB per 90° tile, cached forever after) — the honest cost of a
global 15 arc-second basemap.

## "Country borders" is not a variable

Natural Earth country polygons are **cartographic context, not a data
variable**. A description like *"country borders of the
mediterranean"* parses to variable `country-borders`, which has no
fetch adapter — `sources.default_source("country-borders")` returns
`""` and the request is refused honestly:

> no source: 'country-borders' is cartographic context, not a data
> variable — Natural Earth country vectors are drawn as a map
> underlay (see docs/BASEMAPS.md), never as the visualization
> itself, so 'country borders' alone is refused rather than rendered
> as an empty map.

Rendering an empty map would be silent wrongness; refusing is the
feature. Country borders still appear on every map *as the coastline
underlay* — the honest way to show them.

## GEBCO as a variable: bathymetry / elevation

Descriptions containing *bathymetry, seafloor, depth, trench, abyss*
→ variable `bathymetry`; *elevation, terrain, topography, mountain*
→ variable `elevation`. Both route **globally** to source `gebco`
(GEBCO 2024, static compilation) with an inspectable
`VizSpec.source_reason`, e.g.:

```
bathymetry description -> GEBCO 2024 global topography/bathymetry (15 arc-second)
```

GEBCO is a static compilation, not a time series: the cadence is
`yearly`, and with no explicit time phrase the spec collapses to a
single frame (`start == end == today`). Colormaps: `Blues_r` for
bathymetry (deep = dark), `terrain` for elevation. Units: metres.

Bare "depth" matches but the hyphenated intensifier "in-depth" does
not (so *"an in-depth look at lake superior surface temperature"*
still parses as SST).
