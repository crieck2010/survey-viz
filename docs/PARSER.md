# survey-viz parser grammar (deterministic, offline, stdlib-only)

`viz.parser.parse_description(text, title=None, today=None) -> VizSpec`

The parser is intentionally **not** an LLM: regex + gazetteer lookup, fully
offline, fully deterministic. Same input → same spec, every time. A future app
layer may add an LLM upgrade path for free-form descriptions; the engine
stays deterministic.

## Pipeline

```
text -> normalize -> region -> variable -> time -> title -> VizSpec
```

Normalization: strip, collapse whitespace, lowercase. Periods are dropped
before region matching so "St. Lawrence" matches "st lawrence".

## 1. Region matching

- Source: `data/regions.yaml` — 25 entries: the 5 Great Lakes + 20 coastal
  regions/bays/seas. Each entry has `key`, display `name`, `aliases`,
  `bbox`, and a `notes` data-availability note.
- Matching is **case-insensitive substring** over display names + aliases.
- **Longest match wins**: "Lake Superior" (13 chars) beats the bare alias
  "superior" when both appear in the text.
- No match → `UnparseableDescription`.

Example aliases: `superior`, `gitche gumee` → lake-superior; `sea of cortez`
→ gulf-of-california; `med sea` → mediterranean-sea; `sf bay` →
san-francisco-bay.

## 2. Variable keywords

| VizSpec.variable | keywords |
|---|---|
| `sst` | sst, temperature(s), thermal, warmth, warm, cold, degree(s), celsius, °c, deg c |
| `currents` | current(s), flow(s), stream(s), velocity, circulation, drift, eddy/eddies |
| `chlorophyll` | chlorophyll, chl, algae, algal, bloom(s), phytoplankton |
| `wind` *(ERA5)* | wind(s), windy, gale(s), gust(s) |
| `msl` *(ERA5)* | pressure(s), isobar(s), sea-level pressure |
| `t2m` *(ERA5)* | air temperature(s), atmospheric temperature(s), heatwave(s), **heat** |
| `tp` *(ERA5)* | rain, rainfall, precipitation, deluge(s), downpour(s) |
| `fire` *(FIRMS)* | fire(s), wildfire(s), burning, burn(s) |
| `burn-scar` *(no adapter)* | burn scar(s), burned area(s), burn severity |

Rules:

- **Earliest keyword in the text wins.** "temperature and currents" → `sst`;
  "currents and temperature" → `currents`. (Deterministic, no priority list
  to memorize.)
- **`burn-scar` wins ties against `fire`.** "burn scar" matches both the
  `fire` pattern (`burns?`) and the `burn-scar` pattern at the same
  position; the scar reading wins because FIRMS is active-fire
  *detections* only — burned-area / burn-severity mapping is
  `survey-burn`'s future imagery adapter, and the honest refusal (below)
  names it rather than misrouting to fire detections.
- **`burn-scar` is an honest refusal, not a fetch.** It parses (so the
  failure message is precise) but `viz.sources.default_source` returns
  `""` for it — no adapter exists. Downstream, `reel-studio` refuses
  with a message pointing at `survey-burn` as the future home of
  burn-scar mapping. This is the `chlorophyll` precedent: parseable,
  not fetchable, never silently misrouted.
- **Fire specs render daily.** `fire` is the one variable whose parser
  default cadence is `daily` (not `monthly`): fires are fast phenomena,
  and the FIRMS density grid is one grid per UTC date — monthly
  bucketing would show only a single day per month.
- **Storm keywords** (`storm(s)`, `cyclone(s)`, `hurricane(s)`, `typhoon(s)`)
  compete in the same earliest-wins race and map to the
  **`wind` + `overlays=["msl"]` combination**: a wind base map with pressure
  isobars overlaid. If another variable keyword appears *earlier* in the
  text, that variable wins without the overlay (the storm keyword only
  fires when it is the earliest variable keyword).
- **`heat` means air heat.** The disambiguation rule (v0.3.0): bare "heat"
  (as in "heat in Texas", "the heat last summer") maps to ERA5 2-m air
  temperature (`t2m`), not water temperature. `warm` / `warmth` / `cold` /
  `thermal` still map to SST — they describe water in every existing test
  ("superior warmth", "cold water", "thermal map") and stay backward
  compatible. "air temperature" and "heatwave" are unambiguous `t2m`.
- **No variable keyword → `sst`.** This is the documented default rule:
  "Show me Lake Superior" is a temperature visualization. Rationale: sst is
  the historical default variable; the default keeps every parse actionable.
- No `UnparseableDescription` is raised for a missing variable — only for a
  missing region, conflicting time phrases, or an invalid year range.

## 2a. Overlays

`VizSpec.overlays` (max 1 in v0.3.0) names contour overlays drawn over the
base-variable map. Today the only producer is the storm combination
(`overlays=["msl"]` → isobars over the wind map). The renderer draws one
contour per frame with per-frame levels (4 hPa steps for `msl`), labeled
in the overlay's units. If the fetched field cannot supply the overlay
grid, `render_viz` raises `ValueError` rather than silently dropping it.
`VizSpec` rejects an overlay that duplicates the base variable or exceeds
the cap; v0.2.0 spec dicts (no `"overlays"` key) deserialize unchanged.

## 3. Time phrases

`today` defaults to the current date (pass it explicitly for deterministic tests).

| phrase | start | end |
|---|---|---|
| `past N years` / `last N years` | today − N years | today |
| `past N months` | today − N calendar months | today |
| `last summer` | Jun 1 of the most recent **fully-completed** Jun–Aug window | Aug 31 of same |
| `this year` | Jan 1 of current year | today |
| `2015 to 2020`, `2015-2020`, `2015–2020`, `2015 through 2020` | Jan 1, 2015 | Dec 31, 2020 |
| `since 2018` | Jan 1, 2018 | today |
| *(no time phrase)* | **today − 1 year (documented default)** | today |

Notes:

- Year subtraction handles Feb 29 → Feb 28.
- `last summer` detail: if today's date is past Aug 31 of this year, "last
  summer" is this year's Jun–Aug; otherwise it is last year's (the
  in-progress summer doesn't count as complete).
- **Conflicting time phrases raise** `UnparseableDescription`
  (e.g. "past 3 years 2015 to 2020" — use exactly one).
- **Invalid ranges raise**: "2020 to 2015" → start must not be after end.

## 4. Title

- `title=` kwarg wins when given.
- Otherwise derived: `{Region Name} — {Variable Label}, {years}`
  - Labels: sst → "Surface Water Temperature", currents → "Surface Currents",
    chlorophyll → "Chlorophyll-a", wind → "10-m Wind", msl → "Sea-Level Pressure",
    t2m → "2-m Air Temperature", tp → "Precipitation",
    fire → "Active Fires", burn-scar → "Burn Scar".
  - Years: `2021–2026` (en dash) across years, or `2026` for a single year.
  - Example: "Lake Superior — Surface Water Temperature, 2021–2026".

## 5. Cadence / layout / style

The parser always emits `layout="reel-vertical"`, `style="reel-dark"`,
and `cadence="monthly"` — except for `fire`, which emits
`cadence="daily"`. (Finer control belongs to the spec/CLI layer, not to
free text.)

## 6. Source pinning (quality keywords)

`VizSpec.source` is pinned when the description asks for high resolution —
keywords `high resolution` / `high-resolution`, `ultra`,
`coastal detail`, `1 km`, `kilometre`/`kilometer`:

- variable is SST → pinned to `"mur"` (NASA JPL MUR v4.1, ~1 km);
- variable is `currents` → pinned to `"cmems-currents"` (CMEMS global
  ocean physics, 1/12° — the coarsest true fit for a high-resolution
  current request).

Anything else leaves `source` empty (`""`), so
`viz.sources.resolve_source` applies the regional default at fetch time:
SST over the 5 Great Lakes → `glsea`, SST anywhere else → `oisst`,
`wind`/`msl`/`t2m`/`tp` anywhere → `era5` (Copernicus ERA5 reanalysis via
the CDS API — fetchable in **any** region), `currents` anywhere except
the 5 Great Lakes → `oscar` (NASA PODAAC OSCAR v2.0 — fetchable in any
non-Great-Lakes region, including the 35th region, `gulf-stream`),
`fire` anywhere → `firms` (NASA FIRMS — fetchable in **any** region,
including the 5 new fire regions: `california`, `pacific-northwest`,
`amazon-basin`, `australia-southeast`, `boreal-canada`).
Great Lakes `currents`, `chlorophyll`, and `burn-scar` have no adapter
→ `""`, and `is_fetchable` refuses them honestly.

Examples:

- "High resolution North Atlantic sea surface temperature over the past
  2 years" → `source="mur"` (NASA JPL MUR v4.1, ~1 km)
- "North Atlantic sea surface temperature over the past 2 years" →
  `source=""` → resolves to `oisst` (NOAA OISST v2.1, 0.25°)
- "Lake Superior surface temperature over the past 5 years" →
  `source=""` → resolves to `glsea` (NOAA GLSEA)
- "North Atlantic winds over the past year" → `variable="wind"`,
  `source=""` → resolves to `era5` (Copernicus ERA5, 10-m wind)
- "Hurricane conditions in the Gulf of Mexico last summer" →
  `variable="wind"`, `overlays=["msl"]`, `source=""` → `era5`
- "Air temperature across the US East Coast this year" →
  `variable="t2m"`, `region_key="us-east-coast"`, `source=""` → `era5`
- "North Atlantic currents over the past year" → `variable="currents"`,
  `source=""` → resolves to `oscar` (NASA PODAAC OSCAR v2.0)
- "Gulf Stream eddies last summer" → `variable="currents"`,
  `region_key="gulf-stream"`, `source=""` → resolves to `oscar`
- "ultra high resolution currents in the Caribbean" →
  `variable="currents"`, `region_key="caribbean-sea"`,
  `source="cmems-currents"` (CMEMS global ocean physics)
- "California wildfires last summer" → `variable="fire"`,
  `region_key="california"`, `cadence="daily"`, `source=""` →
  resolves to `firms` (NASA FIRMS active-fire detections)
- "Amazon burning since 2019" → `variable="fire"`,
  `region_key="amazon-basin"`, `source=""` → `firms`
- "Mediterranean burn scars 2020 to 2024" → `variable="burn-scar"` →
  honest refusal (no adapter; burn-severity mapping is survey-burn's
  future domain, not FIRMS)

## 7. Failure mode

`UnparseableDescription` messages contain:

1. the offending description,
2. what was understood (`region=…; variable=…; time=…`),
3. what failed,
4. 2–3 example descriptions that DO parse,
5. a note that a future app layer may add an LLM upgrade path.

Examples that DO parse:

- "Lake Superior surface temperature over the past 5 years"
- "Gulf of Mexico currents, 2015 to 2020"
- "Chlorophyll in Puget Sound last summer"
