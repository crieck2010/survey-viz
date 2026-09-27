# Storm tracks (IBTrACS) — `storm-tracks` variable

`survey-viz` v0.10.0 routes tropical-cyclone **track / identity /
history / ranking** requests to NOAA IBTrACS v04r01 best tracks
(`survey-currents` v0.11.0+, `currents.storms`), while **ambient storm
environment** requests keep the ERA5 reanalysis fields. The distinction
is deliberate: IBTrACS answers "which storm went where and how strong",
ERA5 answers "what did the wind and pressure fields look like".

## Routing examples

| Description | Routes to | Why |
|---|---|---|
| "Hurricane Katrina's track across the Atlantic" | `storm-tracks`, `source="ibtracs"`, `storm_name="katrina"` | Named storm + track wording = track identity |
| "Hurricane conditions in the Gulf of Mexico last summer" | `wind` + `msl` overlay, source default (ERA5) | Generic "conditions" = ambient field, not a track |
| "Strongest hurricanes of the 2024 season" | `storm-tracks`, `source="ibtracs"`, `storm_rank="strongest"` | Ranking wording = intensity-ordered tracks |
| "Hurricane Milton's track with the wind field" | `storm-tracks` + `wind` overlay | IBTrACS tracks with ERA5 wind contours underneath |
| "Hurricane rainfall last week" | `tp`, `source="imerg"` | Precipitation wording wins the earliest-wins race first (unchanged) |

## Parser rules (deterministic, offline)

Track/identity/ranking intent is detected **before** the earliest-wins
keyword race, and requires a storm word (`storm`, `cyclone`,
`hurricane`, `typhoon`):

- **Named storm** — `Hurricane Katrina`, `Tropical Storm Milton`,
  `Typhoon Haiyan's`. The name candidate must be capitalized (storm
  names are proper nouns) and not be a common noun like "season",
  "track", "conditions", or "pressure" — so "Hurricane pressure drops"
  and "Hurricane season outlook" keep the ERA5 reading. The name is
  stored lowercase in `VizSpec.storm_name` and passed to
  `fetch_ibtracs(storm_name=...)` (case-insensitive exact match).
- **Track wording** — `track(s)`, `path(s)`, `trajectory/trajectories`
  together with a storm word ("typhoon tracks in the North Pacific").
- **Ranking wording** — `strongest`, `most intense`, `most powerful`
  with a storm word ("strongest hurricanes of the 2024 season").
  Sets `VizSpec.storm_rank="strongest"`; `top N` phrasing sets
  `VizSpec.storm_top_n` (`None` = default 5). The fetch layer keeps
  the N most intense storms by **lifetime maximum sustained wind**
  (kt) — the documented aggregation rule.

A bare name with no storm word ("Katrina's path") is **not** detected —
the parser has no storm-name gazetteer. Track wording still routes to
`storm-tracks`, just without the name pin (all storms in the
bbox/window).

**ERA5 context wording** — on a `storm-tracks` request, "with the wind
field" / "with winds" adds the `wind` overlay, "with the pressure
field" / "with pressures" adds `msl` (at most one overlay). The
pipeline fetches it from ERA5 and the renderer draws it as contours
under the tracks; when the context is unavailable the renderer degrades
gracefully (records `era5_context: {name: "absent"}` in the manifest,
never a crash).

## VizSpec fields

- `storm_name: str = ""` — named-storm pin, `""` = all storms.
- `storm_rank: str = ""` — `""` or `"strongest"`.
- `storm_top_n: Optional[int] = None` — how many `storm_rank` keeps.
- `source` is always pinned to `"ibtracs"` with an inspectable
  `source_reason`; `resolve_source` / `explain_source` / `is_fetchable`
  treat it like the other pinned sources (global — no regional
  restriction, the archive covers every basin).
- Cadence is `daily`; frames show tracks **cumulatively** (frame for
  day D draws every fix on/before D).

Old spec dicts (pre-v0.10.0) load unchanged — the new fields default
safely.

## Rendering

`render_viz` detects `spec.variable == "storm-tracks"` and runs a
dedicated track renderer (the scalar pcolormesh path cannot draw
polylines). The field is duck-typed: a `StormField` or its
`to_dict()` form (`"storm_tracks"` key).

- **Track polylines** colored by Saffir-Simpson category of each
  segment's stronger endpoint (table below). Contiguous same-category
  segments are drawn as one run.
- **Dateline crossings** break the polyline — a segment whose
  endpoints jump more than 180° in longitude is never drawn, so no
  false line crosses the Pacific.
- **Genesis marker + name label** at each shown track's first fix
  (white dot, bold label with halo).
- **Category legend** (only categories actually shown), **burned-in
  timestamp**, and a **peak-wind readout** ("Peak 120 kt (Category 4
  (113–136 kt))").
- **Z-order**: GEBCO tint/hillshade (underlay) → optional ERA5
  contours → Natural Earth coastlines → **tracks on top**.
- **Manifest**: `render.cmap = "sshs-categorical"`, `vmin`/`vmax` = the
  overall wind range, `render.storms[]` = per-storm
  `{name, sid, n_fixes, max_wind_kt, max_category}`, and
  `render.era5_context` = per-overlay `"ok"`/`"absent"`.

### Saffir-Simpson mapping (mirrors `currents.storms`)

| Max sustained wind | Code | Render color |
|---|---|---|
| < 34 kt | TD (tropical depression) | `#5ebaff` (light blue) |
| 34–63 kt | TS (tropical storm) | `#00e676` (green) |
| 64–82 kt | C1 | `#ffea00` (yellow) |
| 83–95 kt | C2 | `#ff9100` (orange) |
| 96–112 kt | C3 | `#ff3d00` (red-orange) |
| 113–136 kt | C4 | `#d500f9` (magenta) |
| ≥ 137 kt | C5 | `#ff1744` (red) |
| missing | unknown | `#9aa3b2` (grey) |

**Agency caveat.** Saffir-Simpson categories are formally defined on
1-minute sustained winds. IBTrACS carries per-agency winds with
different averaging conventions; `survey-currents` prefers USA-agency
values (`usa_wind`, 1-minute) with WMO fallback, so the category
mapping is most defensible for storms/agencies reporting 1-minute
winds. Non-USA-agency intensities are colored with the same scale —
documented here rather than hidden.

## Pipeline contract (reel-studio)

`fetch_for_source("ibtracs")` returns `currents.storms.fetch_ibtracs`
(`survey-currents >= 0.11.0`). The fetch layer passes
`(bbox, start, end, min_wind=..., storm_name=spec.storm_name or None)`,
applies the `storm_rank="strongest"` top-N selection via
`StormField.rank_by_intensity()`, and hands the `StormField` (or its
`to_dict()` form) to `render_viz` directly — **not** through the
scalar `_field_to_dict` adapter. ERA5 context rides in
`field["overlay_grids"][name]` as `{"times", "lats", "lons", "grid"}`
dicts.

## Limitations / deferred

- **Landfall labels** are not drawn — genesis labels only. Landfall
  detection needs a land mask / coastline-intersection pass over the
  track; deferred.
- **Bare names** ("Katrina's path" with no storm word) do not pin the
  storm name (no storm-name gazetteer in the parser).
- **Ranking = lifetime max sustained wind.** "Strongest" by ACE,
  damage, or central pressure is not offered — the aggregation rule is
  documented so the ranking is never misread.
- Numbered depressions ("tropical depression 10") do not pin a name;
  track wording still routes them to `storm-tracks`.
