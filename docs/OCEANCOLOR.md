# Ocean Color in survey-viz

Ocean color maps **chlorophyll-a** (phytoplankton biomass) as the
canonical variable `ocean-color`. `chlorophyll` is kept as a legacy
alias and is canonicalized by `VizSpec` — old serialized specs keep
working and render identically.

## How it parses

| Request shape | variable | context |
|---|---|---|
| "chlorophyll / ocean color / phytoplankton / algae bloom in …" | `ocean-color` | () |
| bloom **with ocean currents** / "bloom drifting with currents" | `ocean-color` | ("currents",) |
| "bloom conditions", "chlorophyll vs temperature", SST wording | `ocean-color` | ("sst",) |
| "green ocean in …" | `ocean-color` | () — never a terrestrial water product |
| "algae bloom in Lake Erie" | `ocean-color` | () — the L3 grid covers the lake; see limits below |
| "nitrate / turbidity / dissolved oxygen / water quality in …" | — | **refused**: no terrestrial water-quality adapter |

Cadence defaults to `monthly`; "daily" wording requests daily (the
adapter refuses yearly).

## How it renders

- Chlorophyll-a is lognormally distributed (0.01–100 mg/m³ is the
  honest dynamic range), so the map uses a **logarithmic color scale**
  (`matplotlib.colors.LogNorm`, `viridis` colormap, units mg/m³).
  The manifest records `"log_scale": true`.
- Cloud-covered, land, and missing cells stay **NaN — never filled or
  interpolated**. On a frame they show the GEBCO / Natural Earth
  underlay beneath as honest "no observation" regions.
- A frame that is entirely cloud-covered renders a
  **"NO OCEAN COLOR OBSERVATION"** panel (mirroring the GRACE gap
  treatment) and is listed in the manifest under
  `render.gap_frames`. The footer notes how many frames have no
  observation.
- `context` companion datasets are fetched alongside the main one
  (currently supported: `"currents"`, `"sst"`). Context is separate
  from contour `overlays` and is recorded in the manifest
  (`render.context`) and footer ("… + currents context").

## Data source

`"oceancolor"` (survey-currents ≥ 0.14.0, lazy import —
`currents.oceancolor.fetch_oceancolor`): NOAA CoastWatch ERDDAP keyless
grids (MODIS Aqua R2022 L3 chlorophyll-a, ~4 km, monthly default,
2002-present; VIIRS SNPP L3, ~4 km, daily/8-day/monthly, 2012-present;
ESA OC-CCI v6.0 merge, monthly, 1997-present). The required
credentials-gated fallback is NASA OBPG MODIS Aqua L3 mapped
chlorophyll-a (free Earthdata Login); Copernicus Marine is an optional
extra authenticated source. `source="auto"` tries CoastWatch, then OBPG
(when Earthdata credentials exist), then CMEMS (when CMEMS credentials
exist). Partial and full cloud gaps are part of the product, not a bug
— see "How it renders".

## Limits

- Global L3 ocean-color products are tuned for open ocean. **Coastal
  and inland waters** (estuaries, Lake Erie, the nearshore zone) are
  affected by land adjacency, bottom reflectance, and
  case-2-water constituents (CDOM, suspended sediment) that the
  standard chlorophyll algorithm does not separate. Values there are
  indicative, not quantitative.
- Cloud cover means some frames or regions have no observation at
  all — the map says so instead of inventing data.
- Genuine terrestrial water-quality parameters (nitrate, turbidity,
  dissolved oxygen, "water quality") have no adapter and are refused
  honestly rather than silently mapped to SST.
