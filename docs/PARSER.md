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
| `sst` | sst, temperature(s), thermal, warmth, warm, cold, heat, degree(s), celsius, °c, deg c |
| `currents` | current(s), flow(s), stream(s), velocity, circulation, drift |
| `chlorophyll` | chlorophyll, chl, algae, algal, bloom(s), phytoplankton |

Rules:

- **Earliest keyword in the text wins.** "temperature and currents" → `sst`;
  "currents and temperature" → `currents`. (Deterministic, no priority list
  to memorize.)
- **No variable keyword → `sst`.** This is the documented default rule:
  "Show me Lake Superior" is a temperature visualization. Rationale: sst is
  the only variable with a fetch adapter today (GLSEA), so the default keeps
  every parse actionable.
- No `UnparseableDescription` is raised for a missing variable — only for a
  missing region, conflicting time phrases, or an invalid year range.

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
    chlorophyll → "Chlorophyll-a".
  - Years: `2021–2026` (en dash) across years, or `2026` for a single year.
  - Example: "Lake Superior — Surface Water Temperature, 2021–2026".

## 5. Cadence / layout / style

The parser always emits `cadence="monthly"`, `layout="reel-vertical"`,
`style="reel-dark"`. (Finer control belongs to the spec/CLI layer, not to
free text.)

## 6. Source pinning (SST quality keywords)

`VizSpec.source` is pinned to `"mur"` when the description asks for high
resolution — keywords `high resolution` / `high-resolution`, `ultra`,
`coastal detail`, `1 km`, `kilometre`/`kilometer` — **and** the variable
is SST. Anything else leaves `source` empty (`""`), so
`viz.sources.resolve_source` applies the regional default at fetch time:
SST over the 5 Great Lakes → `glsea`, SST anywhere else → `oisst`.
Non-SST variables never pin a source (MUR is SST-only).

Examples:

- "High resolution North Atlantic sea surface temperature over the past
  2 years" → `source="mur"` (NASA JPL MUR v4.1, ~1 km)
- "North Atlantic sea surface temperature over the past 2 years" →
  `source=""` → resolves to `oisst` (NOAA OISST v2.1, 0.25°)
- "Lake Superior surface temperature over the past 5 years" →
  `source=""` → resolves to `glsea` (NOAA GLSEA)

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
