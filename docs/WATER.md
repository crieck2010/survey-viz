# Terrestrial water storage (GRACE/GRACE-FO) — `water-storage` variable

`variable="water-storage"` always pins `source="grace"`: **CSR
GRACE/GRACE-FO RL06.3 mascons** — monthly terrestrial water storage
anomalies in cm of liquid-water-equivalent thickness, land-only, the
2004–2009 time-mean removed, 2002–present, keyless
(survey-currents v0.12.0+). Routing is global — the CSR archive covers
all land — so no regional restriction applies.

## Routing examples

- "Groundwater decline in California over the past 5 years" →
  `variable="water-storage"`, `source="grace"`, cadence monthly,
  `source_reason="water storage / groundwater description ->
  CSR GRACE/GRACE-FO RL06.3 terrestrial water storage anomalies
  (monthly, cm LWE, land-only)"`.
- "Total water storage across California since 2002" →
  `variable="water-storage"`, `source="grace"`.
- "Groundwater decline vs rainfall in California over the past 5
  years" → `variable="water-storage"`, `source="grace"`,
  `overlays=("tp",)` — GRACE stays the base map; precipitation is a
  contour overlay fetched as context by the pipeline.

## Parser rules (deterministic, offline)

Water-storage keywords (earliest wins against other variables):
"water storage", "groundwater", "aquifer", "terrestrial water",
"total water storage", "GRACE", "GRACE-FO", "TWS", "drought".

Disambiguation (the honest-routing rules — documented, tested):

- **Precipitation never routes to GRACE.** "rain", "rainfall",
  "precipitation" keep their own earliest-wins race to `variable="tp"`
  (IMERG for recent/observed wording, ERA5 otherwise — see
  PARSER.md §6a). Only when `water-storage` already won the primary
  race does "vs rainfall" / "with rainfall" wording add `"tp"` as a
  contour overlay.
- **River discharge routes to USGS streamgages, not GRACE.**
  "streamflow" / "river discharge" → `variable="streamflow"`,
  `source="usgs"` (USGS Water Services NWIS, survey-currents
  v0.13.0+) — see docs/STREAMFLOW.md. GRACE anomalies are monthly
  land-water-storage changes; discharge is point-source daily gage
  data. Never a GRACE map.
- **Sea level is refused, not rendered.** "sea level" (without
  "pressure") → `variable="sea-level"`, which has no fetch adapter —
  sea level is satellite altimetry, a different observable from GRACE
  terrestrial water storage. "Sea level pressure" keeps its `msl`
  (ERA5) reading via the negative lookahead in the sea-level
  patterns.

Cadence is always monthly: the GRACE product is a monthly product,
and the parser pins `cadence="monthly"` regardless of time phrasing.

## VizSpec fields

`source="grace"` (pinned, with inspectable `source_reason`),
`overlays=("tp",)` at most (MAX_OVERLAYS == 1), `vmin`/`vmax`
optional (explicit values override the zero-centered scale).

## Rendering

The generic scalar-grid renderer draws the field with:

- **Units:** cm (liquid-water-equivalent thickness anomaly).
- **Colormap:** `BrBG` diverging — brown = drier than the 2004–2009
  mean, blue = wetter.
- **Scale:** always zero-centered and symmetric (`±max|anomaly|`
  across all frames) unless `vmin`/`vmax` are pinned explicitly — the
  colormap never flickers between frames and zero always means
  "no anomaly".
- **Gap months:** months with no GRACE/GRACE-FO solution (e.g. the
  2017-07 … 2018-05 inter-mission gap, plus isolated missing months)
  are all-NaN frames. They are never interpolated; each renders as a
  **"NO GRACE OBSERVATION"** panel with the month's burned-in
  timestamp, and the manifest lists them under
  `render.gap_months`.
- **Footer:** carries the anomaly baseline ("anomaly vs 2004–2009
  mean") and the count of months with no GRACE data; the manifest
  additionally records the full baseline string under
  `render.anomaly_baseline`.
- **Overlays:** a requested `"tp"` overlay draws as contours over the
  anomaly map (the pipeline aligns monthly precipitation to the
  GRACE grid); when the context cannot be fetched the manifest
  records it as `"absent"` instead of crashing.

## Pipeline contract (reel-studio)

`fetch["grace"]` carries the `WaterField` provenance (product,
version, solution+mask URLs, SHA-256 digests, anomaly baseline, gap
months, `retrieved_at`). The pipeline fetches
`fetch_grace(bbox, start, end)` on the spec bbox and passes the field
through the ordinary scalar-grid path; an optional `"tp"` overlay is
fetched best-effort from ERA5 (monthly-aggregated daily
precipitation, resampled to the GRACE grid) and recorded as
`"absent"` in the manifest when unavailable.

## Limitations / deferred

- GRACE measures **total** water storage (groundwater + soil moisture
  + snow + surface water) — it cannot isolate groundwater alone.
  Captions and titles say "water storage", not "groundwater", unless
  the user's description said groundwater.
- Native mascon resolving power is coarser than the 0.25° output
  grid: do not claim true 0.25° resolving power in captions.
- The file's `months_missing` attribute under-reports (2011-11 and
  2015-05 are missing from the time axis but absent from the
  attribute) — gap detection diffs the actual time axis, never the
  attribute alone.
- No GRACE adapter exists for sea level — it stays an honest
  refusal (see above). River discharge / streamflow routes to USGS
  streamgages (docs/STREAMFLOW.md), not GRACE.
- Precipitation context ("vs rainfall") is best-effort: ERA5 needs a
  free CDS account; without credentials the overlay degrades to
  `"absent"`, not an error.
