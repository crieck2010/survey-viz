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
| `sea-ice` *(NSIDC)* | sea ice / sea-ice, pack ice, ice concentration, ice cover, ice extent, **ice** |
| `land-ice` *(no adapter)* | glacier(s), ice sheet(s), iceberg(s), land ice |
| `night-lights` *(Black Marble)* | black marble, vnp46a2, night/city/urban light(s), light pollution, electrification |
| `power-outage` *(no adapter)* | blackout(s), power outage(s) |
| `bathymetry` *(GEBCO)* | bathymetry, seafloor, depth(s) (not "in-depth"), trench(es), abyss, hadal |
| `elevation` *(GEBCO)* | elevation(s), terrain(s), topography, mountain(s) |
| `country-borders` *(no adapter)* | country borders/boundaries, national borders, political boundaries |

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
- **`land-ice` wins ties against `sea-ice`.** "ice sheet" matches both
  the `sea-ice` pattern (`\bice\b`) and the `land-ice` pattern at the
  same position; the land-ice reading wins because glaciers / ice
  sheets / icebergs are a different physical product from sea-ice
  concentration — routing them to NSIDC would be a lie. (Same
  mechanism as `burn-scar` winning ties against `fire`.)
- **`land-ice` is an honest refusal, not a fetch.** It parses (so the
  failure message is precise) but `viz.sources.default_source` returns
  `""` for it — no adapter exists. Downstream, `reel-studio` refuses
  with a message explaining that glaciers / ice sheets / icebergs need
  a different product (future work), and that "sea ice" routes to
  NSIDC.
- **`sea-ice` routes to NSIDC only in polar regions.** `default_source`
  returns `"nsidc"` for the `arctic-ocean` / `southern-ocean` gazetteer
  regions and `""` elsewhere: the G02135 grids cover north of 30.98°N /
  south of 39.23°S, so mid-latitude "sea ice" has no ice domain in the
  product and would return an all-NaN field.
- **Fire specs render daily.** `fire` is the one variable whose parser
  default cadence is `daily` (not `monthly`): fires are fast phenomena,
  and the FIRMS density grid is one grid per UTC date — monthly
  bucketing would show only a single day per month.
- **Sea-ice specs render daily too.** `sea-ice` joins `fire` in the
  `daily` cadence default (v0.6.0): ice moves fast, and the NSIDC
  source is daily — monthly bucketing would show only a single day
  per month.
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
    fire → "Active Fires", burn-scar → "Burn Scar",
    sea-ice → "Sea Ice", land-ice → "Land Ice".
  - Years: `2021–2026` (en dash) across years, or `2026` for a single year.
  - Example: "Lake Superior — Surface Water Temperature, 2021–2026".

## 5. Cadence / layout / style

The parser always emits `layout="reel-vertical"`, `style="reel-dark"`,
and `cadence="monthly"` — except for `fire`, `sea-ice`, and
`night-lights`, which emit `cadence="daily"`, and `bathymetry` /
`elevation`, which emit `cadence="yearly"` (GEBCO is a static
compilation, not a time series; with no explicit time phrase the
spec collapses to a single frame, `start == end == today`). (Finer
control belongs to the spec/CLI layer, not to free text.)

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
`amazon-basin`, `australia-southeast`, `boreal-canada`),
`sea-ice` in the polar regions (`arctic-ocean`, `southern-ocean`) →
`nsidc` (NSIDC G02135 v4.0 — daily sea-ice concentration over keyless
HTTPS; `sea-ice` elsewhere, Great Lakes `currents`, `chlorophyll`,
`burn-scar`, `land-ice`, and `power-outage` have no adapter → `""`,
and `is_fetchable` refuses them honestly).
`night-lights` → `blackmarble` in any region (NASA Black Marble VNP46A2
V002 daily gap-filled lunar BRDF-adjusted DNB radiance,
2012-01-19–present, free Earthdata Login — the parser always pins it,
with `source_reason="night-lights observations -> NASA Black Marble
VNP46A2 daily corrected radiance"`).
`bathymetry` / `elevation` → `gebco` in any region (GEBCO 2024 global
topography/bathymetry, 15 arc-second — the parser always pins it,
with an inspectable `source_reason`, e.g. `"bathymetry description ->
GEBCO 2024 global topography/bathymetry (15 arc-second)"`).
`country-borders` has no adapter — Natural Earth country vectors are
a cartographic *underlay*, not a data variable, so "country borders"
alone is refused honestly (see `docs/BASEMAPS.md`).

### 6a. Precipitation: observed (IMERG) vs reanalysis (ERA5)

Precipitation (`variable="tp"`) is the one variable with two honest
sources, and the parser chooses between them from the description:

- **NASA GPM IMERG V07** (`source="imerg"`) — *satellite-observed*
  precipitation: half-hourly, 0.1°, 2000–present, Early (~4 h) / Late
  (~14 h) / Final (~3.5 months, gauge-adjusted) latency runs. This is
  the right source for *what actually fell*: recent rain, a storm last
  week, hurricane rainfall, an observed event.
- **Copernicus ERA5** (`source="era5"`) — *reanalysis* precipitation:
  hourly, 0.25°, 1940–present, model physics constrained by
  observations. This is the right source for *climatology and trends*:
  multi-decade rainfall trends, precipitation since 1980,
  long-term climatology.

Pinning order for `tp` (first match wins, word boundaries throughout):

1. explicit `IMERG` → `imerg` ("explicit IMERG request");
2. explicit `ERA5` → `era5` ("explicit ERA5 request");
3. recent/observed/event wording — `recent`, `last week`, `event`,
   `storm`, `hurricane`, `typhoon`, `cyclone`, `observed`,
   `satellite`, `high resolution`, `ultra` → `imerg`;
4. long-record wording — `trend`, `climatology`, `since 19…` /
   `since 20…`, `decades`, `long-term` → `era5`;
5. anything else → `source=""` → the regional default, `era5`.

Whenever the parser pins a source it also records
`VizSpec.source_reason` (a short human-readable justification, e.g.
`"recent/observed precipitation wording -> NASA GPM IMERG V07"`).
`viz.sources.explain_source(spec)` turns any spec — pinned, defaulted,
or refused — into a one-line explanation ("source 'imerg' (NASA GPM
IMERG V07): explicit IMERG request." / "source 'era5' (Copernicus ERA5
(CDS)): regional default — 'tp' is an ERA5 reanalysis variable; the
parser did not pin a source." / "no source: …").

Note: descriptions *beginning* with "hurricane"/"storm" parse as the
`(wind, overlays=["msl"])` storm combination (earliest keyword wins),
so hurricane *rainfall* requests must put the rainfall keyword first
("rainfall from the hurricane …"). And "recent decades" contains
"recent" — it pins IMERG by rule 3; write "across the decades" for an
ERA5 trend.

### 6b. Night lights (Black Marble) vs power outages (honest refusal)

Night lights (`variable="night-lights"`) always pin
`source="blackmarble"`: **NASA Black Marble VNP46A2 V002** — daily
gap-filled lunar BRDF-adjusted DNB radiance, 2012-01-19–present, free
Earthdata Login (survey-currents v0.9.0+). Keywords: `night lights`,
`city lights`, `urban lights`, `light pollution`, `electrification`,
`development`, `growth of cities`, `Black Marble`, `VNP46A2`.
Cadence is daily (one frame per day), like fire and sea ice.

**Bare `development` wins the earliest-wins contest against later
variable keywords** — "development of a hurricane" parses as
`night-lights`, not the storm combination. In night-lights context
this is the intended reading (urban development); in storm context,
put the storm keyword first.

"Power outage" / "blackout" is a *different variable*,
`variable="power-outage"`, and it has **no fetch adapter** — the
parser produces an honest refusal instead of a map. Outage mapping is
temporal change detection across two or more epochs: a single daily
Black Marble map cannot show a blackout, so routing blackout wording
to the lights product would be a lie. Safety rule: `power-outage`
wording *anywhere* in the text overrides a `night-lights` win
("city lights during the blackout" → `power-outage`, refused —
the change-detection reading is the honest one, the same way
`burn-scar` wins ties against `fire`).

- "City lights across California this year" →
  `variable="night-lights"`, `source="blackmarble"`
  ("night-lights observations -> NASA Black Marble VNP46A2 daily
  corrected radiance"), cadence daily.
- "Electrification across the Amazon Basin since 2019" →
  `variable="night-lights"`, `source="blackmarble"`.
- "Growth of cities in Southeast Australia over the past 5 years"
  → `variable="night-lights"`, `source="blackmarble"`.
- "Power outage in California last week" →
  `variable="power-outage"`, no source — honest refusal
  ("no source: 'power-outage' (blackout / power-outage mapping) is
  temporal change detection across two or more epochs …").

### 6c. Storm tracks (IBTrACS) vs storm conditions (ERA5)

`variable="storm-tracks"` always pins `source="ibtracs"`: **NOAA
IBTrACS v04r01** — global tropical-cyclone best tracks, 1980–present,
keyless (survey-currents v0.11.0+). Track/identity/ranking intent is
detected **before** the earliest-wins keyword race, and requires a
storm word (`storm`, `cyclone`, `hurricane`, `typhoon`):

- **Named storm** — "Hurricane Katrina", "Tropical Storm Milton",
  "Typhoon Haiyan's". The name candidate must be capitalized (storm
  names are proper nouns) and must not be a common noun — "season",
  "track", "conditions", "pressure", "wind", "rain", "surge" are
  excluded, so "Hurricane pressure drops" and "Hurricane season
  outlook" keep the ERA5 reading. Stored lowercase in
  `VizSpec.storm_name` (`""` = all storms).
- **Track wording** — "track(s)", "path(s)",
  "trajectory/trajectories" with a storm word.
- **Ranking wording** — "strongest", "most intense", "most powerful"
  with a storm word → `VizSpec.storm_rank="strongest"`; "top N"
  phrasing sets `VizSpec.storm_top_n` (`None` = default 5). Ranked by
  lifetime maximum sustained wind (kt) — documented, never implicit.

A bare name with no storm word ("Katrina's path") does **not** pin
the name (no storm-name gazetteer); track wording still routes to
`storm-tracks` without the pin.

- "Hurricane Katrina's track across the Atlantic" →
  `variable="storm-tracks"`, `source="ibtracs"`, `storm_name="katrina"`.
- "Hurricane conditions in the Gulf of Mexico last summer" →
  `variable="wind"` + `("msl",)` overlay, no source pin — the ambient
  storm-environment reading (unchanged behavior).
- "Strongest hurricanes of the 2024 season" →
  `variable="storm-tracks"`, `source="ibtracs"`, `storm_rank="strongest"`.
- "Hurricane Milton's track with the wind field" →
  `variable="storm-tracks"`, `source="ibtracs"`, overlays `("wind",)` —
  the **IBTrACS+ERA5** combination: tracks from IBTrACS, ambient wind
  contours from ERA5 (graceful "absent" if unavailable).

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
- "Arctic Ocean sea ice over the past 5 years" →
  `variable="sea-ice"`, `region_key="arctic-ocean"`,
  `cadence="daily"`, `source=""` → resolves to `nsidc` (NSIDC G02135
  v4.0 daily sea-ice concentration)
- "Southern Ocean ice concentration last summer" →
  `variable="sea-ice"`, `region_key="southern-ocean"`, `source=""` →
  resolves to `nsidc`
- "Antarctic pack ice this year" → `variable="sea-ice"`,
  `region_key="southern-ocean"` (via the new `antarctic` alias),
  `source=""` → resolves to `nsidc`
- "Antarctic ice sheet this year" → `variable="land-ice"` → honest
  refusal (glaciers / ice sheets / icebergs are a different physical
  product from sea-ice concentration — never routed to NSIDC)
- "IMERG rainfall over the Gulf of Mexico last week" →
  `variable="tp"`, `source="imerg"` ("explicit IMERG request")
- "recent satellite rainfall over the Gulf of Mexico" →
  `variable="tp"`, `source="imerg"` (recent/observed wording →
  NASA GPM IMERG V07)
- "rainfall from the hurricane over the Gulf of Mexico" →
  `variable="tp"`, `source="imerg"` (event wording → IMERG; the
  rainfall keyword must come first — a leading "hurricane" parses as
  the wind+isobars storm combination)
- "ERA5 precipitation over the Gulf of Mexico since 1980" →
  `variable="tp"`, `source="era5"` ("explicit ERA5 request")
- "precipitation climatology over the Gulf of Mexico" →
  `variable="tp"`, `source="era5"` (long-record wording → ERA5
  reanalysis, 1940–present)
- "rainfall over the Gulf of Mexico" → `variable="tp"`, `source=""` →
  resolves to `era5` (regional default; `explain_source` says so)

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
